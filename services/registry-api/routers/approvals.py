"""
AgentShield Registry API — Approvals router.

Endpoints
---------
  POST  /api/v1/approvals/           — create approval request (called by Safety Orchestrator)
  GET   /api/v1/approvals/           — list approvals (production context, scoped to authority)
  GET   /api/v1/approvals/{id}       — get single approval (reviewer fetches before deciding)
  PATCH /api/v1/approvals/{id}       — approve/reject with optimistic lock (authority-gated)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from approval_timeout_worker import _agent_pod_url
from db import AsyncSessionLocal, get_db
from models import AgentRun, Approval, ApprovalAuthority
from schemas import ApprovalCreate, ApprovalDecision, ApprovalResponse, PaginatedResponse

# Roles that always have authority to see/decide production approvals,
# even without a specific per-resource ApprovalAuthority record.
_ADMIN_ROLES = {"platform_admin", "team_lead"}


class ReopenRequest(BaseModel):
    timeout_seconds: int = Field(1800, ge=60, le=86400)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


async def _resume_and_advance(
    agent_name: str, team: str, thread_id: str,
    decision: str, reviewer_id: str | None, reason: str | None,
) -> None:
    """Background: resume the paused agent pod, then advance the workflow if this
    approval belonged to a composite-workflow member.

    Runs fire-and-forget from decide_approval so the reviewer's request returns
    immediately (the member may take a while to finish the rest of its graph).
    Best-effort throughout — never raises.
    """
    member_output, member_status = "", "failed"
    try:
        pod_url = _agent_pod_url(agent_name, team)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{pod_url}/resume/{thread_id}",
                json={"decision": decision, "reviewer_id": reviewer_id, "reason": reason},
            )
        member_status = "completed" if resp.status_code == 200 else "failed"
        try:
            member_output = (resp.json() or {}).get("response", "") or ""
        except Exception:
            member_output = ""
        logger.info("resume posted thread_id=%s status=%d", thread_id, resp.status_code)
    except Exception as exc:
        logger.warning("failed to resume agent pod for thread_id=%s: %s", thread_id, exc)
        return

    # Workflow re-entry: is this thread a member of a paused composite workflow?
    try:
        async with AsyncSessionLocal() as s:
            child = (await s.execute(
                select(AgentRun)
                .where(AgentRun.thread_id == thread_id, AgentRun.parent_run_id.isnot(None))
                .order_by(AgentRun.started_at.desc())
            )).scalars().first()
            if not child or not child.parent_run_id:
                return
            parent = (await s.execute(
                select(AgentRun).where(AgentRun.id == child.parent_run_id)
            )).scalar_one_or_none()
            if not parent or not parent.workflow_id or not parent.orchestrator_state:
                return
            # Close out the paused child with the resumed member's result.
            child.status = member_status
            child.output = member_output[:4000] if member_output else None
            child.completed_at = datetime.now(tz=timezone.utc)
            await s.commit()
            parent_id = str(parent.id)
    except Exception as exc:
        logger.warning("workflow re-entry lookup failed for thread_id=%s: %s", thread_id, exc)
        return

    try:
        from workflow_orchestrator import resume_orchestration
        asyncio.create_task(resume_orchestration(parent_id, member_output, member_status))
    except Exception as exc:
        logger.warning("failed to schedule resume_orchestration for parent %s: %s", parent_id, exc)


async def _resolve(approval_id: uuid.UUID, db: AsyncSession) -> Approval:
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval '{approval_id}' not found.",
        )
    return row


async def _get_authority_tool_names(caller: str, db: AsyncSession) -> list[str]:
    """Return tool names this caller has active ApprovalAuthority over."""
    q = select(ApprovalAuthority.resource_id).where(
        ApprovalAuthority.resource_type == "tool",
        ApprovalAuthority.revoked_at.is_(None),
        ApprovalAuthority.approver_user_id == caller,
    )
    result = await db.execute(q)
    return [row[0] for row in result.all()]


async def _has_authority_for_tool(caller: str, tool_name: str, db: AsyncSession) -> bool:
    """Check if caller has active ApprovalAuthority for a specific tool."""
    q = select(ApprovalAuthority).where(
        ApprovalAuthority.resource_type == "tool",
        ApprovalAuthority.resource_id == tool_name,
        ApprovalAuthority.revoked_at.is_(None),
        ApprovalAuthority.approver_user_id == caller,
    )
    result = await db.execute(q)
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=ApprovalResponse,
    summary="Create approval request",
)
async def create_approval(
    body: ApprovalCreate,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Create a HITL approval request. Called by the Safety Orchestrator when an OPA
    evaluation returns `require_approval`. `timeout_seconds` controls auto-expiry."""
    now = datetime.now(tz=timezone.utc)
    expires_at = now + timedelta(seconds=body.timeout_seconds)

    # Idempotency: LangGraph re-runs the tool node on resume, so `governed_tool`
    # re-POSTs the same approval before `interrupt()` returns the cached decision.
    # If a PENDING approval already exists for this exact (thread_id, tool, args),
    # return it instead of creating a phantom duplicate that would linger in the
    # panel. (Matches only pending — a decided/expired one shouldn't be reused.)
    existing = (
        await db.execute(
            select(Approval)
            .where(
                Approval.thread_id == body.thread_id,
                Approval.tool_name == body.tool_name,
                Approval.tool_args == body.tool_args,
                Approval.status == "pending",
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "create_approval: reusing pending approval id=%s (idempotent re-post) thread=%s tool=%s",
            existing.id, body.thread_id, body.tool_name,
        )
        return ApprovalResponse.model_validate(existing)

    # The agent pod cannot tell whether a run came from the Evaluate tab, a
    # sandbox deployment chat, or a production chat (the same pod serves all,
    # and its context env var is static). The Registry is the source of truth:
    # derive context from the run this approval belongs to.
    context = await _derive_context(body.thread_id, body.context, db)

    # Only production approvals trigger Slack/on-call notifications.
    notify_slack = context == "production"

    approval = Approval(
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        team=body.team,
        thread_id=body.thread_id,
        tool_name=body.tool_name,
        tool_args=body.tool_args,
        risk_level=body.risk_level,
        trace_id=body.trace_id,
        expires_at=expires_at,
        session_id=body.session_id,
        opa_decision_id=body.opa_decision_id,
        context=context,
        reasoning=body.reasoning,
        notify_slack=notify_slack,
    )
    db.add(approval)
    await db.flush()

    # Notify approvers — look up ApprovalAuthority records for this tool
    # (Slack notification deferred to Phase 11; log for now)
    if context == "production":
        auth_q = select(ApprovalAuthority).where(
            ApprovalAuthority.resource_type == "tool",
            ApprovalAuthority.resource_id == body.tool_name,
            ApprovalAuthority.revoked_at.is_(None),
        )
        auth_result = await db.execute(auth_q)
        authorities = auth_result.scalars().all()
        approvers = [
            a.approver_user_id or f"role:{a.approver_role}" for a in authorities
        ]
        if approvers:
            logger.info(
                "create_approval: would notify approvers %s for tool=%s approval_id=%s",
                approvers, body.tool_name, approval.id,
            )

    logger.info(
        "create_approval: id=%s agent=%s tool=%s risk=%s context=%s expires=%s",
        approval.id, approval.agent_name, approval.tool_name,
        approval.risk_level, approval.context, approval.expires_at,
    )
    return ApprovalResponse.model_validate(approval)


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[ApprovalResponse],
    summary="List approval requests",
)
async def list_approvals(
    agent_name: Optional[str] = Query(None, description="Filter by agent name"),
    status_filter: Optional[str] = Query(
        None, alias="status",
        pattern="^(pending|approved|rejected|timed_out)$",
    ),
    thread_id: Optional[str] = Query(None),
    team: Optional[str] = Query(None, description="Filter by agent team"),
    context: Optional[str] = Query(
        None,
        pattern="^(production|playground|sandbox)$",
        description="Filter by context. Defaults to 'production' when not specified.",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ApprovalResponse]:
    """List approvals. Defaults to production context. Pass context=playground
    to list playground approvals (self-service, no reviewer authority required)."""
    q = select(Approval).order_by(Approval.created_at.desc())

    # Default to production; explicit context param overrides
    effective_context = context if context else "production"
    q = q.where(Approval.context == effective_context)

    if agent_name:
        q = q.where(Approval.agent_name == agent_name)
    if status_filter:
        q = q.where(Approval.status == status_filter)
    if thread_id:
        q = q.where(Approval.thread_id == thread_id)
    if team:
        q = q.where(Approval.team == team)

    if x_user_sub:
        # Scope to tools where caller has authority OR there's an authority record
        # for an admin role (hardcoded admin roles get all-access)
        auth_tool_names = await _get_authority_tool_names(x_user_sub, db)

        # Also include tools with role-based authority (platform_admin/team_lead)
        role_q = select(ApprovalAuthority.resource_id).where(
            ApprovalAuthority.resource_type == "tool",
            ApprovalAuthority.revoked_at.is_(None),
            ApprovalAuthority.approver_role.in_(list(_ADMIN_ROLES)),
        )
        role_result = await db.execute(role_q)
        role_tool_names = [row[0] for row in role_result.all()]

        visible_tools = list(set(auth_tool_names + role_tool_names))
        if visible_tools:
            q = q.where(Approval.tool_name.in_(visible_tools))
        else:
            # Caller has no authority records — return empty (not an admin)
            return PaginatedResponse(items=[], total=0)

    count_q = q.with_only_columns(Approval.id)
    total = len((await db.execute(count_q)).all())

    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()

    # Provenance enrichment: approval.thread_id == playground_runs.id. Join to
    # surface the requester (run.user_id) and the deployment/environment the
    # run targeted, so reviewers see *who* asked and *where* from.
    provenance = await _load_provenance([r.thread_id for r in rows], db)

    items = []
    for r in rows:
        item = ApprovalResponse.model_validate(r)
        prov = provenance.get(r.thread_id)
        if prov:
            item.requested_by = prov.get("requested_by")
            item.requested_by_team = prov.get("requested_by_team")
            item.deployment_name = prov.get("deployment_name")
            item.environment = prov.get("environment")
        items.append(item)

    return PaginatedResponse(items=items, total=total)


async def _derive_context(thread_id: str, fallback: str, db: AsyncSession) -> str:
    """Decide an approval's context from the run it belongs to (Registry is the
    source of truth — the agent pod's static env var can't distinguish callers).

    thread_id is the PlaygroundRun id. Rules:
      - run.context == 'production'                          -> 'production'
      - run on a deployment whose environment == 'sandbox'  -> 'sandbox'
      - otherwise (Evaluate-tab playground run)              -> 'playground'
      - no run found                                         -> fallback (pod's claim)
    """
    from models import Deployment, PlaygroundRun

    try:
        run_id = uuid.UUID(thread_id)
    except (ValueError, AttributeError, TypeError):
        return fallback

    row = (
        await db.execute(
            select(PlaygroundRun.context, Deployment.environment)
            .outerjoin(Deployment, Deployment.id == PlaygroundRun.deployment_id)
            .where(PlaygroundRun.id == run_id)
        )
    ).first()
    if row is None:
        return fallback
    run_context, environment = row
    if run_context == "production":
        return "production"
    if environment == "sandbox":
        return "sandbox"
    return "playground"


async def _load_provenance(
    thread_ids: list[str], db: AsyncSession
) -> dict[str, dict]:
    """Resolve requester + deployment for a batch of approval thread_ids.

    thread_id is the PlaygroundRun id. Returns {thread_id: {requested_by,
    deployment_name, environment}}. Threads that don't parse as a run id (or
    have no matching run) are simply absent from the map.
    """
    from models import Deployment, PlaygroundRun

    parsed: dict[str, uuid.UUID] = {}
    for tid in thread_ids:
        try:
            parsed[tid] = uuid.UUID(tid)
        except (ValueError, AttributeError):
            continue
    if not parsed:
        return {}

    run_rows = (
        await db.execute(
            select(
                PlaygroundRun.id,
                PlaygroundRun.user_id,
                PlaygroundRun.requested_by_username,
                PlaygroundRun.requested_by_team,
                PlaygroundRun.deployment_id,
                Deployment.name,
                Deployment.environment,
            )
            .outerjoin(Deployment, Deployment.id == PlaygroundRun.deployment_id)
            .where(PlaygroundRun.id.in_(list(parsed.values())))
        )
    ).all()

    by_run_id = {
        # Prefer the human username; fall back to the raw sub for pre-migration rows.
        str(row.id): {
            "requested_by": row.requested_by_username or row.user_id,
            "requested_by_team": row.requested_by_team,
            "deployment_name": row.name,
            "environment": row.environment,
        }
        for row in run_rows
    }
    # Re-key from run-id back to the original thread_id string form.
    return {tid: by_run_id[str(rid)] for tid, rid in parsed.items() if str(rid) in by_run_id}


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/{approval_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
    summary="Get approval by ID",
)
async def get_approval(
    approval_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Fetch a single approval. Reviewers call this first to get the current
    `version` field needed for the optimistic-lock PATCH."""
    approval = await _resolve(approval_id, db)
    return ApprovalResponse.model_validate(approval)


# ---------------------------------------------------------------------------
# PATCH /api/v1/approvals/{approval_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/{approval_id}",
    response_model=ApprovalResponse,
    summary="Approve or reject (optimistic lock, authority-gated)",
)
async def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Submit approve/reject. Caller must have an active ApprovalAuthority
    record for this approval's tool_name. 'system' bypasses authority check
    (testing only). `version` must match current row version (optimistic lock)."""
    approval = await _resolve(approval_id, db)

    # Authority check — only for production approvals
    if approval.context == "production":
        caller = x_user_sub or body.reviewer_id
        if caller and caller != "system":
            has_auth = await _has_authority_for_tool(caller, approval.tool_name, db)
            if not has_auth:
                # Check if caller has a role-based authority record
                role_q = select(ApprovalAuthority).where(
                    ApprovalAuthority.resource_type == "tool",
                    ApprovalAuthority.resource_id == approval.tool_name,
                    ApprovalAuthority.revoked_at.is_(None),
                    ApprovalAuthority.approver_role.in_(list(_ADMIN_ROLES)),
                )
                role_result = await db.execute(role_q)
                if role_result.scalar_one_or_none() is None:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="not_authorized_to_decide",
                    )

    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval is already '{approval.status}' — cannot re-decide.",
        )

    now = datetime.now(tz=timezone.utc)
    if approval.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval has expired.",
        )

    if approval.version != body.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Optimistic lock conflict: expected version {approval.version}, "
                f"got {body.version}. Fetch the latest and retry."
            ),
        )

    approval.status = body.decision
    approval.reviewer_id = body.reviewer_id
    approval.reviewer_notes = body.reviewer_notes
    approval.decision_at = now
    approval.version = approval.version + 1
    await db.flush()

    logger.info(
        "decide_approval: id=%s decision=%s reviewer=%s",
        approval.id, body.decision, body.reviewer_id,
    )

    # Best-effort resume: notify the agent pod so the suspended LangGraph thread
    # can continue, and (if this approval belonged to a composite-workflow member)
    # advance the paused workflow run tree. Runs in the background so the reviewer's
    # request returns immediately; a failed resume MUST NOT fail the decision.
    if approval.thread_id and approval.agent_name and approval.team:
        asyncio.create_task(_resume_and_advance(
            approval.agent_name, approval.team, approval.thread_id,
            body.decision, body.reviewer_id, body.reviewer_notes,
        ))

    # Emit Langfuse platform action trace
    from tracing import trace_platform_action
    trace_platform_action(
        trace_id=str(approval.id),
        action=f"approval.{body.decision}",
        user_id=body.reviewer_id or "unknown",
        agent_name=approval.agent_name,
        metadata={
            "tool_name": approval.tool_name,
            "context": approval.context,
        },
    )

    return ApprovalResponse.model_validate(approval)


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/{approval_id}/reopen
# ---------------------------------------------------------------------------
@router.post(
    "/{approval_id}/reopen",
    response_model=ApprovalResponse,
    summary="Reopen a timed-out or rejected approval",
)
async def reopen_approval(
    approval_id: uuid.UUID,
    body: ReopenRequest,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Reset a timed-out or rejected approval back to 'pending' with a fresh expiry window.

    Only approvals in 'timed_out' or 'rejected' status may be reopened.
    Attempting to reopen a 'pending' or 'approved' approval returns 409."""
    approval = await _resolve(approval_id, db)

    if approval.status in ("pending", "approved"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Approval is '{approval.status}' — only 'timed_out' or 'rejected' "
                "approvals can be reopened."
            ),
        )

    now = datetime.now(tz=timezone.utc)
    approval.status = "pending"
    approval.expires_at = now + timedelta(seconds=body.timeout_seconds)
    approval.decision_at = None
    approval.reviewer_id = None
    approval.reviewer_notes = None
    approval.version = approval.version + 1
    await db.flush()

    logger.info(
        "reopen_approval: id=%s new_expires=%s",
        approval.id, approval.expires_at,
    )
    return ApprovalResponse.model_validate(approval)

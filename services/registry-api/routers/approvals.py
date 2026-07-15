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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from approval_timeout_worker import _agent_pod_url
from db import AsyncSessionLocal, get_db
from identity import principal_display as _principal_display
from models import AgentRun, Approval, ApprovalAuthority
from schemas import ApprovalCreate, ApprovalDecision, ApprovalResponse, PaginatedResponse

# Roles that always have authority to see/decide production approvals,
# even without a specific per-resource ApprovalAuthority record.
_ADMIN_ROLES = {"platform_admin", "team_lead"}

# WS-2 T011 — default reviewer role a DAEMON trigger-run's approval routes to when the
# trigger carries no explicit approver-role config. The role literal is matched against
# `user_team_assignments.role` (a caller holding this role may decide). T014 persists the
# per-trigger override as `agent_triggers.approver_role`; until then every daemon approval
# routes to this default (read via getattr so no column is required now).
_DEFAULT_REVIEWER_SCOPE = "agent:reviewer"


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

    # --- Durable WORKFLOW MEMBER (console decide) ------------------------------
    # A durable member parks its CHILD AgentRun at id == thread_id (parent_run_id set,
    # RunStep rows written). Resume it through the member pod at its ACTUAL deployment
    # environment (the legacy path below hits a hardcoded `-production` pod that
    # DNS-fails for a sandbox/playground member) and poll to terminal, then advance the
    # parent workflow. Returns early so the legacy chat / top-level-durable paths stay
    # unchanged (extend-not-alter).
    try:
        _tid = uuid.UUID(thread_id)
    except (ValueError, TypeError):
        _tid = None
    if _tid is not None:
        async with AsyncSessionLocal() as s:
            _child = (await s.execute(select(AgentRun).where(AgentRun.id == _tid))).scalar_one_or_none()
            _is_member = _child is not None and _child.parent_run_id is not None
            _parent_id = str(_child.parent_run_id) if _is_member else None
            _has_steps = False
            if _is_member:
                from models import RunStep
                _has_steps = (await s.execute(
                    select(RunStep.id).where(RunStep.run_id == _tid).limit(1)
                )).first() is not None
        if _is_member and _has_steps:
            from workflow_orchestrator import resume_durable_member, resume_orchestration
            status_val, output, err = await resume_durable_member(
                agent_name, team, str(_tid), decision, reviewer_id, reason,
            )
            if status_val == "awaiting_approval":
                logger.info("workflow member %s re-parked after resume (another gate)", _tid)
                return
            logger.info(
                "workflow member %s resumed (status=%s, err=%s) — advancing parent %s",
                _tid, status_val, err, _parent_id,
            )
            asyncio.create_task(resume_orchestration(_parent_id, output or "", status_val))
            return

    # Durable /run runs park their AgentRun at id == thread_id and write RunStep rows
    # (chat runs do neither). If this thread IS such a run, resume it DURABLY — the pod
    # re-drives the shared harness and posts the remaining steps to the step-update
    # callback (a console decide is server-driven; no client stream is listening). The
    # chat + workflow-member paths below are unchanged (extend-not-alter, WS-1 T4).
    resume_body = {"decision": decision, "reviewer_id": reviewer_id, "reason": reason}
    is_durable = False
    try:
        tid = uuid.UUID(thread_id)
        from models import RunStep, PlaygroundRun
        from durable_dispatch import registry_internal_base
        async with AsyncSessionLocal() as s:
            has_steps = (await s.execute(
                select(RunStep.id).where(RunStep.run_id == tid).limit(1)
            )).first() is not None
            run = (await s.execute(select(AgentRun).where(AgentRun.id == tid))).scalar_one_or_none()
            if run is not None and run.parent_run_id is None and has_steps:
                # top-level durable AgentRun (production internal path).
                resume_body["run_id"] = str(tid)
                resume_body["callback_url"] = f"{registry_internal_base()}/api/v1/internal/runs/{tid}/step-update"
                is_durable = True
            elif run is None and has_steps:
                # top-level durable PLAYGROUND run — its thread_id is a PlaygroundRun id,
                # and its completion arrives at the PLAYGROUND step-update callback (bug #12:
                # this path used to fall through to a synchronous chat resume against the
                # -production pod, so single-agent durable HITL never resumed).
                pr = (await s.execute(select(PlaygroundRun).where(PlaygroundRun.id == tid))).scalar_one_or_none()
                if pr is not None:
                    resume_body["run_id"] = str(tid)
                    resume_body["callback_url"] = f"{registry_internal_base()}/api/v1/playground/runs/{tid}/step-update"
                    is_durable = True
    except Exception:
        pass  # thread_id is not a durable run id → fall through to chat/workflow resume

    try:
        if is_durable:
            # Hit the agent's ACTUAL deployed pod (env-aware) — the -production default
            # DNS-fails for a sandbox/playground agent (same wrong-target as the workflow path).
            from workflow_orchestrator import _resolve_agent_environment, _team_namespace
            env = await _resolve_agent_environment(agent_name)
            pod_url = f"http://{agent_name}-{env}.{_team_namespace(team)}.svc.cluster.local:8080"
        else:
            pod_url = _agent_pod_url(agent_name, team)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{pod_url}/resume/{thread_id}", json=resume_body)
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


async def _caller_roles(caller: str, db: AsyncSession) -> set[str]:
    """The roles a caller holds (from `user_team_assignments`, same source as /me).

    Used for the DAEMON reviewer-scope authority check: a caller may decide a
    reviewer-routed approval only if they actually hold the routed reviewer role
    (or an admin role). This reads the caller's real role — it does NOT infer
    authority from a role record attached to the resource (fail-closed).
    """
    rows = (
        await db.execute(
            text("SELECT role FROM user_team_assignments WHERE user_sub = :sub"),
            {"sub": caller},
        )
    ).all()
    return {r[0] for r in rows if r[0]}


async def _derive_reviewer_audit(
    approval: Approval, requester_display: Optional[str], db: AsyncSession
) -> tuple[Optional[str], Optional[str]]:
    """Derive ``(reviewer_scope, principal_display)`` for an approval at READ time.

    WS-2 T011 — scope is derived from the run's ``agent_class`` + the trigger's
    approver-role config, NOT stored (data-model.md: "reviewer_scope NOT stored").

    * ``reviewer_scope`` — the reviewer role a DAEMON (service-identity) trigger-run's
      approval routes to (the trigger's ``approver_role`` config, else
      ``agent:reviewer``). ``None`` for an interactive / user-delegated approval —
      those keep the existing per-tool ``ApprovalAuthority`` path.
    * ``principal_display`` — reuses ``identity.principal_display`` (single source of
      the display string):
        - daemon agent trigger-run  → ``service:{agent} on behalf of {armed_by}``
        - daemon workflow member     → ``workflow:{wf} (service) on behalf of {armed_by}``
        - otherwise                  → the requesting user's display.

    ``approval.thread_id`` is the run id. A trigger-run parks its ``AgentRun`` at that
    id (a workflow member parks its CHILD AgentRun, ``parent_run_id`` → the parent
    workflow run). An interactive ``/chat`` approval's thread_id is a ``PlaygroundRun``
    (no matching AgentRun) → the requester fallback.
    """
    from models import Agent, CompositeWorkflow, AgentTrigger

    fallback = (None, requester_display)

    try:
        run_id = uuid.UUID(approval.thread_id)
    except (ValueError, AttributeError, TypeError):
        return fallback

    run = (
        await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        return fallback  # PlaygroundRun (interactive) — not a service run

    # Only a TRIGGER-run has no live caller and can act under a service identity;
    # an interactive /chat run is 'manual' (caller present → caller identity).
    if (run.trigger_type or "manual") == "manual":
        return fallback

    # Workflow member → authority is the WORKFLOW's class (D1), not the member's.
    # The member parks its child AgentRun; walk to the parent workflow run.
    trigger_run = run
    if run.parent_run_id is not None:
        parent = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == run.parent_run_id)
            )
        ).scalar_one_or_none()
        if parent is not None:
            trigger_run = parent

    workflow = None
    if trigger_run.workflow_id is not None:
        workflow = (
            await db.execute(
                select(CompositeWorkflow).where(
                    CompositeWorkflow.id == trigger_run.workflow_id
                )
            )
        ).scalar_one_or_none()

    if workflow is not None:
        agent_class = workflow.agent_class
    else:
        agent = (
            await db.execute(
                select(Agent).where(Agent.name == trigger_run.agent_name)
            )
        ).scalar_one_or_none()
        agent_class = getattr(agent, "agent_class", None)

    # A user_delegated trigger-run runs under the arming human — it is NOT routed to a
    # reviewer role (keeps the existing per-tool path). Only a daemon → reviewer scope.
    if agent_class != "daemon":
        return fallback

    # The authorizing human (armed_by) + reviewer-role config live on the trigger.
    trig = None
    if trigger_run.trigger_id is not None:
        trig = (
            await db.execute(
                select(AgentTrigger).where(AgentTrigger.id == trigger_run.trigger_id)
            )
        ).scalar_one_or_none()
    armed_by = getattr(trig, "armed_by", None)
    # T014 will persist `agent_triggers.approver_role`; read via getattr so the default
    # applies today (no column) and the configured role wins once T014 lands.
    reviewer_scope = getattr(trig, "approver_role", None) or _DEFAULT_REVIEWER_SCOPE

    if workflow is not None:
        display = _principal_display(workflow_name=workflow.name, armed_by=armed_by)
    else:
        display = _principal_display(
            agent_name=trigger_run.agent_name, armed_by=armed_by, is_service=True
        )
    return reviewer_scope, display


async def _caller_can_review(
    caller: str, approval: Approval, reviewer_scope: str, db: AsyncSession
) -> bool:
    """Fail-closed authority for a DAEMON reviewer-scoped approval.

    A caller may decide only if they actually hold the routed reviewer role, OR an
    admin role (platform_admin/team_lead — matching the existing admin-authority
    pattern), OR an explicit per-tool ApprovalAuthority grant. No role → cannot decide.
    """
    roles = await _caller_roles(caller, db)
    if reviewer_scope in roles:
        return True
    if roles & _ADMIN_ROLES:
        return True
    return await _has_authority_for_tool(caller, approval.tool_name, db)


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
    # Idempotency — the SAME interrupt must never mint two approvals. A durable
    # HITL member re-runs its interrupt node ON RESUME (LangGraph replays the node
    # from the top), so require_approval POSTs create_approval AGAIN with identical
    # (thread_id, tool_name, tool_args). If we only matched status='pending' the
    # original would already be 'approved'/'rejected' by then → we'd mint a DUPLICATE
    # pending approval and the user gets "prompted twice". Match any ACTIVE status
    # (pending/approved/rejected) so the re-post reuses the existing decision instead.
    # thread_id is per-run, so this can only collide with the same interrupt, never a
    # different run. (timed_out is excluded so a genuinely expired gate can re-open.)
    existing = (
        await db.execute(
            select(Approval)
            .where(
                Approval.thread_id == body.thread_id,
                Approval.tool_name == body.tool_name,
                Approval.tool_args == body.tool_args,
                Approval.status.in_(["pending", "approved", "rejected"]),
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "create_approval: reusing %s approval id=%s (idempotent re-post — likely a "
            "resume replay) thread=%s tool=%s",
            existing.status, existing.id, body.thread_id, body.tool_name,
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
        requested_by = None
        if prov:
            requested_by = prov.get("requested_by")
            item.requested_by = requested_by
            item.requested_by_team = prov.get("requested_by_team")
            item.deployment_name = prov.get("deployment_name")
            item.environment = prov.get("environment")
        # WS-2 T011 — reviewer scope + audit display (derived, not stored). Read by the
        # Studio inbox (T012/T013) to render "service:X on behalf of Y" + filter by role.
        item.reviewer_scope, item.principal_display = await _derive_reviewer_audit(
            r, requested_by, db
        )
        items.append(item)

    return PaginatedResponse(items=items, total=total)


async def _derive_context(thread_id: str, fallback: str, db: AsyncSession) -> str:
    """Decide an approval's context from the run it belongs to (Registry is the
    source of truth — the agent pod's static env var can't distinguish callers).

    thread_id is the run id. It may be a PlaygroundRun (single-agent playground /
    sandbox chat / Evaluate tab) OR an AgentRun (a WORKFLOW member — a durable
    member sets thread_id = its child AgentRun.id). Rules:
      - PlaygroundRun: run.context=='production' -> 'production';
                       deployment.environment=='sandbox' -> 'sandbox';
                       else -> 'playground'
      - AgentRun (workflow member): inherit the member run's context
                       ('production' or 'playground' — a builder/test run is
                       'playground', so its high-risk member parks self-service)
      - no run found  -> fallback (the pod's static claim)
    """
    from models import AgentRun, Deployment, PlaygroundRun

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
    if row is not None:
        run_context, environment = row
        if run_context == "production":
            return "production"
        if environment == "sandbox":
            return "sandbox"
        return "playground"

    # Not a PlaygroundRun — a workflow member's thread_id is its AgentRun id.
    # Inherit that run's context so a playground/test workflow run yields a
    # self-service (inline) approval, not a console (production) one.
    agent_run_context = (
        await db.execute(select(AgentRun.context).where(AgentRun.id == run_id))
    ).scalar_one_or_none()
    if agent_run_context is not None:
        return agent_run_context

    return fallback


async def _load_provenance(
    thread_ids: list[str], db: AsyncSession
) -> dict[str, dict]:
    """Resolve requester + deployment for a batch of approval thread_ids.

    thread_id is the PlaygroundRun id. Returns {thread_id: {requested_by,
    deployment_name, environment}}. Threads that don't parse as a run id (or
    have no matching run) are simply absent from the map.
    """
    from models import Deployment, PlaygroundRun, ProductionDeployment

    parsed: dict[str, uuid.UUID] = {}
    for tid in thread_ids:
        try:
            parsed[tid] = uuid.UUID(tid)
        except (ValueError, AttributeError):
            continue
    if not parsed:
        return {}

    # A run targets EITHER a sandbox deployment (deployment_id → deployments) OR a
    # production one (production_deployment_id → production_deployments). Join both
    # and coalesce so the console shows deployment/environment for either context.
    run_rows = (
        await db.execute(
            select(
                PlaygroundRun.id,
                PlaygroundRun.user_id,
                PlaygroundRun.requested_by_username,
                PlaygroundRun.requested_by_team,
                PlaygroundRun.production_deployment_id,
                Deployment.name,
                Deployment.environment,
                ProductionDeployment.namespace,
            )
            .outerjoin(Deployment, Deployment.id == PlaygroundRun.deployment_id)
            .outerjoin(
                ProductionDeployment,
                ProductionDeployment.id == PlaygroundRun.production_deployment_id,
            )
            .where(PlaygroundRun.id.in_(list(parsed.values())))
        )
    ).all()

    by_run_id = {
        # Prefer the human username; fall back to the raw sub for pre-migration rows.
        str(row.id): {
            "requested_by": row.requested_by_username or row.user_id,
            "requested_by_team": row.requested_by_team,
            # Sandbox deployment name, else the production namespace.
            "deployment_name": row.name or (row.namespace if row.production_deployment_id else None),
            # Sandbox env, else 'production' when a production deployment is linked.
            "environment": row.environment or ("production" if row.production_deployment_id else None),
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
    item = ApprovalResponse.model_validate(approval)
    # Provenance (requester) + WS-2 T011 reviewer scope / audit display — same
    # enrichment as the list endpoint so a reviewer opening one approval sees
    # "service:X on behalf of Y" and the routed reviewer role.
    prov = (await _load_provenance([approval.thread_id], db)).get(approval.thread_id)
    requested_by = None
    if prov:
        requested_by = prov.get("requested_by")
        item.requested_by = requested_by
        item.requested_by_team = prov.get("requested_by_team")
        item.deployment_name = prov.get("deployment_name")
        item.environment = prov.get("environment")
    item.reviewer_scope, item.principal_display = await _derive_reviewer_audit(
        approval, requested_by, db
    )
    return item


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

    # Authority check. A DAEMON trigger-run's approval is routed ASYNC to a reviewer
    # role (WS-2 T011) — no live user is on the connection — so it is gated by the
    # routed reviewer scope, NOT the per-tool ApprovalAuthority path. Deriving a
    # non-None reviewer_scope IS the discriminator (explicit, no agent_class sniffing).
    caller = x_user_sub or body.reviewer_id
    reviewer_scope, _ = await _derive_reviewer_audit(approval, None, db)
    if reviewer_scope is not None:
        # Daemon approval → fail-closed reviewer-role authority. A caller not in the
        # reviewer scope (nor an admin / explicit grantee) is REJECTED (403), never
        # silently allowed. 'system' is the internal auto-actor (timeout worker).
        if caller and caller != "system":
            if not await _caller_can_review(caller, approval, reviewer_scope, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="not_authorized_to_decide",
                )
    elif approval.context == "production":
        # Interactive / user-delegated production approval — existing per-tool path.
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

"""
Composite Workflows router (Decision 22) — a Workflow is a collection of
EXISTING member agents, orchestrated as a run tree.

  GET    /api/v1/workflows                         — list (team + visibility filtered)
  POST   /api/v1/workflows                         — create (409 on dup name+team)
  GET    /api/v1/workflows/{id}                    — get with members
  PATCH  /api/v1/workflows/{id}                    — update
  DELETE /api/v1/workflows/{id}                    — archive (status=archived)
  POST   /api/v1/workflows/{id}/members            — add existing agent (same team)
  DELETE /api/v1/workflows/{id}/members/{agent_id} — remove member
  POST   /api/v1/workflows/{id}/edges              — add a routing edge (source→target)
  GET    /api/v1/workflows/{id}/edges              — list edges
  DELETE /api/v1/workflows/{id}/edges/{edge_id}    — remove edge
  POST   /api/v1/workflows/{id}/runs               — start a run (sequential/conditional/supervisor/handoff)
  GET    /api/v1/workflows/{id}/runs/{run_id}/tree — run tree (parent + child runs)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import asyncio

from auth_middleware import get_optional_user, require_user
from db import get_db
from observability_backend import get_observability_backend
from rbac import ENFORCE_TRIGGER_MGMT, can_manage_artifact, grant_creator_admin
from models import Agent, AgentMemory, AgentRun, AgentTool, AgentTrigger, AgentVersion, CompositeWorkflow, RunStep, Tool, WorkflowEdge, WorkflowMember
from schemas import (
    AgentMemoryResponse,
    AgentRunResponse,
    AgentTriggerCreate,
    AgentTriggerUpdate,
    CompositeWorkflowCreate,
    CompositeWorkflowResponse,
    CompositeWorkflowUpdate,
    CompositeWorkflowWithMembersResponse,
    ConversationSummary,
    RotateTokenResponse,
    ToolCallProjection,
    WorkflowEdgeCreate,
    WorkflowEdgeResponse,
    WorkflowMemberCreate,
    WorkflowMemberResponse,
    WorkflowRunCreate,
    WorkflowRunStartResponse,
    WorkflowRunStreamRequest,
    WorkflowRunTreeResponse,
    WorkflowTriggerResponse,
)
from store_factory import get_conversation_store
from trigger_utils import _new_token, workflow_webhook_url
from workflow_orchestrator import dispatch_to_orchestrator_pod, orchestrate, orchestrate_stream, resolve_member_names

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


async def _get_workflow(workflow_id: uuid.UUID, db: AsyncSession) -> CompositeWorkflow:
    result = await db.execute(select(CompositeWorkflow).where(CompositeWorkflow.id == workflow_id))
    wf = result.scalar_one_or_none()
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found.")
    return wf


async def _member_count(db: AsyncSession, workflow_id: uuid.UUID) -> int:
    return int(
        (await db.execute(
            select(func.count()).select_from(WorkflowMember).where(WorkflowMember.workflow_id == workflow_id)
        )).scalar() or 0
    )


def _to_response(
    wf: CompositeWorkflow, member_count: int, warnings: list[str] | None = None
) -> CompositeWorkflowResponse:
    return CompositeWorkflowResponse(
        id=wf.id, name=wf.name, team=wf.team, description=wf.description,
        execution_shape=wf.execution_shape, orchestration=wf.orchestration,
        agent_class=wf.agent_class,
        memory_enabled=wf.memory_enabled, status=wf.status, publish_status=wf.publish_status,
        created_by=wf.created_by, created_at=wf.created_at, updated_at=wf.updated_at,
        member_count=member_count, warnings=warnings or [],
    )


async def compute_reactive_approval_warnings(
    db: AsyncSession, workflow_id: uuid.UUID, execution_shape: str
) -> list[str]:
    """Non-blocking author warning (best-effort half of S2): a reactive workflow with a
    statically high-risk-tool member STOPS EARLY at runtime if that tool trips an approval
    gate. A reactive workflow can't resume through approvals, so the run emits the member's
    ``approval_requested`` frame and then ``done`` WITHOUT executing the gated tool or any
    downstream members (verified against the runtime — not a hard failure/error). Returns []
    for durable workflows or when no member carries a high-/critical-risk tool."""
    if execution_shape != "reactive":
        return []
    rows = (await db.execute(
        select(Agent.name)
        .select_from(WorkflowMember)
        .join(Agent, Agent.id == WorkflowMember.agent_id)
        .join(AgentTool, AgentTool.agent_id == Agent.id)
        .join(Tool, Tool.id == AgentTool.tool_id)
        .where(
            WorkflowMember.workflow_id == workflow_id,
            Tool.risk_level.in_(["high", "critical"]),
        )
    )).all()
    if not rows:
        return []
    members = sorted({name for (name,) in rows})
    return [
        f"Reactive workflow has high-risk-tool member(s): {', '.join(members)}. "
        f"If a tool trips an approval gate this run STOPS EARLY — it surfaces the approval "
        f"request then ends without running the gated tool or any downstream members "
        f"(reactive workflows can't resume through approvals). Set shape=durable to run "
        f"approval-gated tools."
    ]


async def compute_start_node_warnings(
    db: AsyncSession, workflow_id: uuid.UUID, orchestration: str
) -> list[str]:
    """Non-blocking author warning: multiple start nodes are NOT supported.

    The conditional/handoff engine walks a SINGLE cursor from ONE start node
    (`workflow_orchestrator.find_start_node` = the first member, by position, that
    is never an edge target). If the edge graph has more than one such root, only
    that first root runs and every other root is silently unreachable. Surfaced at
    save time so the author fixes the graph instead of hitting the silent orphan.

    Scope: only conditional/handoff use `find_start_node`. Sequential runs members
    in position order (no start-node concept) and supervisor routes dynamically by
    role and ignores edges entirely (all members are indegree-0 there — that's
    correct, not multiple starts) — so this check does not apply to those modes.
    """
    if orchestration not in ("conditional", "handoff"):
        return []
    member_rows = (await db.execute(
        select(Agent.name)
        .select_from(WorkflowMember)
        .join(Agent, Agent.id == WorkflowMember.agent_id)
        .where(WorkflowMember.workflow_id == workflow_id)
        .order_by(WorkflowMember.position.nulls_last())
    )).all()
    if len(member_rows) <= 1:
        return []
    tgt = Agent.__table__.alias("tgt")
    target_rows = (await db.execute(
        select(tgt.c.name)
        .select_from(WorkflowEdge)
        .join(tgt, tgt.c.id == WorkflowEdge.target_agent_id)
        .where(WorkflowEdge.workflow_id == workflow_id)
    )).all()
    targets = {name for (name,) in target_rows}
    roots = [name for (name,) in member_rows if name not in targets]
    if len(roots) <= 1:
        return []
    entry, orphans = roots[0], roots[1:]
    return [
        f"Multiple start nodes are not supported for a {orchestration} workflow: "
        f"{', '.join(roots)} each have no incoming edge. Only '{entry}' will run — "
        f"{', '.join(orphans)} would be unreachable. Give the graph a single entry "
        f"(add an incoming edge to every node except one)."
    ]


async def _workflow_warnings(
    db: AsyncSession, wf: CompositeWorkflow
) -> list[str]:
    """All save-time author warnings for a workflow (composed, non-blocking)."""
    return (
        await compute_reactive_approval_warnings(db, wf.id, wf.execution_shape)
        + await compute_start_node_warnings(db, wf.id, wf.orchestration)
    )


@router.get("", response_model=list[CompositeWorkflowResponse])
async def list_workflows(
    team: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[CompositeWorkflowResponse]:
    caller = (user or {}).get("sub") or x_user_sub
    q = select(CompositeWorkflow)
    # Deny-by-default visibility (mirrors agents): published to all, private to creator.
    if caller:
        q = q.where(or_(CompositeWorkflow.publish_status == "published", CompositeWorkflow.created_by == caller))
    else:
        q = q.where(CompositeWorkflow.publish_status == "published")
    if team is not None:
        q = q.where(CompositeWorkflow.team == team)
    q = q.where(CompositeWorkflow.status != "archived").order_by(CompositeWorkflow.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(q)).scalars().all()
    return [_to_response(wf, await _member_count(db, wf.id)) for wf in rows]


@router.post("", response_model=CompositeWorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: CompositeWorkflowCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> CompositeWorkflowResponse:
    caller = (user or {}).get("sub") or x_user_sub or "system"
    dup = (await db.execute(
        select(CompositeWorkflow).where(
            CompositeWorkflow.name == body.name, CompositeWorkflow.team == body.team
        )
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"Workflow '{body.name}' already exists for team '{body.team}'.")
    wf = CompositeWorkflow(
        name=body.name, team=body.team, description=body.description,
        execution_shape=body.execution_shape, orchestration=body.orchestration,
        agent_class=body.agent_class,
        memory_enabled=body.memory_enabled, created_by=caller,
    )
    db.add(wf)
    await db.flush()
    await grant_creator_admin(db, "workflow", wf.id, caller)
    await db.commit()
    await db.refresh(wf)
    logger.info("created composite workflow '%s' (id=%s) by %s", wf.name, wf.id, caller)
    return _to_response(wf, 0)


@router.get("/{workflow_id}", response_model=CompositeWorkflowWithMembersResponse)
async def get_workflow(workflow_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> CompositeWorkflowWithMembersResponse:
    wf = await _get_workflow(workflow_id, db)
    rows = (await db.execute(
        select(WorkflowMember, Agent.name)
        .join(Agent, Agent.id == WorkflowMember.agent_id)
        .where(WorkflowMember.workflow_id == workflow_id)
        .order_by(WorkflowMember.position.nulls_last(), WorkflowMember.added_at)
    )).all()
    members = [
        WorkflowMemberResponse(
            workflow_id=m.workflow_id, agent_id=m.agent_id, agent_name=agent_name,
            role=m.role, position=m.position, routing=m.routing or {}, added_at=m.added_at,
        )
        for (m, agent_name) in rows
    ]
    edge_rows = (await db.execute(
        select(WorkflowEdge)
        .where(WorkflowEdge.workflow_id == workflow_id)
        .order_by(WorkflowEdge.position.nulls_last(), WorkflowEdge.created_at)
    )).scalars().all()
    edges = [WorkflowEdgeResponse.model_validate(e) for e in edge_rows]
    warnings = await _workflow_warnings(db, wf)
    base = _to_response(wf, len(members), warnings)
    return CompositeWorkflowWithMembersResponse(**base.model_dump(), members=members, edges=edges)


@router.get(
    "/{workflow_id}/conversations",
    response_model=list[ConversationSummary],
    summary="List the caller's conversations with this workflow (POC-5)",
)
async def list_workflow_conversations(
    workflow_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    """Per-thread conversation summaries for the CALLER with this workflow — the
    resume lens behind the workflow deployment Conversations tab. A workflow's
    transcript is authored by its members (member agent_name, NULL user_id), so
    ownership + identity come from the workflow's PARENT runs (workflow_id + owner),
    not the member rows. Same ConversationSummary shape as the per-agent endpoint."""
    wf = await _get_workflow(workflow_id, db)
    store = get_conversation_store()
    rows = await store.list_workflow_conversations(
        db,
        workflow_id=str(workflow_id),
        workflow_name=wf.name,
        user_id=claims["sub"],
        limit=limit,
        offset=offset,
    )
    return [ConversationSummary.model_validate(r) for r in rows]


@router.get(
    "/{workflow_id}/memory",
    response_model=list[AgentMemoryResponse],
    summary="List this workflow's memory entries for the caller (workflow ledger)",
)
async def list_workflow_memory(
    workflow_id: uuid.UUID,
    thread_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentMemoryResponse]:
    """Workflow memory ENTRIES for the CALLER — the same parent-run-scoped read the
    Conversations tab uses, but returning individual entries. Backs BOTH the workflow
    Memory tab (no thread_id → recent entries, newest-first) and the WorkflowChat
    replay (thread_id → that thread's transcript, oldest-first). Ownership + identity
    come from the workflow's PARENT runs (workflow_id + owner), not the member rows
    (member agent_name, NULL user_id)."""
    await _get_workflow(workflow_id, db)  # 404 if the workflow is unknown
    store = get_conversation_store()
    rows = await store.list_workflow_memory(
        db,
        workflow_id=str(workflow_id),
        user_id=claims["sub"],
        thread_id=thread_id,
        limit=limit,
        offset=offset,
    )
    return [AgentMemoryResponse.model_validate(r) for r in rows]


@router.patch("/{workflow_id}", response_model=CompositeWorkflowResponse)
async def update_workflow(
    workflow_id: uuid.UUID, body: CompositeWorkflowUpdate, db: AsyncSession = Depends(get_db)
) -> CompositeWorkflowResponse:
    wf = await _get_workflow(workflow_id, db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(wf, field, value)
    wf.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(wf)
    warnings = await _workflow_warnings(db, wf)
    return _to_response(wf, await _member_count(db, wf.id), warnings)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def archive_workflow(workflow_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    wf = await _get_workflow(workflow_id, db)
    wf.status = "archived"
    wf.updated_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/{workflow_id}/members", response_model=WorkflowMemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    workflow_id: uuid.UUID, body: WorkflowMemberCreate, db: AsyncSession = Depends(get_db)
) -> WorkflowMemberResponse:
    wf = await _get_workflow(workflow_id, db)
    agent = (await db.execute(select(Agent).where(Agent.id == body.agent_id))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{body.agent_id}' not found.")
    if agent.team != wf.team:
        raise HTTPException(status_code=400, detail="Member agent must be in the same team as the workflow.")
    existing = (await db.execute(
        select(WorkflowMember).where(
            WorkflowMember.workflow_id == workflow_id, WorkflowMember.agent_id == body.agent_id
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Agent is already a member of this workflow.")
    member = WorkflowMember(
        workflow_id=workflow_id, agent_id=body.agent_id, role=body.role,
        position=body.position, routing=body.routing or {},
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return WorkflowMemberResponse(
        workflow_id=member.workflow_id, agent_id=member.agent_id, agent_name=agent.name,
        role=member.role, position=member.position, routing=member.routing or {},
        added_at=member.added_at,
    )


@router.delete("/{workflow_id}/members/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_member(workflow_id: uuid.UUID, agent_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    member = (await db.execute(
        select(WorkflowMember).where(
            WorkflowMember.workflow_id == workflow_id, WorkflowMember.agent_id == agent_id
        )
    )).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    await db.delete(member)
    await db.commit()


# --- Edge endpoints (orchestration graph) ---
async def _is_member(db: AsyncSession, workflow_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
    return (await db.execute(
        select(WorkflowMember.agent_id).where(
            WorkflowMember.workflow_id == workflow_id, WorkflowMember.agent_id == agent_id
        )
    )).scalar_one_or_none() is not None


@router.post("/{workflow_id}/edges", response_model=WorkflowEdgeResponse, status_code=status.HTTP_201_CREATED)
async def add_edge(
    workflow_id: uuid.UUID, body: WorkflowEdgeCreate, db: AsyncSession = Depends(get_db)
) -> WorkflowEdge:
    await _get_workflow(workflow_id, db)
    # Both endpoints must be members of this workflow.
    for aid in (body.source_agent_id, body.target_agent_id):
        if not await _is_member(db, workflow_id, aid):
            raise HTTPException(status_code=400, detail=f"Agent '{aid}' is not a member of this workflow.")
    if body.source_agent_id == body.target_agent_id:
        raise HTTPException(status_code=400, detail="An edge cannot connect an agent to itself.")
    existing = (await db.execute(
        select(WorkflowEdge).where(
            WorkflowEdge.workflow_id == workflow_id,
            WorkflowEdge.source_agent_id == body.source_agent_id,
            WorkflowEdge.target_agent_id == body.target_agent_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An edge between these agents already exists.")
    edge = WorkflowEdge(
        workflow_id=workflow_id, source_agent_id=body.source_agent_id,
        target_agent_id=body.target_agent_id, condition=body.condition, position=body.position,
    )
    db.add(edge)
    await db.commit()
    await db.refresh(edge)
    return edge


@router.get("/{workflow_id}/edges", response_model=list[WorkflowEdgeResponse])
async def list_edges(workflow_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[WorkflowEdge]:
    await _get_workflow(workflow_id, db)
    return list((await db.execute(
        select(WorkflowEdge)
        .where(WorkflowEdge.workflow_id == workflow_id)
        .order_by(WorkflowEdge.position.nulls_last(), WorkflowEdge.created_at)
    )).scalars().all())


@router.delete("/{workflow_id}/edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_edge(workflow_id: uuid.UUID, edge_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    edge = (await db.execute(
        select(WorkflowEdge).where(
            WorkflowEdge.id == edge_id, WorkflowEdge.workflow_id == workflow_id
        )
    )).scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=404, detail="Edge not found.")
    await db.delete(edge)
    await db.commit()


# --- Run endpoints (W3) ---
@router.post("/{workflow_id}/runs", response_model=WorkflowRunStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_workflow_run(
    workflow_id: uuid.UUID, body: WorkflowRunCreate, db: AsyncSession = Depends(get_db)
) -> WorkflowRunStartResponse:
    wf = await _get_workflow(workflow_id, db)
    if wf.status == "archived":
        raise HTTPException(status_code=422, detail="Cannot run an archived workflow.")
    member_names = await resolve_member_names(db, workflow_id)
    if not member_names:
        raise HTTPException(status_code=422, detail="Workflow has no members to run.")

    message = body.input_message or ""
    if not message and body.input_payload:
        import json as _json
        message = body.input_payload.get("message") or _json.dumps(body.input_payload)

    # Soft pre-flight: members with no running/deploying deployment will fail at
    # dispatch (no production pod). Don't block the run (this is fine for testing),
    # but surface a warning so the caller/UI knows which steps can't succeed yet.
    from models import Deployment
    deployed = set((await db.execute(
        select(Agent.name)
        .join(Deployment, Deployment.agent_id == Agent.id)
        .where(Agent.name.in_(member_names), Deployment.status.in_(("deploying", "running")))
    )).scalars().all())
    undeployed = [n for n in member_names if n not in deployed]
    warning = (
        f"Agents without a running deployment will fail at dispatch — deploy them first: {', '.join(undeployed)}"
        if undeployed else None
    )

    # Scope to active workflow deployment (if any)
    active_wf_dep = (await db.execute(
        select(WorkflowDeployment.id).where(
            WorkflowDeployment.workflow_id == workflow_id,
            WorkflowDeployment.status == "running",
        ).order_by(WorkflowDeployment.deployed_at.desc()).limit(1)
    )).scalar_one_or_none()

    # This endpoint is the INTERACTIVE builder test-run (production/triggered runs
    # go through routers/internal.py:_start_workflow_run). So it runs in `playground`
    # context → any high-risk member parks as a self-service approval decided INLINE
    # in the builder run panel, not routed to the reviewer console. Children inherit
    # this context in _run_step.
    parent = AgentRun(
        agent_name=wf.name,
        input=message[:4000] if message else None,
        context="playground",
        status="queued",
        trigger_type=body.trigger_type,
        run_by=body.run_by,
        team=wf.team,
        workflow_id=wf.id,
        workflow_deployment_id=active_wf_dep,
    )
    db.add(parent)
    await db.flush()

    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=str(parent.id),
        agent_name=wf.name,
        user_id=body.run_by or "system",
        context="playground",
        input_message=message[:4000] if message else "",
    )
    if trace_id:
        parent.langfuse_trace_id = trace_id

    await db.commit()
    await db.refresh(parent)

    # Try dispatching to a production orchestrator pod if one exists
    from models import PublishedArtifact, ProductionDeployment
    prod_art = (await db.execute(
        select(PublishedArtifact).where(
            PublishedArtifact.source_id == workflow_id,
            PublishedArtifact.type == "workflow",
        )
    )).scalar_one_or_none()
    has_prod_pod = False
    if prod_art:
        prod_dep = (await db.execute(
            select(ProductionDeployment).where(
                ProductionDeployment.artifact_id == prod_art.id,
                ProductionDeployment.status == "running",
            )
        )).scalar_one_or_none()
        if prod_dep:
            # Resolve member data for the orchestrator pod
            from models import WorkflowMember
            wf_members = (await db.execute(
                select(WorkflowMember, Agent.name)
                .join(Agent, Agent.id == WorkflowMember.agent_id)
                .where(WorkflowMember.workflow_id == workflow_id)
                .order_by(WorkflowMember.position.nulls_last())
            )).all()
            members_data = [
                {"agent_name": aname, "team": wf.team, "position": m.position}
                for (m, aname) in wf_members
            ]
            has_prod_pod = await dispatch_to_orchestrator_pod(
                wf.name, wf.team, str(parent.id), members_data, {"message": message}
            )

    if not has_prod_pod:
        asyncio.create_task(
            orchestrate(str(parent.id), wf.team, str(workflow_id), message, wf.orchestration)
        )

    logger.info(
        "started workflow run %s for workflow '%s' (mode=%s, %d members, prod_pod=%s%s)",
        parent.id, wf.name, wf.orchestration, len(member_names), has_prod_pod,
        f", {len(undeployed)} undeployed" if undeployed else "",
    )
    return WorkflowRunStartResponse(workflow_id=wf.id, run_id=parent.id, status="queued", warning=warning)


@router.post("/{workflow_id}/runs/stream")
async def stream_workflow_run(
    workflow_id: uuid.UUID,
    body: WorkflowRunStreamRequest,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """POC-2b 2b-0 headline: stream a workflow run as multiplexed SSE.

    Creates the parent run exactly like ``start_workflow_run`` (playground context) but
    keyed to ``body.session_id`` for the shared transcript, then streams the ONE
    ``orchestrate_stream`` graph walk as ``data: {json}\\n\\n`` frames. The internal
    ``__member_end__`` sentinel is filtered out (never leaks to the client). Frame vocab:
    ``agent_start`` / ``token`` / ``tool_call`` / ``rationale`` / ``agent_end`` / ``done`` /
    ``error`` — see contracts/sse-frames.md §A.

    In-process only — this endpoint never dispatches to a production orchestrator pod
    (gap-ledgered)."""
    wf = await _get_workflow(workflow_id, db)
    if wf.status == "archived":
        raise HTTPException(status_code=422, detail="Cannot run an archived workflow.")
    member_names = await resolve_member_names(db, workflow_id)
    if not member_names:
        raise HTTPException(status_code=422, detail="Workflow has no members to run.")

    caller_sub = caller.get("sub")
    message = body.message or ""

    parent = AgentRun(
        agent_name=wf.name,
        input=message[:4000] if message else None,
        context="playground",
        status="queued",
        trigger_type="api",
        run_by=caller_sub,
        # POC-3: stamp the interactive caller so reactive members can compose that
        # user's advisory preference directive (_run_step_stream reads parent user_id).
        user_id=caller_sub,
        team=wf.team,
        workflow_id=wf.id,
        session_id=body.session_id,
    )
    db.add(parent)
    await db.flush()

    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=str(parent.id),
        agent_name=wf.name,
        user_id=caller_sub or "system",
        context="playground",
        input_message=message[:4000] if message else "",
    )
    if trace_id:
        parent.langfuse_trace_id = trace_id

    await db.commit()
    await db.refresh(parent)

    parent_id = str(parent.id)
    conversation_id = body.session_id or parent_id
    team = wf.team
    mode = wf.orchestration
    shape = wf.execution_shape

    async def _sse():
        async for frame in orchestrate_stream(
            parent_id, team, str(workflow_id), message, mode, conversation_id, shape
        ):
            # The internal per-member sentinel must never reach the client.
            if frame.get("type") == "__member_end__":
                continue
            yield f"data: {json.dumps(frame)}\n\n"

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{workflow_id}/runs", response_model=list[AgentRunResponse])
async def list_workflow_runs(
    workflow_id: uuid.UUID,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    await _get_workflow(workflow_id, db)
    q = select(AgentRun).where(AgentRun.workflow_id == workflow_id)
    if status_filter:
        q = q.where(AgentRun.status == status_filter)
    q = q.order_by(AgentRun.started_at.desc()).limit(limit).offset(offset)
    rows = list((await db.execute(q)).scalars().all())

    obs = get_observability_backend()
    items: list[AgentRunResponse] = []
    for r in rows:
        resp = AgentRunResponse.model_validate(r)
        resp.trace_url = obs.build_trace_url(r.langfuse_trace_id)
        items.append(resp)
    return items


@router.get("/{workflow_id}/runs/{run_id}/tree", response_model=WorkflowRunTreeResponse)
async def get_workflow_run_tree(
    workflow_id: uuid.UUID, run_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> WorkflowRunTreeResponse:
    parent = (await db.execute(select(AgentRun).where(AgentRun.id == run_id))).scalar_one_or_none()
    if parent is None or parent.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Workflow run not found.")
    children = list((await db.execute(
        select(AgentRun).where(AgentRun.parent_run_id == run_id).order_by(AgentRun.started_at)
    )).scalars().all())

    obs = get_observability_backend()
    # Shared transcript key for this run (§5.2): session_id when a session was supplied to
    # the stream/start endpoint, else the run id. Rationale rows are keyed on it.
    conversation_id = parent.session_id or str(run_id)

    def _with_trace_url(run: AgentRun) -> AgentRunResponse:
        resp = AgentRunResponse.model_validate(run)
        resp.trace_url = obs.build_trace_url(run.langfuse_trace_id)
        return resp

    async def _project_child(run: AgentRun) -> AgentRunResponse:
        resp = _with_trace_url(run)
        # tool_calls (2b-i): reactive tool-call marker rows persisted by the streaming
        # orchestrator (output.kind='tool_call'), ordered as observed.
        steps = list((await db.execute(
            select(RunStep)
            .where(
                RunStep.run_id == run.id,
                RunStep.output["kind"].astext == "tool_call",
            )
            .order_by(RunStep.step_number)
        )).scalars().all())
        resp.tool_calls = [
            ToolCallProjection(
                tool_name=s.name,
                status=(s.output or {}).get("status", "ok"),
            )
            for s in steps
        ]
        # rationale (2b-ii): the latest message_kind='rationale' row this member authored
        # on the shared workflow thread. Null for tool-less/durable members.
        rationale = (await db.execute(
            select(AgentMemory.content)
            .where(
                AgentMemory.thread_id == conversation_id,
                AgentMemory.scope == "workflow_run",
                AgentMemory.message_kind == "rationale",
                AgentMemory.agent_name == run.agent_name,
            )
            .order_by(AgentMemory.message_index.desc())
            .limit(1)
        )).scalar_one_or_none()
        resp.rationale = rationale or None
        return resp

    return WorkflowRunTreeResponse(
        parent=_with_trace_url(parent),
        children=[await _project_child(c) for c in children],
    )


# --- Trigger endpoints (schedule + webhook) ---
# Mirrors the agent trigger CRUD in routers/triggers.py; targets workflow_id
# instead of agent_id so the ck_agent_triggers_target CHECK constraint is satisfied.

@router.post(
    "/{workflow_id}/triggers",
    response_model=WorkflowTriggerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_trigger(
    workflow_id: uuid.UUID,
    body: AgentTriggerCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentTrigger:
    wf = await _get_workflow(workflow_id, db)
    if not await can_manage_artifact(db, claims["sub"], wf.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], wf.id,
        )
    # The human who arms the workflow trigger — authorizes the standing daemon
    # workflow run; audit reads "workflow:X (service) on behalf of {armed_by}".
    armed_by = (user or {}).get("sub") or x_user_sub
    plaintext = None
    token_hash = None
    # WS-4: born `token`, upgraded to `client_signed` on FIRST client registration —
    # the SAME rule as the agent producer (routers/triggers.py, where the full
    # rationale lives), the same column, the same gateway verify hop. A workflow
    # trigger is an `agent_triggers` row with `workflow_id` set, so the upgrade is
    # performed by the same shared registration endpoint with no per-shape copy.
    auth_mode = "token"
    if body.trigger_type == "webhook":
        plaintext, token_hash = _new_token()
    trigger = AgentTrigger(
        workflow_id=wf.id,
        agent_id=None,
        trigger_type=body.trigger_type,
        auth_mode=auth_mode,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        enabled=body.enabled,
        filter_conditions=body.filter_conditions,
        input_payload=body.input_payload,
        alert_email=body.alert_email,
        alert_on_failure=body.alert_on_failure,
        token_hash=token_hash,
        armed_by=armed_by,
        approver_role=body.approver_role,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    # Attach the plaintext token + full webhook URL as transient attributes so they
    # are returned ONCE in this create response (never persisted, never in list/get).
    trigger.token = plaintext
    trigger.webhook_url = workflow_webhook_url(wf.name, plaintext) if plaintext else None
    logger.info(
        "created %s trigger for workflow '%s' (id=%s)", body.trigger_type, wf.name, trigger.id
    )
    return trigger


@router.get("/{workflow_id}/triggers", response_model=list[WorkflowTriggerResponse])
async def list_workflow_triggers(
    workflow_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[AgentTrigger]:
    await _get_workflow(workflow_id, db)
    result = await db.execute(
        select(AgentTrigger)
        .where(AgentTrigger.workflow_id == workflow_id)
        .order_by(AgentTrigger.created_at)
    )
    return list(result.scalars().all())


@router.get("/{workflow_id}/triggers/{trigger_id}", response_model=WorkflowTriggerResponse)
async def get_workflow_trigger(
    workflow_id: uuid.UUID,
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentTrigger:
    await _get_workflow(workflow_id, db)
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.workflow_id == workflow_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return trigger


@router.patch("/{workflow_id}/triggers/{trigger_id}", response_model=WorkflowTriggerResponse)
async def update_workflow_trigger(
    workflow_id: uuid.UUID,
    trigger_id: uuid.UUID,
    body: AgentTriggerUpdate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentTrigger:
    wf = await _get_workflow(workflow_id, db)
    if not await can_manage_artifact(db, claims["sub"], wf.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], wf.id,
        )
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.workflow_id == workflow_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(trigger, field, value)
    trigger.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(trigger)
    return trigger


@router.delete(
    "/{workflow_id}/triggers/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_workflow_trigger(
    workflow_id: uuid.UUID,
    trigger_id: uuid.UUID,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    wf = await _get_workflow(workflow_id, db)
    if not await can_manage_artifact(db, claims["sub"], wf.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], wf.id,
        )
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.workflow_id == workflow_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    await db.delete(trigger)
    await db.commit()
    logger.info("deleted trigger %s for workflow '%s'", trigger_id, workflow_id)


@router.post(
    "/{workflow_id}/triggers/{trigger_id}/rotate-token",
    response_model=RotateTokenResponse,
)
async def rotate_workflow_trigger_token(
    workflow_id: uuid.UUID,
    trigger_id: uuid.UUID,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> RotateTokenResponse:
    """Generate a new webhook token for a workflow trigger, store its sha256, and
    return the plaintext ONCE. The old hash is invalidated immediately."""
    wf = await _get_workflow(workflow_id, db)
    if not await can_manage_artifact(db, claims["sub"], wf.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], wf.id,
        )
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.workflow_id == workflow_id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    if trigger.trigger_type != "webhook":
        raise HTTPException(
            status_code=400, detail="Only webhook triggers have rotatable tokens"
        )

    plaintext, token_hash = _new_token()
    trigger.token_hash = token_hash
    trigger.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("rotated webhook token for workflow '%s' trigger %s", wf.name, trigger_id)
    return RotateTokenResponse(
        trigger_id=trigger.id,
        token=plaintext,
        webhook_url=workflow_webhook_url(wf.name, plaintext),
    )


# ---------------------------------------------------------------------------
# POST /{workflow_id}/publish
# ---------------------------------------------------------------------------
class WorkflowPublishBody(BaseModel):
    version_id: Optional[uuid.UUID] = None


@router.post(
    "/{workflow_id}/publish",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit publish request for a workflow",
)
async def publish_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowPublishBody = WorkflowPublishBody(),
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from models import PublishRequest

    caller = (user or {}).get("sub") or x_user_sub or "system"

    result = await db.execute(
        select(CompositeWorkflow).where(CompositeWorkflow.id == workflow_id)
    )
    wf = result.scalar_one_or_none()
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if wf.publish_status == "pending_review":
        raise HTTPException(status_code=409, detail="A publish request is already pending review.")

    # Eval gate: resolve the target version.
    # If body.version_id is provided (from a deployment context), validate
    # that specific version. Otherwise fall back to the latest version.
    from models import WorkflowVersion
    if body.version_id:
        target_ver = (await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.id == body.version_id, WorkflowVersion.workflow_id == workflow_id)
        )).scalar_one_or_none()
        if target_ver is None:
            raise HTTPException(status_code=404, detail="Specified workflow version not found.")
    else:
        target_ver = (await db.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version_number.desc())
            .limit(1)
        )).scalar_one_or_none()
    if target_ver is None:
        raise HTTPException(status_code=409, detail="No version exists. Create a version before publishing.")
    if not target_ver.eval_passed:
        raise HTTPException(status_code=403, detail="Workflow version has not passed evaluation.")

    pr = PublishRequest(
        asset_id=wf.id,
        asset_type="workflow",
        submitted_by=caller,
        highest_risk_level="low",
        dependency_declaration={},
        source_version_id=target_ver.id,
    )
    db.add(pr)
    wf.publish_status = "pending_review"
    wf.updated_at = datetime.now(timezone.utc)
    await db.flush()

    logger.info("publish_workflow: id=%s submitted_by=%s pr_id=%s version=%s", workflow_id, caller, pr.id, target_ver.id)
    return {"publish_request_id": str(pr.id)}


# ---------------------------------------------------------------------------
# Workflow Versions (snapshot composition for deploy + rollback)
# ---------------------------------------------------------------------------
from models import WorkflowVersion, WorkflowDeployment
from schemas import (
    WorkflowVersionCreate,
    WorkflowVersionPatch,
    WorkflowVersionResponse,
    WorkflowDeploymentCreate,
    WorkflowDeploymentResponse,
    WorkflowDeploymentActionRequest,
)


@router.post(
    "/{workflow_id}/versions",
    response_model=WorkflowVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Snapshot current workflow composition as a version",
)
async def create_workflow_version(
    workflow_id: uuid.UUID,
    body: WorkflowVersionCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> WorkflowVersionResponse:
    wf = await _get_workflow(workflow_id, db)
    caller = (user or {}).get("sub") or x_user_sub or "system"

    # Determine next version number
    max_q = select(func.max(WorkflowVersion.version_number)).where(
        WorkflowVersion.workflow_id == workflow_id
    )
    max_num = (await db.execute(max_q)).scalar() or 0

    # Snapshot members
    members_q = (
        select(WorkflowMember, Agent.name)
        .join(Agent, Agent.id == WorkflowMember.agent_id)
        .where(WorkflowMember.workflow_id == workflow_id)
        .order_by(WorkflowMember.position.nulls_last())
    )
    member_rows = (await db.execute(members_q)).all()
    members_snapshot = []
    for (m, aname) in member_rows:
        latest_ver_id = (await db.execute(
            select(AgentVersion.id)
            .where(AgentVersion.agent_id == m.agent_id)
            .order_by(AgentVersion.version_number.desc())
            .limit(1)
        )).scalar_one_or_none()
        members_snapshot.append({
            "agent_id": str(m.agent_id),
            "agent_name": aname,
            "role": m.role,
            "position": m.position,
            "agent_version_id": str(latest_ver_id) if latest_ver_id else None,
        })

    # Snapshot edges
    edges_q = (
        select(WorkflowEdge)
        .where(WorkflowEdge.workflow_id == workflow_id)
        .order_by(WorkflowEdge.position.nulls_last())
    )
    edge_rows = (await db.execute(edges_q)).scalars().all()
    edges_snapshot = [
        {"source_agent_id": str(e.source_agent_id), "target_agent_id": str(e.target_agent_id),
         "condition": e.condition, "position": e.position}
        for e in edge_rows
    ]

    version = WorkflowVersion(
        workflow_id=workflow_id,
        version_number=max_num + 1,
        members=members_snapshot,
        edges=edges_snapshot,
        orchestration=wf.orchestration,
        execution_shape=wf.execution_shape,
        config={"memory_enabled": wf.memory_enabled},
        eval_passed=body.eval_passed,
        created_by=caller,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    logger.info("created workflow version v%d for '%s'", version.version_number, wf.name)
    return WorkflowVersionResponse.model_validate(version)


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionResponse])
async def list_workflow_versions(
    workflow_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowVersionResponse]:
    await _get_workflow(workflow_id, db)
    q = (
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.version_number.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(q)).scalars().all()
    return [WorkflowVersionResponse.model_validate(v) for v in rows]


@router.delete(
    "/{workflow_id}/versions/{version_id}",
    summary="Delete a workflow version (cascades sandbox deployments)",
)
async def delete_workflow_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a workflow version.

    - Returns 404 if the workflow or version does not exist.
    - Terminates all non-terminated WorkflowDeployment rows for this version.
    """
    await _get_workflow(workflow_id, db)

    ver = (
        await db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.id == version_id,
                WorkflowVersion.workflow_id == workflow_id,
            )
        )
    ).scalar_one_or_none()
    if ver is None:
        raise HTTPException(
            status_code=404,
            detail=f"Version '{version_id}' not found for workflow '{workflow_id}'.",
        )

    # Terminate all non-terminated deployments for this version.
    active_deps = (
        await db.execute(
            select(WorkflowDeployment).where(
                WorkflowDeployment.version_id == version_id,
                WorkflowDeployment.status.notin_(["terminated"]),
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for dep in active_deps:
        dep.status = "terminated"
        dep.terminated_at = now
    terminated_count = len(active_deps)

    await db.delete(ver)
    await db.commit()

    logger.info(
        "delete_workflow_version: deleted version %s for workflow '%s' (terminated %d deployments)",
        version_id,
        workflow_id,
        terminated_count,
    )
    return {"deleted_version_id": str(version_id), "terminated_deployments": terminated_count}


@router.patch(
    "/{workflow_id}/versions/{version_id}",
    response_model=WorkflowVersionResponse,
    summary="Patch workflow version eval result",
)
async def patch_workflow_version(
    workflow_id: uuid.UUID,
    version_id: uuid.UUID,
    body: WorkflowVersionPatch,
    db: AsyncSession = Depends(get_db),
) -> WorkflowVersionResponse:
    await _get_workflow(workflow_id, db)

    ver = (
        await db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.id == version_id,
                WorkflowVersion.workflow_id == workflow_id,
            )
        )
    ).scalar_one_or_none()
    if ver is None:
        raise HTTPException(
            status_code=404,
            detail=f"Version '{version_id}' not found for workflow '{workflow_id}'.",
        )

    changed = False
    if body.eval_passed is not None:
        ver.eval_passed = body.eval_passed
        changed = True
    if body.notes is not None:
        ver.notes = body.notes
        changed = True

    if changed:
        await db.flush()
        await db.refresh(ver)

    logger.info(
        "patch_workflow_version: workflow=%s version=%s eval_passed=%s",
        workflow_id, version_id, ver.eval_passed,
    )
    return WorkflowVersionResponse.model_validate(ver)


# ---------------------------------------------------------------------------
# Workflow Deployments (logical — no pod, platform is the orchestrator)
# ---------------------------------------------------------------------------
@router.post(
    "/{workflow_id}/deploy",
    response_model=WorkflowDeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Deploy a workflow version (logical deployment)",
)
async def deploy_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowDeploymentCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> WorkflowDeploymentResponse:
    wf = await _get_workflow(workflow_id, db)
    caller = (user or {}).get("sub") or x_user_sub or "system"

    # Verify version belongs to this workflow
    ver = (await db.execute(
        select(WorkflowVersion).where(
            WorkflowVersion.id == body.version_id,
            WorkflowVersion.workflow_id == workflow_id,
        )
    )).scalar_one_or_none()
    if ver is None:
        raise HTTPException(status_code=404, detail="Workflow version not found for this workflow.")

    if body.environment == "production" and not ver.eval_passed:
        raise HTTPException(
            status_code=403,
            detail="Workflow version must pass evaluation before production deployment.",
        )

    deployment_name = body.name or f"{wf.name}-{uuid.uuid4().hex[:4]}"

    dep = WorkflowDeployment(
        workflow_id=workflow_id,
        version_id=body.version_id,
        name=deployment_name,
        environment=body.environment,
        status="running",
        replicas=body.replicas,
        ttl_hours=body.ttl_hours,
        deployed_by=caller,
    )
    db.add(dep)
    await db.commit()
    await db.refresh(dep)
    logger.info("deployed workflow '%s' version v%d as '%s'", wf.name, ver.version_number, dep.name)
    return WorkflowDeploymentResponse.model_validate(dep)


@router.get("/{workflow_id}/deployments", response_model=list[WorkflowDeploymentResponse])
async def list_workflow_deployments(
    workflow_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowDeploymentResponse]:
    await _get_workflow(workflow_id, db)
    q = (
        select(WorkflowDeployment)
        .where(WorkflowDeployment.workflow_id == workflow_id)
        .order_by(WorkflowDeployment.deployed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(q)).scalars().all()
    return [WorkflowDeploymentResponse.model_validate(d) for d in rows]


@router.patch(
    "/{workflow_id}/deployments/{deployment_id}",
    response_model=WorkflowDeploymentResponse,
    summary="Lifecycle action on a workflow deployment",
)
async def workflow_deployment_action(
    workflow_id: uuid.UUID,
    deployment_id: uuid.UUID,
    body: WorkflowDeploymentActionRequest,
    db: AsyncSession = Depends(get_db),
) -> WorkflowDeploymentResponse:
    dep = (await db.execute(
        select(WorkflowDeployment).where(
            WorkflowDeployment.id == deployment_id,
            WorkflowDeployment.workflow_id == workflow_id,
        )
    )).scalar_one_or_none()
    if dep is None:
        raise HTTPException(status_code=404, detail="Workflow deployment not found.")

    now = datetime.now(timezone.utc)
    if body.action == "suspend":
        dep.status = "suspended"
        dep.suspended_at = now
    elif body.action == "resume":
        dep.status = "running"
        dep.suspended_at = None
    elif body.action == "terminate":
        dep.status = "terminated"
        dep.terminated_at = now
    elif body.action == "upgrade":
        if body.version_id is None:
            raise HTTPException(status_code=400, detail="version_id required for upgrade action.")
        ver = (await db.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.id == body.version_id,
                WorkflowVersion.workflow_id == workflow_id,
            )
        )).scalar_one_or_none()
        if ver is None:
            raise HTTPException(status_code=404, detail="Version not found for this workflow.")
        dep.previous_version_id = dep.version_id
        dep.version_id = body.version_id
        dep.status = "running"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action '{body.action}'")

    await db.commit()
    await db.refresh(dep)
    logger.info("workflow deployment %s action=%s → status=%s", deployment_id, body.action, dep.status)
    return WorkflowDeploymentResponse.model_validate(dep)


# ---------------------------------------------------------------------------
# Workflow deployment stats + runs (mirrors agent deployment stats)
# ---------------------------------------------------------------------------
@router.get(
    "/{workflow_id}/deployments/{deployment_id}/stats",
    summary="Run statistics for a workflow deployment (last 24h)",
)
async def get_workflow_deployment_stats(
    workflow_id: uuid.UUID,
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from datetime import timedelta
    import math
    from sqlalchemy import case

    dep = (await db.execute(
        select(WorkflowDeployment).where(
            WorkflowDeployment.id == deployment_id,
            WorkflowDeployment.workflow_id == workflow_id,
        )
    )).scalar_one_or_none()
    if dep is None:
        raise HTTPException(status_code=404, detail="Workflow deployment not found.")

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    scope = AgentRun.workflow_deployment_id == deployment_id

    stats_q = select(
        func.count(AgentRun.id).label("run_count"),
        func.sum(case((AgentRun.status == "failed", 1), else_=0)).label("error_count"),
        func.sum(AgentRun.cost_usd).label("total_cost"),
    ).where(scope, AgentRun.started_at >= cutoff)
    row = (await db.execute(stats_q)).first()
    run_count = row.run_count or 0
    error_count = row.error_count or 0
    total_cost = float(row.total_cost or 0)
    error_rate = (error_count / run_count) if run_count > 0 else 0.0

    p50 = p95 = None
    if run_count > 0:
        latency_q = (
            select(AgentRun.latency_ms)
            .where(scope, AgentRun.started_at >= cutoff, AgentRun.latency_ms.isnot(None))
            .order_by(AgentRun.latency_ms)
        )
        latencies = [r[0] for r in (await db.execute(latency_q)).all()]
        if latencies:
            p50 = latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.5))]
            p95 = latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.95))]

    return {
        "run_count": run_count,
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "error_rate": round(error_rate, 4),
        "total_cost_usd": round(total_cost, 6),
    }


@router.get(
    "/{workflow_id}/deployments/{deployment_id}/runs",
    response_model=list[AgentRunResponse],
    summary="List runs scoped to a workflow deployment",
)
async def list_workflow_deployment_runs(
    workflow_id: uuid.UUID,
    deployment_id: uuid.UUID,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    import os

    dep = (await db.execute(
        select(WorkflowDeployment).where(
            WorkflowDeployment.id == deployment_id,
            WorkflowDeployment.workflow_id == workflow_id,
        )
    )).scalar_one_or_none()
    if dep is None:
        raise HTTPException(status_code=404, detail="Workflow deployment not found.")

    q = (
        select(AgentRun)
        .where(AgentRun.workflow_deployment_id == deployment_id)
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status_filter:
        q = q.where(AgentRun.status == status_filter)
    rows = list((await db.execute(q)).scalars().all())

    obs = get_observability_backend()
    items: list[AgentRunResponse] = []
    for r in rows:
        resp = AgentRunResponse.model_validate(r)
        resp.trace_url = obs.build_trace_url(r.langfuse_trace_id)
        items.append(resp)
    return items

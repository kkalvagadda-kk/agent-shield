"""
AgentShield Registry API — Agents router.

Endpoints
---------
  POST   /api/v1/agents                      — register a new agent
  GET    /api/v1/agents                      — list agents (filterable, paginated)
  GET    /api/v1/agents/{name}               — get agent by name
  PUT    /api/v1/agents/{name}               — update agent fields
  DELETE /api/v1/agents/{name}               — soft-delete (set status=deprecated)
  POST   /api/v1/agents/{name}/quarantine    — emergency quarantine
  DELETE /api/v1/agents/{name}/quarantine    — lift quarantine
  POST   /api/v1/agents/{name}/publish       — submit publish request (Phase 9.2)
  GET    /api/v1/agents/{name}/identities    — list agent machine identities
  POST   /api/v1/agents/{name}/identities    — record a new K8s SA identity
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from models import (
    Agent,
    AgentIdentity,
    AgentRun,
    AgentTool,
    AgentTrigger,
    AgentVersion,
    PublishRequest,
    Tool,
)
from schemas import (
    AgentCreate,
    AgentHealthResponse,
    AgentIdentityCreate,
    AgentIdentityResponse,
    AgentPublishRequest,
    AgentResponse,
    AgentStatsResponse,
    AgentUpdate,
    PaginatedResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# POST /
# ---------------------------------------------------------------------------
@router.post(
    "/",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new agent",
)
async def create_agent(
    body: AgentCreate,
    x_user_sub: Optional[str] = Header(default=None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Create a new agent record.  Returns 409 if the name is already taken."""
    caller = (user or {}).get("sub") or x_user_sub or "system"

    # Uniqueness check
    existing = await db.execute(select(Agent).where(Agent.name == body.name))
    if existing.scalar_one_or_none() is not None:
        logger.warning("create_agent: name conflict — '%s' already exists", body.name)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An agent named '{body.name}' already exists.",
        )

    agent = Agent(
        name=body.name,
        team=body.team,
        description=body.description,
        agent_type=body.agent_type,
        agent_class=body.agent_class,
        execution_shape=body.execution_shape,
        memory_enabled=body.memory_enabled,
        metadata_=body.metadata,
        llm_provider_id=body.metadata.get("llm_provider_id") if body.metadata else None,
        created_by=caller,
    )
    db.add(agent)
    await db.flush()  # populate server-generated id / timestamps
    await db.refresh(agent)

    # Bind tools if provided
    if body.tools:
        for tool_name in body.tools:
            tool_row = await db.execute(select(Tool).where(Tool.name == tool_name))
            tool_obj = tool_row.scalar_one_or_none()
            if tool_obj:
                binding = AgentTool(
                    agent_id=agent.id,
                    tool_id=tool_obj.id,
                    added_by=caller,
                )
                db.add(binding)
            else:
                logger.warning("create_agent: tool '%s' not found, skipping binding", tool_name)
        await db.flush()

    logger.info(
        "create_agent: registered agent '%s' (id=%s, created_by=%s, tools=%s)",
        agent.name, agent.id, caller, body.tools or [],
    )
    return AgentResponse.model_validate(agent)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[AgentResponse],
    summary="List agents",
)
async def list_agents(
    team: Optional[str] = Query(None, description="Filter by team name"),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status"
    ),
    composable: bool = Query(
        False,
        description=(
            "When true, exclude agents that have an enabled schedule or webhook "
            "trigger — only pure-capability agents suitable as workflow members are returned."
        ),
    ),
    limit: int = Query(50, ge=1, le=500, description="Maximum records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AgentResponse]:
    """Return a paginated list of agents, optionally filtered by team and/or status.

    Visibility rule: published agents are visible to everyone; private/pending_review
    agents are visible only to their creator. System calls without identity see all.
    """
    from sqlalchemy import or_

    caller = (user or {}).get("sub") or x_user_sub

    base_query = select(Agent)
    count_query = select(func.count()).select_from(Agent)

    # Visibility (multi-tenant isolation): published agents are visible to all;
    # private/pending agents only to their creator. DENY-BY-DEFAULT: an
    # unauthenticated caller (no JWT and no X-User-Sub) sees ONLY published
    # agents — never another tenant's private agents. (Previously a missing
    # caller skipped the filter entirely and leaked every agent.)
    if caller:
        vis_filter = or_(
            Agent.publish_status == "published",
            Agent.created_by == caller,
        )
    else:
        vis_filter = Agent.publish_status == "published"
    base_query = base_query.where(vis_filter)
    count_query = count_query.where(vis_filter)

    if team is not None:
        base_query = base_query.where(Agent.team == team)
        count_query = count_query.where(Agent.team == team)

    if status_filter is not None:
        base_query = base_query.where(Agent.status == status_filter)
        count_query = count_query.where(Agent.status == status_filter)

    if composable:
        # Exclude agents that have any enabled schedule or webhook trigger —
        # workflow members must be pure capabilities with no self-firing trigger.
        self_firing_exists = exists(
            select(1).where(
                AgentTrigger.agent_id == Agent.id,
                AgentTrigger.trigger_type.in_(("schedule", "webhook")),
                AgentTrigger.enabled.is_(True),
            )
        )
        base_query = base_query.where(~self_firing_exists)
        count_query = count_query.where(~self_firing_exists)

    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    rows_result = await db.execute(
        base_query.order_by(Agent.created_at.desc()).limit(limit).offset(offset)
    )
    agents = rows_result.scalars().all()

    logger.debug(
        "list_agents: returning %d/%d agents (team=%s, status=%s)",
        len(agents),
        total,
        team,
        status_filter,
    )

    return PaginatedResponse[AgentResponse](
        items=[AgentResponse.model_validate(a) for a in agents],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /{name}
# ---------------------------------------------------------------------------
@router.get(
    "/{name}",
    response_model=AgentResponse,
    summary="Get agent by name",
)
async def get_agent(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Fetch a single agent by its unique name.  Returns 404 if not found."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )

    logger.debug("get_agent: fetched agent '%s' (id=%s)", agent.name, agent.id)
    return AgentResponse.model_validate(agent)


# ---------------------------------------------------------------------------
# PUT /{name}
# ---------------------------------------------------------------------------
@router.api_route(
    "/{name}",
    methods=["PUT", "PATCH"],
    response_model=AgentResponse,
    summary="Update agent",
)
async def update_agent(
    name: str,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Update mutable agent fields (description, status, metadata).
    Returns 404 if the agent does not exist."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )

    changed = False
    if body.description is not None:
        agent.description = body.description
        changed = True
    if body.status is not None:
        agent.status = body.status
        changed = True
    if body.metadata is not None:
        agent.metadata_ = body.metadata
        agent.llm_provider_id = body.metadata.get("llm_provider_id")
        changed = True
    if body.execution_shape is not None:
        agent.execution_shape = body.execution_shape
        changed = True
    if body.memory_enabled is not None:
        agent.memory_enabled = body.memory_enabled
        changed = True

    if changed:
        agent.updated_at = datetime.now(tz=timezone.utc)
        await db.flush()
        await db.refresh(agent)

    logger.info("update_agent: updated agent '%s' (id=%s)", agent.name, agent.id)
    return AgentResponse.model_validate(agent)


# ---------------------------------------------------------------------------
# DELETE /{name}
# ---------------------------------------------------------------------------
@router.delete(
    "/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Soft-delete agent",
)
async def delete_agent(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete an agent by setting its status to 'deprecated'.
    Returns 404 if not found."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )

    agent.status = "deprecated"
    agent.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()

    logger.info(
        "delete_agent: soft-deleted agent '%s' (id=%s) → status=deprecated",
        name,
        agent.id,
    )


# ---------------------------------------------------------------------------
# POST /{name}/quarantine
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/quarantine",
    response_model=AgentResponse,
    summary="Emergency quarantine",
)
async def quarantine_agent(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Set agent status to 'quarantined'. The Deploy Controller (Phase 3) will
    react to this status change and apply a blocking NetworkPolicy. The agent
    pod is NOT scaled to 0 so forensic state and LangGraph checkpoints are
    preserved for incident review."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )
    if agent.status == "quarantined":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent '{name}' is already quarantined.",
        )

    agent.status = "quarantined"
    agent.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()

    logger.warning("quarantine_agent: agent '%s' (id=%s) quarantined", name, agent.id)
    return AgentResponse.model_validate(agent)


# ---------------------------------------------------------------------------
# DELETE /{name}/quarantine
# ---------------------------------------------------------------------------
@router.delete(
    "/{name}/quarantine",
    response_model=AgentResponse,
    summary="Lift quarantine",
)
async def lift_quarantine(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Restore a quarantined agent to 'active' status."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )
    if agent.status != "quarantined":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent '{name}' is not quarantined (status='{agent.status}').",
        )

    agent.status = "active"
    agent.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()

    logger.info("lift_quarantine: agent '%s' (id=%s) restored to active", name, agent.id)
    return AgentResponse.model_validate(agent)


# ---------------------------------------------------------------------------
# POST /{name}/publish  — submit a publish request (Phase 9.2)
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/publish",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit publish request for an agent",
)
async def publish_agent(
    name: str,
    body: AgentPublishRequest,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Submit a publish request for the named agent.

    - Rejects (422) if any tool assigned to the agent has risk_level='critical'.
    - Sets agent.publish_status = 'pending_review'.
    - Returns 202 with the new publish_request_id.
    """
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )

    # Load all tools assigned to this agent
    tools_result = await db.execute(
        select(Tool)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent.id)
    )
    tools = tools_result.scalars().all()

    # Block if any tool has critical risk
    if any(t.risk_level == "critical" for t in tools):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "critical_risk_not_publishable"},
        )

    # Eval gate (Decision 20) — the agent's latest version must have passed
    # evaluation before it can be published to the catalog. (Moved here from deploy.)
    latest_version = (await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent.id)
        .order_by(AgentVersion.version_number.desc())
        .limit(1)
    )).scalar_one_or_none()
    if latest_version is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "no_version_to_publish"},
        )
    if not latest_version.eval_passed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "eval_not_passed", "version_number": latest_version.version_number},
        )
    version_tools = latest_version.tools or []
    has_risky = any(
        isinstance(t, dict) and t.get("risk", "low") in ("high", "critical")
        for t in version_tools
    ) or any(t.risk_level in ("high", "critical") for t in tools)
    if has_risky and not latest_version.adversarial_eval_passed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "adversarial_eval_not_passed", "version_number": latest_version.version_number},
        )

    # Determine highest risk level across assigned tools
    risk_order = {"low": 0, "medium": 1, "high": 2}
    highest = "low"
    for t in tools:
        if risk_order.get(t.risk_level, 0) > risk_order.get(highest, 0):
            highest = t.risk_level

    # Create the publish request record
    pr = PublishRequest(
        asset_id=agent.id,
        asset_type="agent",
        submitted_by=x_user_sub,
        highest_risk_level=highest,
        dependency_declaration=body.dependency_declaration,
    )
    db.add(pr)

    # Transition agent to pending_review
    agent.publish_status = "pending_review"
    agent.updated_at = datetime.now(tz=timezone.utc)

    await db.flush()
    await db.refresh(pr)

    logger.info(
        "publish_agent: agent='%s' (id=%s) publish_request_id=%s submitted_by=%s",
        name,
        agent.id,
        pr.id,
        x_user_sub,
    )
    return {"publish_request_id": str(pr.id)}


# ---------------------------------------------------------------------------
# GET /{name}/identities  — list machine identities for an agent
# ---------------------------------------------------------------------------
@router.get(
    "/{name}/identities",
    response_model=List[AgentIdentityResponse],
    summary="List agent machine identities",
)
async def list_agent_identities(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> List[AgentIdentityResponse]:
    """Return all provisioned K8s SA identities for the given agent."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")

    rows = await db.execute(
        select(AgentIdentity)
        .where(AgentIdentity.agent_name == name)
        .order_by(AgentIdentity.provisioned_at.desc())
    )
    return [AgentIdentityResponse.model_validate(r) for r in rows.scalars().all()]


# ---------------------------------------------------------------------------
# POST /{name}/identities  — record a new K8s SA identity (called by deploy-controller)
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/identities",
    response_model=AgentIdentityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a new agent machine identity",
)
async def create_agent_identity(
    name: str,
    body: AgentIdentityCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentIdentityResponse:
    """Record a new K8s ServiceAccount identity for the agent.

    Called by the deploy-controller immediately after creating the SA.
    Any existing non-revoked identity for the same sa_subject is left in place
    (idempotent: the controller may retry on failure).
    """
    result = await db.execute(select(Agent).where(Agent.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")

    identity = AgentIdentity(
        agent_name=name,
        deployment_id=body.deployment_id,
        sa_subject=body.sa_subject,
        sa_namespace=body.sa_namespace,
    )
    db.add(identity)
    await db.flush()
    await db.refresh(identity)

    logger.info(
        "create_agent_identity: agent='%s' sa_subject='%s'", name, body.sa_subject
    )
    return AgentIdentityResponse.model_validate(identity)


# ---------------------------------------------------------------------------
# GET /{name}/stats  — last-24h run aggregates
# ---------------------------------------------------------------------------
@router.get(
    "/{name}/stats",
    response_model=AgentStatsResponse,
    summary="Get agent run statistics (last 24 hours)",
)
async def get_agent_stats(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> AgentStatsResponse:
    """Return last-24h aggregates: run_count, latency percentiles, error_rate, total_cost."""
    from datetime import timedelta

    result = await db.execute(select(Agent).where(Agent.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)

    stats_q = select(
        func.count(AgentRun.id).label("run_count"),
        func.sum(case((AgentRun.status == "failed", 1), else_=0)).label("error_count"),
        func.sum(AgentRun.cost_usd).label("total_cost"),
    ).where(
        AgentRun.agent_name == name,
        AgentRun.started_at >= cutoff,
    )
    row = (await db.execute(stats_q)).first()
    run_count = row.run_count or 0
    error_count = row.error_count or 0
    total_cost = float(row.total_cost or 0)
    error_rate = (error_count / run_count) if run_count > 0 else 0.0

    p50 = None
    p95 = None
    if run_count > 0:
        latency_q = select(AgentRun.latency_ms).where(
            AgentRun.agent_name == name,
            AgentRun.started_at >= cutoff,
            AgentRun.latency_ms.isnot(None),
        ).order_by(AgentRun.latency_ms)
        latencies = [r[0] for r in (await db.execute(latency_q)).all()]
        if latencies:
            import math
            p50 = latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.5))]
            p95 = latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.95))]

    return AgentStatsResponse(
        run_count=run_count,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        error_rate=round(error_rate, 4),
        total_cost_usd=round(total_cost, 6),
    )


async def _derive_mode(db: AsyncSession, agent: Agent) -> str:
    """An agent's health mode: scheduled/event-driven if it has an enabled
    trigger of that kind, else durable/reactive from its execution_shape."""
    trig_q = select(AgentTrigger.trigger_type).where(
        AgentTrigger.agent_id == agent.id, AgentTrigger.enabled.is_(True)
    )
    types = {r[0] for r in (await db.execute(trig_q)).all()}
    if "schedule" in types:
        return "scheduled"
    if "webhook" in types:
        return "event-driven"
    if agent.execution_shape == "durable":
        return "durable"
    return "reactive"


@router.get(
    "/{name}/health",
    response_model=AgentHealthResponse,
    summary="Get mode-aware health signals for an agent (last 24 hours)",
)
async def get_agent_health(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> AgentHealthResponse:
    from datetime import timedelta

    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")

    mode = await _derive_mode(db, agent)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    resp = AgentHealthResponse(agent_name=name, mode=mode)

    # 24h aggregates shared across modes
    agg = (await db.execute(
        select(
            func.count(AgentRun.id).label("total"),
            func.sum(case((AgentRun.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((AgentRun.status == "completed", 1), else_=0)).label("completed"),
            func.sum(case((AgentRun.status == "blocked", 1), else_=0)).label("blocked"),
            func.sum(case((AgentRun.status == "awaiting_approval", 1), else_=0)).label("awaiting"),
            func.sum(AgentRun.cost_usd).label("cost"),
            func.avg(AgentRun.latency_ms).label("avg_latency"),
        ).where(AgentRun.agent_name == name, AgentRun.started_at >= cutoff)
    )).first()
    total = agg.total or 0
    failed = agg.failed or 0
    completed = agg.completed or 0
    blocked = agg.blocked or 0

    if mode == "reactive":
        p95 = None
        if total:
            lat = [r[0] for r in (await db.execute(
                select(AgentRun.latency_ms).where(
                    AgentRun.agent_name == name,
                    AgentRun.started_at >= cutoff,
                    AgentRun.latency_ms.isnot(None),
                ).order_by(AgentRun.latency_ms)
            )).all()]
            if lat:
                import math
                p95 = lat[min(len(lat) - 1, math.floor(len(lat) * 0.95))]
        err = (failed / total) if total else 0.0
        resp.p95_latency_ms = p95
        resp.error_rate = round(err, 4)
        resp.runs_24h = total
        resp.cost_24h = round(float(agg.cost or 0), 6)
        resp.health = "failing" if err > 0.5 else ("degraded" if err > 0.1 else "healthy")

    elif mode == "durable":
        # awaiting count is not time-bounded — approvals may sit open for a while
        awaiting = (await db.execute(
            select(func.count(AgentRun.id)).where(
                AgentRun.agent_name == name, AgentRun.status == "awaiting_approval"
            )
        )).scalar() or 0
        resp.awaiting_approval_count = int(awaiting)
        resp.failed_24h = failed
        resp.avg_duration_ms = int(agg.avg_latency) if agg.avg_latency is not None else None
        resp.health = "failing" if failed > 0 else ("degraded" if awaiting > 0 else "healthy")

    elif mode == "scheduled":
        last = (await db.execute(
            select(AgentRun.status).where(AgentRun.agent_name == name)
            .order_by(AgentRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        resp.last_run_status = last
        resp.missed_fires = 0
        # Next fire time from the first enabled schedule trigger's cron.
        cron = (await db.execute(
            select(AgentTrigger.cron_expression).where(
                AgentTrigger.agent_id == agent.id,
                AgentTrigger.enabled.is_(True),
                AgentTrigger.trigger_type == "schedule",
            ).limit(1)
        )).scalar_one_or_none()
        if cron:
            try:
                from croniter import croniter
                base = datetime.now(tz=timezone.utc)
                resp.next_fire_at = croniter(cron, base).get_next(datetime)
            except Exception:  # bad cron / lib missing — leave null
                resp.next_fire_at = None
        resp.health = "failing" if last == "failed" else "healthy"

    else:  # event-driven
        rate = (completed / total) if total else None
        resp.match_rate_24h = round(rate, 4) if rate is not None else None
        resp.rejected_count_24h = blocked
        resp.health = "degraded" if blocked > 0 else "healthy"

    return resp

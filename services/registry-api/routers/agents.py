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
from sqlalchemy import case, delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_endpoints import DispatchTargetError, resolve_dispatch_target
from auth_middleware import get_optional_user, require_user
from db import get_db
from publish_cascade import blocked_detail, plan_tool_cascade
from rbac import (
    can_create_agent,
    can_manage_artifact,
    get_user_global_role,
    grant_creator_admin,
    require_global_role,
)
from models import (
    Agent,
    AgentIdentity,
    AgentKnowledgeBinding,
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


async def _require_manage(db: AsyncSession, claims: dict, agent: Agent) -> None:
    """403 unless the caller may manage THIS agent: platform-admin, or `agent-admin`
    granted on this artifact (which its creator receives automatically).

    R3, 2026-08-07. Until this landed, `PATCH /agents/{name}`, `DELETE /agents/{name}`
    and `POST /agents/{name}/publish` took only `Depends(get_db)` — no authentication at
    all. Anyone who could reach the API could rename, soft-delete (which also terminates
    the agent's deployments and disarms its triggers) or submit for publish ANY agent on
    the platform. R2 closed a read disclosure on `/admin/*` while these destructive
    mutations stayed anonymous; measured on 0.2.263, `agents.py` was 1 protected / 11
    exempt (suite-97 T-S97-011).

    One helper rather than the same six lines inlined five times, and it takes the
    already-fetched `agent` instead of re-querying: two lookups of one row is how a
    handler ends up authorizing a different object than it mutates.

    NOTE the 404-before-403 ordering in the callers: the agent must be fetched before it
    can be authorized, so a caller without access can still distinguish "exists" from
    "does not". Not a new leak — `GET /agents/{name}` is deliberately open for the
    in-cluster machine callers (declarative-runner, deploy-controller, eval-runner), so
    names are already enumerable. Closing that is the service-identity work in
    identity-propagation-architecture.md Phase 3, not this phase.
    """
    caller = claims["sub"]
    if await can_manage_artifact(db, caller, agent.id):
        return
    role = await get_user_global_role(db, caller)
    logger.warning(
        "agents: DENY sub=%s role=%s agent=%s — needs platform-admin or agent-admin on it",
        caller, role, agent.name,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Managing '{agent.name}' requires the 'agent-admin' role on it, or "
            f"platform-admin; you have '{role}' and no grant on this agent."
        ),
    )


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
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Create a new agent record. contributor+ required. Returns 409 on a name clash.

    R2, 2026-08-06. This handler took `get_optional_user` and fell back to an
    `X-User-Sub` header and then to the literal `"system"`, so an ANONYMOUS caller
    could register an agent and attribute it to whoever it liked — `created_by` and
    the `grant_creator_admin` auto-grant were both caller-supplied. `can_create_agent`
    has existed and been correct in `rbac.py` since R-phase 1 with zero call sites
    (§1.3); this is its first.

    The `x_user_sub` fallback is DELETED, not merely outranked. Leaving it as a
    secondary source keeps a header that any client can set feeding an identity field,
    which is the forgeable-attribution defect the identity doc owns (Phase 3). No
    in-cluster machine caller creates agents — checked: eval-runner's `X-User-Sub` goes
    to `/playground/eval/score` and `PATCH /playground/eval-runs/{id}`, never here, and
    the only other producer is `sdk/agentshield_sdk/cli.py`, a human-run CLI.
    """
    caller = claims["sub"]
    if not await can_create_agent(db, caller):
        # Second lookup, on the DENY path only, purely to name the role in the message.
        # Deliberately NOT hoisted above the check by inlining the hierarchy comparison:
        # `can_create_agent` is the one place that decides this, and a copy of its rule
        # here would be a second answer to one question — the exact drift that made
        # `approvals._ADMIN_ROLES` disagree with `rbac` in production
        # (docs/bugs/production-hitl-decide-403-authority.md). One extra query on a
        # refusal is cheaper than two definitions of who may create an agent.
        role = await get_user_global_role(db, caller)
        logger.warning("create_agent: DENY sub=%s role=%s — needs contributor+", caller, role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Creating an agent requires the 'contributor' role or higher; you have '{role}'.",
        )

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

    # Bind tools if provided (top-level field or metadata.tools from Studio)
    tool_names = body.tools or (body.metadata.get("tools", []) if body.metadata else [])
    if tool_names:
        for tool_name in tool_names:
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

    await grant_creator_admin(db, "agent", agent.id, caller)

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

    # Latest version number per agent (single grouped query — no N+1).
    ver_map = {}
    if agents:
        from models import AgentVersion
        ver_rows = await db.execute(
            select(AgentVersion.agent_id, func.max(AgentVersion.version_number))
            .where(AgentVersion.agent_id.in_([a.id for a in agents]))
            .group_by(AgentVersion.agent_id)
        )
        ver_map = {aid: vn for aid, vn in ver_rows.all()}

    items = []
    for a in agents:
        resp = AgentResponse.model_validate(a)
        resp.latest_version_number = ver_map.get(a.id)
        items.append(resp)

    return PaginatedResponse[AgentResponse](items=items, total=total)


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
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Update mutable agent fields (description, status, metadata).

    Requires platform-admin or `agent-admin` on this agent (R3) — see
    `_require_manage`. Returns 404 if the agent does not exist."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )
    await _require_manage(db, claims, agent)

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

        new_tool_names = body.metadata.get("tools", [])
        if isinstance(new_tool_names, list):
            await db.execute(
                delete(AgentTool).where(AgentTool.agent_id == agent.id)
            )
            for tool_name in new_tool_names:
                tool_row = (await db.execute(
                    select(Tool).where(Tool.name == tool_name)
                )).scalar_one_or_none()
                if tool_row:
                    db.add(AgentTool(agent_id=agent.id, tool_id=tool_row.id, added_by="system"))

            # `knowledge_search` is NOT a hand-picked tool — it is DERIVED from KB
            # bindings (attaching a KB wires it; detaching the last one removes it).
            # The rebuild above projects agent_tools purely from metadata.tools, which
            # would drop the binding-attached row. Re-assert the invariant centrally so
            # ANY caller of updateAgent is protected, not just the agent-config UI:
            #   knowledge_search ∈ agent_tools  ⟺  the agent has ≥1 KB binding.
            ks_tool_id = (await db.execute(
                select(Tool.id).where(Tool.name == "knowledge_search")
            )).scalar_one_or_none()
            if ks_tool_id is not None:
                await db.execute(
                    delete(AgentTool).where(
                        AgentTool.agent_id == agent.id,
                        AgentTool.tool_id == ks_tool_id,
                    )
                )
                has_kb = (await db.execute(
                    select(AgentKnowledgeBinding.kb_id)
                    .where(AgentKnowledgeBinding.agent_id == agent.id)
                    .limit(1)
                )).first() is not None
                if has_kb:
                    db.add(AgentTool(agent_id=agent.id, tool_id=ks_tool_id, added_by="system"))
    if body.execution_shape is not None:
        agent.execution_shape = body.execution_shape
        changed = True
    if body.agent_class is not None:
        agent.agent_class = body.agent_class
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
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete an agent by setting its status to 'deprecated'.

    Requires platform-admin or `agent-admin` on this agent (R3). This is the most
    destructive route in the router and was the most exposed: it also terminates the
    agent's running deployments and disarms its triggers, and it took no credential at
    all. Returns 404 if not found."""
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )
    await _require_manage(db, claims, agent)

    agent.status = "deprecated"
    # Cascade: terminate active deployments. Set 'terminating' (NOT 'terminated')
    # so the deploy-controller's terminating→delete_deployment→terminated state
    # machine runs and GCs the k8s Deployment/pods. Jumping straight to
    # 'terminated' skips that GC step, orphaning the pods (they linger until the
    # node fills up). Mirrors the explicit undeploy path (deployments.py /
    # catalog.py), which already uses 'terminating'.
    from models import Deployment
    await db.execute(
        update(Deployment)
        .where(
            Deployment.agent_id == agent.id,
            Deployment.status.in_(["pending", "deploying", "running", "suspended", "suspending"]),
        )
        .values(status="terminating")
    )
    # Disarm the agent's triggers in THIS transaction. A soft-delete used to leave
    # them armed, so a deleted agent's cron kept firing forever — demonstrated in one
    # click by the Chrome journey's leg 8 (delete produced `deprecated` agent +
    # `enabled=True` hourly schedule). Same transaction as the status change, or the
    # two can disagree. Re-activating does NOT re-arm: turning a schedule back on is
    # a deliberate act. See trigger_lifecycle for why not undeploy/suspend.
    from trigger_lifecycle import delete_schedule_triggers, disarm_triggers

    # Two named operations, each doing one thing, rather than one call with a flag.
    # Webhook triggers are DISARMED (deleting them cascades away their registered
    # webhook_clients and credentials); schedule triggers are REMOVED, because a
    # disarmed schedule on a deleted agent is inert and only clutters the operations
    # page. See trigger_lifecycle.delete_schedule_triggers.
    disarmed = await disarm_triggers(db, agent_id=agent.id, reason="agent deleted")
    await delete_schedule_triggers(db, agent_id=agent.id)
    agent.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()

    logger.info(
        "delete_agent: soft-deleted agent '%s' (id=%s) → status=deprecated, disarmed %d trigger(s)",
        name,
        agent.id,
        disarmed,
    )


# ---------------------------------------------------------------------------
# POST /{name}/quarantine
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/quarantine",
    response_model=AgentResponse,
    summary="Emergency quarantine",
    # platform-admin ONLY, not `agent-admin` — unlike the other mutations below.
    # Quarantine is an incident-response control applied TO an owner, frequently
    # BECAUSE of what their agent did; letting the owner lift their own quarantine
    # would make it advisory. Design §3 matrix. Was fully unauthenticated before R3:
    # anyone could quarantine any agent, which is a denial-of-service on every
    # deployment of it.
    dependencies=[require_global_role("platform-admin")],
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
    # Quarantine is a SECURITY action — a quarantined agent must not be woken by its
    # own cron while the incident is being reviewed. The pod is deliberately left
    # running for forensics (see the docstring), which makes disarming the triggers
    # the only thing standing between "quarantined" and "still executing on a timer".
    from trigger_lifecycle import disarm_triggers

    await disarm_triggers(db, agent_id=agent.id, reason="agent quarantined")
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
    # platform-admin ONLY — see the POST above. Lifting must not be available to the
    # party the quarantine was applied against.
    dependencies=[require_global_role("platform-admin")],
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
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Submit a publish request for the named agent.

    Requires platform-admin or `agent-admin` on this agent (R3).

    `submitted_by` used to come from an `X-User-Sub` header defaulting to the literal
    `"system"`, on a route with no credential — so an anonymous caller could push any
    agent into the review queue under any name. That name is what the reviewer sees when
    deciding, which makes the header a forged signature on a governance record, not just
    a bad audit field. It now comes from the verified token; the header is deleted rather
    than demoted, for the same reason as in `create_agent`.

    - Rejects (422) if any tool assigned to the agent has risk_level='critical'.
    - Sets agent.publish_status = 'pending_review'.
    - Returns 202 with the new publish_request_id.
    """
    submitter = claims["sub"]
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )
    await _require_manage(db, claims, agent)

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

    # Decision 47 option C — tools publish by riding along with the agent, so a request
    # that CANNOT cascade cleanly must be refused here rather than handed to a reviewer.
    # Blocked = bound, not yet published, and not this team's to publish. Approving such a
    # request would either publish another team's private draft (turning one team's review
    # into a publication decision about someone else's work) or leave the agent published
    # while a tool it depends on stays invisible to everyone the agent was published for.
    #
    # The same producer computes the set again at approve time — see publish_cascade.py
    # for why it is derived twice instead of snapshotted.
    cascade = await plan_tool_cascade(db, agent.id, agent.team)
    if cascade.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=blocked_detail(cascade, agent.team),
        )

    # Eval gate (Decision 20) — resolve the target version.
    # If body.version_id is provided (from a deployment context), validate
    # that specific version. Otherwise fall back to the latest version.
    if body.version_id:
        target_version = (await db.execute(
            select(AgentVersion)
            .where(AgentVersion.id == body.version_id, AgentVersion.agent_id == agent.id)
        )).scalar_one_or_none()
        if target_version is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "version_not_found", "version_id": str(body.version_id)},
            )
    else:
        target_version = (await db.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version_number.desc())
            .limit(1)
        )).scalar_one_or_none()
    if target_version is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "no_version_to_publish"},
        )
    if not target_version.eval_passed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "eval_not_passed", "version_number": target_version.version_number},
        )
    version_tools = target_version.tools or []
    has_risky = any(
        isinstance(t, dict) and t.get("risk", "low") in ("high", "critical")
        for t in version_tools
    ) or any(t.risk_level in ("high", "critical") for t in tools)
    if has_risky and not target_version.adversarial_eval_passed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "adversarial_eval_not_passed", "version_number": target_version.version_number},
        )

    # Determine highest risk level across assigned tools
    risk_order = {"low": 0, "medium": 1, "high": 2}
    highest = "low"
    for t in tools:
        if risk_order.get(t.risk_level, 0) > risk_order.get(highest, 0):
            highest = t.risk_level

    # ONE pending request per asset. This endpoint used to `db.add` unconditionally,
    # so every call created another row: no query for an existing pending request, no
    # uniqueness constraint behind it. The live cluster holds two agents with two
    # pending requests each — submitted three seconds apart by two different callers,
    # one pinning a version and one not.
    #
    # A second submission is not a second request. It is the same intent, restated —
    # "publish this agent" — and answering it with another queue row pushes the
    # ambiguity onto a reviewer, who then has two rows for one artifact with nothing
    # saying which supersedes which. Approving one leaves its twin behind.
    #
    # Idempotent rather than 409: there is no withdraw endpoint and `status` admits
    # only pending_review/approved/rejected, so a refusal would leave the operator
    # with no way forward. Re-pointing the existing request at the version they are
    # asking for keeps ONE row that always reflects the latest intent, which is what
    # the reviewer needs to see. docs/bugs/publish-click-gives-no-feedback.md
    existing = (await db.execute(
        select(PublishRequest).where(
            PublishRequest.asset_id == agent.id,
            PublishRequest.status == "pending_review",
        ).order_by(PublishRequest.submitted_at.desc())
    )).scalars().first()

    if existing is not None:
        if existing.source_version_id != target_version.id:
            logger.info(
                "publish_agent: agent=%r already pending (request=%s) — re-pointing "
                "from version %s to %s",
                name, existing.id, existing.source_version_id, target_version.id,
            )
            existing.source_version_id = target_version.id
            existing.highest_risk_level = highest
        else:
            logger.info(
                "publish_agent: agent=%r already pending (request=%s) — returning it "
                "unchanged rather than enqueuing a duplicate",
                name, existing.id,
            )
        agent.publish_status = "pending_review"
        agent.updated_at = datetime.now(tz=timezone.utc)
        await db.flush()
        await db.refresh(existing)
        # Same shape as the create path — the caller cannot tell the two apart, which
        # is the point of idempotency: "publish this" succeeded either way.
        return {"publish_request_id": str(existing.id)}

    # Create the publish request record, pinning the evaluated version
    pr = PublishRequest(
        asset_id=agent.id,
        asset_type="agent",
        submitted_by=submitter,
        highest_risk_level=highest,
        dependency_declaration=body.dependency_declaration,
        source_version_id=target_version.id,
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
        submitter,
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
    """Record or update a K8s ServiceAccount identity for the agent.

    Called by the deploy-controller after creating/confirming the SA.
    If an active (non-revoked) identity for the same sa_subject already exists,
    update its deployment_id to the new deployment. This keeps the OPA bundle
    generator happy when an agent is re-deployed (new deployment, same SA).
    """
    result = await db.execute(select(Agent).where(Agent.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")

    existing_result = await db.execute(
        select(AgentIdentity).where(
            AgentIdentity.sa_subject == body.sa_subject,
            AgentIdentity.revoked_at.is_(None),
        )
    )
    identity = existing_result.scalar_one_or_none()

    if identity:
        identity.deployment_id = body.deployment_id
        identity.production_deployment_id = body.production_deployment_id
        identity.sa_namespace = body.sa_namespace
        logger.info(
            "create_agent_identity: updated existing identity for agent='%s' "
            "sa_subject='%s' -> deployment_id='%s' production_deployment_id='%s'",
            name, body.sa_subject, body.deployment_id, body.production_deployment_id,
        )
    else:
        identity = AgentIdentity(
            agent_name=name,
            deployment_id=body.deployment_id,
            production_deployment_id=body.production_deployment_id,
            sa_subject=body.sa_subject,
            sa_namespace=body.sa_namespace,
        )
        db.add(identity)
        logger.info(
            "create_agent_identity: agent='%s' sa_subject='%s'",
            name, body.sa_subject,
        )

    await db.flush()
    await db.refresh(identity)
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
        # Select the status AND its reason in ONE query over ONE row. Fetching the
        # reason separately would let the badge and the explanation come from
        # different runs — a smaller copy of the bug this field exists to fix.
        last_row = (await db.execute(
            select(AgentRun.status, AgentRun.error_message)
            .where(AgentRun.agent_name == name)
            .order_by(AgentRun.started_at.desc()).limit(1)
        )).first()
        last = last_row.status if last_row else None
        resp.last_run_status = last
        resp.last_error = (last_row.error_message if last_row else None) if last == "failed" else None
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

        # CONFIG FIRST, HISTORY SECOND.
        #
        # This used to be `"failing" if last == "failed" else "healthy"` — health
        # derived purely from the most recent run. That answered "did the last run
        # fail?" when the operator is asking "is this schedule OK now?", and the two
        # come apart in both directions:
        #
        #   * Fix the cause (deploy to production) and the badge stayed RED until the
        #     next fire — up to an hour of "I fixed it and nothing changed". Reported
        #     from the UI with a screenshot; this is that fix.
        #   * A brand-new schedule on an agent that can NEVER dispatch read
        #     "healthy", because no run had failed yet. The most broken state the
        #     product can be in rendered green.
        #
        # So ask the resolver the live question. It is the same single owner the
        # dispatch door uses, so the badge cannot disagree with what a fire would
        # actually do.
        try:
            await resolve_dispatch_target(db, agent, environment="production")
            resp.dispatch_error = None
        except DispatchTargetError as exc:
            resp.dispatch_error = str(exc)

        if resp.dispatch_error:
            # Cannot dispatch: every fire WILL fail until this is fixed. Actionable now.
            resp.health = "failing"
        elif last == "failed":
            # Config is sound and a run still failed — a real problem, but a different
            # one: worth investigating rather than blocking, and the run rows carry the
            # detail. `degraded` keeps it visibly amber without claiming the schedule
            # can never work, which is what `failing` now means.
            resp.health = "degraded"
        else:
            resp.health = "healthy"

    else:  # event-driven
        rate = (completed / total) if total else None
        resp.match_rate_24h = round(rate, 4) if rate is not None else None
        resp.rejected_count_24h = blocked
        resp.health = "degraded" if blocked > 0 else "healthy"

    return resp

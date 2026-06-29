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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent, AgentIdentity, AgentTool, PublishRequest, Tool
from schemas import (
    AgentCreate,
    AgentIdentityCreate,
    AgentIdentityResponse,
    AgentPublishRequest,
    AgentResponse,
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
    db: AsyncSession = Depends(get_db),
) -> AgentResponse:
    """Create a new agent record.  Returns 409 if the name is already taken."""
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
        metadata_=body.metadata,
    )
    db.add(agent)
    await db.flush()  # populate server-generated id / timestamps
    await db.refresh(agent)

    logger.info("create_agent: registered agent '%s' (id=%s)", agent.name, agent.id)
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
    limit: int = Query(50, ge=1, le=500, description="Maximum records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AgentResponse]:
    """Return a paginated list of agents, optionally filtered by team and/or status."""
    base_query = select(Agent)
    count_query = select(func.count()).select_from(Agent)

    if team is not None:
        base_query = base_query.where(Agent.team == team)
        count_query = count_query.where(Agent.team == team)

    if status_filter is not None:
        base_query = base_query.where(Agent.status == status_filter)
        count_query = count_query.where(Agent.status == status_filter)

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
@router.put(
    "/{name}",
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

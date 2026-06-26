"""
AgentShield Registry API — Agents router.

Endpoints
---------
  POST   /api/v1/agents                    — register a new agent
  GET    /api/v1/agents                    — list agents (filterable, paginated)
  GET    /api/v1/agents/{name}             — get agent by name
  PUT    /api/v1/agents/{name}             — update agent fields
  DELETE /api/v1/agents/{name}             — soft-delete (set status=deprecated)
  POST   /api/v1/agents/{name}/quarantine  — emergency quarantine (sets status=quarantined)
  DELETE /api/v1/agents/{name}/quarantine  — lift quarantine (restores to active)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent
from schemas import AgentCreate, AgentResponse, AgentUpdate, PaginatedResponse

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

"""
AgentShield Registry API — Teams router.

Endpoints
---------
  POST /api/v1/teams/              — create team
  GET  /api/v1/teams/              — list teams
  GET  /api/v1/teams/{id}          — get team
  PUT  /api/v1/teams/{id}          — update team
  GET  /api/v1/teams/{id}/agents   — list agents belonging to this team
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent, Team
from schemas import AgentResponse, PaginatedResponse, TeamCreate, TeamResponse, TeamUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/teams", tags=["teams"])


async def _resolve(team_id: uuid.UUID, db: AsyncSession) -> Team:
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team '{team_id}' not found.",
        )
    return team


# ---------------------------------------------------------------------------
# POST /api/v1/teams/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=TeamResponse,
    summary="Create team",
)
async def create_team(
    body: TeamCreate,
    db: AsyncSession = Depends(get_db),
) -> TeamResponse:
    existing = (
        await db.execute(select(Team).where(Team.name == body.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Team '{body.name}' already exists.",
        )

    team = Team(
        name=body.name,
        namespace=body.namespace,
        keycloak_role_id=body.keycloak_role_id,
        description=body.description,
    )
    db.add(team)
    await db.flush()
    logger.info("create_team: id=%s name=%s namespace=%s", team.id, team.name, team.namespace)
    return TeamResponse.model_validate(team)


# ---------------------------------------------------------------------------
# GET /api/v1/teams/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[TeamResponse],
    summary="List teams",
)
async def list_teams(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TeamResponse]:
    q = select(Team).order_by(Team.name)
    total = len((await db.execute(q.with_only_columns(Team.id))).all())
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return PaginatedResponse(
        items=[TeamResponse.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/teams/{team_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{team_id}",
    response_model=TeamResponse,
    summary="Get team by ID",
)
async def get_team(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TeamResponse:
    team = await _resolve(team_id, db)
    return TeamResponse.model_validate(team)


# ---------------------------------------------------------------------------
# PUT /api/v1/teams/{team_id}
# ---------------------------------------------------------------------------
@router.put(
    "/{team_id}",
    response_model=TeamResponse,
    summary="Update team",
)
async def update_team(
    team_id: uuid.UUID,
    body: TeamUpdate,
    db: AsyncSession = Depends(get_db),
) -> TeamResponse:
    team = await _resolve(team_id, db)
    if body.namespace is not None:
        team.namespace = body.namespace
    if body.keycloak_role_id is not None:
        team.keycloak_role_id = body.keycloak_role_id
    if body.description is not None:
        team.description = body.description
    team.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    logger.info("update_team: id=%s", team.id)
    return TeamResponse.model_validate(team)


# ---------------------------------------------------------------------------
# GET /api/v1/teams/{team_id}/agents
# ---------------------------------------------------------------------------
@router.get(
    "/{team_id}/agents",
    response_model=PaginatedResponse[AgentResponse],
    summary="List agents in team",
)
async def list_team_agents(
    team_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AgentResponse]:
    team = await _resolve(team_id, db)
    q = (
        select(Agent)
        .where(Agent.team == team.name)
        .order_by(Agent.name)
    )
    total = len((await db.execute(q.with_only_columns(Agent.id))).all())
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return PaginatedResponse(
        items=[AgentResponse.model_validate(r) for r in rows],
        total=total,
    )

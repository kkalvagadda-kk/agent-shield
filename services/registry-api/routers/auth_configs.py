"""
AgentShield Registry API — Auth Configs router.

Endpoints
---------
  POST   /api/v1/auth-configs/        — create auth config (k8s_secret_ref stored, never returned)
  GET    /api/v1/auth-configs/        — list auth configs
  GET    /api/v1/auth-configs/{id}    — get auth config by ID
  PUT    /api/v1/auth-configs/{id}    — update auth config
  DELETE /api/v1/auth-configs/{id}    — delete auth config
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import AuthConfig
from schemas import AuthConfigCreate, AuthConfigResponse, AuthConfigUpdate, PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth-configs", tags=["auth-configs"])


async def _get_auth_config(config_id: uuid.UUID, db: AsyncSession) -> AuthConfig:
    result = await db.execute(select(AuthConfig).where(AuthConfig.id == config_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AuthConfig '{config_id}' not found.",
        )
    return row


# ---------------------------------------------------------------------------
# POST /api/v1/auth-configs/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthConfigResponse,
    summary="Create auth config",
)
async def create_auth_config(
    body: AuthConfigCreate,
    db: AsyncSession = Depends(get_db),
) -> AuthConfigResponse:
    config = AuthConfig(**body.model_dump())
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return AuthConfigResponse.model_validate(config)


# ---------------------------------------------------------------------------
# GET /api/v1/auth-configs/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[AuthConfigResponse],
    summary="List auth configs",
)
async def list_auth_configs(
    type: str | None = Query(None),
    owner_team: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AuthConfigResponse]:
    q = select(AuthConfig)
    if type:
        q = q.where(AuthConfig.type == type)
    if owner_team:
        q = q.where(AuthConfig.owner_team == owner_team)

    total = len((await db.execute(q.with_only_columns(AuthConfig.id))).all())
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(
        items=[AuthConfigResponse.model_validate(c) for c in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/auth-configs/{id}
# ---------------------------------------------------------------------------
@router.get(
    "/{config_id}",
    response_model=AuthConfigResponse,
    summary="Get auth config by ID",
)
async def get_auth_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuthConfigResponse:
    return AuthConfigResponse.model_validate(await _get_auth_config(config_id, db))


# ---------------------------------------------------------------------------
# PUT /api/v1/auth-configs/{id}
# ---------------------------------------------------------------------------
@router.put(
    "/{config_id}",
    response_model=AuthConfigResponse,
    summary="Update auth config",
)
async def update_auth_config(
    config_id: uuid.UUID,
    body: AuthConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> AuthConfigResponse:
    config = await _get_auth_config(config_id, db)

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(config, field, value)

    await db.commit()
    await db.refresh(config)
    return AuthConfigResponse.model_validate(config)


# ---------------------------------------------------------------------------
# DELETE /api/v1/auth-configs/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete auth config",
)
async def delete_auth_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    config = await _get_auth_config(config_id, db)
    await db.delete(config)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

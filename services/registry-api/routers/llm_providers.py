"""
AgentShield Registry API — LLM Providers router.

Endpoints
---------
  POST   /api/v1/llm-providers/      — register a provider (encrypts credentials)
  GET    /api/v1/llm-providers/      — list providers (optional ?team= filter)
  GET    /api/v1/llm-providers/{id}  — get a provider
  PUT    /api/v1/llm-providers/{id}  — update (re-encrypts if credentials included)
  DELETE /api/v1/llm-providers/{id}  — hard delete (409 if agents reference it)

Credentials are stored AES-256 (Fernet) encrypted in Postgres and are NEVER
returned in any API response. K8s Secrets are derived artifacts written by the
deployments router at deploy time.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crypto import encrypt_json, decrypt_json
from db import get_db
from models import Agent, LLMProvider
from schemas import (
    LLMProviderCreate,
    LLMProviderResponse,
    LLMProviderUpdate,
    PaginatedResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/llm-providers", tags=["llm-providers"])


async def _get_or_404(provider_id: uuid.UUID, db: AsyncSession) -> LLMProvider:
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"LLM provider '{provider_id}' not found.",
        )
    return provider


@router.post(
    "/",
    response_model=LLMProviderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an LLM provider",
)
async def create_provider(
    body: LLMProviderCreate,
    db: AsyncSession = Depends(get_db),
) -> LLMProviderResponse:
    existing = await db.execute(
        select(LLMProvider).where(
            LLMProvider.name == body.name,
            LLMProvider.team == body.team,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A provider named '{body.name}' already exists for team '{body.team}'.",
        )

    provider = LLMProvider(
        name=body.name,
        provider=body.provider,
        default_model=body.default_model,
        credentials_encrypted=encrypt_json(body.credentials.model_dump()),
        team=body.team,
    )
    db.add(provider)
    await db.flush()
    await db.refresh(provider)
    logger.info("create_provider: id=%s name=%s team=%s", provider.id, provider.name, provider.team)
    return LLMProviderResponse.model_validate(provider)


@router.get(
    "/",
    response_model=PaginatedResponse[LLMProviderResponse],
    summary="List LLM providers",
)
async def list_providers(
    team: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[LLMProviderResponse]:
    q = select(LLMProvider).order_by(LLMProvider.name)
    if team:
        q = q.where(LLMProvider.team == team)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return PaginatedResponse(
        items=[LLMProviderResponse.model_validate(r) for r in rows],
        total=total,
    )


@router.get(
    "/{provider_id}",
    response_model=LLMProviderResponse,
    summary="Get an LLM provider",
)
async def get_provider(
    provider_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LLMProviderResponse:
    provider = await _get_or_404(provider_id, db)
    return LLMProviderResponse.model_validate(provider)


@router.put(
    "/{provider_id}",
    response_model=LLMProviderResponse,
    summary="Update an LLM provider",
)
async def update_provider(
    provider_id: uuid.UUID,
    body: LLMProviderUpdate,
    db: AsyncSession = Depends(get_db),
) -> LLMProviderResponse:
    provider = await _get_or_404(provider_id, db)

    if body.name is not None:
        provider.name = body.name
    if body.default_model is not None:
        provider.default_model = body.default_model
    if body.credentials is not None:
        provider.credentials_encrypted = encrypt_json(body.credentials.model_dump())
    provider.updated_at = datetime.now(tz=timezone.utc)

    await db.flush()
    await db.refresh(provider)
    logger.info("update_provider: id=%s", provider_id)
    return LLMProviderResponse.model_validate(provider)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an LLM provider",
)
async def delete_provider(
    provider_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    provider = await _get_or_404(provider_id, db)

    # Reject if any active agent references this provider
    ref_count = (
        await db.execute(
            select(func.count(Agent.id)).where(
                Agent.llm_provider_id == provider_id,
                Agent.status == "active",
            )
        )
    ).scalar_one()
    if ref_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{ref_count} active agent(s) reference this provider. "
                "Re-assign or archive them before deleting."
            ),
        )

    await db.delete(provider)
    logger.info("delete_provider: id=%s name=%s", provider_id, provider.name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

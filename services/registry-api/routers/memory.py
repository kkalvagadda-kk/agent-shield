"""
Memory router — CRUD + semantic search for agent conversation memory.

Endpoints
---------
  POST   /api/v1/agents/{name}/memory                — save a turn
  GET    /api/v1/agents/{name}/memory                — list memory (paginated)
  POST   /api/v1/agents/{name}/memory/search         — semantic search
  DELETE /api/v1/agents/{name}/memory/{thread_id}    — delete thread (GDPR)
  DELETE /api/v1/agents/{name}/memory/clear           — wipe all memory
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent, AgentMemory
from schemas import (
    AgentMemoryResponse,
    MemorySaveTurnRequest,
    MemorySearchRequest,
    MemorySearchResult,
)
import memory as memory_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["memory"])


async def _get_agent_or_404(name: str, db: AsyncSession) -> Agent:
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")
    return agent


@router.post(
    "/{name}/memory",
    response_model=list[AgentMemoryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Save a conversation turn to memory",
)
async def save_turn(
    name: str,
    body: MemorySaveTurnRequest,
    db: AsyncSession = Depends(get_db),
) -> list[AgentMemoryResponse]:
    agent = await _get_agent_or_404(name, db)
    if not agent.memory_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Memory is not enabled for agent '{name}'.",
        )

    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    rows = await memory_service.save_turn(
        db=db,
        agent_name=name,
        team=agent.team,
        thread_id=body.thread_id,
        messages=messages,
        user_id=body.user_id,
        session_id=body.session_id,
        deployment_id=body.deployment_id,
    )
    await db.commit()
    return [AgentMemoryResponse.model_validate(r) for r in rows]


@router.get(
    "/{name}/memory",
    response_model=list[AgentMemoryResponse],
    summary="List memory messages",
)
async def list_memory(
    name: str,
    thread_id: Optional[str] = Query(None),
    deployment_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentMemoryResponse]:
    await _get_agent_or_404(name, db)
    q = select(AgentMemory).where(AgentMemory.agent_name == name)
    if thread_id:
        q = q.where(AgentMemory.thread_id == thread_id)
    if deployment_id:
        import uuid as _uuid
        q = q.where(AgentMemory.deployment_id == _uuid.UUID(deployment_id))
    q = q.order_by(AgentMemory.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return [AgentMemoryResponse.model_validate(r) for r in result.scalars().all()]


@router.post(
    "/{name}/memory/search",
    response_model=list[MemorySearchResult],
    summary="Semantic search over agent memory",
)
async def search_memory(
    name: str,
    body: MemorySearchRequest,
    db: AsyncSession = Depends(get_db),
) -> list[MemorySearchResult]:
    await _get_agent_or_404(name, db)

    query_embedding = [0.0] * 1536  # placeholder
    results = await memory_service.search_memory(
        db=db,
        agent_name=name,
        query_embedding=query_embedding,
        top_k=body.top_k,
        deployment_id=body.deployment_id,
    )
    return [MemorySearchResult(**r) for r in results]


# NOTE: /clear must be declared BEFORE /{thread_id} — FastAPI matches routes in
# definition order, so a static path segment ("clear") has to precede the
# path-parameter route or DELETE /memory/clear would bind thread_id="clear".
@router.delete(
    "/{name}/memory/clear",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Wipe all memory for an agent",
)
async def clear_memory(
    name: str,
    deployment_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_agent_or_404(name, db)
    await memory_service.clear_agent_memory(db, name, deployment_id=deployment_id)
    await db.commit()


@router.delete(
    "/{name}/memory/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete all memory for a thread (GDPR)",
)
async def delete_memory_thread(
    name: str,
    thread_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_agent_or_404(name, db)
    count = await memory_service.delete_thread(db, name, thread_id)
    await db.commit()
    if count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No memory found for thread.")

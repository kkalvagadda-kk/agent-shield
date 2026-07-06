"""
Agent Events router — read the inbound webhook log (Phase 9 event gateway).

GET /api/v1/agents/{name}/events?trigger_id=...&status=...
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal
from models import Agent, AgentEvent
from schemas import AgentEventResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["events"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/{name}/events", response_model=list[AgentEventResponse])
async def list_agent_events(
    name: str,
    trigger_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(
        None, alias="status", description="matched | filtered | rejected"
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentEvent]:
    # Confirm the agent exists (404 otherwise) for a clear error.
    agent = (await db.execute(select(Agent).where(Agent.name == name))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")

    if status_filter is not None and status_filter not in ("matched", "filtered", "rejected"):
        raise HTTPException(status_code=422, detail="status must be matched|filtered|rejected")

    q = select(AgentEvent).where(AgentEvent.agent_name == name)
    if trigger_id is not None:
        q = q.where(AgentEvent.trigger_id == trigger_id)
    if status_filter is not None:
        q = q.where(AgentEvent.status == status_filter)
    q = q.order_by(AgentEvent.received_at.desc()).limit(limit).offset(offset)

    rows = (await db.execute(q)).scalars().all()
    return list(rows)

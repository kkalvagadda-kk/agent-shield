"""
Agent Runs router — POST/GET /api/v1/agent-runs

Central invocation primitive. Every agent invocation (production or playground)
creates one row. Enables cost tracking, latency instrumentation, and Langfuse
trace linkage across the platform.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal
from models import AgentRun
from schemas import AgentRunCreate, AgentRunResponse, AgentRunUpdate

router = APIRouter(prefix="/api/v1/agent-runs", tags=["agent-runs"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_run(
    body: AgentRunCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentRun:
    run = AgentRun(
        agent_name=body.agent_name,
        agent_version_id=body.agent_version_id,
        session_id=body.session_id,
        user_id=body.user_id,
        input=body.input,
        langfuse_trace_id=body.langfuse_trace_id,
        context=body.context,
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


@router.patch("/{run_id}", response_model=AgentRunResponse)
async def update_agent_run(
    run_id: uuid.UUID,
    body: AgentRunUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentRun:
    result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(run, field, value)

    if body.status in ("completed", "failed", "blocked") and run.completed_at is None:
        run.completed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(run)
    return run


@router.get("", response_model=list[AgentRunResponse])
async def list_agent_runs(
    agent_name: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    context: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRun]:
    q = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
    if agent_name:
        q = q.where(AgentRun.agent_name == agent_name)
    if session_id:
        q = q.where(AgentRun.session_id == session_id)
    if context:
        q = q.where(AgentRun.context == context)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentRun:
    result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="agent run not found")
    return run

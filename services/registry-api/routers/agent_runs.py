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
from models import AgentRun, RunStep
from schemas import AgentRunCreate, AgentRunResponse, AgentRunUpdate, RunStepCreate, RunStepResponse

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
        trigger_type=body.trigger_type,
        run_by=body.run_by,
        team=body.team,
        thread_id=body.thread_id,
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

    if body.status in ("completed", "failed", "blocked", "cancelled") and run.completed_at is None:
        run.completed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(run)
    return run


@router.get("", response_model=list[AgentRunResponse])
async def list_agent_runs(
    agent_name: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    context: Optional[str] = Query(None),
    trigger_type: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    import os
    q = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit).offset(offset)
    if agent_name:
        q = q.where(AgentRun.agent_name == agent_name)
    if session_id:
        q = q.where(AgentRun.session_id == session_id)
    if context:
        q = q.where(AgentRun.context == context)
    if trigger_type:
        q = q.where(AgentRun.trigger_type == trigger_type)
    if team:
        q = q.where(AgentRun.team == team)
    if status_filter:
        q = q.where(AgentRun.status == status_filter)
    result = await db.execute(q)
    rows = list(result.scalars().all())

    lf_public_url = os.getenv("LANGFUSE_PUBLIC_URL", "")
    lf_project_id = os.getenv("LANGFUSE_PROJECT_ID", "")
    items: list[AgentRunResponse] = []
    for r in rows:
        resp = AgentRunResponse.model_validate(r)
        if r.langfuse_trace_id and lf_public_url and lf_project_id:
            resp.trace_url = f"{lf_public_url}/project/{lf_project_id}/traces/{r.langfuse_trace_id}"
        items.append(resp)
    return items


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


@router.get("/{run_id}/children", response_model=list[AgentRunResponse])
async def list_child_runs(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[AgentRun]:
    """Child agent runs of a composite-workflow parent run (Decision 22 run tree),
    ordered by start time."""
    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.parent_run_id == run_id)
        .order_by(AgentRun.started_at)
    )
    return list(result.scalars().all())


@router.post("/{run_id}/steps", response_model=RunStepResponse, status_code=status.HTTP_201_CREATED)
async def upsert_run_step(
    run_id: uuid.UUID,
    body: RunStepCreate,
    db: AsyncSession = Depends(get_db),
) -> RunStep:
    """Create or update a step for a durable run. Called by the run executor."""
    run_result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    if not run_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="agent run not found")

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(RunStep).where(RunStep.run_id == run_id, RunStep.step_number == body.step_number)
    )
    step = result.scalar_one_or_none()

    if step:
        step.status = body.status
        if body.output is not None:
            step.output = body.output
        if body.approval_id:
            step.approval_id = uuid.UUID(body.approval_id)
        if body.error_message:
            step.error_message = body.error_message
        if body.status == "running" and step.started_at is None:
            step.started_at = now
        if body.status in ("completed", "failed", "cancelled"):
            step.completed_at = now
    else:
        step = RunStep(
            run_id=run_id,
            step_number=body.step_number,
            name=body.name,
            status=body.status,
            output=body.output,
            error_message=body.error_message,
            started_at=now if body.status == "running" else None,
            completed_at=now if body.status in ("completed", "failed") else None,
        )
        if body.approval_id:
            step.approval_id = uuid.UUID(body.approval_id)
        db.add(step)

    await db.commit()
    await db.refresh(step)
    return step


@router.get("/{run_id}/steps", response_model=list[RunStepResponse])
async def list_run_steps(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[RunStep]:
    run_result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    if not run_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="agent run not found")
    result = await db.execute(
        select(RunStep)
        .where(RunStep.run_id == run_id)
        .order_by(RunStep.step_number)
    )
    return list(result.scalars().all())

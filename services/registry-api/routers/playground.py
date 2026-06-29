"""
Playground run endpoints — allows testing agents without a full deploy.

Endpoints
---------
  POST /api/v1/playground/runs          — start a playground run
  GET  /api/v1/playground/runs          — list runs for the caller
  GET  /api/v1/playground/runs/{id}/stream — SSE stream of run output
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent, PlaygroundRun
from playground_sa import ensure_playground_sa
from schemas import PlaygroundRunCreate, PlaygroundRunResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/playground", tags=["playground"])


# ---------------------------------------------------------------------------
# POST /api/v1/playground/runs
# ---------------------------------------------------------------------------
@router.post(
    "/runs",
    status_code=status.HTTP_201_CREATED,
    summary="Start a playground run",
)
async def create_playground_run(
    body: PlaygroundRunCreate,
    background_tasks: BackgroundTasks,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Create a playground run for an agent. Returns run_id and stream_url."""
    # Look up agent
    result = await db.execute(
        select(Agent).where(Agent.name == body.agent_name)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{body.agent_name}' not found.",
        )

    # Owner check (skip in dev mode when no header)
    caller = x_user_sub or "dev"
    if x_user_sub and agent.created_by and agent.created_by != x_user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the agent owner can run it in the playground.",
        )

    # Ensure per-user playground SA exists (best-effort; non-blocking)
    background_tasks.add_task(ensure_playground_sa, caller)

    now = datetime.now(tz=timezone.utc)
    run = PlaygroundRun(
        user_id=caller,
        agent_name=body.agent_name,
        agent_version_id=body.agent_version_id,
        context="playground",
        sandbox=True,
        input_message=body.input_message,
        status="running",
        started_at=now,
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)
    await db.commit()

    logger.info(
        "create_playground_run: run_id=%s agent=%s user=%s",
        run_id, body.agent_name, caller,
    )

    return {
        "run_id": run_id,
        "stream_url": f"/api/v1/playground/runs/{run_id}/stream",
    }


# ---------------------------------------------------------------------------
# GET /api/v1/playground/runs
# ---------------------------------------------------------------------------
@router.get(
    "/runs",
    response_model=list[PlaygroundRunResponse],
    summary="List playground runs",
)
async def list_playground_runs(
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> list[PlaygroundRunResponse]:
    """List playground runs for the calling user (or all runs if no header)."""
    q = select(PlaygroundRun).order_by(PlaygroundRun.started_at.desc())
    if x_user_sub:
        q = q.where(PlaygroundRun.user_id == x_user_sub)
    result = await db.execute(q)
    rows = result.scalars().all()
    return [PlaygroundRunResponse.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /api/v1/playground/runs/{run_id}/stream
# ---------------------------------------------------------------------------
async def _simulate_agent_stream(
    run_id: str,
    agent_name: str,
    input_message: str,
) -> AsyncIterator[str]:
    """Simulate an SSE stream. In production this would proxy to the agent pod."""
    # Simulate processing delay
    await asyncio.sleep(0.1)

    events = [
        {
            "event": "text_delta",
            "content": f"Playground mode: [{agent_name}] processing your message...",
        },
        {
            "event": "text_delta",
            "content": f"\n\nYour input: {input_message[:200]}",
        },
        {
            "event": "text_delta",
            "content": "\n\nPlayground response: This is a simulated response. "
            "The agent is running in sandbox mode.",
        },
        {"event": "done"},
    ]

    for ev in events:
        yield f"data: {json.dumps(ev)}\n\n"
        await asyncio.sleep(0.05)


async def _complete_run(run_id_str: str) -> None:
    """Background task: mark run as completed after stream ends."""
    from db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            parsed = uuid.UUID(run_id_str)
            result = await session.execute(
                select(PlaygroundRun).where(PlaygroundRun.id == parsed)
            )
            run = result.scalar_one_or_none()
            if run:
                run.status = "completed"
                run.completed_at = datetime.now(tz=timezone.utc)
                await session.commit()
                logger.debug("Marked playground run %s as completed", run_id_str)
        except Exception as exc:
            logger.warning("_complete_run: could not update run %s: %s", run_id_str, exc)


@router.get(
    "/runs/{run_id}/stream",
    summary="Stream playground run output (SSE)",
    response_class=StreamingResponse,
)
async def stream_playground_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE stream of playground run output. Returns simulated agent response."""
    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Playground run '{run_id}' not found.",
        )

    # Schedule completion update after stream
    background_tasks.add_task(_complete_run, run_id)

    return StreamingResponse(
        _simulate_agent_stream(
            run_id=run_id,
            agent_name=run.agent_name,
            input_message=run.input_message or "",
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-AgentShield-Playground": "true",
            "X-AgentShield-Sandbox": "true",
        },
    )

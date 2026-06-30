"""
Playground run endpoints — allows testing agents without a full deploy.

Endpoints
---------
  POST /api/v1/playground/runs               — start a playground run
  GET  /api/v1/playground/runs               — list runs for the caller
  GET  /api/v1/playground/runs/{id}/stream   — SSE stream of run output
  GET  /api/v1/playground/runs/{id}/trace    — fetch Langfuse trace for a run
  POST /api/v1/playground/runs/{id}/save-to-dataset — save run to a dataset
  POST /api/v1/playground/runs/{id}/feedback — submit thumbs-up/down feedback
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
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent, PlaygroundDataset, PlaygroundRun
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
    """Background task: mark run as completed after stream ends, then fire judge."""
    import asyncio
    from db import AsyncSessionLocal

    agent_name = "unknown"
    input_message = ""
    output_text = ""
    team = "platform"

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
                agent_name = run.agent_name
                input_message = run.input_message or ""
                await session.commit()
                logger.debug("Marked playground run %s as completed", run_id_str)
        except Exception as exc:
            logger.warning("_complete_run: could not update run %s: %s", run_id_str, exc)
            return

    # Fire-and-forget LLM-as-Judge scorer (non-blocking, 30s timeout in judge.py)
    if input_message:
        try:
            from judge import score_run
            asyncio.create_task(
                score_run(
                    run_id=uuid.UUID(run_id_str),
                    agent_name=agent_name,
                    input_text=input_message,
                    output_text=output_text,
                    team=team,
                )
            )
        except Exception as exc:
            logger.debug("_complete_run: could not launch judge for run %s: %s", run_id_str, exc)


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


# ---------------------------------------------------------------------------
# GET /api/v1/playground/runs/{run_id}/trace
# ---------------------------------------------------------------------------
@router.get(
    "/runs/{run_id}/trace",
    summary="Fetch Langfuse trace for a playground run",
)
async def get_playground_run_trace(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the Langfuse trace URL and trace ID for a completed playground run."""
    import os
    import urllib.error
    import urllib.request as urlreq

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Playground run '{run_id}' not found")

    if not run.langfuse_trace_id:
        return {
            "run_id": run_id,
            "trace_id": None,
            "trace_url": None,
            "status": run.status,
            "message": "No Langfuse trace associated with this run yet",
        }

    lf_host = os.getenv("LANGFUSE_HOST", "http://agentshield-langfuse-web:3000")
    lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")

    trace_url = f"{lf_host}/trace/{run.langfuse_trace_id}"
    trace_data: dict[str, Any] = {}

    if lf_pk and lf_sk:
        import base64
        creds = base64.b64encode(f"{lf_pk}:{lf_sk}".encode()).decode()
        try:
            req = urlreq.Request(
                f"{lf_host}/api/public/traces/{run.langfuse_trace_id}",
                headers={"Authorization": f"Basic {creds}"},
            )
            with urlreq.urlopen(req, timeout=5) as r:
                trace_data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                trace_data = {"warning": "trace not yet ingested by Langfuse"}
            else:
                logger.debug("Langfuse trace fetch error %s: %s", exc.code, exc)
        except Exception as exc:
            logger.debug("Langfuse trace fetch failed: %s", exc)

    return {
        "run_id": run_id,
        "trace_id": run.langfuse_trace_id,
        "trace_url": trace_url,
        "status": run.status,
        "langfuse": trace_data,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/playground/runs/{run_id}/save-to-dataset
# ---------------------------------------------------------------------------
class SaveToDatasetRequest(BaseModel):
    dataset_id: uuid.UUID
    label: Optional[str] = None


@router.post(
    "/runs/{run_id}/save-to-dataset",
    status_code=status.HTTP_201_CREATED,
    summary="Save a playground run as a dataset item",
)
async def save_run_to_dataset(
    run_id: str,
    body: SaveToDatasetRequest,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Append the run's input/output as a new item in the target dataset."""
    try:
        parsed_run_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    run_result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_run_id)
    )
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Playground run '{run_id}' not found")

    ds_result = await db.execute(
        select(PlaygroundDataset).where(PlaygroundDataset.id == body.dataset_id)
    )
    dataset = ds_result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail=f"Dataset '{body.dataset_id}' not found")

    new_item = {
        "id": str(uuid.uuid4()),
        "source_run_id": run_id,
        "agent_name": run.agent_name,
        "input": run.input_message,
        "label": body.label,
        "langfuse_trace_id": run.langfuse_trace_id,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "added_by": x_user_sub or "unknown",
    }

    dataset.items = (dataset.items or []) + [new_item]
    await db.commit()
    await db.refresh(dataset)

    return {
        "dataset_id": str(body.dataset_id),
        "item_id": new_item["id"],
        "items_count": len(dataset.items),
    }


# ---------------------------------------------------------------------------
# POST /api/v1/playground/runs/{run_id}/feedback
# ---------------------------------------------------------------------------
class RunFeedbackRequest(BaseModel):
    score: int  # 1 = thumbs up, -1 = thumbs down
    comment: Optional[str] = None


@router.post(
    "/runs/{run_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="Submit feedback for a playground run",
)
async def submit_run_feedback(
    run_id: str,
    body: RunFeedbackRequest,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record thumbs-up/down feedback. If run has a Langfuse trace_id, also
    pushes a score to Langfuse so it appears in the observability dashboard."""
    import os
    import urllib.request as urlreq

    if body.score not in (1, -1):
        raise HTTPException(status_code=422, detail="score must be 1 (thumbs up) or -1 (thumbs down)")

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    run_result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
    )
    run = run_result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Playground run '{run_id}' not found")

    langfuse_score_id: Optional[str] = None

    if run.langfuse_trace_id:
        lf_host = os.getenv("LANGFUSE_HOST", "http://agentshield-langfuse-web:3000")
        lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")

        if lf_pk and lf_sk:
            import base64
            creds = base64.b64encode(f"{lf_pk}:{lf_sk}".encode()).decode()
            score_payload = json.dumps({
                "traceId": run.langfuse_trace_id,
                "name": "user-feedback",
                "value": float(body.score),
                "comment": body.comment or ("thumbs up" if body.score == 1 else "thumbs down"),
                "source": "HUMAN_ANNOTATION",
                "dataType": "NUMERIC",
            }).encode()
            try:
                req = urlreq.Request(
                    f"{lf_host}/api/public/scores",
                    data=score_payload,
                    headers={
                        "Authorization": f"Basic {creds}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlreq.urlopen(req, timeout=5) as r:
                    score_resp = json.loads(r.read())
                    langfuse_score_id = score_resp.get("id")
            except Exception as exc:
                logger.debug("Langfuse score push failed: %s", exc)

    return {
        "run_id": run_id,
        "score": body.score,
        "comment": body.comment,
        "langfuse_trace_id": run.langfuse_trace_id,
        "langfuse_score_id": langfuse_score_id,
        "submitted_by": x_user_sub or "unknown",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }

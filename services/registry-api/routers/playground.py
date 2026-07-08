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
import os
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from models import Agent, Deployment, PlaygroundDataset, PlaygroundRun
from playground_sa import ensure_playground_sa
from schemas import PlaygroundRunCreate, PlaygroundRunResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/playground", tags=["playground"])

# Reserved service identities that bypass the per-agent owner check — they run
# agents they don't own (e.g. the eval-runner Job iterating a dataset).
_SERVICE_IDENTITIES = {"eval-runner"}


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
    user: dict | None = Depends(get_optional_user),
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

    # Resolve caller identity: JWT sub takes precedence over X-User-Sub header
    caller = (user or {}).get("sub") or x_user_sub or "dev"

    # Owner check (skip in dev mode when no header, and for reserved service
    # identities like the eval-runner that run agents they don't own).
    if (
        caller != "dev"
        and caller not in _SERVICE_IDENTITIES
        and agent.created_by
        and agent.created_by != caller
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the agent owner can run it in the playground.",
        )

    # Ensure per-user playground SA exists (best-effort; non-blocking)
    background_tasks.add_task(ensure_playground_sa, caller)

    shape = body.execution_shape or agent.execution_shape or "reactive"
    now = datetime.now(tz=timezone.utc)
    run = PlaygroundRun(
        user_id=caller,
        agent_name=body.agent_name,
        agent_version_id=body.agent_version_id,
        context="playground",
        sandbox=True,
        input_message=body.input_message,
        execution_shape=shape,
        input_payload=body.input_payload,
        trigger_type=body.trigger_type,
        trigger_payload=body.trigger_payload,
        status="running",
        started_at=now,
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)

    # Create Langfuse root trace for this run
    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=run_id,
        agent_name=body.agent_name,
        user_id=caller,
        context="playground",
        input_message=body.input_message or json.dumps(body.input_payload or {}),
    )
    if trace_id:
        run.langfuse_trace_id = trace_id
        await db.flush()

    await db.commit()

    logger.info(
        "create_playground_run: run_id=%s agent=%s user=%s shape=%s trace=%s",
        run_id, body.agent_name, caller, shape, trace_id,
    )

    # For durable runs, dispatch to the runner pod's /run endpoint
    if shape == "durable":
        background_tasks.add_task(
            _dispatch_durable_run, run_id, body.agent_name, body.input_payload, db
        )

    return {
        "run_id": run_id,
        "stream_url": f"/api/v1/playground/runs/{run_id}/stream",
        "execution_shape": shape,
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
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[PlaygroundRunResponse]:
    """List playground runs for the calling user.

    Playground runs are always user-scoped (tenant isolation). DENY-BY-DEFAULT:
    with no caller identity we return an empty list rather than every user's
    runs (previously a missing caller leaked all playground runs)."""
    caller = (user or {}).get("sub") or x_user_sub
    if not caller:
        return []
    q = (
        select(PlaygroundRun)
        .where(PlaygroundRun.user_id == caller)
        .order_by(PlaygroundRun.started_at.desc())
    )
    result = await db.execute(q)
    rows = result.scalars().all()
    return [PlaygroundRunResponse.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /api/v1/playground/runs/{run_id}
# ---------------------------------------------------------------------------
@router.get(
    "/runs/{run_id}",
    response_model=PlaygroundRunResponse,
    summary="Get a single playground run (includes judge fields)",
)
async def get_playground_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> PlaygroundRunResponse:
    """Return one playground run, including the LLM-judge fields. No owner check
    (consistent with /stream and /trace)."""
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
    return PlaygroundRunResponse.model_validate(run)


# ---------------------------------------------------------------------------
# Durable run dispatch
# ---------------------------------------------------------------------------

async def _dispatch_durable_run(
    run_id: str,
    agent_name: str,
    input_payload: dict | None,
    db: AsyncSession,
) -> None:
    """Dispatch a durable run to the declarative-runner pod's /run endpoint."""
    from db import AsyncSessionLocal
    from models import AgentRun, RunStep

    async with AsyncSessionLocal() as session:
        agent_result = await session.execute(
            select(Agent).where(Agent.name == agent_name)
        )
        agent = agent_result.scalar_one_or_none()
        if not agent:
            logger.error("_dispatch_durable_run: agent '%s' not found", agent_name)
            return

        dep_result = await session.execute(
            select(Deployment).where(
                Deployment.agent_name == agent_name,
                Deployment.status == "running",
            ).order_by(Deployment.created_at.desc()).limit(1)
        )
        deployment = dep_result.scalar_one_or_none()

        runner_url = os.getenv("DECLARATIVE_RUNNER_URL", "http://declarative-runner.agentshield-platform.svc.cluster.local:8080")
        callback_url = os.getenv("REGISTRY_API_INTERNAL_URL", "http://registry-api.agentshield-platform.svc.cluster.local:8000")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{runner_url}/run",
                    json={
                        "agent_name": agent_name,
                        "run_id": run_id,
                        "input_payload": input_payload or {},
                        "callback_url": f"{callback_url}/api/v1/playground/runs/{run_id}/step-update",
                    },
                )
                if resp.status_code not in (200, 201, 202):
                    logger.warning(
                        "_dispatch_durable_run: runner returned %d: %s",
                        resp.status_code, resp.text[:200],
                    )
        except Exception as exc:
            logger.error("_dispatch_durable_run: failed to dispatch run %s: %s", run_id, exc)
            async with AsyncSessionLocal() as err_session:
                result = await err_session.execute(
                    select(PlaygroundRun).where(PlaygroundRun.id == uuid.UUID(run_id))
                )
                run = result.scalar_one_or_none()
                if run:
                    run.status = "failed"
                    run.completed_at = datetime.now(tz=timezone.utc)
                    await err_session.commit()


@router.post(
    "/runs/{run_id}/step-update",
    status_code=status.HTTP_200_OK,
    summary="Callback for durable run step updates",
)
async def step_update_callback(
    run_id: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Receives step update callbacks from the declarative-runner."""
    from models import RunStep

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    step_number = body.get("step_number", 1)
    step_name = body.get("step_name", f"step-{step_number}")
    step_status = body.get("status", "running")

    from sqlalchemy import and_
    existing = await db.execute(
        select(RunStep).where(
            and_(RunStep.run_id == parsed_id, RunStep.step_number == step_number)
        )
    )
    step = existing.scalar_one_or_none()

    now = datetime.now(tz=timezone.utc)
    if step:
        step.status = step_status
        step.name = step_name
        if step_status in ("completed", "failed"):
            step.completed_at = now
        if body.get("output"):
            step.output = body["output"]
        if body.get("error_message"):
            step.error_message = body["error_message"]
    else:
        step = RunStep(
            run_id=parsed_id,
            step_number=step_number,
            name=step_name,
            status=step_status,
            started_at=now if step_status == "running" else None,
            completed_at=now if step_status in ("completed", "failed") else None,
            output=body.get("output"),
            error_message=body.get("error_message"),
        )
        db.add(step)

    if step_status == "awaiting_approval":
        run.status = "blocked"
    elif body.get("run_completed"):
        run.status = step_status
        run.completed_at = now
        if body.get("output_text"):
            run.output_text = body["output_text"]

    await db.commit()

    # Push SSE event into Redis pubsub for the stream endpoint
    _publish_step_event(run_id, {
        "event": "step_update",
        "step_number": step_number,
        "step_name": step_name,
        "status": step_status,
        "output": body.get("output"),
        "approval_id": body.get("approval_id"),
    })

    return {"status": "ok"}


def _publish_step_event(run_id: str, event: dict[str, Any]) -> None:
    """Best-effort publish of step events. Falls back to in-memory store."""
    _STEP_EVENTS.setdefault(run_id, []).append(event)


# In-memory step event store (sufficient for single-pod playground; Redis upgrade in Phase 5)
_STEP_EVENTS: dict[str, list[dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# GET /api/v1/playground/runs/{run_id}/steps
# ---------------------------------------------------------------------------
@router.get(
    "/runs/{run_id}/steps",
    summary="List steps for a durable playground run",
)
async def list_run_steps(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return all steps for a durable playground run."""
    from models import RunStep

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    result = await db.execute(
        select(RunStep).where(RunStep.run_id == parsed_id).order_by(RunStep.step_number)
    )
    steps = result.scalars().all()
    return [
        {
            "id": str(s.id),
            "run_id": str(s.run_id),
            "step_number": s.step_number,
            "name": s.name,
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "output": s.output,
            "error_message": s.error_message,
            "approval_id": str(s.approval_id) if s.approval_id else None,
        }
        for s in steps
    ]


# ---------------------------------------------------------------------------
# GET /api/v1/playground/runs/{run_id}/stream
# ---------------------------------------------------------------------------
_AGENT_STREAM_TIMEOUT = float(os.getenv("AGENT_STREAM_TIMEOUT", "120"))


def _agent_svc_url(deployment: Deployment) -> str:
    """Derive the in-cluster Kubernetes service URL for a deployed agent pod."""
    return (
        f"http://{deployment.k8s_deployment_name}"
        f".{deployment.k8s_namespace}.svc.cluster.local:8080"
    )


async def _real_agent_stream(
    agent_svc_url: str,
    input_message: str,
    thread_id: str,
) -> AsyncIterator[str]:
    """Proxy SSE from the agent pod, converting named events to unnamed events.

    The agent pod (sdk/agentshield_sdk/streaming.py) emits named SSE:
        event: text_delta
        id: <uuid>
        data: {"content": "...", "tool": "...", "risk": "..."}

    ChatPane.tsx uses EventSource.onmessage which only fires for unnamed events,
    and expects the event type embedded in the JSON data field.  We convert each
    named event to an unnamed event with the type in the payload, e.g.:
        data: {"event": "text_delta", "content": "..."}

    Also remaps pod field names to frontend expectations:
        tool  -> tool_name
        risk  -> risk_level
    """
    body = {"message": input_message, "thread_id": thread_id}

    try:
        async with httpx.AsyncClient(timeout=_AGENT_STREAM_TIMEOUT) as aclient:
            async with aclient.stream(
                "POST",
                f"{agent_svc_url}/chat/stream",
                json=body,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if response.status_code != 200:
                    err_body = await response.aread()
                    yield f"data: {json.dumps({'event': 'error', 'message': f'Agent pod returned {response.status_code}: {err_body.decode()[:200]}'})}\n\n"
                    yield f"data: {json.dumps({'event': 'done'})}\n\n"
                    return

                current_event: str | None = None
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        raw_data = line[len("data:"):].strip()
                        try:
                            payload = json.loads(raw_data)
                        except json.JSONDecodeError:
                            payload = {"raw": raw_data}

                        # Remap pod field names to what ChatPane expects
                        if "tool" in payload and "tool_name" not in payload:
                            payload["tool_name"] = payload.pop("tool")
                        if "risk" in payload and "risk_level" not in payload:
                            payload["risk_level"] = payload.pop("risk")

                        # Embed the named event type into the JSON body so that
                        # an unnamed SSE (onmessage) carries the event type.
                        if current_event:
                            payload["event"] = current_event
                        elif "event" not in payload:
                            payload["event"] = "message"

                        yield f"data: {json.dumps(payload)}\n\n"
                        current_event = None
                    elif line == "":
                        current_event = None

    except httpx.ConnectError as exc:
        logger.warning("_real_agent_stream: connect error to %s: %s", agent_svc_url, exc)
        yield f"data: {json.dumps({'event': 'error', 'message': 'Could not connect to agent pod. Is the agent deployed and running?'})}\n\n"
        yield f"data: {json.dumps({'event': 'done'})}\n\n"
    except httpx.TimeoutException as exc:
        logger.warning("_real_agent_stream: timeout from %s: %s", agent_svc_url, exc)
        yield f"data: {json.dumps({'event': 'error', 'message': 'Agent stream timed out.'})}\n\n"
        yield f"data: {json.dumps({'event': 'done'})}\n\n"
    except Exception as exc:
        logger.error("_real_agent_stream: unexpected error from %s: %s", agent_svc_url, exc)
        yield f"data: {json.dumps({'event': 'error', 'message': f'Stream error: {exc}'})}\n\n"
        yield f"data: {json.dumps({'event': 'done'})}\n\n"


async def _complete_run(run_id_str: str, output_text: str = "") -> None:
    """Background task: mark run as completed after stream ends, then fire judge."""
    import asyncio
    from db import AsyncSessionLocal

    agent_name = "unknown"
    input_message = ""
    team = "platform"
    langfuse_trace_id: str | None = None

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
                if output_text:
                    run.output_text = output_text
                agent_name = run.agent_name
                input_message = run.input_message or ""
                langfuse_trace_id = run.langfuse_trace_id
                await session.commit()
                logger.debug("Marked playground run %s as completed", run_id_str)
        except Exception as exc:
            logger.warning("_complete_run: could not update run %s: %s", run_id_str, exc)
            return

    # Update Langfuse trace with completion
    if langfuse_trace_id:
        from tracing import trace_complete_run
        trace_complete_run(
            run_id=langfuse_trace_id,
            status="completed",
            output_text=output_text,
        )

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
                    langfuse_trace_id=langfuse_trace_id,
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
    """SSE stream of playground run output. Proxies to the live agent pod."""
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

    # Resolve the running deployment before the DB session closes.
    # We need k8s_deployment_name and k8s_namespace to build the service URL.
    deploy_result = await db.execute(
        select(Deployment)
        .join(Agent, Deployment.agent_id == Agent.id)
        .where(
            Agent.name == run.agent_name,
            Deployment.status == "running",
        )
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    deployment = deploy_result.scalar_one_or_none()

    agent_svc_url: str | None = None
    if deployment:
        agent_svc_url = _agent_svc_url(deployment)
        logger.info(
            "stream_playground_run: run=%s agent=%s -> %s",
            run_id, run.agent_name, agent_svc_url,
        )
    else:
        logger.warning(
            "stream_playground_run: no running deployment for agent '%s'",
            run.agent_name,
        )

    agent_name = run.agent_name
    execution_shape = run.execution_shape or "reactive"
    input_message = run.input_message or ""
    thread_id = run_id  # use run_id as thread_id for traceability

    async def _stream_reactive():
        if not agent_svc_url:
            no_deploy_msg = f'No running deployment found for agent "{agent_name}". Deploy the agent first.'
            yield "data: " + json.dumps({"event": "error", "message": no_deploy_msg}) + "\n\n"
            yield "data: " + json.dumps({"event": "done"}) + "\n\n"
            background_tasks.add_task(_complete_run, run_id, "")
            return

        output_parts: list[str] = []
        async for chunk in _real_agent_stream(
            agent_svc_url=agent_svc_url,
            input_message=input_message,
            thread_id=thread_id,
        ):
            if chunk.startswith("data: "):
                try:
                    ev = json.loads(chunk[6:].strip())
                    if ev.get("event") == "text_delta":
                        output_parts.append(ev.get("content", ""))
                except Exception:
                    pass
            yield chunk

        background_tasks.add_task(_complete_run, run_id, "".join(output_parts))

    async def _stream_durable():
        """Poll in-memory step events for durable runs."""
        emitted = 0
        max_wait = 600  # 10 minute timeout
        waited = 0.0
        poll_interval = 1.0

        while waited < max_wait:
            events = _STEP_EVENTS.get(run_id, [])
            while emitted < len(events):
                ev = events[emitted]
                yield f"data: {json.dumps(ev)}\n\n"
                emitted += 1
                if ev.get("status") in ("completed", "failed"):
                    from db import AsyncSessionLocal
                    async with AsyncSessionLocal() as check_session:
                        check_result = await check_session.execute(
                            select(PlaygroundRun).where(PlaygroundRun.id == uuid.UUID(run_id))
                        )
                        check_run = check_result.scalar_one_or_none()
                        if check_run and check_run.status in ("completed", "failed"):
                            yield f"data: {json.dumps({'event': 'done'})}\n\n"
                            _STEP_EVENTS.pop(run_id, None)
                            return

            await asyncio.sleep(poll_interval)
            waited += poll_interval

        yield f"data: {json.dumps({'event': 'error', 'message': 'Durable run stream timed out.'})}\n\n"
        yield f"data: {json.dumps({'event': 'done'})}\n\n"

    stream_fn = _stream_durable if execution_shape == "durable" else _stream_reactive

    return StreamingResponse(
        stream_fn(),
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
    lf_public_url = os.getenv("LANGFUSE_PUBLIC_URL", "")
    lf_project_id = os.getenv("LANGFUSE_PROJECT_ID", "")
    lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")

    # Full path avoids Langfuse /trace short-link redirect (loses path prefix behind Gateway)
    if lf_public_url and lf_project_id:
        trace_url = f"{lf_public_url}/project/{lf_project_id}/traces/{run.langfuse_trace_id}"
    else:
        trace_url = None
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
# GET /api/v1/playground/traces/{trace_id}
# ---------------------------------------------------------------------------
@router.get(
    "/traces/{trace_id}",
    summary="Fetch Langfuse trace data by trace ID",
)
async def get_trace_by_id(trace_id: str) -> dict[str, Any]:
    """Return Langfuse trace data directly by trace_id (used by eval result View Trace)."""
    import os
    import urllib.error
    import urllib.request as urlreq

    lf_host = os.getenv("LANGFUSE_HOST", "http://agentshield-langfuse-web:3000")
    lf_public_url = os.getenv("LANGFUSE_PUBLIC_URL", "")
    lf_project_id = os.getenv("LANGFUSE_PROJECT_ID", "")
    lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")

    # Full path avoids Langfuse /trace short-link redirect (loses path prefix behind Gateway)
    if lf_public_url and lf_project_id:
        trace_url = f"{lf_public_url}/project/{lf_project_id}/traces/{trace_id}"
    else:
        trace_url = None
    trace_data: dict[str, Any] = {}

    if lf_pk and lf_sk:
        import base64
        creds = base64.b64encode(f"{lf_pk}:{lf_sk}".encode()).decode()
        try:
            req = urlreq.Request(
                f"{lf_host}/api/public/traces/{trace_id}",
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
        "trace_id": trace_id,
        "trace_url": trace_url,
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
        "expected_output": run.output_text or "",
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


# ---------------------------------------------------------------------------
# POST /api/v1/playground/judge  (eval-mode, synchronous)
# ---------------------------------------------------------------------------
class JudgeRequest(BaseModel):
    input_message: str
    response_text: str
    expected_output: str
    team: str = "platform"


class JudgeResponse(BaseModel):
    score: float
    reason: str


@router.post(
    "/judge",
    response_model=JudgeResponse,
    summary="Score a response against an expected answer (eval-mode LLM judge)",
)
async def judge_eval(body: JudgeRequest) -> JudgeResponse:
    """Synchronous eval-mode judge endpoint.

    Calls the platform LLM (Haiku via Bedrock) with a correctness prompt that
    includes the expected answer. Returns score + reason in ~5s. Used by the
    eval-runner instead of polling the background quality judge.
    """
    from judge import judge_for_eval

    try:
        async with asyncio.timeout(35.0):
            score, reason = await judge_for_eval(
                input_text=body.input_message,
                output_text=body.response_text,
                expected_output=body.expected_output,
                team=body.team,
            )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Judge timed out (35s)")
    except Exception as exc:
        logger.warning("judge endpoint error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Judge error: {exc}")

    return JudgeResponse(score=score, reason=reason)


# ---------------------------------------------------------------------------
# POST /api/v1/playground/test-event
# ---------------------------------------------------------------------------
class TestEventRequest(BaseModel):
    agent_name: str
    payload: dict[str, Any]


@router.post(
    "/test-event",
    status_code=status.HTTP_200_OK,
    summary="Test a webhook trigger with a sample payload",
)
async def test_event(
    body: TestEventRequest,
    background_tasks: BackgroundTasks,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Evaluate an agent's webhook trigger filters against a test payload.
    If matched, create a playground run with trigger_type=webhook."""
    from filter_engine import evaluate_filters
    from models import AgentTrigger

    agent_result = await db.execute(
        select(Agent).where(Agent.name == body.agent_name)
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{body.agent_name}' not found")

    caller = (user or {}).get("sub") or x_user_sub or "dev"

    # Find webhook triggers for this agent
    trigger_result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.agent_id == agent.id,
            AgentTrigger.trigger_type == "webhook",
            AgentTrigger.enabled == True,
        )
    )
    triggers = list(trigger_result.scalars().all())

    if not triggers:
        return {
            "matched": False,
            "reason": "no enabled webhook triggers configured for this agent",
        }

    # Evaluate filters against each trigger; first match wins
    for trigger in triggers:
        conditions = trigger.filter_conditions
        if isinstance(conditions, dict):
            conditions = [conditions]
        result = evaluate_filters(conditions, body.payload)
        if result["matched"]:
            now = datetime.now(tz=timezone.utc)
            run = PlaygroundRun(
                user_id=caller,
                agent_name=body.agent_name,
                context="playground",
                sandbox=True,
                execution_shape=agent.execution_shape or "reactive",
                trigger_type="webhook",
                trigger_payload=body.payload,
                input_message=json.dumps(body.payload),
                status="running",
                started_at=now,
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)

            return {
                "matched": True,
                "reason": result["reason"],
                "trigger_id": str(trigger.id),
                "run_id": str(run.id),
                "stream_url": f"/api/v1/playground/runs/{run.id}/stream",
            }

    # No trigger matched
    last_reason = evaluate_filters(
        triggers[-1].filter_conditions if isinstance(triggers[-1].filter_conditions, list)
        else [triggers[-1].filter_conditions] if triggers[-1].filter_conditions else None,
        body.payload,
    )
    return {
        "matched": False,
        "reason": last_reason.get("reason", "no trigger filter matched"),
    }

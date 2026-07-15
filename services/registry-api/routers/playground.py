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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from models import Agent, Deployment, PlaygroundDataset, PlaygroundRun
from playground_sa import ensure_playground_sa
from schemas import (
    EvalScoreRequest,
    EvalScoreResponse,
    PlaygroundRunCreate,
    PlaygroundRunResponse,
)

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

    # Requester provenance for the HITL panel (WHO): username from the JWT, team
    # from user_team_assignments. Skipped for service identities (eval-runner).
    requested_by_username = (user or {}).get("preferred_username")
    requested_by_team = None
    if caller and caller not in _SERVICE_IDENTITIES:
        _tr = await db.execute(
            text("SELECT team_name FROM user_team_assignments WHERE user_sub = :sub LIMIT 1"),
            {"sub": caller},
        )
        _row = _tr.first()
        if _row:
            requested_by_team = _row[0]

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
        requested_by_username=requested_by_username,
        requested_by_team=requested_by_team,
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
    """Dispatch a durable playground run to the declarative-runner /run endpoint.

    Thin wrapper over the shared ``durable_dispatch.dispatch_durable_run`` — the ONE
    place the /run POST lives (parity with the production internal path; the
    2026-07-11 HITL retro root cause was exactly a sandbox/production copy). On a
    dispatch failure, mark THIS PlaygroundRun failed (fail-closed)."""
    from db import AsyncSessionLocal
    from durable_dispatch import dispatch_durable_run, registry_internal_base
    from workflow_orchestrator import _resolve_agent_environment, _team_namespace
    from models import Agent

    # Target the agent's OWN deployed pod (`{agent}-{env}` in its team namespace) —
    # the same resolution the workflow path uses. The default shared `declarative-runner`
    # service is not deployed (agents run as their own pods), so dispatching there
    # DNS-fails and the run dies before its first step (single-agent durable HITL never
    # worked for a deployed agent). Fall back to the shared default only if we can't
    # resolve the agent's team.
    runner_url = None
    async with AsyncSessionLocal() as session:
        team = (await session.execute(
            select(Agent.team).where(Agent.name == agent_name)
        )).scalar_one_or_none()
    if team:
        env = await _resolve_agent_environment(agent_name)
        runner_url = f"http://{agent_name}-{env}.{_team_namespace(team)}.svc.cluster.local:8080"

    callback = f"{registry_internal_base()}/api/v1/playground/runs/{run_id}/step-update"
    ok, err = await dispatch_durable_run(
        run_id=run_id,
        agent_name=agent_name,
        input_payload=input_payload,
        callback_url=callback,
        runner_url=runner_url,
    )
    if not ok:
        logger.warning("_dispatch_durable_run: marking playground run %s failed: %s", run_id, err)
        async with AsyncSessionLocal() as session:
            run = (await session.execute(
                select(PlaygroundRun).where(PlaygroundRun.id == uuid.UUID(run_id))
            )).scalar_one_or_none()
            if run:
                run.status = "failed"
                run.completed_at = datetime.now(tz=timezone.utc)
                await session.commit()


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
    # The durable SDK emits the agent's ANSWER as `output_text` on the completing
    # step (run.py: StepUpdate(..., output_text=final_text)), while per-node/tool
    # steps use `output`. Capture BOTH into the step so the StepTracker step detail
    # actually shows the result — previously only `output` was read, so a durable
    # agent's step was blank (the answer only reached run.output_text).
    step_out = body.get("output")
    if step_out is None:
        step_out = body.get("output_text")
    # Persist the HITL approval_id emitted on an `awaiting_approval` boundary
    # (durable harness StepUpdate.approval_id). Without this it never reached the
    # run_steps row, so the durable eval could neither self-approve the gate nor
    # project the parked step for `expect_approval` scoring (Eval v2 E-1). Only
    # SET it (never clear) so a later resume that overwrites the step's live
    # status back to completed keeps the durable evidence the gate fired.
    appr_raw = body.get("approval_id")
    appr_uuid: Optional[uuid.UUID] = None
    if appr_raw:
        try:
            appr_uuid = uuid.UUID(str(appr_raw))
        except (ValueError, TypeError):
            appr_uuid = None
    if step:
        step.status = step_status
        step.name = step_name
        if step_status in ("completed", "failed"):
            step.completed_at = now
        if step_out is not None:
            step.output = step_out
        if appr_uuid is not None:
            step.approval_id = appr_uuid
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
            output=step_out,
            approval_id=appr_uuid,
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
    """No-op. Durable step streaming now reads the shared `run_steps` table
    (see `_stream_durable`), not this per-replica in-memory buffer — the buffer
    broke multi-replica ('Connection lost'). Kept as a call-site stub so the
    step-update callback contract is unchanged; the step is persisted to RunStep
    by the caller."""
    return


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
    trace_id: str | None = None,
    user_id: str = "",
    user_team: str = "",
    requested_by: str | None = None,
    requested_by_team: str | None = None,
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
            req_headers = {"Accept": "text/event-stream"}
            if trace_id:
                req_headers["x-agentshield-trace-id"] = trace_id
            if user_id:
                req_headers["x-user-sub"] = user_id
            if user_team:
                req_headers["x-agent-team"] = user_team
            # Batch/dataset eval runs non-interactively — no human to approve HITL.
            # Only the internal eval-runner service identity may auto-approve; the
            # SDK re-checks this identity as defense-in-depth. Interactive chats
            # (real user subs) never take this branch.
            if user_id in _SERVICE_IDENTITIES:
                req_headers["x-agentshield-auto-approve"] = "true"
            async with aclient.stream(
                "POST",
                f"{agent_svc_url}/chat/stream",
                json=body,
                headers=req_headers,
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

                        # Enrich the approval with the requester (WHO) — the pod
                        # doesn't know it; the registry captured it on the run.
                        if payload.get("event") == "approval_requested":
                            if requested_by:
                                payload["requested_by"] = requested_by
                            if requested_by_team:
                                payload["requested_by_team"] = requested_by_team

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

    # Resolve user team for OPA identity propagation
    caller_id = run.user_id or ""
    caller_team = ""
    if caller_id:
        team_result = await db.execute(
            text("SELECT team_name FROM user_team_assignments WHERE user_sub = :sub LIMIT 1"),
            {"sub": caller_id},
        )
        row = team_result.first()
        if row:
            caller_team = row[0]

    # Requester provenance (WHO) captured on the run at create time — surfaced on
    # the approval so the HitlPanel shows who asked (falls back to team only).
    requested_by = run.requested_by_username
    requested_by_team = run.requested_by_team or caller_team

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
            trace_id=run_id,
            user_id=caller_id,
            user_team=caller_team,
            requested_by=requested_by,
            requested_by_team=requested_by_team,
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
        """Stream durable-run steps from the SHARED run_steps table so the stream
        works regardless of which registry-api replica serves it.

        The step-update callback already persists every step to `RunStep`; it also
        appended to the in-memory `_STEP_EVENTS` dict, but that buffer is PER-REPLICA
        — with >1 registry-api replica the pod's callback and this SSE request are
        load-balanced independently, so this stream saw an empty buffer, no data
        flowed, and the gateway dropped the idle connection → the client showed
        'Connection lost'. Polling the DB (shared) fixes it. Emits a step_update
        whenever a step is new or its status changed (the client dedups by
        step_number), and 'done' once the run row is terminal."""
        from db import AsyncSessionLocal
        from models import RunStep, Approval

        last: dict[int, str] = {}   # step_number -> last emitted status
        max_wait = 600  # 10 minute timeout
        waited = 0.0
        poll_interval = 1.0

        while waited < max_wait:
            async with AsyncSessionLocal() as sess:
                rows = (await sess.execute(
                    select(RunStep).where(RunStep.run_id == parsed_id).order_by(RunStep.step_number)
                )).scalars().all()
                snap = [(r.step_number, r.name, r.status, r.output, r.error_message) for r in rows]
                prun = (await sess.execute(
                    select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
                )).scalar_one_or_none()
                run_status = prun.status if prun else None
                appr_id = None
                if any(s[2] == "awaiting_approval" for s in snap):
                    a = (await sess.execute(
                        select(Approval).where(Approval.thread_id == run_id, Approval.status == "pending")
                    )).scalars().first()
                    appr_id = str(a.id) if a else None

            for sn, name, st, out, err in snap:
                if last.get(sn) != st:   # new step OR status changed since last emit
                    yield "data: " + json.dumps({
                        "event": "step_update",
                        "step_number": sn,
                        "step_name": name,
                        "status": st,
                        "output": out,
                        "approval_id": appr_id if st == "awaiting_approval" else None,
                    }) + "\n\n"
                    last[sn] = st

            if run_status in ("completed", "failed"):
                yield f"data: {json.dumps({'event': 'done'})}\n\n"
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
    """Return the provider-neutral trace (spans/scores) for a completed run."""
    from observability_backend import get_observability_backend

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
            "message": "No trace associated with this run yet",
        }

    obs = get_observability_backend()
    trace = obs.get_trace(run.langfuse_trace_id)
    return {
        "run_id": run_id,
        "trace_id": run.langfuse_trace_id,
        "trace_url": obs.build_trace_url(run.langfuse_trace_id),
        "status": run.status,
        "trace": trace.model_dump() if trace else None,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/playground/traces/{trace_id}
# ---------------------------------------------------------------------------
@router.get(
    "/traces/{trace_id}",
    summary="Fetch Langfuse trace data by trace ID",
)
async def get_trace_by_id(trace_id: str) -> dict[str, Any]:
    """Return the provider-neutral trace by trace_id (used by eval result View Trace)."""
    from observability_backend import get_observability_backend

    obs = get_observability_backend()
    trace = obs.get_trace(trace_id)
    return {
        "trace_id": trace_id,
        "trace_url": obs.build_trace_url(trace_id),
        "trace": trace.model_dump() if trace else None,
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

    # Persist locally (source of truth for the dashboard feedback-ratio panel);
    # the Langfuse score push below is best-effort and only for the trace view.
    run.user_feedback = body.score
    await db.commit()

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
# POST /api/v1/playground/eval/score  (Eval v2 E-0 — the ONE scoring door)
# ---------------------------------------------------------------------------
@router.post(
    "/eval/score",
    response_model=EvalScoreResponse,
    summary="Score an eval item by mode (the single scoring door)",
)
async def eval_score(body: EvalScoreRequest) -> EvalScoreResponse:
    """Single scoring door for batch eval — dispatches by ``mode``.

    E-0 wires the **reactive** branch: it scores ``response`` against the
    item's ``expected_output`` (reference-based) via ``score_response`` and
    reduces to a composite via ``score_composite``. For reactive that is one
    ``response`` dimension, so ``composite == dimension_scores["response"]`` —
    numerically identical to the legacy ``judge_for_eval`` path.

    E-1 wires the **durable** branch: ``response`` (LLM) + ``trajectory`` +
    ``tool_call`` (both deterministic, over the projected ``actual_trajectory``)
    reduced by ``weighted_mean`` with durable default weights 0.4/0.4/0.2
    (overridable per run via ``dimension_weights``). A reference-free durable
    item (no ``expected_trajectory``) degrades to ``{response}`` only. Other
    modes (scheduled/webhook/workflow) return 501 until their slices land — one
    scoring door, no parallel scoring path.
    """
    if body.mode not in ("reactive", "durable", "workflow"):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                f"eval scoring for mode '{body.mode}' is not implemented yet "
                "(E-1+); only 'reactive', 'durable' and 'workflow' are wired"
            ),
        )

    from judge import (
        score_composite,
        score_member_path,
        score_response,
        score_tool_calls,
        score_trajectory,
        weighted_mean,
    )

    item = body.item or {}
    input_text = body.input if body.input is not None else (item.get("input_message") or item.get("input") or "")
    response_text = body.response or ""
    expected_output = item.get("expected_output")
    rubric = item.get("rubric")
    team = item.get("team") or "platform"

    try:
        async with asyncio.timeout(35.0):
            score, reason = await score_response(
                input_text=input_text,
                output_text=response_text,
                expected_output=expected_output,
                rubric=rubric,
                team=team,
            )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Judge timed out (35s)")
    except Exception as exc:
        logger.warning("eval/score error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Judge error: {exc}")

    # --- Reactive: single response dimension, composite == response (unchanged) ---
    if body.mode == "reactive":
        dimension_scores = {"response": score}
        composite = score_composite(dimension_scores, weights=None)
        return EvalScoreResponse(
            composite=composite,
            dimension_scores=dimension_scores,
            detail={"response_reason": reason},
        )

    # --- Workflow: member_path (run tree) + response + optional per-member rubric ---
    if body.mode == "workflow":
        expected_member_path = item.get("expected_member_path") or []
        actual_member_path = body.member_path or []
        # Default ordered so a correct answer via the WRONG route scores <1.0
        # (the reason E-5 exists); an item may override via member_path_match_mode.
        mp_match_mode = item.get("member_path_match_mode", "ordered")
        mp_score, member_diff = score_member_path(
            actual_member_path, expected_member_path, mp_match_mode,
        )

        # `score` (computed above) is the response dimension vs expected_output.
        dimension_scores = {"member_path": mp_score, "response": score}

        # Per-member rubric zoom: an LLM score_response over each requested
        # member's projected run_steps (reference-free, rubric-scored). A member
        # with no steps (reactive child) degrades to an empty-behavior score —
        # surfaced in detail, never silently passed.
        per_member = item.get("per_member") or {}
        per_member_steps = body.per_member_steps or {}
        per_member_detail: list[dict[str, Any]] = []
        per_member_scores: list[float] = []
        for member, spec in per_member.items():
            rubric_text = spec.get("rubric") if isinstance(spec, dict) else None
            member_steps = per_member_steps.get(member) or []
            steps_text = json.dumps(member_steps)[:1600] if member_steps else ""
            try:
                async with asyncio.timeout(35.0):
                    pm_score, pm_reason = await score_response(
                        input_text=f"Member '{member}' execution steps",
                        output_text=steps_text,
                        expected_output=None,
                        rubric=rubric_text,
                        team=team,
                    )
            except Exception as exc:
                logger.warning("per-member score error member=%s: %s", member, exc)
                pm_score, pm_reason = 0.0, f"per-member score error: {exc}"
            per_member_scores.append(pm_score)
            per_member_detail.append({
                "member": member,
                "score": pm_score,
                "reason": pm_reason,
                "rubric": rubric_text,
                "had_steps": bool(member_steps),
            })
        if per_member_scores:
            dimension_scores["per_member"] = sum(per_member_scores) / len(per_member_scores)

        # Workflow composite weights: default 0.4/0.4/0.2, overridable per run.
        # The reducer sums only PRESENT dimensions, so a no-per_member item
        # collapses to member_path + response (No-Bandaid: one reducer).
        weights = body.dimension_weights or {"member_path": 0.4, "response": 0.4, "per_member": 0.2}
        composite = weighted_mean(dimension_scores, weights)
        return EvalScoreResponse(
            composite=composite,
            dimension_scores=dimension_scores,
            detail={
                "response_reason": reason,
                "expected_member_path": expected_member_path,
                "actual_member_path": actual_member_path,
                "member_diff": member_diff,
                "per_member": per_member_detail,
            },
        )

    # --- Durable: response + (trajectory + tool_call) over the projected run_steps ---
    dimension_scores = {"response": score}
    actual_trajectory = body.actual_trajectory or []
    expected_trajectory = item.get("expected_trajectory") or None
    expected_steps = (expected_trajectory or {}).get("steps") or []
    detail: dict[str, Any] = {
        "response_reason": reason,
        "expected_trajectory": expected_trajectory,
        "actual_trajectory": actual_trajectory,
        "tool_diffs": [],
        "approvals": [],
    }

    if expected_trajectory and expected_steps:
        match_mode = expected_trajectory.get("match_mode", "superset")
        traj_score, traj_detail = score_trajectory(
            actual_trajectory, expected_trajectory, match_mode,
        )
        tool_score, tool_detail = score_tool_calls(actual_trajectory, expected_steps)
        dimension_scores["trajectory"] = traj_score
        dimension_scores["tool_call"] = tool_score
        detail["trajectory_detail"] = traj_detail
        detail["tool_diffs"] = tool_detail.get("tool_diffs", [])
        detail["approvals"] = tool_detail.get("approvals", [])
    # else: reference-free durable → dimension_scores == {"response"} (graceful degrade)

    # Durable composite weights: default 0.4/0.4/0.2, overridable per run. The
    # reducer sums only the weights of PRESENT dimensions, so a degraded
    # {response}-only item collapses to the response score (No-Bandaid: one reducer).
    weights = body.dimension_weights or {"response": 0.4, "trajectory": 0.4, "tool_call": 0.2}
    composite = weighted_mean(dimension_scores, weights)
    return EvalScoreResponse(
        composite=composite,
        dimension_scores=dimension_scores,
        detail=detail,
    )


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


# ---------------------------------------------------------------------------
# POST /api/v1/playground/approvals/{approval_id}/decide
# ---------------------------------------------------------------------------
class PlaygroundApprovalDecision(BaseModel):
    decision: str  # "approved" | "denied"


@router.post(
    "/approvals/{approval_id}/decide",
    summary="Approve or deny a playground HITL request",
)
async def decide_playground_approval(
    approval_id: str,
    body: PlaygroundApprovalDecision,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Lightweight decide endpoint for playground approvals.

    Updates the approval record in the DB.  Does NOT resume the agent here —
    the caller opens ``/runs/{run_id}/resume-stream`` afterwards, which proxies
    the streaming resume to the agent pod.
    """
    from datetime import timedelta
    from models import Approval

    try:
        parsed_id = uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid approval_id format")

    result = await db.execute(
        select(Approval).where(Approval.id == parsed_id)
    )
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    if approval.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Approval is already '{approval.status}'",
        )

    now = datetime.now(tz=timezone.utc)
    # DB constraint uses "rejected" not "denied"; normalize for callers
    db_status = "rejected" if body.decision == "denied" else body.decision
    approval.status = db_status
    approval.decision_at = now
    approval.reviewer_id = x_user_sub or "playground-user"
    approval.version = approval.version + 1
    await db.commit()

    logger.info(
        "decide_playground_approval: id=%s decision=%s thread_id=%s",
        approval_id, body.decision, approval.thread_id,
    )

    return {
        "approval_id": approval_id,
        "status": body.decision,
        "thread_id": approval.thread_id,
        "agent_name": approval.agent_name,
        "team": approval.team,
    }


# ---------------------------------------------------------------------------
# GET /api/v1/playground/runs/{run_id}/resume-stream
# ---------------------------------------------------------------------------
@router.get(
    "/runs/{run_id}/resume-stream",
    summary="Stream the resumed agent output after HITL approval (SSE)",
    response_class=StreamingResponse,
)
async def resume_stream_playground_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """After a playground approval, this proxies SSE from the agent pod's
    streaming resume endpoint.  The latest approval decision for this
    thread is read from the DB and forwarded to the agent."""
    from models import Approval

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Playground run not found")

    thread_id = run_id

    # Find the most recent decided approval for this thread.
    approval_result = await db.execute(
        select(Approval)
        .where(
            Approval.thread_id == thread_id,
            Approval.status.in_(["approved", "rejected", "denied"]),
        )
        .order_by(Approval.decision_at.desc())
        .limit(1)
    )
    approval = approval_result.scalar_one_or_none()
    if not approval:
        raise HTTPException(
            status_code=404,
            detail="No decided approval found for this run",
        )

    # Resolve agent pod URL.
    deploy_result = await db.execute(
        select(Deployment)
        .join(Agent, Deployment.agent_id == Agent.id)
        .where(Agent.name == run.agent_name, Deployment.status == "running")
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    deployment = deploy_result.scalar_one_or_none()
    if not deployment:
        async def _no_deploy():
            yield f"data: {json.dumps({'event': 'error', 'message': 'No running deployment'})}\n\n"
            yield f"data: {json.dumps({'event': 'done'})}\n\n"
        return StreamingResponse(_no_deploy(), media_type="text/event-stream")

    agent_svc_url = _agent_svc_url(deployment)
    decision_str = approval.status  # "approved" or "denied"
    reviewer = approval.reviewer_id or "playground-user"

    async def _proxy_resume() -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(timeout=_AGENT_STREAM_TIMEOUT) as aclient:
                async with aclient.stream(
                    "POST",
                    f"{agent_svc_url}/resume/{thread_id}/stream",
                    json={
                        "decision": decision_str,
                        "reviewer_id": reviewer,
                        "reason": approval.reviewer_notes,
                    },
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    if response.status_code != 200:
                        err = await response.aread()
                        yield f"data: {json.dumps({'event': 'error', 'message': f'Agent pod returned {response.status_code}: {err.decode()[:200]}'})}\n\n"
                        yield f"data: {json.dumps({'event': 'done'})}\n\n"
                        return

                    output_parts: list[str] = []
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
                            if "tool" in payload and "tool_name" not in payload:
                                payload["tool_name"] = payload.pop("tool")
                            if "risk" in payload and "risk_level" not in payload:
                                payload["risk_level"] = payload.pop("risk")
                            if current_event:
                                payload["event"] = current_event
                                if current_event == "text_delta":
                                    output_parts.append(payload.get("content", ""))
                            elif "event" not in payload:
                                payload["event"] = "message"
                            yield f"data: {json.dumps(payload)}\n\n"
                            current_event = None
                        elif line == "":
                            current_event = None

                    background_tasks.add_task(
                        _complete_run, run_id, "".join(output_parts),
                    )

        except httpx.ConnectError as exc:
            logger.warning("resume_stream: connect error: %s", exc)
            yield f"data: {json.dumps({'event': 'error', 'message': 'Could not connect to agent pod'})}\n\n"
            yield f"data: {json.dumps({'event': 'done'})}\n\n"
        except Exception as exc:
            logger.error("resume_stream: error: %s", exc)
            yield f"data: {json.dumps({'event': 'error', 'message': f'Stream error: {exc}'})}\n\n"
            yield f"data: {json.dumps({'event': 'done'})}\n\n"

    return StreamingResponse(_proxy_resume(), media_type="text/event-stream")

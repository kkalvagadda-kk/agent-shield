"""
FastAPI server for the declarative workflow runner.

Satisfies the agent-contract (docs/plan/contracts/agent-contract.yaml):
    GET  /health             — liveness probe
    GET  /ready              — readiness probe (checks Safety Orchestrator + OPA)
    GET  /metrics            — Prometheus text format
    POST /chat               — sync invoke via WorkflowExecutor
    POST /chat/stream        — SSE stream via WorkflowExecutor
    POST /resume/{thread_id} — resume a HITL-paused graph thread
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (  # type: ignore[import]
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel
from starlette.responses import StreamingResponse  # type: ignore[import]

import config as cfg
from agentshield_sdk.safety_client import SafetyBlockedError  # type: ignore[import]

logger = logging.getLogger(__name__)

# Emit INFO logs to the pod's stdout. Without this the root logger defaults to
# WARNING, so the SDK's governance/HITL INFO lines (e.g. "HITL approval record
# created …") never reach the pod log — only failures did, which made HITL issues
# hard to diagnose. Governance flow should be visible in the pod log by default.
logging.basicConfig(level=logging.INFO)
logging.getLogger("agentshield_sdk").setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

REQUEST_COUNTER = Counter(
    "agentshield_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "agentshield_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint"],
)
SAFETY_BLOCKS = Counter(
    "agentshield_safety_blocks_total",
    "Requests blocked by the safety scanner",
)

# ---------------------------------------------------------------------------
# Application lifespan — build WorkflowExecutor with proper checkpointer
# ---------------------------------------------------------------------------

workflow_executor: Any = None  # set during lifespan startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    global workflow_executor

    # Enable OpenTelemetry LLM/tool span capture (OpenInference instruments
    # langchain/langgraph globally; exports OTLP to the configured backend —
    # Langfuse today). No-ops if unconfigured. Must run before any langchain
    # object is constructed so the instrumentation hooks are in place.
    try:
        from agentshield_sdk.otel import setup_otel
        setup_otel()
    except Exception as exc:  # never let tracing setup break startup
        logger.warning("OTEL setup skipped: %s", exc)

    if cfg.COMPOSITE_WORKFLOW_ID:
        import base64 as _b64
        logger.info(
            "Composite workflow orchestrator mode — COMPOSITE_WORKFLOW_ID=%s",
            cfg.COMPOSITE_WORKFLOW_ID,
        )
        wf_config = {}
        if cfg.WORKFLOW_CONFIG:
            try:
                wf_config = json.loads(_b64.b64decode(cfg.WORKFLOW_CONFIG))
            except Exception:
                logger.warning("Failed to decode WORKFLOW_CONFIG, using empty config")
        app.state.workflow_config = wf_config
        yield
        logger.info("Workflow orchestrator shutting down")
        return

    from workflow_executor import WorkflowExecutor  # type: ignore[import]

    workflow_executor = WorkflowExecutor()

    if not cfg.WORKFLOW_JSON:
        await workflow_executor.setup_simple_agent_mode()

    await workflow_executor.setup()
    logger.info("WorkflowExecutor ready — declarative runner is up")

    import asyncio
    asyncio.create_task(_resume_interrupted_runs())

    yield
    logger.info("WorkflowExecutor shutting down")


async def _resume_interrupted_runs() -> None:
    """On startup, re-enter runs stuck in 'running' from the LangGraph PostgresSaver
    checkpoint — the SINGLE checkpoint-of-record (B3). resume_durable(decision=None)
    continues the graph from where the pod crashed (node boundary), emitting steps via
    the same harness the live path uses. A run with no checkpoint state is marked failed
    (lost state) rather than re-run from scratch (which would double-execute)."""
    from checkpoint import list_interrupted_runs

    from agentshield_sdk.durable import Bookmark, StepEmitter, resume_durable  # type: ignore[import]
    from agentshield_sdk.otel import otel_run_context  # type: ignore[import]

    try:
        interrupted = await list_interrupted_runs(cfg.AGENT_NAME)
    except Exception as exc:
        logger.warning("_resume_interrupted_runs: list failed: %s", exc)
        return

    for run_id in interrupted:
        config = {"configurable": {"thread_id": run_id}}
        try:
            snapshot = workflow_executor.graph.get_state(config)
            has_state = bool(getattr(snapshot, "next", None)) or bool(getattr(snapshot, "values", None))
        except Exception:
            has_state = False

        if not has_state:
            logger.info("Run %s has no checkpoint state — marking failed (lost state)", run_id)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.patch(
                        f"{cfg.REGISTRY_API_URL}/api/v1/agent-runs/{run_id}",
                        json={"status": "failed", "error_message": "Pod restarted without checkpoint"},
                    )
            except Exception:
                pass
            continue

        logger.info("Resuming interrupted run %s from PostgresSaver checkpoint", run_id)
        callback = f"{cfg.REGISTRY_API_URL}/api/v1/internal/runs/{run_id}/step-update"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                emitter = StepEmitter(callback, client, bookmark=Bookmark(run_id))
                with otel_run_context(run_id):
                    await resume_durable(
                        workflow_executor.graph, thread_id=run_id, decision=None,
                        callback_url=callback, emitter=emitter,
                    )
        except Exception as exc:
            logger.warning("crash-resume failed for run %s: %s", run_id, exc)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AgentShield Declarative Runner",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / response models (mirrors agentshield_sdk.server)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    # Optional: a daemon/scheduled run may dispatch with no user input at all. The
    # executor substitutes a kickoff (daemon_kickoff_if_empty) so an empty message is
    # valid rather than a 422 — user input is not required for triggered runs.
    message: str = ""
    thread_id: str | None = None          # LangGraph checkpoint key
    conversation_id: str | None = None    # transcript key; defaults to thread_id
    scope: str = "agent"                   # agent | workflow_run
    workflow_run_id: str | None = None
    metadata: dict | None = None


class ResumeRequest(BaseModel):
    decision: str  # "approved" | "rejected"
    reviewer_id: str | None = None
    reason: str | None = None
    run_id: str | None = None        # set for a durable /run resume (WS-1 T4)
    callback_url: str | None = None  # durable resume re-drives the harness, posting steps here
    # Eval v2 E-2: the mode the run STARTED in, read back off the persisted
    # PlaygroundRun by the registry (routers/playground.py resume-stream,
    # routers/approvals.py console decide). A resume re-drives the graph and
    # re-crosses the tool delivery edge, so it must be re-set on the ContextVar —
    # otherwise a parked record-mode eval would deliver its post-approval tool calls
    # for real. Defaults to 'live': a resume that says nothing delivers normally.
    eval_mode: str = "live"


class WorkflowRunRequest(BaseModel):
    parent_run_id: str
    members: list[dict] = []          # each: {agent_name, team, position}
    input_payload: dict | None = None


# ---------------------------------------------------------------------------
# Middleware — request timing
# ---------------------------------------------------------------------------

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    endpoint = request.url.path
    REQUEST_COUNTER.labels(
        method=request.method, endpoint=endpoint, status=response.status_code
    ).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Liveness probe — returns 200 if the process is alive."""
    return {
        "status": "ok",
        "agent_name": cfg.AGENT_NAME,
        "version": "0.1.0",
    }


@app.post("/workflow-run")
async def workflow_run(req: WorkflowRunRequest, request: Request):
    """Composite-workflow orchestration entrypoint (Decision 22).

    Active when this pod is deployed as a workflow orchestrator
    (COMPOSITE_WORKFLOW_ID set by deploy-controller). On single-agent
    deployments this returns 404."""
    import asyncio

    from orchestrator import WorkflowOrchestrator

    if not cfg.COMPOSITE_WORKFLOW_ID:
        raise HTTPException(status_code=404, detail="This pod is not a workflow orchestrator.")

    wf_config = getattr(request.app.state, "workflow_config", {})
    members = req.members or wf_config.get("members", [])
    team = members[0].get("team", "") if members else ""

    orch = WorkflowOrchestrator(
        cfg.COMPOSITE_WORKFLOW_ID, req.parent_run_id, cfg.REGISTRY_API_URL, team
    )
    asyncio.create_task(orch.run_sequential(members, req.input_payload or {}))
    return {"status": "accepted", "parent_run_id": req.parent_run_id}


@app.get("/ready")
async def ready():
    """Readiness probe — checks Safety Orchestrator and OPA sidecar."""
    checks: dict[str, str] = {}

    # Safety Orchestrator
    if cfg.AGENTSHIELD_SAFETY_URL:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{cfg.AGENTSHIELD_SAFETY_URL}/health")
            checks["safety_orchestrator"] = "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            checks["safety_orchestrator"] = "unreachable"
    else:
        checks["safety_orchestrator"] = "mock"

    # OPA sidecar
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{cfg.AGENTSHIELD_OPA_URL}/health")
        checks["opa"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        checks["opa"] = "unreachable" if not cfg.DEV_MODE else "mock"

    # Langfuse
    if cfg.LANGFUSE_PUBLIC_KEY and cfg.LANGFUSE_SECRET_KEY:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(
                    f"{cfg.LANGFUSE_HOST}/api/public/health"
                )
            checks["langfuse"] = "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            checks["langfuse"] = "unreachable"
    else:
        checks["langfuse"] = "disabled"

    # Postgres (via checkpointer)
    if cfg.DIRECT_DATABASE_URL:
        try:
            import asyncpg  # type: ignore[import]
            conn = await asyncpg.connect(
                cfg.DIRECT_DATABASE_URL.replace("+asyncpg", ""), timeout=2
            )
            await conn.close()
            checks["postgres"] = "ok"
        except Exception:
            checks["postgres"] = "unreachable"
    else:
        checks["postgres"] = "memory"

    # Langfuse is observability, not a serving dependency — a trace-backend blip
    # must never make an agent un-servable. Report its status but don't gate on it.
    is_ready = all(
        v in ("ok", "mock", "disabled", "memory")
        for k, v in checks.items()
        if k != "langfuse"
    )
    status = "ready" if is_ready else "not_ready"
    return JSONResponse(
        {"status": status, "checks": checks},
        status_code=200 if is_ready else 503,
    )


@app.get("/metrics")
async def metrics():
    """Prometheus text format metrics."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def _create_agent_run(agent_name: str, user_id: str, team: str, input_msg: str, trace_id: str | None) -> str | None:
    """POST to registry-api to create an AgentRun row for production tracking."""
    registry_url = cfg.REGISTRY_API_URL
    if not registry_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{registry_url}/api/v1/agent-runs",
                json={
                    "agent_name": agent_name,
                    "user_id": user_id,
                    "input": input_msg[:4000],
                    "context": "production",
                    "trigger_type": "api",
                    "run_by": user_id,
                    "team": team,
                    "langfuse_trace_id": trace_id,
                },
            )
            if resp.status_code == 201:
                return resp.json().get("id")
    except Exception as exc:
        logger.warning("Failed to create agent_run: %s", exc)
    return None


async def _complete_agent_run(run_id: str, status: str, output: str | None, latency_ms: int | None) -> None:
    """PATCH the AgentRun row on completion."""
    registry_url = cfg.REGISTRY_API_URL
    if not registry_url or not run_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{registry_url}/api/v1/agent-runs/{run_id}",
                json={
                    "status": status,
                    "output": (output[:4000] if output else None),
                    "latency_ms": latency_ms,
                },
            )
    except Exception as exc:
        logger.warning("Failed to update agent_run %s: %s", run_id, exc)


async def _load_memory_context(
    agent_name: str,
    conversation_id: str | None,
    scope: str = "agent",
    user_id: str = "",
    deployment_id: str = "",
) -> list[dict[str, str]]:
    """Load prior transcript from the memory service for context injection.

    Keyed by ``conversation_id`` (the transcript key). For ``scope='workflow_run'``
    the memory API drops the agent_name filter, so returned rows carry their author
    ``agent_name`` (a member sees peers' turns). Rows come back oldest-first
    (message_index ascending), so we preserve order — no reverse."""
    if not conversation_id or not cfg.REGISTRY_API_URL:
        return []
    try:
        params: dict[str, str | int] = {
            "thread_id": conversation_id,
            "scope": scope,
            "limit": 20,
        }
        if user_id:
            params["user_id"] = user_id
        if deployment_id:
            params["deployment_id"] = deployment_id
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{cfg.REGISTRY_API_URL}/api/v1/agents/{agent_name}/memory",
                params=params,
            )
            if resp.status_code == 200:
                rows = resp.json()
                out: list[dict[str, str]] = []
                for r in rows:
                    content = r["content"]
                    author = r.get("agent_name")
                    # Peer attribution (context-storage §5.2): in a shared workflow
                    # transcript (scope='workflow_run', which drops the agent_name filter
                    # so this member sees peers' turns) a turn authored by a DIFFERENT
                    # member is prefixed `[<agent_name>]: ` so it reads as a peer's
                    # contribution, not this member's own words. Same-author turns (and
                    # every scope='agent' row, which is always self-authored) stay verbatim.
                    # No graph-state schema change — the prefix rides in `content`.
                    if author and author != agent_name:
                        content = f"[{author}]: {content}"
                    turn: dict[str, str] = {"role": r["role"], "content": content}
                    if author:
                        turn["agent_name"] = author
                    out.append(turn)
                return out
    except Exception as exc:
        logger.warning("Memory load failed for %s/%s: %s", agent_name, conversation_id, exc)
    return []


async def _save_memory_turn(
    agent_name: str,
    conversation_id: str | None,
    user_msg: str,
    assistant_msg: str,
    user_id: str,
    scope: str = "agent",
    workflow_run_id: str | None = None,
    deployment_id: str = "",
    author_agent_name: str | None = None,
    message_kind: str = "agent_output",
) -> None:
    """Persist the user+assistant messages to the transcript via registry-api.

    For a workflow member (``scope='workflow_run'``) the row is tagged with
    ``author_agent_name`` + ``workflow_run_id`` so the shared transcript records
    which member produced it."""
    if not conversation_id or not cfg.REGISTRY_API_URL:
        return
    try:
        body: dict[str, Any] = {
            "thread_id": conversation_id,
            "user_id": user_id or None,
            "scope": scope,
            "messages": [
                {"role": "user", "content": user_msg, "message_kind": "user"},
                {"role": "assistant", "content": assistant_msg, "message_kind": message_kind},
            ],
        }
        if deployment_id:
            body["deployment_id"] = deployment_id
        if workflow_run_id:
            body["workflow_run_id"] = workflow_run_id
        if author_agent_name:
            body["author_agent_name"] = author_agent_name
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{cfg.REGISTRY_API_URL}/api/v1/agents/{agent_name}/memory",
                json=body,
            )
    except Exception as exc:
        logger.warning("Memory save failed for %s/%s: %s", agent_name, conversation_id, exc)


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Synchronous chat — invoke the workflow and return the complete response."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
    trace_id = request.headers.get("x-agentshield-trace-id")
    user_id = request.headers.get("x-user-sub", "")
    team = request.headers.get("x-agent-team", "")
    deployment_id = request.headers.get("x-deployment-id") or os.getenv("AGENTSHIELD_DEPLOYMENT_ID", "")
    # conversation_id (transcript key) defaults to thread_id when the caller omits it.
    conversation_id = req.conversation_id or req.thread_id
    start_ms = int(time.perf_counter() * 1000)

    agent_run_id = await _create_agent_run(cfg.AGENT_NAME, user_id, team, req.message, trace_id)

    # Load conversation memory for context (scope-aware; workflow members see peers).
    memory_context = await _load_memory_context(
        cfg.AGENT_NAME, conversation_id, scope=req.scope,
        user_id=user_id, deployment_id=deployment_id,
    )

    try:
        result = await workflow_executor.run(
            req.message, thread_id=req.thread_id, trace_id=trace_id,
            memory_context=memory_context,
        )
        elapsed = int(time.perf_counter() * 1000) - start_ms
        output_text = result.get("output", str(result)) if isinstance(result, dict) else str(result)
        if agent_run_id:
            import asyncio
            asyncio.create_task(_complete_agent_run(agent_run_id, "completed", output_text, elapsed))

        # Save turn to memory (fire-and-forget)
        import asyncio
        asyncio.create_task(_save_memory_turn(
            cfg.AGENT_NAME, conversation_id, req.message, output_text[:4000], user_id,
            scope=req.scope, workflow_run_id=req.workflow_run_id,
            deployment_id=deployment_id,
            author_agent_name=cfg.AGENT_NAME if req.scope == "workflow_run" else None,
        ))

        return result
    except SafetyBlockedError as exc:
        SAFETY_BLOCKS.inc()
        elapsed = int(time.perf_counter() * 1000) - start_ms
        if agent_run_id:
            import asyncio
            asyncio.create_task(_complete_agent_run(agent_run_id, "failed", f"Safety block: {exc.reason}", elapsed))
        raise HTTPException(status_code=400, detail=f"Safety block: {exc.reason}")
    except Exception as exc:
        logger.exception("Unhandled error in /chat")
        elapsed = int(time.perf_counter() * 1000) - start_ms
        if agent_run_id:
            import asyncio
            asyncio.create_task(_complete_agent_run(agent_run_id, "failed", str(exc), elapsed))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """Streaming chat — return workflow output as Server-Sent Events.

    POC-0 core fix: this handler now loads the prior transcript before streaming
    and persists the turn after it closes — symmetric with /chat, which it was
    not before (it did neither, so streamed chats never remembered anything)."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
    trace_id = request.headers.get("x-agentshield-trace-id")
    user_id = request.headers.get("x-user-sub", "")
    user_team = request.headers.get("x-agent-team", "")
    deployment_id = request.headers.get("x-deployment-id") or os.getenv("AGENTSHIELD_DEPLOYMENT_ID", "")
    # conversation_id (transcript key) defaults to thread_id when the caller omits it.
    conversation_id = req.conversation_id or req.thread_id
    # Batch/dataset eval sets this (registry-side, only for the eval-runner
    # identity) so high-risk tools auto-approve instead of hanging on HITL. The
    # SDK additionally gates it on a trusted batch identity (defense-in-depth).
    auto_approve = request.headers.get("x-agentshield-auto-approve", "").lower() == "true"

    from agentshield_sdk.graph_builder import _current_user_context

    async def sse_generator():
        import asyncio
        import json as _json
        # Capture the reset token so this request's identity never leaks into a
        # later request served on the same worker (§6.3 leak fix).
        token = _current_user_context.set({
            "user_id": user_id,
            "user_team": user_team,
            "auto_approve": auto_approve,
        })
        accumulated: list[str] = []
        try:
            # 1. Load prior transcript BEFORE streaming (symmetry with /chat).
            memory_context = await _load_memory_context(
                cfg.AGENT_NAME, conversation_id, scope=req.scope,
                user_id=user_id, deployment_id=deployment_id,
            )
            # 2. Stream, injecting the transcript as prior messages and
            #    accumulating assistant text_delta content to persist afterwards.
            async for chunk in workflow_executor.run_streamed(
                req.message, thread_id=req.thread_id, trace_id=trace_id,
                memory_context=memory_context,
            ):
                event_name = None
                data_str = None
                for line in chunk.splitlines():
                    if line.startswith("event:"):
                        event_name = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_str = line[len("data:"):].strip()
                if event_name == "text_delta" and data_str:
                    try:
                        accumulated.append(_json.loads(data_str).get("content", ""))
                    except Exception:
                        pass
                yield chunk
        except SafetyBlockedError as exc:
            SAFETY_BLOCKS.inc()
            yield (
                f"event: error\n"
                f"data: {_json.dumps({'reason': exc.reason, 'type': 'safety_blocked'})}\n\n"
            )
        except Exception as exc:
            logger.exception("Streaming error in /chat/stream")
            yield (
                f"event: error\n"
                f"data: {_json.dumps({'reason': str(exc), 'type': 'internal_error'})}\n\n"
            )
        finally:
            _current_user_context.reset(token)
        # 3. Persist the turn (fire-and-forget, after the stream closes — never
        #    delays the client; failures are logged, not raised).
        asyncio.create_task(_save_memory_turn(
            cfg.AGENT_NAME, conversation_id, req.message, "".join(accumulated)[:4000], user_id,
            scope=req.scope, workflow_run_id=req.workflow_run_id,
            deployment_id=deployment_id,
            author_agent_name=cfg.AGENT_NAME if req.scope == "workflow_run" else None,
        ))

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@app.post("/resume/{thread_id}")
async def resume_thread(thread_id: str, req: ResumeRequest):
    """Resume a HITL-paused thread after an approval decision.

    Durable /run resume (callback_url present) re-drives the shared harness so the
    remaining per-node steps are posted to the callback (the console decide is
    server-driven — no client stream is listening); fire-and-forget. A chat resume
    (no callback_url) keeps the existing synchronous path unchanged."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
    decision = {"decision": req.decision, "reviewer_id": req.reviewer_id, "reason": req.reason}

    if req.callback_url and req.run_id:
        import asyncio
        asyncio.create_task(
            _resume_durable_run(thread_id, decision, req.run_id, req.callback_url, req.eval_mode)
        )
        return {"status": "accepted", "thread_id": thread_id}

    try:
        result = await workflow_executor.resume(thread_id, decision)
        return result
    except Exception as exc:
        logger.exception("Error resuming thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(exc))


async def _resume_durable_run(
    thread_id: str, decision: dict, run_id: str, callback_url: str, eval_mode: str = "live",
) -> None:
    """Re-enter a parked durable run through the shared harness, emitting the remaining
    steps to the callback. Fail-loud: on error, post a terminal failed step so the run
    never hangs.

    ``eval_mode`` (Eval v2 E-2) is the mode the run STARTED in, sent by the registry
    off the persisted PlaygroundRun: the resume re-drives the graph and re-crosses the
    tool delivery edge, so the seam must be re-armed here or a parked record-mode eval
    would deliver its post-approval tool calls for real."""
    from agentshield_sdk.durable import Bookmark, StepEmitter, _exc_reason, resume_durable  # type: ignore[import]
    from agentshield_sdk.graph_builder import begin_eval_context  # type: ignore[import]
    from agentshield_sdk.otel import otel_run_context  # type: ignore[import]

    recorded = begin_eval_context(eval_mode)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            emitter = StepEmitter(callback_url, client, bookmark=Bookmark(run_id))
            with otel_run_context(run_id):
                await resume_durable(
                    workflow_executor.graph, thread_id=thread_id, decision=decision,
                    callback_url=callback_url, emitter=emitter,
                    recorded_side_effects=recorded,
                )
    except Exception as exc:
        logger.exception("durable resume failed thread=%s: %s", thread_id, exc)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(callback_url, json={
                    "step_number": 999, "step_name": "agent", "status": "failed",
                    "error_message": f"resume failed: {_exc_reason(exc)}"[:500], "run_completed": True,
                })
        except Exception:
            pass


@app.post("/resume/{thread_id}/stream")
async def resume_thread_stream(thread_id: str, req: ResumeRequest):
    """Resume a HITL-paused workflow thread and stream the continuation as SSE.

    Eval v2 E-2: this is the path the eval-runner's self-approve drives, so it too
    re-crosses the tool delivery edge and must re-arm the seam from the run's
    PERSISTED `eval_mode` (forwarded by routers/playground.py::resume_stream) —
    otherwise the tool call the approval just unblocked would be delivered for real.
    Records made on this path are not persisted to run_steps (it is the streaming chat
    resume, which emits no step callbacks); see the E-2 gap ledger."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")

    from agentshield_sdk.graph_builder import begin_eval_context  # type: ignore[import]
    begin_eval_context(req.eval_mode)

    decision = {
        "decision": req.decision,
        "reviewer_id": req.reviewer_id,
        "reason": req.reason,
    }
    return StreamingResponse(
        workflow_executor.resume_stream(thread_id, decision),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# POST /run — durable (multi-step) run execution
# ---------------------------------------------------------------------------

class DurableRunRequest(BaseModel):
    agent_name: str
    run_id: str
    input_payload: dict[str, Any] = {}
    callback_url: str
    # Eval v2 E-2: 'live' (default — deliver tool calls for real) | 'record' (a batch
    # eval: a side-effecting tool call is recorded + mocked at the delivery edge and
    # NOT invoked). Set by registry-api's durable dispatch body
    # (durable_dispatch.dispatch_durable_run) off the persisted PlaygroundRun. The
    # 'live' default is what keeps every non-eval dispatch delivering for real.
    eval_mode: str = "live"


@app.post("/run")
async def durable_run(req: DurableRunRequest, request: Request):
    """Start a durable multi-step run. Steps are reported back via callback_url."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")

    import asyncio
    asyncio.create_task(_execute_durable_run(req))
    return {"status": "accepted", "run_id": req.run_id}


async def _execute_durable_run(req: DurableRunRequest) -> None:
    """Execute a durable run via the shared harness (WS-1) — real per-node/tool steps
    + HITL park, replacing the old 2-step `input_processing`/`agent_execution` skeleton.

    Mirrors WorkflowExecutor.run()'s input safety-scan + OTEL trace binding (safety
    lives OUTSIDE the graph); the streamed output is not re-scanned mid-run, same as
    run_streamed(). run_durable owns the drive loop, step emission, and fail-closed
    park — this consumer differs from the SDK's only by the callback_url."""
    import json as _json

    from langchain_core.messages import HumanMessage  # type: ignore[import]

    from agentshield_sdk.durable import Bookmark, StepEmitter, _exc_reason, run_durable  # type: ignore[import]
    from agentshield_sdk.graph_builder import begin_eval_context  # type: ignore[import]
    from agentshield_sdk.otel import otel_run_context  # type: ignore[import]
    from agentshield_sdk.safety_client import scan_input  # type: ignore[import]

    async def post_fail(step_name: str, error_message: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(req.callback_url, json={
                    "step_number": 1, "step_name": step_name, "status": "failed",
                    "error_message": error_message, "run_completed": True,
                })
        except Exception as exc:  # a failed fail-post must not raise
            logger.warning("post_fail failed for run %s: %s", req.run_id, exc)

    # Resolve the driving turn: an explicit message wins; else the job-spec payload
    # (the agent parses it); else — a schedule/webhook that fired with no payload at
    # all — a daemon kickoff, so we never hand the LLM an empty user turn.
    from workflow_executor import DAEMON_KICKOFF
    input_msg = (
        req.input_payload.get("message")
        or (_json.dumps(req.input_payload) if req.input_payload else DAEMON_KICKOFF)
    )
    # Eval v2 E-2: arm the record/mock seam for THIS run before the graph runs. We are
    # inside the run's own asyncio task, so the ContextVar + buffer are scoped to this
    # run and cannot leak into another (a concurrent live run stays live). Under
    # `eval_mode=record` the governed-tool delivery edge records + mocks side-effecting
    # calls instead of invoking them; `recorded` is the buffer it appends to, drained by
    # the harness onto the tool step's run_steps.output.
    recorded = begin_eval_context(req.eval_mode)
    logger.info("durable run %s eval_mode=%s", req.run_id, req.eval_mode)
    try:
        scan = await scan_input(input_msg, agent_name=cfg.AGENT_NAME, session_id=req.run_id)
        state = {"messages": [HumanMessage(content=scan.sanitized_text)]}
        async with httpx.AsyncClient(timeout=30.0) as client:
            emitter = StepEmitter(req.callback_url, client, bookmark=Bookmark(req.run_id))
            # trace_id = run_id so the durable run's LLM/tool spans land on the platform trace.
            with otel_run_context(req.run_id):
                result = await run_durable(
                    workflow_executor.graph, state,
                    thread_id=req.run_id, callback_url=req.callback_url, emitter=emitter,
                    recorded_side_effects=recorded,
                )
        logger.info("durable run %s finished status=%s steps=%d",
                    req.run_id, result.status, result.steps_emitted)
    except SafetyBlockedError as exc:
        SAFETY_BLOCKS.inc()
        await post_fail("safety_scan_input", f"Safety block: {exc.reason}")
    except Exception as exc:  # never leave the run hanging — fail loud
        logger.exception("Durable run %s failed", req.run_id)
        await post_fail("agent", _exc_reason(exc)[:500])

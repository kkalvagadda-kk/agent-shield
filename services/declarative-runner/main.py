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
    """On startup, find runs stuck in 'running' and resume from checkpoint."""
    from checkpoint import list_interrupted_runs, load_checkpoint
    from run_executor import RunExecutor

    try:
        interrupted = await list_interrupted_runs(cfg.AGENT_NAME)
        for run_id in interrupted:
            cp = await load_checkpoint(run_id)
            if cp and cp.last_completed_step > 0:
                logger.info(
                    "Resuming interrupted run %s from step %d",
                    run_id, cp.last_completed_step,
                )
                executor = RunExecutor(run_id=run_id, agent_name=cfg.AGENT_NAME)
                next_step = cp.last_completed_step + 1
                await executor.begin_step(next_step, "resumed_execution")
                try:
                    input_msg = cp.state.get("last_input", "")
                    result = await workflow_executor.run(
                        input_msg, thread_id=run_id, trace_id=run_id
                    )
                    output = result.get("output", str(result)) if isinstance(result, dict) else str(result)
                    await executor.complete_step(next_step, {"response": output[:2000]})
                    await executor.complete_run("completed", output)
                except Exception as exc:
                    logger.warning("Resume failed for run %s: %s", run_id, exc)
                    await executor.fail_step(next_step, str(exc)[:500])
                    await executor.complete_run("failed")
            else:
                logger.info("Run %s has no checkpoint — marking as failed (lost state)", run_id)
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.patch(
                            f"{cfg.REGISTRY_API_URL}/api/v1/agent-runs/{run_id}",
                            json={"status": "failed", "error_message": "Pod restarted without checkpoint"},
                        )
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("_resume_interrupted_runs error: %s", exc)


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
    message: str
    thread_id: str | None = None
    metadata: dict | None = None


class ResumeRequest(BaseModel):
    decision: str  # "approved" | "rejected"
    reviewer_id: str | None = None
    reason: str | None = None


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


async def _load_memory_context(agent_name: str, thread_id: str | None) -> list[dict[str, str]]:
    """Load conversation history from memory service for context injection."""
    if not thread_id or not cfg.REGISTRY_API_URL:
        return []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{cfg.REGISTRY_API_URL}/api/v1/agents/{agent_name}/memory",
                params={"thread_id": thread_id, "limit": 20},
            )
            if resp.status_code == 200:
                rows = resp.json()
                return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception as exc:
        logger.warning("Memory load failed for %s/%s: %s", agent_name, thread_id, exc)
    return []


async def _save_memory_turn(agent_name: str, thread_id: str | None, user_msg: str, assistant_msg: str, user_id: str) -> None:
    """Persist the user+assistant messages to memory via registry-api."""
    if not thread_id or not cfg.REGISTRY_API_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{cfg.REGISTRY_API_URL}/api/v1/agents/{agent_name}/memory",
                json={
                    "thread_id": thread_id,
                    "user_id": user_id or None,
                    "messages": [
                        {"role": "user", "content": user_msg},
                        {"role": "assistant", "content": assistant_msg},
                    ],
                },
            )
    except Exception as exc:
        logger.warning("Memory save failed for %s/%s: %s", agent_name, thread_id, exc)


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Synchronous chat — invoke the workflow and return the complete response."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
    trace_id = request.headers.get("x-agentshield-trace-id")
    user_id = request.headers.get("x-user-sub", "")
    team = request.headers.get("x-agent-team", "")
    start_ms = int(time.perf_counter() * 1000)

    agent_run_id = await _create_agent_run(cfg.AGENT_NAME, user_id, team, req.message, trace_id)

    # Load conversation memory for context
    memory_context = await _load_memory_context(cfg.AGENT_NAME, req.thread_id)

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
        asyncio.create_task(_save_memory_turn(cfg.AGENT_NAME, req.thread_id, req.message, output_text[:4000], user_id))

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
    """Streaming chat — return workflow output as Server-Sent Events."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
    trace_id = request.headers.get("x-agentshield-trace-id")
    user_id = request.headers.get("x-user-sub", "")
    user_team = request.headers.get("x-agent-team", "")
    # Batch/dataset eval sets this (registry-side, only for the eval-runner
    # identity) so high-risk tools auto-approve instead of hanging on HITL. The
    # SDK additionally gates it on a trusted batch identity (defense-in-depth).
    auto_approve = request.headers.get("x-agentshield-auto-approve", "").lower() == "true"

    from agentshield_sdk.graph_builder import _current_user_context
    _current_user_context.set({
        "user_id": user_id,
        "user_team": user_team,
        "auto_approve": auto_approve,
    })

    async def sse_generator():
        try:
            async for chunk in workflow_executor.run_streamed(
                req.message, thread_id=req.thread_id, trace_id=trace_id
            ):
                yield chunk
        except SafetyBlockedError as exc:
            SAFETY_BLOCKS.inc()
            import json as _json
            yield (
                f"event: error\n"
                f"data: {_json.dumps({'reason': exc.reason, 'type': 'safety_blocked'})}\n\n"
            )
        except Exception as exc:
            import json as _json
            logger.exception("Streaming error in /chat/stream")
            yield (
                f"event: error\n"
                f"data: {_json.dumps({'reason': str(exc), 'type': 'internal_error'})}\n\n"
            )

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@app.post("/resume/{thread_id}")
async def resume_thread(thread_id: str, req: ResumeRequest):
    """Resume a HITL-paused workflow thread after an approval decision."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
    try:
        decision = {
            "decision": req.decision,
            "reviewer_id": req.reviewer_id,
            "reason": req.reason,
        }
        result = await workflow_executor.resume(thread_id, decision)
        return result
    except Exception as exc:
        logger.exception("Error resuming thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/resume/{thread_id}/stream")
async def resume_thread_stream(thread_id: str, req: ResumeRequest):
    """Resume a HITL-paused workflow thread and stream the continuation as SSE."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
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


@app.post("/run")
async def durable_run(req: DurableRunRequest, request: Request):
    """Start a durable multi-step run. Steps are reported back via callback_url."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")

    import asyncio
    asyncio.create_task(_execute_durable_run(req))
    return {"status": "accepted", "run_id": req.run_id}


async def _execute_durable_run(req: DurableRunRequest) -> None:
    """Execute a durable run, posting step updates to the callback URL."""
    import json as _json

    async def post_step(step_data: dict[str, Any]) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(req.callback_url, json=step_data)
        except Exception as exc:
            logger.warning("Failed to post step update for run %s: %s", req.run_id, exc)

    try:
        # Step 1: Input processing
        await post_step({
            "step_number": 1,
            "step_name": "input_processing",
            "status": "running",
        })

        trace_id = req.run_id
        input_msg = req.input_payload.get("message", _json.dumps(req.input_payload))

        await post_step({
            "step_number": 1,
            "step_name": "input_processing",
            "status": "completed",
            "output": {"message": "Input processed"},
        })

        # Step 2: Agent execution
        await post_step({
            "step_number": 2,
            "step_name": "agent_execution",
            "status": "running",
        })

        result = await workflow_executor.run(
            input_msg, thread_id=req.run_id, trace_id=trace_id
        )

        output_text = ""
        if isinstance(result, dict):
            output_text = result.get("output", result.get("response", str(result)))
        elif isinstance(result, str):
            output_text = result
        else:
            output_text = str(result)

        await post_step({
            "step_number": 2,
            "step_name": "agent_execution",
            "status": "completed",
            "output": {"response": output_text[:2000]},
            "run_completed": True,
            "output_text": output_text,
        })

    except SafetyBlockedError as exc:
        SAFETY_BLOCKS.inc()
        await post_step({
            "step_number": 2,
            "step_name": "agent_execution",
            "status": "failed",
            "error_message": f"Safety block: {exc.reason}",
            "run_completed": True,
        })
    except Exception as exc:
        logger.exception("Durable run %s failed", req.run_id)
        await post_step({
            "step_number": 2,
            "step_name": "agent_execution",
            "status": "failed",
            "error_message": str(exc)[:500],
            "run_completed": True,
        })

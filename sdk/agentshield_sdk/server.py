"""
FastAPI server — the HTTP interface for deployed agent pods.

Endpoints:
    GET  /health             — liveness probe
    GET  /ready              — readiness probe (checks deps)
    GET  /metrics            — Prometheus metrics
    POST /chat               — sync invoke
    POST /chat/stream        — SSE streaming invoke
    POST /run                — durable fire-and-forget run (real steps + HITL park)
    POST /resume/{thread_id} — resume a HITL-paused thread
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
from sse_starlette.sse import EventSourceResponse  # type: ignore[import]

from . import config
from .otel import setup_otel
from .safety_client import SafetyBlockedError

logger = logging.getLogger(__name__)

# --- Prometheus metrics ---
REQUEST_COUNTER = Counter(
    "agentshield_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
REQUEST_LATENCY = Histogram(
    "agentshield_request_duration_seconds",
    "HTTP request latency",
    ["endpoint"],
)
SAFETY_BLOCKS = Counter(
    "agentshield_safety_blocks_total",
    "Requests blocked by the safety scanner",
)

# --- App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Process startup: enable OpenTelemetry LLM/tool span capture.

    OpenInference instruments langchain/langgraph globally and exports OTLP to
    the configured backend (Langfuse today); ``setup_otel()`` no-ops when
    unconfigured. Mirrors the declarative-runner's lifespan wiring
    (services/declarative-runner/main.py) so SDK-container agents emit the same
    LLM/tool generation spans. Runs before any request is served, so the
    instrumentation is in place before the first graph invocation. Must never
    raise — ``setup_otel`` already swallows its own errors, but we guard here
    too so a tracing misconfig can never stop the agent from serving.
    """
    try:
        enabled = setup_otel()
        logger.info("SDK agent OTEL span capture enabled=%s", enabled)
    except Exception as exc:  # never let tracing setup break startup
        logger.warning("OTEL setup skipped: %s", exc)
    yield


app = FastAPI(
    title="AgentShield Agent",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# Set by cli.py before uvicorn starts.
runner: Any = None


# --- Request / response models ---
class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    metadata: dict | None = None


class ResumeRequest(BaseModel):
    decision: str  # "approved" | "rejected"
    reviewer_id: str | None = None
    reason: str | None = None
    run_id: str | None = None        # set for a durable /run resume (WS-1 T4)
    callback_url: str | None = None  # durable resume re-drives the harness, posting steps here
    # Eval v2 E-2 — the mode the run started in (sent by the registry off the persisted
    # PlaygroundRun). A resume re-drives the graph and re-crosses the tool delivery
    # edge, so the record/mock seam must be re-armed. Parity with the declarative-runner.
    eval_mode: str = "live"


# --- Middleware: basic request timing ---
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


# --- Endpoints ---

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent_name": config.AGENT_NAME,
        "version": "0.1.0",
    }


@app.get("/ready")
async def ready():
    checks: dict[str, str] = {}

    # Safety Orchestrator
    if config.AGENTSHIELD_SAFETY_URL:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{config.AGENTSHIELD_SAFETY_URL}/health")
            checks["safety_orchestrator"] = "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            checks["safety_orchestrator"] = "unreachable"
    else:
        checks["safety_orchestrator"] = "mock"

    # OPA
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{config.AGENTSHIELD_OPA_URL}/health")
        checks["opa"] = "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        checks["opa"] = "unreachable" if not config.DEV_MODE else "mock"

    # Langfuse
    if config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{config.LANGFUSE_HOST}/api/public/health")
            checks["langfuse"] = "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            checks["langfuse"] = "unreachable"
    else:
        checks["langfuse"] = "disabled"

    # Postgres (via checkpointer)
    if config.DIRECT_DATABASE_URL:
        try:
            import asyncpg  # type: ignore[import]
            conn = await asyncpg.connect(
                config.DIRECT_DATABASE_URL.replace("+asyncpg", ""), timeout=2
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
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    if runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialised")
    trace_id = request.headers.get("x-agentshield-trace-id")
    try:
        result = await runner.run(
            req.message, thread_id=req.thread_id, metadata=req.metadata,
            trace_id=trace_id,
        )
        return result
    except SafetyBlockedError as exc:
        SAFETY_BLOCKS.inc()
        raise HTTPException(status_code=400, detail=f"Safety block: {exc.reason}")
    except Exception as exc:
        logger.exception("Unhandled error in /chat")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    if runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialised")
    trace_id = request.headers.get("x-agentshield-trace-id")

    async def sse_generator():
        try:
            async for chunk in runner.run_streamed(
                req.message, thread_id=req.thread_id, trace_id=trace_id
            ):
                yield chunk
        except SafetyBlockedError as exc:
            SAFETY_BLOCKS.inc()
            import json
            yield f"event: error\ndata: {json.dumps({'reason': exc.reason, 'type': 'safety_blocked'})}\n\n"
        except Exception as exc:
            import json
            logger.exception("Streaming error")
            yield f"event: error\ndata: {json.dumps({'reason': str(exc), 'type': 'internal_error'})}\n\n"

    return EventSourceResponse(sse_generator())


class DurableRunRequest(BaseModel):
    run_id: str
    callback_url: str
    input_payload: dict[str, Any] = {}
    agent_name: str | None = None  # ignored — this pod IS the agent (accepted for parity with the dispatch body)
    # Eval v2 E-2 — 'live' (default) | 'record'. Same field, same dispatch body, same
    # `governed_tool` seam as the declarative-runner: a custom-container SDK agent
    # evaluated in record mode records + mocks its side-effecting calls too.
    eval_mode: str = "live"


@app.post("/run")
async def run_durable_endpoint(req: DurableRunRequest, request: Request):
    """Durable fire-and-forget run (WS-1). Accepts the same body the registry-api
    durable dispatch posts; step progress + terminal/park status arrive asynchronously
    at ``callback_url``. Parity with the declarative-runner ``/run`` — both drive the
    shared ``agentshield_sdk.durable.run_durable`` harness."""
    if runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialised")
    import asyncio
    import json

    message = req.input_payload.get("message") or json.dumps(req.input_payload)
    trace_id = request.headers.get("x-agentshield-trace-id") or req.run_id
    asyncio.create_task(
        _execute_durable_run_bg(message, req.run_id, req.callback_url, trace_id, req.eval_mode)
    )
    return {"status": "accepted", "run_id": req.run_id}


async def _execute_durable_run_bg(
    message: str, run_id: str, callback_url: str, trace_id: str, eval_mode: str = "live",
) -> None:
    try:
        result = await runner.run_durable(
            message, run_id=run_id, callback_url=callback_url, trace_id=trace_id,
            eval_mode=eval_mode,
        )
        logger.info("SDK durable run %s finished status=%s", run_id, result.status)
    except SafetyBlockedError as exc:
        SAFETY_BLOCKS.inc()
        await _post_durable_fail(callback_url, "safety_scan_input", f"Safety block: {exc.reason}")
    except Exception as exc:  # never leave the run hanging — fail loud
        logger.exception("SDK durable run %s failed", run_id)
        await _post_durable_fail(callback_url, "agent", str(exc)[:500])


async def _post_durable_fail(callback_url: str, step_name: str, error_message: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(callback_url, json={
                "step_number": 1, "step_name": step_name, "status": "failed",
                "error_message": error_message, "run_completed": True,
            })
    except Exception as exc:
        logger.warning("durable fail-post failed for run: %s", exc)


@app.post("/resume/{thread_id}")
async def resume_thread(thread_id: str, req: ResumeRequest, request: Request):
    if runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialised")
    trace_id = request.headers.get("x-agentshield-trace-id")
    decision = {"decision": req.decision, "reviewer_id": req.reviewer_id, "reason": req.reason}

    # Durable /run resume (callback_url present): re-drive the harness fire-and-forget so
    # the remaining steps reach the callback. Chat resume (no callback_url) is unchanged.
    if req.callback_url and req.run_id:
        import asyncio
        asyncio.create_task(
            _resume_durable_bg(thread_id, decision, req.run_id, req.callback_url, trace_id,
                               req.eval_mode)
        )
        return {"status": "accepted", "thread_id": thread_id}

    try:
        result = await runner.resume(thread_id, decision, trace_id=trace_id)
        return result
    except Exception as exc:
        logger.exception("Error resuming thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(exc))


async def _resume_durable_bg(thread_id: str, decision: dict, run_id: str, callback_url: str,
                             trace_id: str | None, eval_mode: str = "live") -> None:
    try:
        result = await runner.resume_durable(
            thread_id, decision, run_id=run_id, callback_url=callback_url, trace_id=trace_id,
            eval_mode=eval_mode,
        )
        logger.info("SDK durable resume %s finished status=%s", run_id, result.status)
    except Exception as exc:  # fail loud — post a terminal failed step so the run never hangs
        logger.exception("SDK durable resume %s failed", run_id)
        await _post_durable_fail(callback_url, "agent", f"resume failed: {exc}"[:500])

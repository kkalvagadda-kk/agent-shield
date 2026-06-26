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
from sse_starlette.sse import EventSourceResponse  # type: ignore[import]

import config as cfg
from agentshield_sdk.safety_client import SafetyBlockedError  # type: ignore[import]

logger = logging.getLogger(__name__)

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

    # Import here so that config.py (which validates WORKFLOW_JSON) is already
    # loaded when this code runs.  If WORKFLOW_JSON is absent the process will
    # have already crashed during module import.
    from workflow_executor import WorkflowExecutor  # type: ignore[import]

    workflow_executor = WorkflowExecutor()
    await workflow_executor.setup()  # replaces MemorySaver with Postgres if configured
    logger.info("WorkflowExecutor ready — declarative runner is up")
    yield
    logger.info("WorkflowExecutor shutting down")


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
    if cfg.AGENTSHIELD_LANGFUSE_KEY:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(
                    f"{cfg.AGENTSHIELD_LANGFUSE_HOST}/api/public/health"
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

    is_ready = all(
        v in ("ok", "mock", "disabled", "memory") for v in checks.values()
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


@app.post("/chat")
async def chat(req: ChatRequest):
    """Synchronous chat — invoke the workflow and return the complete response."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")
    try:
        result = await workflow_executor.run(req.message, thread_id=req.thread_id)
        return result
    except SafetyBlockedError as exc:
        SAFETY_BLOCKS.inc()
        raise HTTPException(status_code=400, detail=f"Safety block: {exc.reason}")
    except Exception as exc:
        logger.exception("Unhandled error in /chat")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Streaming chat — return workflow output as Server-Sent Events."""
    if workflow_executor is None:
        raise HTTPException(status_code=503, detail="WorkflowExecutor not initialised")

    async def sse_generator():
        try:
            async for chunk in workflow_executor.run_streamed(
                req.message, thread_id=req.thread_id
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

    return EventSourceResponse(sse_generator())


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

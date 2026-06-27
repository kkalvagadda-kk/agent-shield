"""
FastAPI server — the HTTP interface for deployed agent pods.

Endpoints:
    GET  /health             — liveness probe
    GET  /ready              — readiness probe (checks deps)
    GET  /metrics            — Prometheus metrics
    POST /chat               — sync invoke
    POST /chat/stream        — SSE streaming invoke
    POST /resume/{thread_id} — resume a HITL-paused thread
"""
from __future__ import annotations

import logging
import time
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
app = FastAPI(
    title="AgentShield Agent",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
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
    if config.AGENTSHIELD_LANGFUSE_KEY:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{config.AGENTSHIELD_LANGFUSE_HOST}/api/public/health")
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
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/chat")
async def chat(req: ChatRequest):
    if runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialised")
    try:
        result = await runner.run(
            req.message, thread_id=req.thread_id, metadata=req.metadata
        )
        return result
    except SafetyBlockedError as exc:
        SAFETY_BLOCKS.inc()
        raise HTTPException(status_code=400, detail=f"Safety block: {exc.reason}")
    except Exception as exc:
        logger.exception("Unhandled error in /chat")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    if runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialised")

    async def sse_generator():
        try:
            async for chunk in runner.run_streamed(
                req.message, thread_id=req.thread_id
            ):
                # EventSourceResponse expects plain strings; we yield pre-formatted
                # SSE frames from streaming.py.
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


@app.post("/resume/{thread_id}")
async def resume_thread(thread_id: str, req: ResumeRequest):
    if runner is None:
        raise HTTPException(status_code=503, detail="Runner not initialised")
    try:
        decision = {
            "decision": req.decision,
            "reviewer_id": req.reviewer_id,
            "reason": req.reason,
        }
        result = await runner.resume(thread_id, decision)
        return result
    except Exception as exc:
        logger.exception("Error resuming thread %s", thread_id)
        raise HTTPException(status_code=500, detail=str(exc))

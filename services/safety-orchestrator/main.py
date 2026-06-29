import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from config import settings
from orchestrator import SafetyOrchestrator
from pii_store import PiiStore
from scanner_clients import LLMGuardClient, NeMoClient, PresidioClient
from schemas import (
    ReadinessResponse,
    ScanInputRequest,
    ScanInputResponse,
    ScanOutputRequest,
    ScanOutputResponse,
)

_orchestrator: SafetyOrchestrator | None = None
_llm_guard: LLMGuardClient | None = None
_presidio: PresidioClient | None = None
_nemo: NeMoClient | None = None
_pii_store: PiiStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _orchestrator, _llm_guard, _presidio, _nemo, _pii_store

    _llm_guard = LLMGuardClient(settings.llmguard_url)
    _presidio = PresidioClient(settings.presidio_analyzer_url, settings.presidio_anonymizer_url)
    _nemo = NeMoClient(settings.nemo_url)
    _pii_store = PiiStore(settings.database_url, settings.pii_ttl_hours)
    _orchestrator = SafetyOrchestrator(_llm_guard, _presidio, _nemo, _pii_store)

    yield

    await _llm_guard.aclose()
    await _presidio.aclose()
    await _nemo.aclose()
    await _pii_store.aclose()


app = FastAPI(title="AgentShield Safety Orchestrator", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ready", response_model=ReadinessResponse)
async def ready() -> ReadinessResponse:
    assert _llm_guard and _presidio and _nemo
    llm_guard_ok, presidio_ok, nemo_ok = await asyncio.gather(
        _llm_guard.ping(),
        _presidio.ping(),
        _nemo.ping(),
    )
    all_ready = llm_guard_ok and presidio_ok and nemo_ok
    return ReadinessResponse(
        ready=all_ready,
        scanners={
            "llm_guard": llm_guard_ok,
            "presidio": presidio_ok,
            "nemo": nemo_ok,
        },
    )


@app.post("/api/v1/scan/input", response_model=ScanInputResponse)
async def scan_input(
    req: ScanInputRequest,
    response: Response,
    x_agentshield_trace_id: str | None = Header(None, alias="X-AgentShield-Trace-ID"),
) -> ScanInputResponse:
    assert _orchestrator
    result = await _orchestrator.scan_input(req, trace_id=x_agentshield_trace_id)
    # Echo trace ID so callers can stitch spans
    out_trace_id = x_agentshield_trace_id or req.session_id or ""
    if out_trace_id:
        response.headers["X-AgentShield-Trace-ID"] = out_trace_id
    return result


@app.post("/api/v1/scan/output", response_model=ScanOutputResponse)
async def scan_output(
    req: ScanOutputRequest,
    response: Response,
    x_agentshield_trace_id: str | None = Header(None, alias="X-AgentShield-Trace-ID"),
) -> ScanOutputResponse:
    assert _orchestrator
    result = await _orchestrator.scan_output(req, trace_id=x_agentshield_trace_id)
    out_trace_id = x_agentshield_trace_id or req.session_id or ""
    if out_trace_id:
        response.headers["X-AgentShield-Trace-ID"] = out_trace_id
    return result

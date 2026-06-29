"""
Safety Orchestrator — fan-out to enabled scanners with 5s timeout.

Fail-closed applies only to *enabled* scanners: if an enabled scanner errors
or times out the request is blocked. Disabled scanners are skipped entirely
and never contribute to a block decision.

When all scanners are disabled the orchestrator is a pure pass-through.

Input scan flow:
  1. Presidio (if enabled): detect PII → anonymize → store mapping
  2. LLM Guard + NeMo (if enabled): parallel fan-out on (anonymized) text
  3. Merge scores; block if any enabled scanner signals a violation

Output scan flow:
  1. LLM Guard (if enabled): scan output text
  2. Presidio (if enabled): de-anonymize via stored PII mapping
"""

import asyncio
import logging
import time
from typing import Any

from config import settings
from pii_store import PiiStore
from scanner_clients import LLMGuardClient, NeMoClient, PresidioClient
from schemas import (
    ScanInputRequest,
    ScanInputResponse,
    ScanOutputRequest,
    ScanOutputResponse,
)

logger = logging.getLogger(__name__)

# Langfuse client — initialised lazily; None if SDK unavailable or keys not set.
_langfuse: Any = None


def _lf():
    global _langfuse
    if _langfuse is None and settings.langfuse_public_key:
        try:
            from langfuse import Langfuse
            _langfuse = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host or None,
            )
        except Exception as exc:
            logger.warning("Langfuse init failed (tracing disabled): %s", exc)
    return _langfuse

_SCAN_TIMEOUT = 5.0  # seconds for entire fan-out


class SafetyOrchestrator:
    def __init__(
        self,
        llm_guard: LLMGuardClient,
        presidio: PresidioClient,
        nemo: NeMoClient,
        pii_store: PiiStore,
    ) -> None:
        self._llm_guard = llm_guard
        self._presidio = presidio
        self._nemo = nemo
        self._pii_store = pii_store

    # ------------------------------------------------------------------
    # Input scanning
    # ------------------------------------------------------------------

    async def scan_input(
        self, req: ScanInputRequest, trace_id: str | None = None
    ) -> ScanInputResponse:
        lf = _lf()
        trace = span = None
        t0 = time.monotonic()
        try:
            if lf:
                trace = lf.trace(
                    id=trace_id or None,
                    name="safety-scan-input",
                    session_id=req.session_id,
                    metadata={"agent_name": req.agent_name},
                )
                span = trace.span(
                    name="safety-scan-input",
                    input={"message": req.message[:200] if req.message else ""},
                )
        except Exception:
            pass
        try:
            result = await asyncio.wait_for(
                self._scan_input_inner(req, trace=trace),
                timeout=_SCAN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Input scan timed out for session=%s agent=%s", req.session_id, req.agent_name)
            result = ScanInputResponse(allowed=False, blocked=True, reason="safety-scan-timeout")
        except Exception as exc:
            logger.error("Input scan error: %s", exc)
            result = ScanInputResponse(allowed=False, blocked=True, reason="safety-scan-error")
        latency_ms = int((time.monotonic() - t0) * 1000)
        try:
            if span:
                span.end(output={
                    "blocked": result.blocked,
                    "reason": result.reason,
                    "latency_ms": latency_ms,
                })
            if lf:
                lf.flush()
        except Exception:
            pass
        return result

    async def _emit_scanner_span(
        self, trace: Any, scanner_name: str, input_text: str,
        result: dict, latency_ms: int
    ) -> None:
        """Emit a child span for a single scanner call with latency."""
        try:
            if trace:
                span = trace.span(
                    name=f"safety-scan-{scanner_name}",
                    input={"text": input_text[:200]},
                    output={
                        "blocked": result.get("is_blocked", False) or result.get("blocked", False),
                        "risk_score": result.get("risk_score", 0.0),
                        "reason": result.get("reason", ""),
                        "latency_ms": latency_ms,
                    },
                    metadata={"scanner": scanner_name},
                )
                span.end()
        except Exception:
            pass

    async def _scan_input_inner(
        self, req: ScanInputRequest, trace: Any = None
    ) -> ScanInputResponse:
        scan_text = req.message
        pii_detected = False
        anonymized_text: str | None = None

        # Step 1: PII detection + anonymization (Presidio)
        if settings.presidio_enabled:
            try:
                t0 = time.monotonic()
                entities = await self._presidio.analyze(req.message)
                if entities:
                    pii_detected = True
                    anon_result = await self._presidio.anonymize(req.message, entities)
                    anonymized_text = anon_result.get("text", req.message)
                    scan_text = anonymized_text

                    for entity in entities:
                        await self._pii_store.store_mapping(
                            session_id=req.session_id,
                            agent_name=req.agent_name,
                            original_text=entity.get("text", ""),
                            anonymized_text=entity.get("anonymized", anonymized_text),
                            entity_type=entity.get("entity_type", "UNKNOWN"),
                        )
                await self._emit_scanner_span(
                    trace, "presidio", req.message,
                    {"blocked": False, "pii_detected": pii_detected},
                    int((time.monotonic() - t0) * 1000),
                )
            except Exception as exc:
                logger.error("Presidio scan failed: %s", exc)
                return ScanInputResponse(allowed=False, blocked=True, reason="presidio-error")
        else:
            logger.debug("Presidio disabled — skipping PII scan for session=%s", req.session_id)

        # Step 2: Parallel fan-out to LLM Guard + NeMo
        scores: dict[str, float] = {}

        tasks: list = []
        task_labels: list[str] = []
        task_t0s: list[float] = []

        if settings.llmguard_enabled:
            task_t0s.append(time.monotonic())
            tasks.append(self._llm_guard.scan(scan_text))
            task_labels.append("llm_guard")
        if settings.nemo_enabled:
            task_t0s.append(time.monotonic())
            tasks.append(self._nemo.check(scan_text))
            task_labels.append("nemo")

        if tasks:
            scan_t0 = time.monotonic()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for label, result in zip(task_labels, results):
                latency = int((time.monotonic() - scan_t0) * 1000)
                if isinstance(result, Exception):
                    logger.error("%s scan failed: %s", label, result)
                    return ScanInputResponse(allowed=False, blocked=True, reason=f"{label}-error")
                if isinstance(result, dict):
                    score = result.get("risk_score", 0.0)
                    scores[label] = score
                    blocked_flag = result.get("is_blocked", False) or result.get("blocked", False)
                    await self._emit_scanner_span(trace, label, scan_text, result, latency)
                    if blocked_flag or score >= 0.8:
                        return ScanInputResponse(
                            allowed=False, blocked=True, reason=f"{label}-violation", scores=scores
                        )
        else:
            logger.debug("All active scanners disabled — pass-through for session=%s", req.session_id)

        return ScanInputResponse(
            allowed=True,
            blocked=False,
            anonymized_message=anonymized_text,
            pii_detected=pii_detected,
            scores=scores,
        )

    # ------------------------------------------------------------------
    # Output scanning
    # ------------------------------------------------------------------

    async def scan_output(
        self, req: ScanOutputRequest, trace_id: str | None = None
    ) -> ScanOutputResponse:
        lf = _lf()
        trace = span = None
        t0 = time.monotonic()
        try:
            if lf:
                trace = lf.trace(
                    id=trace_id or None,
                    name="safety-scan-output",
                    session_id=req.session_id,
                    metadata={"agent_name": req.agent_name},
                )
                span = trace.span(
                    name="safety-scan-output",
                    input={"message": req.message[:200] if req.message else ""},
                )
        except Exception:
            pass
        try:
            result = await asyncio.wait_for(
                self._scan_output_inner(req, trace=trace),
                timeout=_SCAN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("Output scan timed out for session=%s", req.session_id)
            result = ScanOutputResponse(allowed=False, blocked=True, reason="safety-scan-timeout")
        except Exception as exc:
            logger.error("Output scan error: %s", exc)
            result = ScanOutputResponse(allowed=False, blocked=True, reason="safety-scan-error")
        latency_ms = int((time.monotonic() - t0) * 1000)
        try:
            if span:
                span.end(output={
                    "blocked": result.blocked,
                    "reason": result.reason,
                    "latency_ms": latency_ms,
                })
            if lf:
                lf.flush()
        except Exception:
            pass
        return result

    async def _scan_output_inner(
        self, req: ScanOutputRequest, trace: Any = None
    ) -> ScanOutputResponse:
        scores: dict[str, float] = {}

        # Step 1: LLM Guard output scan
        if settings.llmguard_enabled:
            try:
                t0 = time.monotonic()
                guard_result = await self._llm_guard.scan(req.message)
                latency = int((time.monotonic() - t0) * 1000)
                score = guard_result.get("risk_score", 0.0)
                scores["llm_guard"] = score
                await self._emit_scanner_span(trace, "llm_guard", req.message, guard_result, latency)
                if guard_result.get("is_blocked", False) or score >= 0.8:
                    return ScanOutputResponse(
                        allowed=False, blocked=True, reason="llmguard-output-violation", scores=scores
                    )
            except Exception as exc:
                logger.error("LLM Guard output scan failed: %s", exc)
                return ScanOutputResponse(allowed=False, blocked=True, reason="llmguard-error")
        else:
            logger.debug("LLM Guard disabled — skipping output scan for session=%s", req.session_id)

        # Step 2: De-anonymize if session had PII (requires Presidio)
        deanonymized: str | None = None
        if settings.presidio_enabled:
            mappings = await self._pii_store.get_mappings(req.session_id, req.agent_name)
            if mappings:
                try:
                    denanon_result = await self._presidio.deanonymize(
                        req.message,
                        anonymizer_results={m.entity_type: m.original_text for m in mappings},
                    )
                    deanonymized = denanon_result.get("text")
                except Exception as exc:
                    logger.warning("De-anonymization failed (non-fatal): %s", exc)

        return ScanOutputResponse(
            allowed=True,
            blocked=False,
            deanonymized_message=deanonymized,
            scores=scores,
        )

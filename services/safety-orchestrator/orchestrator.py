"""
Safety Orchestrator — fan-out to all scanners with 5s timeout.

Fail-closed: any scanner error, timeout, or open circuit → block the request.

Input scan flow:
  1. Presidio: analyze for PII entities
  2. If PII found: anonymize and store mapping
  3. Fan-out in parallel: LLM Guard + NeMo on (anonymized) text
  4. Merge scores; block if any scanner signals violation

Output scan flow:
  1. LLM Guard scan of output text
  2. If session had PII: de-anonymize via stored mapping
"""

import asyncio
import logging

from pii_store import PiiStore
from scanner_clients import LLMGuardClient, NeMoClient, PresidioClient
from schemas import (
    ScanInputRequest,
    ScanInputResponse,
    ScanOutputRequest,
    ScanOutputResponse,
)

logger = logging.getLogger(__name__)

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

    async def scan_input(self, req: ScanInputRequest) -> ScanInputResponse:
        try:
            return await asyncio.wait_for(self._scan_input_inner(req), timeout=_SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Input scan timed out for session=%s agent=%s", req.session_id, req.agent_name)
            return ScanInputResponse(allowed=False, blocked=True, reason="safety-scan-timeout")
        except Exception as exc:
            logger.error("Input scan error: %s", exc)
            return ScanInputResponse(allowed=False, blocked=True, reason="safety-scan-error")

    async def _scan_input_inner(self, req: ScanInputRequest) -> ScanInputResponse:
        scan_text = req.message
        pii_detected = False
        anonymized_text: str | None = None
        anonymizer_results: dict | None = None

        # Step 1: PII detection + anonymization
        try:
            entities = await self._presidio.analyze(req.message)
            if entities:
                pii_detected = True
                anon_result = await self._presidio.anonymize(req.message, entities)
                anonymized_text = anon_result.get("text", req.message)
                anonymizer_results = anon_result.get("anonymizer_results", {})
                scan_text = anonymized_text

                # Store PII mappings for de-anonymization of outputs
                for entity in entities:
                    await self._pii_store.store_mapping(
                        session_id=req.session_id,
                        agent_name=req.agent_name,
                        original_text=entity.get("text", ""),
                        anonymized_text=entity.get("anonymized", anonymized_text),
                        entity_type=entity.get("entity_type", "UNKNOWN"),
                    )
        except Exception as exc:
            logger.error("Presidio scan failed: %s", exc)
            return ScanInputResponse(allowed=False, blocked=True, reason="presidio-error")

        # Step 2: Parallel fan-out to LLM Guard + NeMo
        guard_result, nemo_result = await asyncio.gather(
            self._llm_guard.scan(scan_text),
            self._nemo.check(scan_text),
            return_exceptions=True,
        )

        scores: dict[str, float] = {}

        if isinstance(guard_result, Exception):
            logger.error("LLM Guard scan failed: %s", guard_result)
            return ScanInputResponse(allowed=False, blocked=True, reason="llmguard-error")
        if isinstance(guard_result, dict):
            score = guard_result.get("risk_score", 0.0)
            scores["llm_guard"] = score
            if guard_result.get("is_blocked", False) or score >= 0.8:
                return ScanInputResponse(
                    allowed=False, blocked=True, reason="llmguard-violation", scores=scores
                )

        if isinstance(nemo_result, Exception):
            logger.error("NeMo scan failed: %s", nemo_result)
            return ScanInputResponse(allowed=False, blocked=True, reason="nemo-error")
        if isinstance(nemo_result, dict):
            score = nemo_result.get("risk_score", 0.0)
            scores["nemo"] = score
            if nemo_result.get("blocked", False) or score >= 0.8:
                return ScanInputResponse(
                    allowed=False, blocked=True, reason="nemo-violation", scores=scores
                )

        return ScanInputResponse(
            allowed=True,
            blocked=False,
            anonymized_message=anonymized_text,
            pii_detected=pii_detected,
            scores=scores,
        )

    async def scan_output(self, req: ScanOutputRequest) -> ScanOutputResponse:
        try:
            return await asyncio.wait_for(self._scan_output_inner(req), timeout=_SCAN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Output scan timed out for session=%s", req.session_id)
            return ScanOutputResponse(allowed=False, blocked=True, reason="safety-scan-timeout")
        except Exception as exc:
            logger.error("Output scan error: %s", exc)
            return ScanOutputResponse(allowed=False, blocked=True, reason="safety-scan-error")

    async def _scan_output_inner(self, req: ScanOutputRequest) -> ScanOutputResponse:
        scores: dict[str, float] = {}

        # Step 1: LLM Guard scan
        try:
            guard_result = await self._llm_guard.scan(req.message)
            score = guard_result.get("risk_score", 0.0)
            scores["llm_guard"] = score
            if guard_result.get("is_blocked", False) or score >= 0.8:
                return ScanOutputResponse(
                    allowed=False, blocked=True, reason="llmguard-output-violation", scores=scores
                )
        except Exception as exc:
            logger.error("LLM Guard output scan failed: %s", exc)
            return ScanOutputResponse(allowed=False, blocked=True, reason="llmguard-error")

        # Step 2: De-anonymize if session had PII
        deanonymized: str | None = None
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

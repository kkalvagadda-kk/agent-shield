"""
Safety Orchestrator client.

Behaviour:
- If AGENTSHIELD_SAFETY_URL is not set → delegates to mock_safety (local dev).
- If AGENTSHIELD_SAFETY_URL is set but unreachable → FAIL CLOSED (raises
  SafetyBlockedError with reason="scanner_error").  We never pass through on a
  real deployment when the scanner is unavailable.
- If the scanner returns blocked=True → raises SafetyBlockedError.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from . import config, mock_safety

logger = logging.getLogger(__name__)


class SafetyBlockedError(Exception):
    """Raised when the Safety Orchestrator blocks a message or is unavailable."""

    def __init__(self, reason: str, scores: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.scores = scores or {}


@dataclass
class ScanInputResult:
    sanitized_text: str
    scores: dict


@dataclass
class ScanOutputResult:
    clean_text: str
    scores: dict


async def scan_input(
    text: str,
    agent_name: str,
    session_id: str | None = None,
    trace_id: str | None = None,
) -> ScanInputResult:
    """Scan user input before passing it to the agent graph.

    Raises:
        SafetyBlockedError: If the text is blocked or the scanner is unavailable.
    """
    if not config.AGENTSHIELD_SAFETY_URL:
        result = await mock_safety.scan_input(text)
        return ScanInputResult(
            sanitized_text=result["sanitized_text"], scores=result["scores"]
        )

    # Wire contract = services/safety-orchestrator/schemas.py ScanInputRequest:
    # `message` + `thread_id` (NOT `text`/`trace_id`). `session_id` is required
    # (non-optional) server-side, so default it to "" rather than send null.
    payload = {
        "session_id": session_id or "",
        "agent_name": agent_name,
        "message": text,
        "thread_id": trace_id,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{config.AGENTSHIELD_SAFETY_URL}/api/v1/scan/input", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise SafetyBlockedError(
            reason="scanner_error",
            scores={"error": str(exc)},
        ) from exc

    if data.get("blocked"):
        raise SafetyBlockedError(
            reason=data.get("reason", "blocked_by_scanner"),
            scores=data.get("scores", {}),
        )

    # ScanInputResponse returns `anonymized_message` (fall back to the original
    # text when the scanner didn't anonymize). SDK-side dataclass keeps its own
    # field name (`sanitized_text`) — only the wire read changes.
    return ScanInputResult(
        sanitized_text=data.get("anonymized_message") or text,
        scores=data.get("scores", {}),
    )


async def scan_output(
    text: str,
    agent_name: str,
    session_id: str | None = None,
    trace_id: str | None = None,
) -> ScanOutputResult:
    """Scan agent output before returning it to the caller.

    Raises:
        SafetyBlockedError: If the text is blocked or the scanner is unavailable.
    """
    if not config.AGENTSHIELD_SAFETY_URL:
        result = await mock_safety.scan_output(text)
        return ScanOutputResult(
            clean_text=result["clean_text"], scores=result["scores"]
        )

    # Wire contract = ScanOutputRequest: `message` + `thread_id` (see scan_input).
    payload = {
        "session_id": session_id or "",
        "agent_name": agent_name,
        "message": text,
        "thread_id": trace_id,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{config.AGENTSHIELD_SAFETY_URL}/api/v1/scan/output", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        raise SafetyBlockedError(
            reason="scanner_error",
            scores={"error": str(exc)},
        ) from exc

    if data.get("blocked"):
        raise SafetyBlockedError(
            reason=data.get("reason", "blocked_by_scanner"),
            scores=data.get("scores", {}),
        )

    # ScanOutputResponse returns `deanonymized_message` (fall back to the original
    # text). SDK-side dataclass keeps `clean_text`.
    return ScanOutputResult(
        clean_text=data.get("deanonymized_message") or text,
        scores=data.get("scores", {}),
    )


async def deanonymize_args(
    args: dict,
    agent_name: str,
    session_id: str,
) -> dict:
    """De-anonymize a tool's arguments before an ALLOWED de-anon tool call.

    Decision 27: when OPA returns allow_deanonymize, governed_tool calls this to
    substitute anonymized placeholders in the tool arguments back to the original
    PII (using the per-session PiiStore mappings the orchestrator holds).

    FAIL-OPEN: any failure returns ``args`` UNCHANGED (still anonymized) and never
    raises — a de-anon outage must degrade to "the tool sees the placeholder", not
    break the tool call. (Contrast scan_input/scan_output, which fail CLOSED: a
    scanner outage there blocks. De-anon is an enrichment, not a safety gate.)
    """
    if not config.AGENTSHIELD_SAFETY_URL:
        return await mock_safety.deanonymize_args(args, agent_name, session_id)

    payload = {
        "session_id": session_id or "",
        "agent_name": agent_name,
        "args": args,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{config.AGENTSHIELD_SAFETY_URL}/api/v1/deanonymize/args", json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        deanon = data.get("args")
        return deanon if isinstance(deanon, dict) else args
    except Exception as exc:  # fail-open — de-anon is best-effort enrichment
        logger.warning(
            "deanonymize_args failed for agent '%s' (session '%s'): %s — passing args through unchanged",
            agent_name,
            session_id,
            exc,
        )
        return args

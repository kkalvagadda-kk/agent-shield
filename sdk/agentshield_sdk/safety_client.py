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

from dataclasses import dataclass

import httpx

from . import config, mock_safety


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

    payload = {
        "text": text,
        "agent_name": agent_name,
        "session_id": session_id,
        "trace_id": trace_id,
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

    return ScanInputResult(
        sanitized_text=data.get("sanitized_text", text),
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

    payload = {
        "text": text,
        "agent_name": agent_name,
        "session_id": session_id,
        "trace_id": trace_id,
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

    return ScanOutputResult(
        clean_text=data.get("clean_text", text),
        scores=data.get("scores", {}),
    )

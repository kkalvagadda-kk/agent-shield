"""Shared durable-run dispatch — the ONE place a durable run is handed to the
declarative-runner's /run endpoint.

Both the sandbox playground path (`routers/playground.py`) and the production
internal-run path (`routers/internal.py`) call this. The only per-caller
differences — the step-update callback URL and which run-status table to mark
failed — are explicit parameters, not sniffed. Parity rule (the 2026-07-11 HITL
retro root cause): the `/run` POST literal lives here and nowhere else, so the
sandbox and production paths can never drift.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def default_runner_url() -> str:
    return os.getenv(
        "DECLARATIVE_RUNNER_URL",
        "http://declarative-runner.agentshield-platform.svc.cluster.local:8080",
    )


def registry_internal_base() -> str:
    return os.getenv(
        "REGISTRY_API_INTERNAL_URL",
        "http://registry-api.agentshield-platform.svc.cluster.local:8000",
    )


async def dispatch_durable_run(
    *,
    run_id: str,
    agent_name: str,
    input_payload: dict | None,
    callback_url: str,
    runner_url: str | None = None,
    timeout_s: float = 10.0,
) -> tuple[bool, str | None]:
    """POST a durable run to the declarative-runner /run. Returns (accepted, error).

    Never raises — a dispatch failure returns (False, "<reason>") so the caller can
    mark ITS OWN run row failed (PlaygroundRun for sandbox, AgentRun for production).
    Fire-and-forget: step progress + terminal status arrive asynchronously at
    ``callback_url``.
    """
    url = f"{(runner_url or default_runner_url()).rstrip('/')}/run"
    body = {
        "agent_name": agent_name,
        "run_id": run_id,
        "input_payload": input_payload or {},
        "callback_url": callback_url,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=body)
        if resp.status_code in (200, 201, 202):
            logger.info(
                "dispatch_durable_run: accepted run=%s agent=%s -> %s", run_id, agent_name, url
            )
            return True, None
        err = f"runner returned {resp.status_code}: {resp.text[:200]}"
        logger.warning("dispatch_durable_run: %s (run=%s)", err, run_id)
        return False, err
    except Exception as exc:  # network / pod not ready
        logger.error("dispatch_durable_run: failed run=%s: %s", run_id, exc)
        return False, f"dispatch failed: {exc}"

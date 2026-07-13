"""Interrupted-run discovery for durable-run crash recovery.

B3 (WS-1) consolidation: the graph state is checkpointed by the LangGraph
PostgresSaver (keyed by thread_id = run_id) — the SINGLE checkpoint-of-record.
The old `save_checkpoint`/`load_checkpoint` that duplicated graph state into the
AgentRun's `trigger_payload` are removed; crash recovery re-enters from the
PostgresSaver via `agentshield_sdk.durable.resume_durable`. This module now only
answers "which runs for this agent are still 'running' and should be re-entered
on startup?".
"""
from __future__ import annotations

import logging

import httpx

import config as cfg

logger = logging.getLogger(__name__)


async def list_interrupted_runs(agent_name: str) -> list[str]:
    """Find runs still in 'running' status for this agent (candidates for resume)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{cfg.REGISTRY_API_URL}/api/v1/agent-runs",
                params={"agent_name": agent_name, "status": "running", "limit": 20},
            )
            if resp.status_code != 200:
                return []
            return [r["id"] for r in resp.json()]
    except Exception as exc:
        logger.warning("list_interrupted_runs failed: %s", exc)
        return []

"""
Checkpoint persistence for durable runs.

Serializes run state to Postgres via registry-api so runs can
survive pod restarts. The checkpoint is stored in the AgentRun's
trigger_payload JSONB column (reused as checkpoint storage for
durable runs since trigger_payload is unused in that context).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

import config as cfg

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    run_id: str
    last_completed_step: int
    state: dict[str, Any]


async def save_checkpoint(run_id: str, last_completed_step: int, state: dict[str, Any]) -> None:
    """Persist checkpoint to registry-api."""
    payload = {
        "trigger_payload": {
            "_checkpoint": {
                "last_completed_step": last_completed_step,
                "state": state,
            }
        }
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{cfg.REGISTRY_API_URL}/api/v1/agent-runs/{run_id}",
                json=payload,
            )
    except Exception as exc:
        logger.warning("save_checkpoint failed for run %s: %s", run_id, exc)


async def load_checkpoint(run_id: str) -> Checkpoint | None:
    """Load checkpoint from registry-api. Returns None if no checkpoint exists."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{cfg.REGISTRY_API_URL}/api/v1/agent-runs/{run_id}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            tp = data.get("trigger_payload") or {}
            cp = tp.get("_checkpoint")
            if not cp:
                return None
            return Checkpoint(
                run_id=run_id,
                last_completed_step=cp.get("last_completed_step", 0),
                state=cp.get("state", {}),
            )
    except Exception as exc:
        logger.warning("load_checkpoint failed for run %s: %s", run_id, exc)
        return None


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

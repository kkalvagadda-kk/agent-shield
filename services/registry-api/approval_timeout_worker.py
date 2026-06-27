"""
Registry API — Approval Timeout Worker.

Background asyncio task that periodically sweeps the approvals table for pending
rows whose expires_at has passed, atomically marks them as 'timed_out', then
notifies the agent pod via POST /resume/{thread_id} so the paused LangGraph graph
can clean up gracefully.

Race safety: uses a single UPDATE ... WHERE status='pending' AND expires_at < now()
RETURNING ... statement so only one worker wins even if deploy-controller's
timeout_worker.py runs simultaneously.

Agent pod URL is derived from the approval's agent_name + team fields:
    http://{agent_name}-production.agents-{team}.svc.cluster.local:8080
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import text, update

from db import AsyncSessionLocal
from models import Approval

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 60
_RESUME_TIMEOUT_SECONDS = 5


def _agent_pod_url(agent_name: str, team: str, environment: str = "production") -> str:
    """Derive the agent pod's K8s cluster-internal service URL."""
    svc_name = f"{agent_name}-{environment}"
    namespace = f"agents-{team}"
    return f"http://{svc_name}.{namespace}.svc.cluster.local:8080"


async def _notify_agent(agent_name: str, team: str, thread_id: str, approval_id: str) -> None:
    """POST /resume/{thread_id} to the agent pod with a timed_out decision."""
    base_url = _agent_pod_url(agent_name, team)
    url = f"{base_url}/resume/{thread_id}"
    payload = {
        "decision": "timed_out",
        "reviewer_id": None,
        "reason": "Approval window expired",
    }
    try:
        async with httpx.AsyncClient(timeout=_RESUME_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 404):
                # 404 = thread already completed (race with normal completion), safe to ignore
                logger.info(
                    "timeout_worker: notified agent pod thread_id=%s approval_id=%s status=%d",
                    thread_id, approval_id, resp.status_code,
                )
            else:
                logger.warning(
                    "timeout_worker: unexpected status %d notifying agent_name=%s thread_id=%s",
                    resp.status_code, agent_name, thread_id,
                )
    except httpx.RequestError as exc:
        # Agent pod may not be running (crashed, scaled to 0, etc.) — log and move on.
        logger.warning(
            "timeout_worker: could not reach agent pod agent_name=%s thread_id=%s error=%s",
            agent_name, thread_id, exc,
        )


async def _sweep_once() -> int:
    """Mark expired pending approvals as timed_out and notify their agent pods.

    Returns:
        Number of approvals timed out in this sweep.
    """
    now = datetime.now(tz=timezone.utc)

    async with AsyncSessionLocal() as db:
        # Atomic: only rows still 'pending' and past expiry are updated.
        # RETURNING lets us notify agents without a second query.
        stmt = (
            update(Approval)
            .where(
                Approval.status == "pending",
                Approval.expires_at < now,
            )
            .values(status="timed_out")
            .returning(
                Approval.id,
                Approval.thread_id,
                Approval.agent_name,
                Approval.team,
            )
        )
        result = await db.execute(stmt)
        timed_out_rows = result.fetchall()
        await db.commit()

    if not timed_out_rows:
        return 0

    logger.info("timeout_worker: timed out %d approval(s)", len(timed_out_rows))

    # Notify each agent pod concurrently (fire-and-forget; failures are logged, not raised).
    notify_tasks = [
        _notify_agent(
            agent_name=row.agent_name,
            team=row.team,
            thread_id=row.thread_id,
            approval_id=str(row.id),
        )
        for row in timed_out_rows
    ]
    await asyncio.gather(*notify_tasks, return_exceptions=True)

    return len(timed_out_rows)


async def approval_timeout_worker() -> None:
    """Long-running background task. Runs until the event loop is cancelled."""
    logger.info(
        "approval_timeout_worker: starting (poll_interval=%ds)", _POLL_INTERVAL_SECONDS
    )
    while True:
        try:
            count = await _sweep_once()
            if count:
                logger.info("approval_timeout_worker: swept %d expired approval(s)", count)
        except asyncio.CancelledError:
            logger.info("approval_timeout_worker: cancelled, shutting down")
            return
        except Exception as exc:
            # Never let a sweep failure kill the worker; log and continue.
            logger.error("approval_timeout_worker: sweep error: %s", exc, exc_info=True)

        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

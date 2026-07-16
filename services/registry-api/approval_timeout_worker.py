"""
Registry API — Approval Timeout Worker.

Background asyncio task that periodically sweeps the approvals table for pending
rows whose expires_at has passed, atomically marks them as 'timed_out', then
notifies the agent pod via POST /resume/{thread_id} so the paused LangGraph graph
can clean up gracefully.

Race safety: uses a single UPDATE ... WHERE status='pending' AND expires_at < now()
RETURNING ... statement so only one worker wins even if deploy-controller's
timeout_worker.py runs simultaneously.

Agent pod URL comes from `agent_endpoints.agent_pod_base(agent_name, team, environment)`
— the ONE definition. `environment` is resolved per agent, never assumed: this module
used to hardcode `-production`, so every sandbox approval's resume POST went to a
Service that does not exist, the RequestError was swallowed to a warning below, and the
approval was still marked resolved (TODO-8).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import text, update

from db import AsyncSessionLocal
from models import AgentRun, Approval, PlaygroundRun, RunStep

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 60
_RESUME_TIMEOUT_SECONDS = 5
_DURABLE_RUN_TTL_MINUTES = 10


from agent_endpoints import agent_pod_base as _agent_pod_url


async def _notify_agent(agent_name: str, team: str, thread_id: str, approval_id: str) -> None:
    """POST /resume/{thread_id} to the agent pod with a timed_out decision."""
    # Resolve the agent's ACTUAL running environment — a sandbox agent has no
    # `{agent}-production` Service, so the old default silently DNS-failed and the
    # timeout was recorded without the agent ever being told.
    from workflow_orchestrator import _resolve_agent_environment

    environment = await _resolve_agent_environment(agent_name)
    base_url = _agent_pod_url(agent_name, team, environment)
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

    # Cancel production durable runs whose approval just timed out
    await _cancel_runs_for_timed_out_approvals(
        [(str(row.id), row.agent_name) for row in timed_out_rows]
    )

    return len(timed_out_rows)


async def _cancel_runs_for_timed_out_approvals(timed_out: list[tuple[str, str]]) -> None:
    """Cancel AgentRun + RunStep rows linked to timed-out approvals."""
    if not timed_out:
        return

    from sqlalchemy import select
    import uuid as _uuid

    now = datetime.now(tz=timezone.utc)
    async with AsyncSessionLocal() as db:
        for approval_id_str, agent_name in timed_out:
            try:
                aid = _uuid.UUID(approval_id_str)
                step_result = await db.execute(
                    select(RunStep).where(RunStep.approval_id == aid)
                )
                step = step_result.scalar_one_or_none()
                if step:
                    step.status = "cancelled"
                    step.completed_at = now
                    step.error_message = "Approval timed out"
                    # Cancel the parent run
                    run_result = await db.execute(
                        select(AgentRun).where(AgentRun.id == step.run_id)
                    )
                    run = run_result.scalar_one_or_none()
                    if run and run.status in ("running", "awaiting_approval"):
                        run.status = "cancelled"
                        run.completed_at = now
                        logger.info(
                            "timeout_worker: cancelled run %s due to timed-out approval %s",
                            run.id, approval_id_str,
                        )
            except Exception as exc:
                logger.warning("_cancel_runs_for_timed_out_approvals: %s: %s", approval_id_str, exc)
        await db.commit()


async def _sweep_stale_durable_runs() -> int:
    """Cancel durable playground runs that exceed the wall-clock TTL."""
    from datetime import timedelta
    from sqlalchemy import and_

    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=_DURABLE_RUN_TTL_MINUTES)

    async with AsyncSessionLocal() as db:
        stmt = (
            update(PlaygroundRun)
            .where(
                and_(
                    PlaygroundRun.execution_shape == "durable",
                    PlaygroundRun.status.in_(["running", "blocked"]),
                    PlaygroundRun.started_at < cutoff,
                )
            )
            .values(status="cancelled", completed_at=datetime.now(tz=timezone.utc))
            .returning(PlaygroundRun.id)
        )
        result = await db.execute(stmt)
        cancelled = result.fetchall()
        await db.commit()

    if cancelled:
        logger.info("timeout_worker: cancelled %d stale durable run(s)", len(cancelled))
    return len(cancelled)


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
            durable_count = await _sweep_stale_durable_runs()
            if durable_count:
                logger.info("approval_timeout_worker: cancelled %d stale durable run(s)", durable_count)
        except asyncio.CancelledError:
            logger.info("approval_timeout_worker: cancelled, shutting down")
            return
        except Exception as exc:
            logger.error("approval_timeout_worker: sweep error: %s", exc, exc_info=True)

        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

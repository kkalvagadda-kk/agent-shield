"""
Cost backfill sweep — persists LLM $ + token usage onto agent_runs.

Langfuse already computes per-LLM-call cost and token counts on every OTEL
``GENERATION`` observation (once ingested). Nothing, however, copies that back
into the platform's own ``agent_runs.cost_usd`` / ``prompt_tokens`` /
``completion_tokens`` columns — so every cost query returned 0.

Rather than sprinkle fire-and-forget cost fetches into each of the ~three run
completion paths (chat, the SDK/runner PATCH callback, scheduled/workflow
dispatch) — which also races Langfuse ingestion (spans aren't in Langfuse the
instant a run completes) — a single periodic sweep handles every run type
uniformly:

  every INTERVAL seconds → find recently-completed runs that have a trace but no
  cost yet → sum their GENERATION cost/tokens from Langfuse → write it back.

Idempotent (only touches ``cost_usd IS NULL`` rows) so it is safe to run on
every registry-api replica concurrently — a double-fetch just wastes one
Langfuse read. Bounded to runs completed in the last DAY so a run whose trace
never carries an LLM call (e.g. a blocked run) is eventually abandoned rather
than retried forever.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 60
LOOKBACK_HOURS = 24
BATCH_LIMIT = 100


async def _sweep_once() -> int:
    """Backfill one batch of uncosted runs. Returns count updated."""
    from db import AsyncSessionLocal
    from models import AgentRun
    from observability_backend import get_observability_backend

    backend = get_observability_backend()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    updated = 0
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(AgentRun)
            .where(
                AgentRun.status.in_(("completed", "failed", "blocked", "cancelled")),
                AgentRun.langfuse_trace_id.isnot(None),
                AgentRun.cost_usd.is_(None),
                AgentRun.completed_at >= cutoff,
            )
            .order_by(AgentRun.completed_at.desc())
            .limit(BATCH_LIMIT)
        )).scalars().all()

        for run in rows:
            # Backend read is blocking (urllib) — keep it off the event loop.
            usage = await asyncio.to_thread(backend.get_run_cost, run.langfuse_trace_id)
            if not usage or usage.cost_usd is None:
                continue  # not ingested yet, or no LLM cost on this trace
            run.cost_usd = usage.cost_usd
            if usage.prompt_tokens is not None:
                run.prompt_tokens = usage.prompt_tokens
            if usage.completion_tokens is not None:
                run.completion_tokens = usage.completion_tokens
            updated += 1

        if updated:
            await session.commit()
    return updated


async def cost_backfill_loop() -> None:
    """Long-running background task — sweep on an interval, forever."""
    logger.info("cost backfill sweep started (interval=%ss)", INTERVAL_SECONDS)
    while True:
        try:
            n = await _sweep_once()
            if n:
                logger.info("cost backfill: wrote cost for %d run(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — never let the loop die
            logger.warning("cost backfill sweep error: %s", exc)
        await asyncio.sleep(INTERVAL_SECONDS)

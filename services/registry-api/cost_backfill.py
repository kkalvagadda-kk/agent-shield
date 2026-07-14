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
# A workflow parent run makes no LLM calls itself — its cost is the sum of its
# member (child) runs, which are costed asynchronously by the leaf sweep. Give the
# children a couple of sweeps to settle before rolling the parent up so we never
# persist a partial sum (cost_usd is written once).
PARENT_SETTLE_SECONDS = 120


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


async def _rollup_workflow_parents() -> int:
    """Roll member (child) costs up onto their workflow PARENT run.

    A workflow parent orchestrates members but issues no LLM calls itself, so its
    own Langfuse trace has no GENERATION cost — the leaf sweep above can never cost
    it. Instead its cost is the sum of its children's `cost_usd` (children link via
    `parent_run_id`). We only roll up once EVERY child is terminal AND none is still
    awaiting its own leaf backfill — otherwise the sum would be partial and, because
    cost_usd is written once, never corrected. Returns count updated."""
    from db import AsyncSessionLocal
    from models import AgentRun

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    settle = now - timedelta(seconds=PARENT_SETTLE_SECONDS)
    _TERMINAL = ("completed", "failed", "cancelled")
    updated = 0
    async with AsyncSessionLocal() as session:
        parents = (await session.execute(
            select(AgentRun)
            .where(
                AgentRun.workflow_id.isnot(None),   # a workflow parent run
                AgentRun.cost_usd.is_(None),
                AgentRun.status.in_(_TERMINAL),
                AgentRun.completed_at >= cutoff,
                AgentRun.completed_at <= settle,
            )
            .order_by(AgentRun.completed_at.desc())
            .limit(BATCH_LIMIT)
        )).scalars().all()

        for parent in parents:
            kids = (await session.execute(
                select(AgentRun).where(AgentRun.parent_run_id == parent.id)
            )).scalars().all()
            if not kids:
                continue
            # Wait until all children are terminal…
            if any(k.status not in _TERMINAL for k in kids):
                continue
            # …and none is still eligible for its own leaf backfill (has a trace, no
            # cost yet, still inside the lookback window) — else the sum is partial.
            if any(k.langfuse_trace_id is not None and k.cost_usd is None
                   and k.completed_at is not None and k.completed_at >= cutoff
                   for k in kids):
                continue
            total = sum(k.cost_usd or 0.0 for k in kids)
            if total <= 0:
                continue  # no child carried LLM cost (yet) — leave NULL, retry/abandon
            parent.cost_usd = round(total, 6)
            pt = sum(k.prompt_tokens or 0 for k in kids)
            ct = sum(k.completion_tokens or 0 for k in kids)
            if pt:
                parent.prompt_tokens = pt
            if ct:
                parent.completion_tokens = ct
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
            # Roll member costs onto workflow parents (after leaves are costed).
            p = await _rollup_workflow_parents()
            if p:
                logger.info("cost backfill: rolled up cost for %d workflow parent(s)", p)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — never let the loop die
            logger.warning("cost backfill sweep error: %s", exc)
        await asyncio.sleep(INTERVAL_SECONDS)

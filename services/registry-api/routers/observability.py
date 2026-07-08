"""
Observability router — unified traces list + dashboard aggregation.

  GET /api/v1/observability/traces     — paginated traces list (agent_runs + playground_runs)
  GET /api/v1/observability/traces/{trace_id} — full trace detail from Langfuse
  GET /api/v1/observability/dashboard  — aggregated metrics (latency, scores, cost)
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func, text, case, literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Agent, AgentRun, PlaygroundRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TraceSummary(BaseModel):
    id: str
    agent_name: str
    status: str
    trigger_type: str | None = None
    context: str
    latency_ms: int | None = None
    cost_usd: float | None = None
    judge_score: float | None = None
    started_at: datetime
    completed_at: datetime | None = None
    trace_id: str | None = None
    trace_url: str | None = None
    run_by: str | None = None


class TracesListResponse(BaseModel):
    items: list[TraceSummary]
    total: int
    has_more: bool


class TimeseriesPoint(BaseModel):
    timestamp: datetime
    p50: float | None = None
    p95: float | None = None
    total_usd: float | None = None
    count: int = 0


class HistogramBucket(BaseModel):
    bucket: str
    count: int


class StatusCount(BaseModel):
    status: str
    count: int


class AgentBlockRate(BaseModel):
    agent_name: str
    total_runs: int
    blocked_runs: int


class DashboardData(BaseModel):
    latency_series: list[TimeseriesPoint]
    score_histogram: list[HistogramBucket]
    status_counts: list[StatusCount]
    cost_series: list[TimeseriesPoint]
    safety_blocks: list[AgentBlockRate]
    total_runs: int
    total_cost_usd: float


# ---------------------------------------------------------------------------
# GET /observability/traces
# ---------------------------------------------------------------------------

@router.get("/traces", response_model=TracesListResponse)
async def list_traces(
    agent_name: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    trigger_type: Optional[str] = Query(None),
    context: Optional[str] = Query(None, description="playground|production|all"),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    x_user_team: str = Header(default="", alias="X-User-Team"),
    db: AsyncSession = Depends(get_db),
) -> TracesListResponse:
    """List traces across playground and agent runs, team-scoped."""
    if not x_user_team:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-User-Team header required")

    lf_public_url = os.getenv("LANGFUSE_PUBLIC_URL", "")
    lf_project_id = os.getenv("LANGFUSE_PROJECT_ID", "")

    items: list[TraceSummary] = []

    # --- Query agent_runs (has team column directly) ---
    if context != "playground":
        aq = select(AgentRun).where(AgentRun.team == x_user_team)
        if agent_name:
            aq = aq.where(AgentRun.agent_name == agent_name)
        if status_filter:
            aq = aq.where(AgentRun.status == status_filter)
        if trigger_type:
            aq = aq.where(AgentRun.trigger_type == trigger_type)
        if context and context != "all":
            aq = aq.where(AgentRun.context == context)
        if from_date:
            aq = aq.where(AgentRun.started_at >= from_date)
        if to_date:
            aq = aq.where(AgentRun.started_at <= to_date)
        # Exclude child workflow runs (show parents only)
        aq = aq.where(AgentRun.parent_run_id.is_(None))
        aq = aq.order_by(AgentRun.started_at.desc()).limit(limit + 1)
        agent_rows = list((await db.execute(aq)).scalars().all())

        for r in agent_rows:
            trace_url = None
            if r.langfuse_trace_id and lf_public_url and lf_project_id:
                trace_url = f"{lf_public_url}/project/{lf_project_id}/traces/{r.langfuse_trace_id}"
            items.append(TraceSummary(
                id=str(r.id),
                agent_name=r.agent_name,
                status=r.status,
                trigger_type=r.trigger_type,
                context=r.context or "production",
                latency_ms=r.latency_ms,
                cost_usd=float(r.cost_usd) if r.cost_usd else None,
                judge_score=float(r.judge_score) if r.judge_score is not None else None,
                started_at=r.started_at,
                completed_at=r.completed_at,
                trace_id=r.langfuse_trace_id,
                trace_url=trace_url,
                run_by=r.run_by,
            ))

    # --- Query playground_runs (team-scoped via agents table) ---
    if context != "production":
        pq = select(PlaygroundRun).where(PlaygroundRun.agent_name.in_(
            select(Agent.name).where(Agent.team == x_user_team)
        ))
        if agent_name:
            pq = pq.where(PlaygroundRun.agent_name == agent_name)
        if status_filter:
            pq = pq.where(PlaygroundRun.status == status_filter)
        if from_date:
            pq = pq.where(PlaygroundRun.started_at >= from_date)
        if to_date:
            pq = pq.where(PlaygroundRun.started_at <= to_date)
        pq = pq.order_by(PlaygroundRun.started_at.desc()).limit(limit + 1)
        pg_rows = list((await db.execute(pq)).scalars().all())

        for r in pg_rows:
            trace_url = None
            if r.langfuse_trace_id and lf_public_url and lf_project_id:
                trace_url = f"{lf_public_url}/project/{lf_project_id}/traces/{r.langfuse_trace_id}"
            latency_ms = None
            if r.started_at and r.completed_at:
                latency_ms = int((r.completed_at - r.started_at).total_seconds() * 1000)
            items.append(TraceSummary(
                id=str(r.id),
                agent_name=r.agent_name,
                status=r.status,
                trigger_type=r.trigger_type,
                context="playground",
                latency_ms=latency_ms,
                cost_usd=None,
                judge_score=float(r.judge_score) if r.judge_score is not None else None,
                started_at=r.started_at or r.completed_at or datetime.now(timezone.utc),
                completed_at=r.completed_at,
                trace_id=r.langfuse_trace_id,
                trace_url=trace_url,
                run_by=r.user_id,
            ))

    # Sort merged results by started_at desc, paginate
    items.sort(key=lambda x: x.started_at, reverse=True)
    total = len(items)
    has_more = total > limit
    items = items[:limit]

    return TracesListResponse(items=items, total=total, has_more=has_more)


# ---------------------------------------------------------------------------
# GET /observability/traces/{trace_id} — fetch full trace from Langfuse
# Same pattern as GET /api/v1/playground/traces/{trace_id}
# ---------------------------------------------------------------------------

@router.get("/traces/{trace_id}")
async def get_trace_detail(
    trace_id: str,
    x_user_team: str = Header(default="", alias="X-User-Team"),
):
    """Fetch full trace (observations/spans) from Langfuse via service creds.

    Follows the same urllib+Basic-auth pattern as playground.get_trace_by_id.
    """
    import base64
    import json as _json
    import urllib.error
    import urllib.request as urlreq

    lf_host = os.getenv("LANGFUSE_HOST", "http://agentshield-langfuse-web:3000")
    lf_public_url = os.getenv("LANGFUSE_PUBLIC_URL", "")
    lf_project_id = os.getenv("LANGFUSE_PROJECT_ID", "")
    lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")

    if lf_public_url and lf_project_id:
        trace_url = f"{lf_public_url}/project/{lf_project_id}/traces/{trace_id}"
    else:
        trace_url = None

    trace_data: dict = {}

    if lf_pk and lf_sk:
        creds = base64.b64encode(f"{lf_pk}:{lf_sk}".encode()).decode()
        try:
            req = urlreq.Request(
                f"{lf_host}/api/public/traces/{trace_id}",
                headers={"Authorization": f"Basic {creds}"},
            )
            with urlreq.urlopen(req, timeout=5) as r:
                trace_data = _json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                trace_data = {"warning": "trace not yet ingested by Langfuse"}
            else:
                logger.debug("Langfuse trace fetch error %s: %s", exc.code, exc)
        except Exception as exc:
            logger.debug("Langfuse trace fetch failed: %s", exc)

    return {
        "trace_id": trace_id,
        "trace_url": trace_url,
        "langfuse": trace_data,
    }


# ---------------------------------------------------------------------------
# GET /observability/dashboard — aggregated metrics
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard(
    agent_name: Optional[str] = Query(None),
    period: str = Query("7d", description="7d|30d|custom"),
    from_date: Optional[datetime] = Query(None),
    to_date: Optional[datetime] = Query(None),
    x_user_team: str = Header(default="", alias="X-User-Team"),
    db: AsyncSession = Depends(get_db),
) -> DashboardData:
    """Aggregated observability metrics for the team's runs."""
    if not x_user_team:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-User-Team header required")

    # Determine time window
    if period == "7d" and not from_date:
        from_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) - timedelta(days=7)
    elif period == "30d" and not from_date:
        from_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0) - timedelta(days=30)

    base_filter = [
        AgentRun.team == x_user_team,
        AgentRun.parent_run_id.is_(None),
    ]
    if from_date:
        base_filter.append(AgentRun.started_at >= from_date)
    if to_date:
        base_filter.append(AgentRun.started_at <= to_date)
    if agent_name:
        base_filter.append(AgentRun.agent_name == agent_name)

    # --- Latency timeseries (hourly P50/P95) ---
    latency_q = (
        select(
            func.date_trunc("hour", AgentRun.started_at).label("ts"),
            func.percentile_cont(0.5).within_group(AgentRun.latency_ms).label("p50"),
            func.percentile_cont(0.95).within_group(AgentRun.latency_ms).label("p95"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .where(AgentRun.latency_ms.isnot(None))
        .group_by(text("1"))
        .order_by(text("1"))
    )
    lat_rows = (await db.execute(latency_q)).all()
    latency_series = [
        TimeseriesPoint(timestamp=r.ts, p50=r.p50, p95=r.p95, count=r.cnt)
        for r in lat_rows
    ]

    # --- Score histogram ---
    score_q = (
        select(
            func.width_bucket(AgentRun.judge_score, 0, 1, 10).label("bucket"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .where(AgentRun.judge_score.isnot(None))
        .group_by(text("1"))
        .order_by(text("1"))
    )
    # For now agent_runs might not have judge_score column yet — guard with try
    score_histogram: list[HistogramBucket] = []
    try:
        score_rows = (await db.execute(score_q)).all()
        for r in score_rows:
            lo = (r.bucket - 1) * 0.1
            hi = r.bucket * 0.1
            score_histogram.append(HistogramBucket(bucket=f"{lo:.1f}-{hi:.1f}", count=r.cnt))
    except Exception:
        pass

    # --- Status counts ---
    status_q = (
        select(AgentRun.status, func.count().label("cnt"))
        .where(*base_filter)
        .group_by(AgentRun.status)
    )
    status_rows = (await db.execute(status_q)).all()
    status_counts = [StatusCount(status=r.status, count=r.cnt) for r in status_rows]

    # --- Cost timeseries ---
    cost_q = (
        select(
            func.date_trunc("day", AgentRun.started_at).label("ts"),
            func.sum(AgentRun.cost_usd).label("total_usd"),
            func.count().label("cnt"),
        )
        .where(*base_filter)
        .where(AgentRun.cost_usd.isnot(None))
        .group_by(text("1"))
        .order_by(text("1"))
    )
    cost_rows = (await db.execute(cost_q)).all()
    cost_series = [
        TimeseriesPoint(timestamp=r.ts, total_usd=float(r.total_usd) if r.total_usd else 0, count=r.cnt)
        for r in cost_rows
    ]

    # --- Safety blocks per agent ---
    safety_q = (
        select(
            AgentRun.agent_name,
            func.count().label("total"),
            func.count().filter(AgentRun.status == "blocked").label("blocked"),
        )
        .where(*base_filter)
        .group_by(AgentRun.agent_name)
        .having(func.count().filter(AgentRun.status == "blocked") > 0)
    )
    safety_rows = (await db.execute(safety_q)).all()
    safety_blocks = [
        AgentBlockRate(agent_name=r.agent_name, total_runs=r.total, blocked_runs=r.blocked)
        for r in safety_rows
    ]

    # --- Totals ---
    totals_q = select(
        func.count().label("total"),
        func.coalesce(func.sum(AgentRun.cost_usd), 0).label("cost"),
    ).where(*base_filter)
    totals = (await db.execute(totals_q)).one()

    return DashboardData(
        latency_series=latency_series,
        score_histogram=score_histogram,
        status_counts=status_counts,
        cost_series=cost_series,
        safety_blocks=safety_blocks,
        total_runs=totals.total,
        total_cost_usd=float(totals.cost),
    )

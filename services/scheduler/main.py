"""
AgentShield Scheduler Service.

Fires published scheduled agents on their cron expression. Runs HA with 2+
replicas; a Postgres advisory lock (see ha.py) ensures each fire dispatches
exactly once. On fire, POSTs to registry-api's cluster-internal run-start
endpoint.

Endpoints:
  GET /health  — liveness (also reports scheduled job count)
"""
from __future__ import annotations

import logging
import os
import threading
import time

import httpx
import psycopg2
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

import ha
import service_token

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("scheduler")

REGISTRY_API_URL = os.getenv("REGISTRY_API_URL", "http://agentshield-registry-api:8000")
RELOAD_INTERVAL_SECONDS = int(os.getenv("RELOAD_INTERVAL_SECONDS", "60"))


def _pg_dsn() -> str:
    """psycopg2-compatible DSN (strip any SQLAlchemy +asyncpg driver suffix)."""
    dsn = os.getenv("DATABASE_URL", "")
    return dsn.replace("+asyncpg", "").replace("postgresql+psycopg2", "postgresql")


scheduler = BackgroundScheduler(timezone="UTC")
app = FastAPI(title="AgentShield Scheduler", version="0.1.0")

# Track registered jobs so reload can diff: trigger_id -> cron_expression
_registered: dict[str, str] = {}


def _fetch_schedule_triggers() -> list[tuple[str, str, str, str | None, str | None]]:
    """Return [(trigger_id, cron_expression, timezone, agent_name_or_none, workflow_id_or_none), ...].

    agent_name is set and workflow_id is None for agent schedule triggers;
    workflow_id is set and agent_name is None for workflow schedule triggers.
    Uses UNION ALL so both shapes are returned in a single query.
    """
    rows: list[tuple[str, str, str, str | None, str | None]] = []
    try:
        conn = psycopg2.connect(_pg_dsn())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    -- Liveness comes from the `trigger_liveness` VIEW (migration 0077),
                    -- the ONE definition shared with the event-gateway and registry-api.
                    -- It is not restated here on purpose: stating it independently is
                    -- how this filter was wrong twice in one day —
                    -- `w.status='published'` matched nothing, then
                    -- `w.publish_status='published'` was too strict. Three images, one
                    -- rule, in the only place all three already reach.
                    --
                    -- `_sync_jobs` removes any job whose trigger stops appearing here, so
                    -- an artifact archived mid-flight is unregistered within one reload
                    -- interval (RELOAD_INTERVAL_SECONDS, default 60) — the accepted bound.
                    SELECT id::text,
                           cron_expression,
                           COALESCE(timezone, 'UTC'),
                           CASE WHEN artifact_kind = 'agent'    THEN artifact_name END,
                           CASE WHEN artifact_kind = 'workflow' THEN artifact_id::text END
                    FROM trigger_liveness
                    WHERE trigger_type = 'schedule'
                      AND enabled
                      AND artifact_is_live
                      AND cron_expression IS NOT NULL
                    """
                )
                rows = [(r[0], r[1], r[2], r[3], r[4]) for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("failed to fetch schedule triggers: %s", exc)
    return rows


def _on_fire(trigger_id: str, agent_name: str | None, workflow_id: str | None) -> None:
    """APScheduler callback: claim the fire (HA) then dispatch to registry-api.

    Exactly one of agent_name / workflow_id is non-None, matching the trigger row.
    The POST body sent to /internal/runs/start differs accordingly.
    """
    fire_epoch = int(time.time() // 60 * 60)  # minute bucket for cross-replica dedup
    try:
        conn = psycopg2.connect(_pg_dsn())
    except Exception as exc:
        logger.error("fire %s: cannot connect to Postgres for lock: %s", trigger_id, exc)
        return
    # The advisory lock is session-scoped: it stays held on `conn` through the
    # dispatch below and releases when we close the connection in `finally`
    # (i.e. "release after dispatch", per the HA design).
    if not ha.try_claim_fire(conn, trigger_id, fire_epoch):
        conn.close()
        return  # another replica dispatches this fire

    label = agent_name if agent_name else f"workflow:{workflow_id}"
    try:
        # Prove WHICH service is firing before building the request (identity P3, §4.5).
        # `run_by` below is now a transport label only — registry-api derives the acting
        # principal from `resolve_principal` and the SERVICE from this token's `azp`, so a
        # caller who types "serviceaccount:scheduler" without the secret gets a 401.
        #
        # Minted BEFORE the body and before the POST: a mint failure must skip the fire
        # entirely rather than send an unauthenticated request that registry-api would
        # reject with a 401 the operator then reads as "the run was refused" instead of
        # "the scheduler could not authenticate".
        headers = service_token.auth_header()

        if agent_name is not None:
            body: dict = {
                "agent_name": agent_name,
                "trigger_type": "schedule",
                "trigger_id": trigger_id,
                "run_by": "serviceaccount:scheduler",
            }
        else:
            body = {
                "workflow_id": workflow_id,
                "trigger_type": "schedule",
                "trigger_id": trigger_id,
                "run_by": "serviceaccount:scheduler",
            }
        resp = httpx.post(
            f"{REGISTRY_API_URL}/api/v1/internal/runs/start",
            json=body,
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            logger.info("dispatched scheduled run: %s trigger=%s", label, trigger_id)
        else:
            logger.warning("dispatch failed %s: %d %s", label, resp.status_code, resp.text[:200])
    except service_token.ServiceTokenError as exc:
        # Named separately from the generic dispatch error below so the log says WHICH
        # layer failed. Both skip the fire; only this one means "Keycloak/our client
        # secret", and diagnosing it as a registry-api problem would waste the session.
        logger.error("fire %s: cannot mint service token, SKIPPING dispatch: %s", label, exc)
    except Exception as exc:
        logger.error("dispatch error %s: %s", label, exc)
    finally:
        conn.close()


def _sync_jobs() -> None:
    """Register new jobs, update changed cron expressions, remove stale jobs."""
    triggers = _fetch_schedule_triggers()
    seen: set[str] = set()
    for trigger_id, cron_expr, tz, agent_name, workflow_id in triggers:
        seen.add(trigger_id)
        if _registered.get(trigger_id) == cron_expr:
            continue  # unchanged
        try:
            ct = CronTrigger.from_crontab(cron_expr, timezone=tz)
        except Exception as exc:
            logger.warning("invalid cron '%s' for trigger %s: %s", cron_expr, trigger_id, exc)
            continue
        scheduler.add_job(
            _on_fire, trigger=ct, args=[trigger_id, agent_name, workflow_id],
            id=trigger_id, replace_existing=True, misfire_grace_time=60,
        )
        _registered[trigger_id] = cron_expr
        label = agent_name if agent_name else f"workflow:{workflow_id}"
        logger.info("scheduled %s trigger=%s cron='%s' tz=%s", label, trigger_id, cron_expr, tz)

    # Remove jobs whose trigger was disabled/deleted
    for stale in [tid for tid in _registered if tid not in seen]:
        try:
            scheduler.remove_job(stale)
        except Exception:
            pass
        _registered.pop(stale, None)
        logger.info("removed stale schedule trigger=%s", stale)


def _reload_loop() -> None:
    while True:
        time.sleep(RELOAD_INTERVAL_SECONDS)
        try:
            _sync_jobs()
        except Exception as exc:
            logger.warning("reload error: %s", exc)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "scheduler", "scheduled_jobs": len(_registered)}


@app.on_event("startup")
def _startup() -> None:
    scheduler.start()
    _sync_jobs()
    threading.Thread(target=_reload_loop, daemon=True).start()
    logger.info("scheduler started with %d jobs; reload every %ds", len(_registered), RELOAD_INTERVAL_SECONDS)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)

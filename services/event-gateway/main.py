"""
AgentShield Event Gateway.

The platform's public webhook ingress for event-driven agents. This is the only
service that accepts unauthenticated traffic from the public internet and turns
it into agent execution, so every request field is treated as hostile. See
docs/design/event-gateway-threat-model.md.

Request pipeline for POST /hooks/{agent_name}/{token}:
  1. body size cap + JSON parse            (T-5)
  2. rate limit: per-agent + per-source-IP (T-4/T-11)  — counts even on later 401
  3. token: sha256 == token_hash, constant-time, agent-scoped, uniform 401 (T-2/T-6/T-9)
  4. replay: X-Webhook-Timestamp / X-Webhook-Nonce      (T-3)
  5. filter: filter_engine over payload                 (T-7 regex bound)
  6. persist agent_events (matched|filtered|rejected) via registry-api
  7. matched ⇒ POST /api/v1/internal/runs/start (trigger_type=webhook)

Endpoints:
  GET  /health                       — liveness
  POST /hooks/{agent_name}/{token}   — public webhook ingress
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

import httpx
import psycopg2
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from filter_engine import evaluate_filters
from rate_limiter import RateLimiter

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("event-gateway")

REGISTRY_API_URL = os.getenv("REGISTRY_API_URL", "http://agentshield-registry-api:8000")
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(256 * 1024)))  # 256 KiB (T-5)
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))       # T-11
REPLAY_MAX_SKEW_SECONDS = int(os.getenv("REPLAY_MAX_SKEW_SECONDS", "300"))  # 5 min (T-3)

app = FastAPI(title="AgentShield Event Gateway", version="0.1.0")
_rl = RateLimiter()


def _pg_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    return dsn.replace("+asyncpg", "").replace("postgresql+psycopg2", "postgresql")


def _client_ip(request: Request, xff: str | None) -> str:
    """Derive the source IP from the trusted proxy hop only (T-11).

    Never trust arbitrary client-supplied X-Forwarded-For. Take the entry
    TRUSTED_PROXY_HOPS from the right (the address our own ingress set).
    """
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            idx = max(0, len(parts) - TRUSTED_PROXY_HOPS)
            return parts[idx]
    return request.client.host if request.client else "unknown"


def _lookup_trigger(agent_name: str, token: str) -> dict | None:
    """Resolve an enabled webhook trigger by agent_name + sha256(token) (T-2/T-6).

    Constant-time hash comparison; returns None (⇒ uniform 401) on any miss.
    """
    token_sha = hashlib.sha256(token.encode()).hexdigest()
    try:
        conn = psycopg2.connect(_pg_dsn())
    except Exception as exc:
        logger.error("trigger lookup: cannot connect to Postgres: %s", exc)
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id::text, t.token_hash, t.filter_conditions
                FROM agent_triggers t
                JOIN agents a ON t.agent_id = a.id
                WHERE a.name = %s
                  AND t.trigger_type = 'webhook'
                  AND t.enabled = true
                """,
                (agent_name,),
            )
            for trigger_id, token_hash, filter_conditions in cur.fetchall():
                if token_hash and hmac.compare_digest(token_hash, token_sha):
                    return {"id": trigger_id, "filter_conditions": filter_conditions}
    except Exception as exc:
        logger.error("trigger lookup query failed for agent=%s: %s", agent_name, exc)
    finally:
        conn.close()
    return None


def _lookup_workflow_trigger(workflow_name: str, token: str) -> dict | None:
    """Resolve an enabled webhook trigger by workflow_name + sha256(token) (T-2/T-6).

    Mirrors _lookup_trigger exactly, but JOINs the workflows table by name
    instead of the agents table. Constant-time hash comparison; returns None
    (⇒ uniform 401) on any miss. Also returns the workflow_id UUID string so
    the caller can pass it to /internal/runs/start and _record_event.
    """
    token_sha = hashlib.sha256(token.encode()).hexdigest()
    try:
        conn = psycopg2.connect(_pg_dsn())
    except Exception as exc:
        logger.error("workflow trigger lookup: cannot connect to Postgres: %s", exc)
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id::text, t.token_hash, t.filter_conditions, w.id::text
                FROM agent_triggers t
                JOIN workflows w ON t.workflow_id = w.id
                WHERE w.name = %s
                  AND t.trigger_type = 'webhook'
                  AND t.enabled = true
                """,
                (workflow_name,),
            )
            for trigger_id, token_hash, filter_conditions, workflow_id in cur.fetchall():
                if token_hash and hmac.compare_digest(token_hash, token_sha):
                    return {
                        "id": trigger_id,
                        "filter_conditions": filter_conditions,
                        "workflow_id": workflow_id,
                    }
    except Exception as exc:
        logger.error("workflow trigger lookup query failed for workflow=%s: %s", workflow_name, exc)
    finally:
        conn.close()
    return None


def _uniform_401() -> JSONResponse:
    # Same response for unknown-agent, disabled trigger, and bad token (T-9).
    return JSONResponse(status_code=401, content={"detail": "invalid webhook credentials"})


def _record_event(agent_name: str, trigger_id: str | None, status: str,
                  filter_reason: str | None, payload: dict, source_ip: str,
                  run_id: str | None = None, workflow_id: str | None = None) -> None:
    """Best-effort direct INSERT into agent_events (like the scheduler's DB access).

    workflow_id is set for workflow webhook events (agent_name is the workflow
    name in that case). Never raises — event logging must not break the response
    path.
    """
    try:
        conn = psycopg2.connect(_pg_dsn())
    except Exception as exc:
        logger.warning("record_event: cannot connect to Postgres: %s", exc)
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_events
                    (trigger_id, agent_name, status, filter_reason, payload, run_id, source_ip, workflow_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trigger_id,
                    agent_name,
                    status,
                    filter_reason,
                    json.dumps(payload),
                    run_id,
                    source_ip if source_ip and source_ip != "unknown" else None,
                    workflow_id,
                ),
            )
        conn.commit()
    except Exception as exc:  # event logging must never break the response path
        logger.warning("failed to record agent_event (%s) for %s: %s", status, agent_name, exc)
    finally:
        conn.close()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "event-gateway"}


@app.post("/hooks/workflow/{workflow_name}/{token}")
async def receive_workflow_hook(
    workflow_name: str,
    token: str,
    request: Request,
    x_forwarded_for: str | None = Header(None),
    x_webhook_timestamp: str | None = Header(None),
    x_webhook_nonce: str | None = Header(None),
    x_webhook_token: str | None = Header(None),
    authorization: str | None = Header(None),
) -> JSONResponse:
    """Webhook ingress for composite-workflow triggers.

    Route ordering note: this path has 4 segments (/hooks/workflow/{name}/{tok})
    while the agent route has 3 (/hooks/{name}/{tok}). FastAPI never matches
    them against the same URL regardless of registration order, but this route
    is declared first as defensive practice.

    Follows the identical security pipeline as receive_hook (T-2 through T-7).
    """
    source_ip = _client_ip(request, x_forwarded_for)
    tok = x_webhook_token or (authorization[7:] if authorization and authorization.startswith("Bearer ") else token)
    tok_prefix = hashlib.sha256(tok.encode()).hexdigest()[:8]
    logger.info("hook workflow=%s ip=%s token_sha8=%s", workflow_name, source_ip, tok_prefix)

    # 1. Body size cap + JSON parse (T-5)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "payload too large"})
    if raw:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return JSONResponse(status_code=400, content={"detail": "payload must be a JSON object"})
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"detail": "invalid JSON payload"})
    else:
        payload = {}

    # 2. Rate limit BEFORE token cost; counts even when we later 401 (T-4)
    ok, dim = _rl.allowed(workflow_name, source_ip)
    if not ok:
        logger.warning("rate limited workflow=%s ip=%s dim=%s", workflow_name, source_ip, dim)
        return JSONResponse(
            status_code=429, content={"detail": f"rate limit exceeded ({dim})"},
            headers={"Retry-After": os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")},
        )

    # 3. Token validation — uniform 401 (T-2/T-6/T-9)
    trigger = _lookup_workflow_trigger(workflow_name, tok)
    if trigger is None:
        _record_event(workflow_name, None, "rejected", "invalid credentials", payload, source_ip)
        return _uniform_401()

    workflow_id: str = trigger["workflow_id"]

    # 4. Replay protection (T-3)
    if x_webhook_timestamp:
        try:
            skew = abs(time.time() - float(x_webhook_timestamp))
            if skew > REPLAY_MAX_SKEW_SECONDS:
                _record_event(workflow_name, trigger["id"], "rejected", "stale timestamp",
                              payload, source_ip, workflow_id=workflow_id)
                return JSONResponse(status_code=401, content={"detail": "stale webhook timestamp"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "invalid X-Webhook-Timestamp"})
    if x_webhook_nonce:
        if not _rl.check_nonce(workflow_name, x_webhook_nonce):
            _record_event(workflow_name, trigger["id"], "rejected", "replayed nonce",
                          payload, source_ip, workflow_id=workflow_id)
            return JSONResponse(status_code=409, content={"detail": "duplicate webhook (replay)"})

    # 5. Filter evaluation (T-7 regex bound inside filter_engine)
    fc = trigger["filter_conditions"]
    if isinstance(fc, dict):
        fc = [fc]
    result = evaluate_filters(fc, payload)
    if not result["matched"]:
        logger.info("filtered workflow=%s reason=%s", workflow_name, result["reason"])
        _record_event(workflow_name, trigger["id"], "filtered", result["reason"],
                      payload, source_ip, workflow_id=workflow_id)
        return JSONResponse(status_code=202, content={"status": "filtered", "reason": result["reason"]})

    # 6 + 7. Matched ⇒ dispatch to the cluster-internal run-start endpoint
    run_id = None
    try:
        resp = httpx.post(
            f"{REGISTRY_API_URL}/api/v1/internal/runs/start",
            json={
                "workflow_id": workflow_id,
                "trigger_type": "webhook",
                "trigger_id": trigger["id"],
                "trigger_payload": payload,
                "run_by": "serviceaccount:event-gateway",
            },
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            run_id = resp.json().get("id")
        else:
            logger.warning("dispatch failed workflow=%s: %d %s", workflow_name, resp.status_code, resp.text[:200])
            _record_event(workflow_name, trigger["id"], "matched", "dispatch failed",
                          payload, source_ip, workflow_id=workflow_id)
            return JSONResponse(status_code=502, content={"detail": "run dispatch failed"})
    except Exception as exc:
        logger.error("dispatch error workflow=%s: %s", workflow_name, exc)
        _record_event(workflow_name, trigger["id"], "matched", f"dispatch error: {exc}",
                      payload, source_ip, workflow_id=workflow_id)
        return JSONResponse(status_code=502, content={"detail": "run dispatch error"})

    _record_event(workflow_name, trigger["id"], "matched", None, payload, source_ip,
                  run_id=run_id, workflow_id=workflow_id)
    return JSONResponse(status_code=202, content={"status": "accepted", "run_id": run_id})


@app.post("/hooks/{agent_name}/{token}")
async def receive_hook(
    agent_name: str,
    token: str,
    request: Request,
    x_forwarded_for: str | None = Header(None),
    x_webhook_timestamp: str | None = Header(None),
    x_webhook_nonce: str | None = Header(None),
    x_webhook_token: str | None = Header(None),
    authorization: str | None = Header(None),
) -> JSONResponse:
    source_ip = _client_ip(request, x_forwarded_for)
    # NEVER log the token (T-1): only agent + a short hash prefix.
    tok = x_webhook_token or (authorization[7:] if authorization and authorization.startswith("Bearer ") else token)
    tok_prefix = hashlib.sha256(tok.encode()).hexdigest()[:8]
    logger.info("hook agent=%s ip=%s token_sha8=%s", agent_name, source_ip, tok_prefix)

    # 1. Body size cap + JSON parse (T-5)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"detail": "payload too large"})
    if raw:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return JSONResponse(status_code=400, content={"detail": "payload must be a JSON object"})
        except json.JSONDecodeError:
            return JSONResponse(status_code=400, content={"detail": "invalid JSON payload"})
    else:
        payload = {}

    # 2. Rate limit BEFORE token cost; counts even when we later 401 (T-4)
    ok, dim = _rl.allowed(agent_name, source_ip)
    if not ok:
        logger.warning("rate limited agent=%s ip=%s dim=%s", agent_name, source_ip, dim)
        return JSONResponse(
            status_code=429, content={"detail": f"rate limit exceeded ({dim})"},
            headers={"Retry-After": os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")},
        )

    # 3. Token validation — uniform 401 (T-2/T-6/T-9)
    trigger = _lookup_trigger(agent_name, tok)
    if trigger is None:
        _record_event(agent_name, None, "rejected", "invalid credentials", payload, source_ip)
        return _uniform_401()

    # 4. Replay protection (T-3)
    if x_webhook_timestamp:
        try:
            skew = abs(time.time() - float(x_webhook_timestamp))
            if skew > REPLAY_MAX_SKEW_SECONDS:
                _record_event(agent_name, trigger["id"], "rejected", "stale timestamp", payload, source_ip)
                return JSONResponse(status_code=401, content={"detail": "stale webhook timestamp"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "invalid X-Webhook-Timestamp"})
    if x_webhook_nonce:
        if not _rl.check_nonce(agent_name, x_webhook_nonce):
            _record_event(agent_name, trigger["id"], "rejected", "replayed nonce", payload, source_ip)
            return JSONResponse(status_code=409, content={"detail": "duplicate webhook (replay)"})

    # 5. Filter evaluation (T-7 regex bound inside filter_engine)
    fc = trigger["filter_conditions"]
    if isinstance(fc, dict):
        fc = [fc]
    result = evaluate_filters(fc, payload)
    if not result["matched"]:
        logger.info("filtered agent=%s reason=%s", agent_name, result["reason"])
        _record_event(agent_name, trigger["id"], "filtered", result["reason"], payload, source_ip)
        return JSONResponse(status_code=202, content={"status": "filtered", "reason": result["reason"]})

    # 6 + 7. Matched ⇒ dispatch to the cluster-internal run-start endpoint
    run_id = None
    try:
        resp = httpx.post(
            f"{REGISTRY_API_URL}/api/v1/internal/runs/start",
            json={
                "agent_name": agent_name,
                "trigger_type": "webhook",
                "trigger_id": trigger["id"],
                "trigger_payload": payload,
                "run_by": "serviceaccount:event-gateway",
            },
            timeout=15.0,
        )
        if resp.status_code in (200, 201):
            run_id = resp.json().get("id")
        else:
            logger.warning("dispatch failed agent=%s: %d %s", agent_name, resp.status_code, resp.text[:200])
            _record_event(agent_name, trigger["id"], "matched", "dispatch failed", payload, source_ip)
            return JSONResponse(status_code=502, content={"detail": "run dispatch failed"})
    except Exception as exc:
        logger.error("dispatch error agent=%s: %s", agent_name, exc)
        _record_event(agent_name, trigger["id"], "matched", f"dispatch error: {exc}", payload, source_ip)
        return JSONResponse(status_code=502, content={"detail": "run dispatch error"})

    _record_event(agent_name, trigger["id"], "matched", None, payload, source_ip, run_id=run_id)
    return JSONResponse(status_code=202, content={"status": "accepted", "run_id": run_id})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8091)

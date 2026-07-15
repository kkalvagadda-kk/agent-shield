"""
AgentShield Event Gateway.

The platform's public webhook ingress for event-driven agents. This is the only
service that accepts unauthenticated traffic from the public internet and turns
it into agent execution, so every request field is treated as hostile. See
docs/design/event-gateway-threat-model.md.

Request pipeline for POST /hooks/{agent_name}/{token}:
  1. body size cap + JSON parse            (T-5)
  2. rate limit: per-agent + per-source-IP (T-4/T-11)  — counts even on later 401
  3. auth: webhook_auth.verify_webhook_auth — resolves the addressed trigger and
     applies ITS auth_mode ('token' bearer, or 'client_signed' client-id + HMAC).
     Every failure ⇒ the SAME uniform 401 (T-2/T-6/T-9). (WS-4)
  4. replay: X-Webhook-Nonce                            (T-3; timestamp freshness is
     decided inside verify_webhook_auth so a stale ts cannot get its own 401 body)
  5. filter: filter_engine over payload                 (T-7 regex bound)
  6. persist agent_events (matched|filtered|rejected) via registry-api
  7. matched ⇒ POST /api/v1/internal/runs/start (trigger_type=webhook)

Both hooks share ONE auth hop (`webhook_auth.verify_webhook_auth`) — see that module's
header for why a per-handler copy is the failure mode being designed out.

Endpoints:
  GET  /health                       — liveness
  POST /hooks/{agent_name}/{token}   — public webhook ingress
"""
from __future__ import annotations

import json
import logging
import os

import httpx
import psycopg2
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from filter_engine import evaluate_filters
from rate_limiter import RateLimiter
from webhook_auth import verify_webhook_auth

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("event-gateway")

REGISTRY_API_URL = os.getenv("REGISTRY_API_URL", "http://agentshield-registry-api:8000")
MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(256 * 1024)))  # 256 KiB (T-5)
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))       # T-11

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


def _uniform_401() -> JSONResponse:
    """The ONE 401 the gateway ever returns. Takes NO arguments — deliberately.

    Every auth failure (unknown agent/workflow, no matching trigger, bad token,
    unknown client, disabled client, wrong-trigger client, stale/malformed timestamp,
    bad signature) funnels through here, so the response is byte-identical and cannot
    tell an attacker WHICH check failed (T-9). The absent parameter list is the
    enforcement: there is no reason to pass, so no reason can be leaked. Before WS-4
    the stale-timestamp branches hand-built their own JSONResponse with a distinct body
    and were exactly that oracle. suite-76 T-S76-003 asserts the byte-identity.
    """
    return JSONResponse(status_code=401, content={"detail": "invalid webhook credentials"})


def _record_event(agent_name: str, trigger_id: str | None, status: str,
                  filter_reason: str | None, payload: dict, source_ip: str,
                  run_id: str | None = None, workflow_id: str | None = None,
                  client_id: str | None = None) -> None:
    """Best-effort direct INSERT into agent_events (like the scheduler's DB access).

    workflow_id is set for workflow webhook events (agent_name is the workflow
    name in that case). client_id is the VERIFIED application (WS-4) — only ever
    stamped after a signature checks out, never the raw X-Client-Id header, which is
    attacker-controlled until proven. NULL on token-mode triggers (no per-app identity
    exists) and on rejected events. Never raises — event logging must not break the
    response path.
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
                    (trigger_id, agent_name, status, filter_reason, payload, run_id, source_ip,
                     workflow_id, client_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    client_id,
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
    x_webhook_nonce: str | None = Header(None),
) -> JSONResponse:
    """Webhook ingress for composite-workflow triggers.

    Route ordering note: this path has 4 segments (/hooks/workflow/{name}/{tok})
    while the agent route has 3 (/hooks/{name}/{tok}). FastAPI never matches
    them against the same URL regardless of registration order, but this route
    is declared first as defensive practice.

    Identical security pipeline to receive_hook (T-2 through T-7) — and the auth
    hop is not merely identical, it is the SAME function (WS-4). The credential
    headers (X-Webhook-Token / Authorization / X-Client-Id / X-Timestamp /
    X-Signature) are read inside verify_webhook_auth off `request.headers`, so
    adding an auth header never means touching two handlers again.
    """
    source_ip = _client_ip(request, x_forwarded_for)
    logger.info("hook workflow=%s ip=%s", workflow_name, source_ip)

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

    # 3. Auth — THE shared hop (call site 1 of 2). Resolves the addressed trigger and
    #    applies its auth_mode: 'token' (bearer) or 'client_signed' (client-id + HMAC).
    #    Timestamp freshness is decided IN THERE, so a stale ts is indistinguishable
    #    from a bad signature out here — one _uniform_401(), no enumeration oracle.
    auth = verify_webhook_auth("workflow", workflow_name, request.headers, raw, token)
    if not auth.ok:
        # Uniform on purpose: the reason is logged inside verify (operator-facing) and
        # never returned or persisted per-reason. `_DENY` carries no reason to leak.
        _record_event(workflow_name, None, "rejected", "invalid credentials", payload, source_ip)
        return _uniform_401()

    trigger = auth.trigger
    workflow_id: str = trigger["workflow_id"]

    # 4. Replay protection (T-3) — nonce only; freshness already enforced in verify.
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
                      payload, source_ip, workflow_id=workflow_id, client_id=auth.client_id)
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
                          payload, source_ip, workflow_id=workflow_id, client_id=auth.client_id)
            return JSONResponse(status_code=502, content={"detail": "run dispatch failed"})
    except Exception as exc:
        logger.error("dispatch error workflow=%s: %s", workflow_name, exc)
        _record_event(workflow_name, trigger["id"], "matched", f"dispatch error: {exc}",
                      payload, source_ip, workflow_id=workflow_id, client_id=auth.client_id)
        return JSONResponse(status_code=502, content={"detail": "run dispatch error"})

    _record_event(workflow_name, trigger["id"], "matched", None, payload, source_ip,
                  run_id=run_id, workflow_id=workflow_id, client_id=auth.client_id)
    return JSONResponse(status_code=202, content={"status": "accepted", "run_id": run_id})


@app.post("/hooks/{agent_name}/{token}")
async def receive_hook(
    agent_name: str,
    token: str,
    request: Request,
    x_forwarded_for: str | None = Header(None),
    x_webhook_nonce: str | None = Header(None),
) -> JSONResponse:
    """Webhook ingress for agent triggers.

    Same pipeline, and the SAME auth function, as receive_workflow_hook (WS-4).
    """
    source_ip = _client_ip(request, x_forwarded_for)
    # NEVER log the token (T-1).
    logger.info("hook agent=%s ip=%s", agent_name, source_ip)

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

    # 3. Auth — THE shared hop (call site 2 of 2). Identical semantics to the workflow
    #    hook because it is literally the same function; `kind` is the only difference.
    auth = verify_webhook_auth("agent", agent_name, request.headers, raw, token)
    if not auth.ok:
        _record_event(agent_name, None, "rejected", "invalid credentials", payload, source_ip)
        return _uniform_401()

    trigger = auth.trigger

    # 4. Replay protection (T-3) — nonce only; freshness already enforced in verify.
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
        _record_event(agent_name, trigger["id"], "filtered", result["reason"], payload,
                      source_ip, client_id=auth.client_id)
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
            _record_event(agent_name, trigger["id"], "matched", "dispatch failed", payload,
                          source_ip, client_id=auth.client_id)
            return JSONResponse(status_code=502, content={"detail": "run dispatch failed"})
    except Exception as exc:
        logger.error("dispatch error agent=%s: %s", agent_name, exc)
        _record_event(agent_name, trigger["id"], "matched", f"dispatch error: {exc}", payload,
                      source_ip, client_id=auth.client_id)
        return JSONResponse(status_code=502, content={"detail": "run dispatch error"})

    _record_event(agent_name, trigger["id"], "matched", None, payload, source_ip,
                  run_id=run_id, client_id=auth.client_id)
    return JSONResponse(status_code=202, content={"status": "accepted", "run_id": run_id})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8091)

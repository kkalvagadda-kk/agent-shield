"""
Webhook authentication for the AgentShield Event Gateway (WS-4).

THE ONE auth hop for BOTH public hooks
--------------------------------------
`verify_webhook_auth()` is called by the agent hook (`main.py` `/hooks/{name}/{token}`)
and the workflow hook (`/hooks/workflow/{name}/{token}`) — **one definition, two call
sites, zero per-handler copies**. That is not stylistic. The recurring defect class in
this repo is two parallel paths drifting: `docs/bugs/side-effecting-lost-on-declarative-runner-path.md`
records `side_effecting` being threaded onto the SDK tool-builder and silently DROPPED
by the declarative-runner's hand-maintained second builder — the fail-closed default
made the bug *safe*, so it survived for weeks while a unit test against the other path
stayed green. The two hook handlers were already that shape: `_lookup_trigger` and
`_lookup_workflow_trigger` were hand-duplicated, differing only in a JOIN. This module
collapses them into ONE lookup parameterised by an explicit `kind` discriminator, so a
future change to the auth rules cannot land on one hook and miss the other.

Dual-mode auth is an EXPLICIT per-trigger property
--------------------------------------------------
`agent_triggers.auth_mode ∈ {'token', 'client_signed'}` selects the credential check.
The gateway branches on the STORED mode and never "tries the token, then falls back to
signed". That priority fallthrough is the No-Bandaid anti-pattern (CLAUDE.md): it would
mean a `client_signed` trigger silently still accepts the coarse bearer token, i.e. the
upgrade would buy nothing. An unrecognised mode is a DENY (fail closed), not a default.

The path token addresses the endpoint in BOTH modes
---------------------------------------------------
`/hooks/{name}/{token}` carries the only trigger selector the URL has, and an agent may
have more than one enabled webhook trigger (the pre-WS-4 lookup already loops over all
of them). So the token resolves WHICH trigger is being addressed in both modes — it is
matched constant-time here, inside verify, exactly as before. What `auth_mode` changes
is what that match *proves*:

  * `token`         — the path token IS the bearer credential; matching it authenticates
                      the sender (the pre-WS-4 posture: per-trigger, not per-application).
  * `client_signed`  — matching it only ADDRESSES the trigger; the sender must further
                      prove per-application identity with X-Client-Id + X-Signature.

The contract doc says the path token is "ignored" under `client_signed`. Taken literally
that is unimplementable: with two enabled webhook triggers on one agent and the token
ignored, there is no deterministic way to say which trigger a request addresses (and a
client_id registered on both would be ambiguous). Treating it as the address costs the
sender nothing — they POST to the URL they were handed, which contains it — and keeps
resolution deterministic. Recorded in the WS-4 report as a contract deviation.

Uniform 401 — the oracle is closed STRUCTURALLY
-----------------------------------------------
Every failure returns the SAME `_DENY` singleton — literally the same object, so there
is no per-reason field for a caller to leak into a response body even by accident. The
reason is logged server-side here, where the operator can see it, and never travels back
to the sender. Combined with `main.py::_uniform_401()` taking NO arguments, an
enumeration oracle is unrepresentable rather than guarded against: unknown-client,
bad-signature, stale-timestamp, disabled-client and wrong-trigger are byte-identical to
the caller. suite-76 `T-S76-003` asserts that byte-identity so it cannot regress.

This CLOSES a pre-existing oracle: the stale-timestamp branches used to return
`{"detail": "stale webhook timestamp"}` while everything else returned
`{"detail": "invalid webhook credentials"}` — telling an attacker exactly which check
failed. Timestamp freshness is now decided in here and reported through the same deny.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass

import psycopg2
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("event-gateway.webhook_auth")

# Same window the token path already used (threat model T-3), same env var.
REPLAY_MAX_SKEW_SECONDS = int(os.getenv("REPLAY_MAX_SKEW_SECONDS", "300"))

# Signed-mode headers (contracts/webhook-signing.md). Distinct from the token path's
# legacy X-Webhook-Timestamp: under client_signed the timestamp is COVERED BY the MAC,
# so it is mandatory, not opt-in.
H_CLIENT_ID = "x-client-id"
H_TIMESTAMP = "x-timestamp"
H_SIGNATURE = "x-signature"


def _pg_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    return dsn.replace("+asyncpg", "").replace("postgresql+psycopg2", "postgresql")


def _fernet() -> Fernet:
    """Mirror of registry-api's `crypto.py::_fernet`.

    The two services share no Python package, so this is a deliberate small port
    rather than an import. It is keyed by the SAME `AGENTSHIELD_ENCRYPTION_KEY`
    (K8s Secret `agentshield-encryption`, key `key`) that the registry-api uses to
    ENCRYPT the secret — if the two ever diverge, every signature verification fails
    closed (deny), never opens.
    """
    key = os.environ.get("AGENTSHIELD_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("AGENTSHIELD_ENCRYPTION_KEY is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _decrypt_secret(secret_encrypted: str) -> str:
    """Recover the raw signing secret from its Fernet token.

    Reversible BY DESIGN: verifying a signature means RECOMPUTING
    HMAC_SHA256(secret, ...), which needs the secret itself. A one-way hash is
    unimplementable here — hence `secret_encrypted`, not `secret_hash`.
    """
    return json.loads(_fernet().decrypt(secret_encrypted.encode()).decode())["secret"]


@dataclass(frozen=True)
class WebhookAuthResult:
    """Outcome of the auth hop.

    `trigger` is the RESOLVED trigger (id, filter_conditions, workflow_id) so the
    caller does not repeat the lookup. Carries NO failure reason — see `_DENY`.
    """

    ok: bool
    trigger: dict | None = None
    client_id: str | None = None


# The single deny value. Every failure path returns THIS object, so "which check
# failed" is not merely undisclosed — it is not representable in the return type.
# A future caller cannot accidentally surface a reason it was never handed.
_DENY = WebhookAuthResult(ok=False)


def _deny(reason: str, **ctx: object) -> WebhookAuthResult:
    """Log the real reason server-side; return the indistinguishable deny.

    The operator gets the diagnosis in the gateway log. The sender gets one uniform
    401 body. Those are deliberately different audiences.
    """
    logger.info("webhook auth denied: %s %s", reason, ctx or "")
    return _DENY


# One row-shape per trigger target. An explicit map, NOT two copy-pasted functions:
# adding a target means adding a query here, and BOTH hooks pick it up because they
# share this module. `workflow_id` is selected as NULL for agents so the result shape
# is identical regardless of kind — the caller never branches on it.
#
# `artifact_id`/`team_name` (the two trailing columns) are the artifact's own id and
# owning team — needed by `_verify_client_signed`'s application/grant resolution
# (`lookup_application(team_name, ...)`, `has_active_invoker_grant(artifact_type,
# artifact_id, ...)`). `artifact_type` itself is not a column: the `kind` parameter
# already distinguishes "agent"/"workflow" one-to-one with
# `artifact_role_grants.artifact_type`'s own CHECK values, so `lookup_triggers`
# carries it through into the row dict instead of selecting it redundantly.
_TRIGGER_SQL = {
    "agent": """
        SELECT t.id::text, t.token_hash, t.filter_conditions, t.auth_mode, NULL,
               a.id::text, a.team
        FROM agent_triggers t
        JOIN agents a ON t.agent_id = a.id
        WHERE a.name = %s
          AND t.trigger_type = 'webhook'
          AND t.enabled = true
    """,
    "workflow": """
        SELECT t.id::text, t.token_hash, t.filter_conditions, t.auth_mode, w.id::text,
               w.id::text, w.team
        FROM agent_triggers t
        JOIN workflows w ON t.workflow_id = w.id
        WHERE w.name = %s
          AND t.trigger_type = 'webhook'
          AND t.enabled = true
    """,
}


def lookup_triggers(kind: str, name: str) -> list[dict]:
    """Every ENABLED webhook trigger on the named agent/workflow.

    `kind` is an explicit context parameter — the No-Bandaid alternative to the two
    hand-maintained lookups this replaces. An unknown kind raises (a programming
    error must be loud, not a silent empty list that would read as "no triggers" and
    deny every request on that hook).
    """
    sql = _TRIGGER_SQL[kind]
    try:
        conn = psycopg2.connect(_pg_dsn())
    except Exception as exc:
        logger.error("trigger lookup: cannot connect to Postgres: %s", exc)
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (name,))
            return [
                {
                    "id": row[0],
                    "token_hash": row[1],
                    "filter_conditions": row[2],
                    "auth_mode": row[3],
                    "workflow_id": row[4],
                    "artifact_id": row[5],
                    "team_name": row[6],
                    "artifact_type": kind,
                }
                for row in cur.fetchall()
            ]
    except Exception as exc:
        logger.error("trigger lookup query failed for %s=%s: %s", kind, name, exc)
        return []
    finally:
        conn.close()


def lookup_application(team_name: str, name: str) -> dict | None:
    """Resolve one application by (owning team, name).

    Deliberately NOT filtered on `enabled` here — the enabled check is a separate
    explicit step in `_verify_client_signed` so "unknown application" and "disabled
    application" can each be logged with their own real reason server-side (still
    the same uniform `_DENY` to the caller either way). Read live on EVERY request —
    there is no cache — same live-no-cache posture the pre-cutover `webhook_clients`
    lookup this module used to have used.
    """
    try:
        conn = psycopg2.connect(_pg_dsn())
    except Exception as exc:
        logger.error("application lookup: cannot connect to Postgres: %s", exc)
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, secret_encrypted, enabled
                FROM applications
                WHERE team_name = %s AND name = %s
                """,
                (team_name, name),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "secret_encrypted": row[1],
                "enabled": row[2],
            }
    except Exception as exc:
        logger.error(
            "application lookup query failed for team=%s name=%s: %s",
            team_name, name, exc,
        )
        return None
    finally:
        conn.close()


def has_active_invoker_grant(artifact_type: str, artifact_id: str, application_id: str) -> bool:
    """Active (`revoked_at IS NULL`) invoker grant for this application on this artifact.

    `artifact_type` is passed explicitly (not re-derived from `application_id` or
    anything else) — the No-Bandaid rule this module's own header already documents:
    an explicit context parameter, never type-sniffed. Read live on EVERY request —
    there is no cache — so a revoked grant takes effect on the very next webhook.
    """
    try:
        conn = psycopg2.connect(_pg_dsn())
    except Exception as exc:
        logger.error("invoker grant lookup: cannot connect to Postgres: %s", exc)
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM artifact_role_grants
                WHERE artifact_type = %s AND artifact_id = %s AND role = 'invoker'
                  AND grantee_type = 'application' AND grantee_id = %s
                  AND revoked_at IS NULL
                LIMIT 1
                """,
                (artifact_type, artifact_id, application_id),
            )
            return cur.fetchone() is not None
    except Exception as exc:
        logger.error(
            "invoker grant lookup failed for artifact_type=%s artifact_id=%s application_id=%s: %s",
            artifact_type, artifact_id, application_id, exc,
        )
        return False
    finally:
        conn.close()


def sign_webhook(secret: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    """SENDER REFERENCE — the canonical way to sign a webhook for a client_signed trigger.

    Shipped from the same module the gateway verifies with, so a real application and
    suite-76 sign IDENTICALLY. If this drifts from the verify below, both drift together
    and the suite catches it — the failure mode where a test signs one way and the
    product expects another cannot happen.

        headers = sign_webhook(secret, body)
        headers["X-Client-Id"] = "billing-app"
        httpx.post(webhook_url, content=body, headers=headers)   # content=, NOT json=

    `body` MUST be the exact bytes that go on the wire. Signing a dict and letting the
    HTTP client re-serialise it changes the bytes (key order, separators) and breaks the
    MAC — that is why this takes `bytes`, not a dict.
    """
    ts = ts if ts is not None else int(time.time())
    mac = hmac.new(
        secret.encode(), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return {"X-Timestamp": str(ts), "X-Signature": f"sha256={mac}"}


def presented_token(headers, path_token: str) -> str:
    """The token the sender presented, by the precedence the gateway has always used:
    `X-Webhook-Token` header, else `Authorization: Bearer …`, else the URL path segment.

    This is NOT the auth_mode fallthrough the No-Bandaid rule forbids — it is one
    credential in three transports, resolved identically in both modes. It was
    hand-duplicated in both handlers before WS-4; it lives here now so the two hooks
    cannot disagree about what a sender presented.
    """
    header_tok = headers.get("x-webhook-token")
    if header_tok:
        return header_tok
    authz = headers.get("authorization") or ""
    if authz.startswith("Bearer "):
        return authz[7:]
    return path_token


def _resolve_trigger(triggers: list[dict], path_token: str) -> dict | None:
    """The trigger this URL addresses: constant-time token_hash match.

    Unchanged in substance from the pre-WS-4 lookup — the comparison is constant-time
    so response timing cannot be walked toward a valid token.
    """
    token_sha = hashlib.sha256(path_token.encode()).hexdigest()
    for t in triggers:
        if t["token_hash"] and hmac.compare_digest(t["token_hash"], token_sha):
            return t
    return None


def _fresh(ts_raw: str | None) -> bool:
    """|now - ts| <= 300s. A malformed or absent timestamp is NOT fresh.

    Returning False (rather than raising a 400) keeps a malformed timestamp
    indistinguishable from a stale one — a 400-vs-401 split would itself be an oracle.
    """
    if not ts_raw:
        return False
    try:
        return abs(time.time() - float(ts_raw)) <= REPLAY_MAX_SKEW_SECONDS
    except (TypeError, ValueError):
        return False


def _verify_client_signed(trigger: dict, headers, raw_body: bytes) -> WebhookAuthResult:
    """Per-application credential check. Verify order per
    contracts/gateway-verification.md (verbatim, do not reorder):
    application lookup → active invoker grant → enabled → freshness → constant-time HMAC.

    The grant check runs BEFORE the enabled check on purpose: a disabled application
    with an otherwise-valid grant must log "application disabled" (not "no active
    invoker grant") so the two failure reasons stay meaningfully distinct for an
    operator reading gateway logs. This costs nothing security-wise — the caller
    still gets the same uniform `_DENY` either way (see the module header docstring's
    "Uniform 401" section).
    """
    client_id = headers.get(H_CLIENT_ID)
    if not client_id:
        return _deny("no X-Client-Id on a client_signed trigger", trigger=trigger["id"])

    application = lookup_application(trigger["team_name"], client_id)
    if application is None:
        return _deny("no application matches this team/client_id",
                     trigger=trigger["id"], client=client_id)

    if not has_active_invoker_grant(
        trigger["artifact_type"], trigger["artifact_id"], application["id"]
    ):
        return _deny("no active invoker grant", trigger=trigger["id"], client=client_id)

    if not application["enabled"]:
        return _deny("application disabled", trigger=trigger["id"], client=client_id)

    if not _fresh(headers.get(H_TIMESTAMP)):
        return _deny("timestamp missing/stale/malformed",
                     trigger=trigger["id"], client=client_id)

    try:
        secret = _decrypt_secret(application["secret_encrypted"])
    except (InvalidToken, ValueError, KeyError, RuntimeError) as exc:
        # Fail CLOSED: a key mismatch or corrupt row denies, never opens. Logged at
        # error because it is an operator problem (wrong AGENTSHIELD_ENCRYPTION_KEY),
        # not a sender problem.
        logger.error("cannot decrypt secret for application=%s on trigger=%s: %s",
                     client_id, trigger["id"], exc)
        return _deny("secret undecryptable", trigger=trigger["id"], client=client_id)

    # Sign the RAW BYTES. `raw_body` is what arrived on the wire; re-serialising the
    # parsed JSON would change the bytes and break every legitimate signature.
    expected = "sha256=" + hmac.new(
        secret.encode(), f"{headers.get(H_TIMESTAMP)}.".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, headers.get(H_SIGNATURE) or ""):
        return _deny("bad signature", trigger=trigger["id"], client=client_id)

    return WebhookAuthResult(ok=True, trigger=trigger, client_id=client_id)


def verify_webhook_auth(
    kind: str,
    name: str,
    headers,
    raw_body: bytes,
    path_token: str,
) -> WebhookAuthResult:
    """THE auth hop. Called by BOTH hook handlers — one definition, two call sites.

    Owns the ENTIRE auth decision (resolve the addressed trigger, then apply that
    trigger's credential rule) so that reading this one function tells you exactly what
    authenticates a webhook. The caller does DB-free pipeline work only: body cap, rate
    limit, filter, dispatch.

    Returns `_DENY` — the single shared sentinel — on every failure. The caller's only
    correct response to `not result.ok` is `_uniform_401()`.
    """
    triggers = lookup_triggers(kind, name)
    if not triggers:
        return _deny("no enabled webhook trigger", kind=kind, name=name)

    trigger = _resolve_trigger(triggers, presented_token(headers, path_token))
    if trigger is None:
        return _deny("presented token matches no trigger", kind=kind, name=name)

    mode = trigger["auth_mode"]
    if mode == "token":
        # The path token IS the bearer credential in this mode, and _resolve_trigger
        # just matched it constant-time. That match is the authentication — there is
        # nothing further to prove. (Pre-WS-4 posture: coarse, per-trigger.)
        return WebhookAuthResult(ok=True, trigger=trigger, client_id=None)
    if mode == "client_signed":
        return _verify_client_signed(trigger, headers, raw_body)

    # Fail closed on an unrecognised mode. NOT a fallthrough to the weaker check:
    # a mode we do not understand must never be treated as 'token'.
    return _deny("unrecognised auth_mode", mode=mode, trigger=trigger["id"])

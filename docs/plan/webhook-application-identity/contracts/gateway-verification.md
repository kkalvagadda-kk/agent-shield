# Contract — Gateway `_verify_client_signed` Resolution Change

`services/event-gateway/webhook_auth.py`. This is the **only** runtime code on the request path that changes (design doc §7). Everything downstream of the auth decision — rate limiting, filter matching, dispatch — is untouched.

## What does NOT change (hard invariants, restated per-endpoint — read the module's full header docstring before touching this file)

1. **Fail-closed posture.** Every new failure path returns the existing `_DENY` sentinel via `_deny(reason, **ctx)`. No new branch returns anything else.
2. **Uniform 401 / no enumeration oracle.** "Unknown application," "no active invoker grant," "application disabled," "bad signature," "stale timestamp" remain byte-identical to the caller — the reason is logged server-side only, via the existing `_deny()` helper, never surfaced in the response body. `suite-83` T-SYY-006 asserts the disabled-application 401 is byte-identical to the revoked-grant 401 (T-SYY-005), extending `suite-76` T-S76-003's existing byte-identity assertion to the two new failure reasons this change introduces.
3. **Constant-time comparison.** The final HMAC comparison stays `hmac.compare_digest(...)`. Nothing about signature verification itself changes — `sign_webhook` (the sender reference) is untouched, so a real sender's bytes-on-the-wire are unaffected (design doc §9.5: "byte-identical to today's WS-4 flow" from the sender's point of view).
4. **Live read, no cache.** Every one of the three new/changed lookups (`lookup_triggers`, `lookup_application`, `has_active_invoker_grant`) hits Postgres directly on every request, exactly like `lookup_webhook_client` did — a revoked grant or a disabled application takes effect on the very next webhook, not after some TTL.
5. **No cross-service import.** `event-gateway` and `registry-api` share no Python package (confirmed: separate `requirements.txt`, separate deployments). The gateway's `_decrypt_secret`/Fernet handling is already a deliberate small **port** of `registry-api/crypto.py`, not an import — `lookup_application`/`has_active_invoker_grant` follow the identical pattern: raw `psycopg2` SQL against `applications`/`artifact_role_grants`, no dependency on `registry-api`'s `rbac.py` or its async SQLAlchemy session. (This is also why `rbac.can_invoke` from the design doc's §6 pseudocode is **not** implemented in `registry-api` at all — there is no live caller for it there; the design doc's illustrative logic is realized here, in the gateway's own port functions. See `research.md` §4/§5.)

---

## `_TRIGGER_SQL` — two new selected columns

**Before** (5 columns; `workflow_id` is `NULL` for the `"agent"` kind):
```sql
SELECT t.id::text, t.token_hash, t.filter_conditions, t.auth_mode, NULL          -- "agent"
SELECT t.id::text, t.token_hash, t.filter_conditions, t.auth_mode, w.id::text    -- "workflow"
```

**After** (7 columns — adds the artifact's own id and owning team, both needed by the new `_verify_client_signed`):
```sql
SELECT t.id::text, t.token_hash, t.filter_conditions, t.auth_mode, NULL,
       a.id::text, a.team                                                        -- "agent"
FROM agent_triggers t JOIN agents a ON t.agent_id = a.id
WHERE a.name = %s AND t.trigger_type = 'webhook' AND t.enabled = true

SELECT t.id::text, t.token_hash, t.filter_conditions, t.auth_mode, w.id::text,
       w.id::text, w.team                                                        -- "workflow"
FROM agent_triggers t JOIN workflows w ON t.workflow_id = w.id
WHERE w.name = %s AND t.trigger_type = 'webhook' AND t.enabled = true
```
`lookup_triggers(kind, name)` also injects `"artifact_type": kind` into each returned dict (the `kind` parameter already distinguishes `"agent"`/`"workflow"` one-to-one with `artifact_role_grants.artifact_type`'s own CHECK values — no new column needed for that part, just carry the parameter through into the row dict alongside the two new SQL columns `artifact_id` and `team_name`).

Resulting dict shape (was 5 keys, now 7): `{id, token_hash, filter_conditions, auth_mode, workflow_id, artifact_id, team_name, artifact_type}` (8 — `workflow_id` is kept for backward compat with any other reader of `lookup_triggers`'s return shape; `artifact_id`/`artifact_type`/`team_name` are new).

---

## New port functions (mirror `lookup_webhook_client`'s shape exactly — same file, same style, same live-no-cache posture)

```python
def lookup_application(team_name: str, name: str) -> dict | None:
    """Resolve one application by (owning team, name) — NOT filtered on `enabled`
    here; enabled is checked as its own explicit step in _verify_client_signed so
    'unknown application' and 'disabled application' can each be logged with their
    own real reason server-side (still the same _DENY to the caller)."""
    # SELECT id::text, secret_encrypted, enabled FROM applications
    # WHERE team_name = %s AND name = %s

def has_active_invoker_grant(artifact_type: str, artifact_id: str, application_id: str) -> bool:
    """Active (revoked_at IS NULL) invoker grant for this application on this
    artifact. artifact_type is passed explicitly (not re-derived) — the No-Bandaid
    rule this module's own header already documents: an explicit context parameter,
    never type-sniffed."""
    # SELECT 1 FROM artifact_role_grants
    # WHERE artifact_type = %s AND artifact_id = %s AND role = 'invoker'
    #   AND grantee_type = 'application' AND grantee_id = %s AND revoked_at IS NULL
    # LIMIT 1
```

`lookup_webhook_client` (the function these two replace) is **deleted** from `webhook_auth.py` — after the cutover it has no caller anywhere in `event-gateway` (confirmed: its only call site was inside `_verify_client_signed`, which no longer calls it). Leaving it in place unused would itself be orphan code inside the exact module whose own header docstring warns against silently-diverging duplicate logic.

---

## `_verify_client_signed` — new resolution order (design doc §7, verbatim order preserved)

```python
def _verify_client_signed(trigger: dict, headers, raw_body: bytes) -> WebhookAuthResult:
    client_id = headers.get(H_CLIENT_ID)
    if not client_id:
        return _deny("no X-Client-Id on a client_signed trigger", trigger=trigger["id"])

    app = lookup_application(trigger["team_name"], client_id)
    if app is None:
        return _deny("no application matches this team/client_id",
                     trigger=trigger["id"], client=client_id)

    if not has_active_invoker_grant(trigger["artifact_type"], trigger["artifact_id"], app["id"]):
        return _deny("no active invoker grant", trigger=trigger["id"], client=client_id)

    if not app["enabled"]:
        return _deny("application disabled", trigger=trigger["id"], client=client_id)

    if not _fresh(headers.get(H_TIMESTAMP)):
        return _deny("timestamp missing/stale/malformed", trigger=trigger["id"], client=client_id)

    try:
        secret = _decrypt_secret(app["secret_encrypted"])
    except (InvalidToken, ValueError, KeyError, RuntimeError) as exc:
        logger.error("cannot decrypt secret for application=%s on trigger=%s: %s",
                     client_id, trigger["id"], exc)
        return _deny("secret undecryptable", trigger=trigger["id"], client=client_id)

    expected = "sha256=" + hmac.new(
        secret.encode(), f"{headers.get(H_TIMESTAMP)}.".encode() + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, headers.get(H_SIGNATURE) or ""):
        return _deny("bad signature", trigger=trigger["id"], client=client_id)

    return WebhookAuthResult(ok=True, trigger=trigger, client_id=client_id)
```

**Ordering rationale (matches design doc §7 exactly):** application-lookup → grant-check → enabled-check → freshness → signature. The grant check runs *before* the enabled check on purpose — a disabled application with an otherwise-valid grant must log `"application disabled"` (not `"no active invoker grant"`), which is what lets an agent-admin's Studio grants list show a distinguishing "application disabled" badge next to a grant that would otherwise look fully active (design doc §9.8) — that distinction is made from the **registry-api side** (reading `applications.enabled` directly, not from anything the gateway leaks), so ordering the gateway's own checks this way costs nothing security-wise (the caller still gets one uniform `_DENY` either way) while keeping the two log reasons meaningfully distinct for the operator reading gateway logs.

---

## Worked example — T-SYY-004 through T-SYY-006

```
# T-SYY-004 — happy path
POST /hooks/invoice-processor/{path_token}
X-Client-Id: billing-service
X-Timestamp: <now>
X-Signature: sha256=<HMAC(secret, "{ts}." + body)>
→ 202, a REAL agent_events row committed, status='matched', client_id='billing-service'

# T-SYY-005 — revoked grant (application still enabled, secret still correct)
DELETE /api/v1/artifacts/agent/{artifact_id}/grants/{grant_id}     # revoke first
POST /hooks/invoice-processor/{path_token}   (same headers as above, same secret)
→ 401  { "detail": "invalid webhook credentials" }   # exact existing uniform body

# T-SYY-006 — disabled application (grant still active, secret still correct)
PATCH /api/v1/teams/payments/applications/{app_id}  { "enabled": false }
POST /hooks/invoice-processor/{path_token}   (same headers as above, same secret)
→ 401  { "detail": "invalid webhook credentials" }   # BYTE-IDENTICAL to T-SYY-005's body
```

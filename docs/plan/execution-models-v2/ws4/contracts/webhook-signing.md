# WS-4 Contract — webhook client-signed wire format + verify order

## Request headers (sender → gateway, `auth_mode=client_signed`)

| Header | Value |
|---|---|
| `X-Client-Id` | the application's client-id (allowlisted per trigger) |
| `X-Timestamp` | unix seconds (integer) at send time |
| `X-Signature` | `sha256=` + hex `HMAC_SHA256(secret, f"{X-Timestamp}.{raw_body}")` |

`raw_body` = the exact bytes of the POST body (sign **before** any parsing; verify against the raw bytes, not
a re-serialized JSON — re-serialization changes bytes and breaks the MAC).

The URL path token (`/hooks/{name}/{token}`) is **ignored** for `client_signed` triggers (it stays for
`token` mode). The route still matches; auth is by header, not path token.

## Verify order (fail-closed, uniform 401)

```python
def verify_webhook_auth(trigger, headers, raw_body) -> WebhookAuthResult:
    if trigger.auth_mode == "token":
        # existing path (main.py:99,:136) — unchanged
        return _verify_bearer_token(trigger, path_token)      # 401 on mismatch

    # client_signed:
    client_id = headers.get("X-Client-Id")
    ts        = headers.get("X-Timestamp")
    sig       = headers.get("X-Signature")

    client = lookup_webhook_client(trigger.id, client_id)     # SELECT ... WHERE trigger_id AND client_id
    if client is None or not client.enabled:
        return _401()                                          # unknown OR disabled → identical 401
    if not _fresh(ts, window=300):                             # |now - ts| <= 300s
        return _401()                                          # stale/replay-window → identical 401
    expected = "sha256=" + hmac_sha256_hex(client.secret, f"{ts}.{raw_body}")
    if not hmac.compare_digest(expected, sig or ""):           # constant-time
        return _401()                                          # bad signature → identical 401
    return _ok(client_id)                                      # → filter_engine → rate_limiter → dispatch
```

- **Every failure returns the identical 401 body** — no distinction between unknown-client, disabled,
  stale, and bad-signature (no enumeration/timing oracle). One shared `_401()`.
- **Constant-time compare** (`hmac.compare_digest`) for the signature.
- On success: proceed into the **existing** `filter_engine` + `rate_limiter`, then dispatch; **stamp
  `agent_events.client_id`** with the resolved client.
- **Both handlers call this** — agent hook (`main.py:314`) and workflow hook (`main.py:199`). One definition,
  two call sites.

## Registration API

> **Re-grounded 2026-07-15 — this is a NEW router.** No `/api/v1/triggers` prefix exists in the live tree:
> `routers/triggers.py` is mounted at `/api/v1/agents` (routes `/{name}/triggers/{trigger_id}`) and workflow
> triggers live in `routers/composite_workflows.py` at `/api/v1/workflows`. These endpoints therefore ship as
> a **new dedicated router `services/registry-api/routers/webhook_clients.py`**, `prefix="/api/v1/triggers"`,
> keyed on **`trigger_id` alone** — so **one** router serves **both** agent and workflow triggers (workflow
> triggers are `agent_triggers` rows with `workflow_id` set) with no per-shape copy.

```
POST /api/v1/triggers/{trigger_id}/clients
  body: {"client_id": "billing-app"}
  201:  {"client_id": "billing-app", "secret": "whsec_...", "created_at": "..."}   ← secret shown ONCE
GET    /api/v1/triggers/{trigger_id}/clients      → [{client_id, enabled, created_by, created_at}]  (no secret)
PATCH  /api/v1/triggers/{trigger_id}/clients/{client_id}  body: {"enabled": false}
DELETE /api/v1/triggers/{trigger_id}/clients/{client_id}
```

- Creating the **first** client on a webhook trigger sets `agent_triggers.auth_mode='client_signed'` (or the
  trigger is created `client_signed` by default per WS-4 goal #2). The `secret` is returned only in the 201;
  it is never retrievable again.
- **Storage is `secret_encrypted`, NOT `secret_hash`** (re-grounded 2026-07-15): the gateway must *recompute*
  `HMAC_SHA256(secret, …)`, so it needs the raw secret back — a one-way hash is unimplementable here. The
  secret is stored **Fernet-encrypted** via the platform helper `crypto.py:34` (`encrypt_json`) and decrypted
  in the gateway. Reveal-once is enforced **structurally**: `WebhookClientResponse` has **no secret field at
  all**, so a leak on the read path is unrepresentable rather than filtered.

## Sender reference (ship this so tests + real apps sign identically)

```python
# reference signer — the e2e suite and real applications import the SAME helper (no drift).
def sign_webhook(secret: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Timestamp": str(ts), "X-Signature": f"sha256={mac}"}
```

## Uniform-401 body

> **Corrected 2026-07-15 against live code.** This section previously specified `{"detail": "unauthorized"}`,
> which **never matched** the running gateway. `_uniform_401()` (`services/event-gateway/main.py:149-151`)
> already returns the body below. The **code is authoritative** — rewriting the body would break the contract
> for every existing token sender for zero security gain. WS-4 **reuses the existing `_uniform_401()`**.

```json
{"detail": "invalid webhook credentials"}
```
(Same for every failure mode; 401 status.)

> ⚠️ **Pre-existing 401 oracle that WS-4 closes.** The stale-timestamp branches
> (`main.py:262` workflow hook, `:366` agent hook) currently return a **different** body —
> `{"detail": "stale webhook timestamp"}` — which tells an attacker *which* check failed. That is an
> enumeration oracle in the live product today. WS-4 routes those branches through `_uniform_401()`, and
> suite-76's `T-S76-003` asserts all five failure modes are **byte-identical**, so it cannot silently regress.

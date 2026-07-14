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
  it is never retrievable again (store `secret_hash`/encrypted, reveal once).

## Sender reference (ship this so tests + real apps sign identically)

```python
# reference signer — the e2e suite and real applications import the SAME helper (no drift).
def sign_webhook(secret: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Timestamp": str(ts), "X-Signature": f"sha256={mac}"}
```

## Uniform-401 body

```json
{"detail": "unauthorized"}
```
(Same for every failure mode; 401 status.)

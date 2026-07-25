# Contract — MCP Proxy internal endpoints, Phase 2 additions

Extends `docs/plan/mcp-tool-source-phase1/contracts/mcp-proxy-internal.md`. Service unchanged: `services/mcp-proxy`, in-cluster `agentshield-mcp-proxy.agentshield-platform.svc.cluster.local:8080`. Phase 2 adds **one new endpoint** (`POST /internal/health`) and changes the **credential-selection behavior** of the two existing session-establishing paths (`/internal/discover`, `/internal/tools/call`) without changing their request/response wire shapes. **Auth model (§3b), the no-DB / no-master-key invariants, and the "tool/transport failure = 200 error body, only real auth failures = 401/403" convention are all unchanged.**

New request/response models go in `services/mcp-proxy/schemas.py`; the endpoint in `services/mcp-proxy/main.py`; the credential branch in the new `services/mcp-proxy/identity.py`; the subscriber in the new `services/mcp-proxy/subscription_manager.py`.

---

## 1. `POST /internal/health` — NEW, admin plane (WS-A / FR-MCP-22)

Caller = **registry-api's health loop only** (same admin-plane restriction as `/internal/discover`: authenticate the Bearer SA token via TokenReview, then require `caller_sa_subject == config.REGISTRY_API_SA_SUBJECT`, else `403`). A lightweight liveness probe that does **not** return the tool list and triggers **no** `Tool`-row change.

### Request — `McpHealthRequest`
```python
class McpHealthRequest(BaseModel):
    server_id: UUID
```

### Server-side flow
1. `_echo_trace(...)`; `sa_subject = await _authenticate(authorization)` (401 on fail); require `sa_subject == config.REGISTRY_API_SA_SUBJECT` else `403`.
2. `connection = await credentials.read_server_secret(str(server_id))`. `ServerSecretNotFound` → `200 McpHealthResponse(ok=False, status="error", health_detail=str(exc))` (never 5xx).
3. Resolve headers via `identity.resolve_headers(connection, user_sub=None, is_data_plane=False)` (admin plane → `none`=static, `service_identity`/`on_behalf_of`=platform SA token; C6/C7). A `service_identity` Keycloak-mint failure → `200 ok=False, status="error", health_detail=f"identity: {exc}"`.
4. `cached = await session_cache.get_or_create(server_id)` (reuses the pooled session; on miss reconnects). Connect failure → evict + `200 ok=False, status="error", health_detail=f"connect failed to {url}: {exc}"`.
5. `await cached.session.list_tools()` as the liveness probe, bounded by `config.MCP_CONNECT_TIMEOUT_SECONDS`. On failure → `await session_cache.evict(server_id)` + `200 ok=False, status="error", health_detail=f"tools/list failed: {exc}"`.
6. On success: if `cached.session.list_changed_supported` → `subscription_manager.ensure_subscription(str(server_id))` (idempotent; WS-B). Return success.

### Response — `McpHealthResponse` (always HTTP `200` for reachability outcomes)
```python
class McpHealthResponse(BaseModel):
    ok: bool                          # true iff the probe (session + tools/list) succeeded
    status: str                       # 'connected' | 'error'  (advisory; registry-api applies the threshold/backoff)
    health_detail: str | None = None  # failure reason string; None on success
    protocol_version: str | None = None
    list_changed_supported: bool = False
    tool_count: int = 0               # len(tools) from the probe; advisory
```
Success example:
```json
{ "ok": true, "status": "connected", "health_detail": null,
  "protocol_version": "2025-06-18", "list_changed_supported": true, "tool_count": 7 }
```
Failure example (still `200`):
```json
{ "ok": false, "status": "error", "health_detail": "connect failed to http://…: [Errno 111]",
  "protocol_version": null, "list_changed_supported": false, "tool_count": 0 }
```
**Ownership split (unchanged principle):** the proxy reports *reachability*; **registry-api owns the DB write** — it applies the failure threshold / backoff (research.md C3) and writes `mcp_servers.status` + `health_detail`. The proxy's `status` field is advisory (a single probe result), **not** the persisted status. `list_changed_supported` in the response lets the loop keep that column fresh.

### Errors (real HTTP status)
- `401` — missing/invalid/wrong-audience token.
- `403` — authenticated but `caller_sa_subject != REGISTRY_API_SA_SUBJECT` (admin plane).
- `422` — malformed body.
- No 5xx for a downstream MCP problem (→ `200 ok=false`).

---

## 2. Credential-selection change to `/internal/discover` and `/internal/tools/call` (WS-C / FR-MCP-21)

**Wire shapes unchanged.** What changes: both endpoints now build the upstream MCP connection headers through `identity.resolve_headers(...)` instead of using `connection.auth_headers` directly. This is transparent for `identity_mode='none'` (the default and every Phase-1 server) — `resolve_headers` returns exactly `connection.auth_headers`, so Phase-1 behavior is byte-identical (proven by suite-84 staying green).

### `identity.resolve_headers` selection matrix

```python
async def resolve_headers(connection: ServerConnection, *,
                          user_sub: str | None, is_data_plane: bool) -> dict[str, str]:
```
| `identity_mode` | plane | Result |
|---|---|---|
| `none` (default) | any | `connection.auth_headers` (Phase-1 static creds). |
| `service_identity` | any | `{"Authorization": "Bearer <keycloak client-credentials token>"}`, audience `connection.identity_audience` (or default); token cached until `exp − skew`. Merged over static headers (SA bearer wins on `Authorization`). |
| `on_behalf_of` | admin (`is_data_plane=False`: discover/health) | Platform SA token (same as `service_identity`) — the platform lists an OBO server's tools as itself. |
| `on_behalf_of` | data (`is_data_plane=True`: tools/call), `user_sub` empty | **raise `OnBehalfOfIdentityRequired`** → caller returns `200 is_error=true, error="on_behalf_of server requires a user identity; none provided"` (fail-closed, no silent fallback — FR-MCP-21 pt 4). |
| `on_behalf_of` | data, `user_sub` present | `mint_on_behalf_of_token(user_sub, connection)` — **STUB in Phase 2**: raises `OnBehalfOfNotAvailable` → caller returns `200 is_error=true, error="on_behalf_of upstream-identity exchange is not yet available (blocked on Decision 29)"`. |

### `/internal/tools/call` changes (data plane)
- Reads the existing `x_user_sub: str | None = Header(default=None)` (already declared).
- Computes the session key with `user_sub` (composite key routing, C8): `cached = session_cache.peek(server_id, user_sub)` / `get_or_create(server_id, user_sub)` — `user_sub` participates in the key **only** for `on_behalf_of` servers (`_effective_user_sub`).
- Builds headers via `resolve_headers(connection, user_sub=x_user_sub, is_data_plane=True)`. An `OnBehalfOfIdentityRequired`/`OnBehalfOfNotAvailable` is caught and returned as a `200 is_error=true` body (not a 5xx, not a 403 — it is a per-call tool error the LLM should see).
- Adds one eviction trigger to the existing evict-and-retry-once: a `401` from an upstream `service_identity` server forces a token-cache refresh (`keycloak_client.invalidate(audience)`) before the retry.

### `/internal/discover` changes (admin plane)
- Builds headers via `resolve_headers(connection, user_sub=None, is_data_plane=False)`.
- On success, if `session.list_changed_supported` → `subscription_manager.ensure_subscription(str(server_id))` (WS-B). (Discover is the first place a fresh server's capability is known.)

**Invariants preserved:** the proxy still reads only the per-server Secret + mints tokens with its **own** Keycloak client secret (a narrow file-mounted credential); it still holds **no** DB connection and **no** `AGENTSHIELD_ENCRYPTION_KEY`. The `x-user-sub` header still drives **no** credential decision for `none`/`service_identity` servers.

---

## 3. `list_changed` subscription behavior (WS-B / FR-MCP-07) — internal, no new caller-facing endpoint

The subscriber is internal to the proxy (`subscription_manager.py`); it exposes no HTTP endpoint. Its **outbound** call is to registry-api (see `contracts/registry-api-internal-mcp-phase2.md`):

- `ensure_subscription(server_id)`: idempotent; if not already running and `MCP_LIST_CHANGED_ENABLED`, spawn a long-lived task that holds an MCP session (via `mcp_client.connect_and_initialize(url, headers, message_handler=…)`) whose notification handler fires on `notifications/tools/list_changed`.
- On a notification: debounce (`MCP_LIST_CHANGED_DEBOUNCE_SECONDS`=5, per-server timer) then `POST {REGISTRY_API_URL}/api/v1/internal/mcp/list-changed {server_id}` (NetworkPolicy-trusted, no Bearer — same trust as the existing `authorize-tool-call` callback). Best-effort; a failed POST is logged and retried on the next notification.
- Reconnect on session drop with capped backoff (`RECONNECT_BACKOFF_SECONDS`=10, `MAX_RECONNECT_ATTEMPTS`=5); after the cap (e.g. the per-server Secret was deleted on server delete), tear the subscription down.
- Multi-replica: each replica with a subscribed session may fire; registry-api coalesces (idempotent + min-interval). Exactly-once is **not** guaranteed (ledgered).

**`mcp_client` change:** `connect_and_initialize(server_url, headers, message_handler=None)` and `McpSession._connect(..., message_handler=None)` gain an optional handler passed to `ClientSession(read, write, message_handler=…)`. The exact SDK hook (handler kwarg vs. iterating `session.incoming_messages`) is **pinned by verification task T-P5** against the installed `mcp` version; the persistent-session + handler + debounced-callback design is unaffected by which hook the SDK exposes.

---

## 4. New proxy config (all env, `services/mcp-proxy/config.py`)

| Constant | Env var | Default | Used by |
|---|---|---|---|
| `MCP_LIST_CHANGED_ENABLED` | `MCP_LIST_CHANGED_ENABLED` | `true` | subscription_manager |
| `MCP_LIST_CHANGED_DEBOUNCE_SECONDS` | `MCP_LIST_CHANGED_DEBOUNCE_SECONDS` | `5` | subscription_manager |
| `MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS` | `MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS` | `10` | subscription_manager |
| `MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS` | `MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS` | `5` | subscription_manager |
| `KEYCLOAK_TOKEN_URL` | `KEYCLOAK_TOKEN_URL` | `""` (must be set for service_identity) | keycloak_client |
| `MCP_PROXY_KEYCLOAK_CLIENT_ID` | `MCP_PROXY_KEYCLOAK_CLIENT_ID` | `"agentshield-mcp-proxy"` | keycloak_client |
| `MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH` | `MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH` | `/var/run/secrets/mcp-proxy-keycloak/client-secret` | keycloak_client |
| `KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS` | `KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS` | `30` | identity token cache |
| (`MCP_CONNECT_TIMEOUT_SECONDS` already exists, default 30, **now wired** into the health probe + subscriber connect) | | | mcp_client/main |

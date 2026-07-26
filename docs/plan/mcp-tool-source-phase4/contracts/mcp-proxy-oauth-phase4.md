# Contract — mcp-proxy, Phase 4 additions (WS-2 OAuth token-read)

Extends `docs/plan/mcp-tool-source-phase2/contracts/mcp-proxy-internal-phase2.md`.

The proxy gains **no new inbound endpoint** and **no DB/master key**. It gains one outbound call (to registry-api's token-read endpoint), one new `ServerConnection` field, one new `resolve_headers` branch, and an in-memory per-`(server,user)` access-token cache. Every OAuth failure is a `200 is_error` body (fail-closed) — never a 5xx, never a silent unauthenticated upstream call.

---

## 1. Credential-selection change to `resolve_headers` — the OAuth branch (WS-2)

`identity.resolve_headers(connection, *, user_sub, is_data_plane)` gains a **first-checked** branch, *before* the `identity_mode` switch (`research.md` C7):

| `external_auth_mode` | `identity_mode` | plane | Result |
|---|---|---|---|
| `static` (default) | any | any | **unchanged** — falls through to the Phase-2 `identity_mode` matrix (byte-identical). |
| `oauth` | `none` (enforced) | data, `user_sub` set | `{**auth_headers, "Authorization": "Bearer <access>"}` where `<access>` = `oauth_tokens.get_oauth_access_token(server_id, user_sub)`. |
| `oauth` | `none` | data, no `user_sub` | raise `OAuthUserRequired` → caller returns `200 is_error` "server requires a user identity". |
| `oauth` | `none` | admin, `user_sub` set | same as data (discovery/health uses the supplied authorizing user's token, C9). |
| `oauth` | `none` | admin, no `user_sub` | raise `OAuthUserRequired` (registry-api always supplies the authorizing user for an OAuth discover; a bare admin probe with no user fails closed). |

`get_oauth_access_token` raises `OAuthAuthorizationRequired` when registry-api returns `status in (needs_auth, error)`, and `OAuthTokenUnavailable` on a transport/registry-api failure. Both map to a `200 is_error` body at the endpoint.

**New exceptions** (`services/mcp-proxy/oauth_tokens.py`):
```python
class OAuthUserRequired(Exception): ...          # data/admin plane, no user_sub for an OAuth server
class OAuthAuthorizationRequired(Exception): ...  # registry-api says needs_auth/error → user must (re-)authorize
class OAuthTokenUnavailable(Exception): ...       # transport / registry-api failure fetching the token
```

**Security invariant:** an OAuth server whose token cannot be produced NEVER downgrades to the static `auth_headers` (which are `{}` anyway) nor to a service token — it fails closed, exactly like `on_behalf_of` (identity.py:105-117).

---

## 2. `oauth_tokens.get_oauth_access_token(server_id, user_sub)` — the token-read path (WS-2)

New module `services/mcp-proxy/oauth_tokens.py`. Mirrors `keycloak_client.py`'s cache+mint shape, but the "mint" is an HTTP pull from registry-api (the proxy never talks to the upstream AS).

```python
# (server_id, user_sub) -> (access_token, exp_epoch). In-memory, per-replica.
# NEVER persisted (design invariant: the proxy stores no token). Mirrors
# keycloak_client._token_cache but keyed per user (OAuth tokens are user-scoped).
_access_cache: dict[tuple[str, str], tuple[str, int]] = {}

async def get_oauth_access_token(server_id: str, user_sub: str) -> str:
    """Return a fresh upstream access token for (server, user).

    Cache hit while now < exp - MCP_OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS. On a miss,
    POST config.REGISTRY_API_OAUTH_TOKEN_URL {server_id, user_sub} with the proxy's
    projected SA token (audience agentshield-registry-api, read fresh from
    MCP_PROXY_REGISTRY_API_TOKEN_PATH). registry-api resolves the refresh token,
    performs the refresh (rotating it), and returns {status, access_token, expires_at}.

    Raises OAuthAuthorizationRequired if status != 'authorized' (needs_auth/error);
    OAuthTokenUnavailable on transport failure / non-2xx / missing token."""

def invalidate(server_id: str, user_sub: str) -> None:
    """Drop the cached access token (called on an upstream 401 so the retry re-pulls)."""
```

### Server-side flow (proxy → registry-api)
1. Cache lookup `(server_id, user_sub)`; return if fresh.
2. Read the projected SA token fresh from `MCP_PROXY_REGISTRY_API_TOKEN_PATH` (rotates hourly; empty/missing → `OAuthTokenUnavailable`).
3. `POST REGISTRY_API_OAUTH_TOKEN_URL {server_id, user_sub}` with `Authorization: Bearer <SA token>`, timeout `REGISTRY_API_TIMEOUT_SECONDS`.
4. Non-2xx (`401`/`403`/`5xx`) or transport error → `OAuthTokenUnavailable`.
5. Body `status != 'authorized'` → `OAuthAuthorizationRequired(detail)`.
6. `status == 'authorized'` → cache `(access_token, parse(expires_at))`, return the token.

**No refresh-token, no client secret, no master key ever touches the proxy** — it receives only a short-lived access token it caches in memory and drops on `exp`.

---

## 3. `/internal/tools/call` and `/internal/discover` deltas (WS-2)

### `/internal/tools/call` (data plane, `main.py::tools_call`)
`resolve_headers(..., user_sub=x_user_sub, is_data_plane=True)` already runs. New `except` arms render the OAuth outcomes as `200 is_error` (alongside the existing OBO arms):
```python
except identity.OAuthUserRequired:
    return McpToolCallResponse(is_error=True,
        error="this MCP server uses OAuth and requires a user identity, but none was provided")
except identity.OAuthAuthorizationRequired as exc:
    return McpToolCallResponse(is_error=True,
        error=f"this MCP server needs (re-)authorization: {exc}")
except identity.OAuthTokenUnavailable as exc:
    return McpToolCallResponse(is_error=True, error=f"oauth token unavailable: {exc}")
```
The evict-and-retry block (main.py:393-427) additionally calls `oauth_tokens.invalidate(server_id, x_user_sub)` before the retry when `connection.external_auth_mode == 'oauth'` (self-heals an upstream 401 from a just-expired access token — the retry re-pulls a fresh one), symmetric to the existing `keycloak_client.invalidate(...)` for `service_identity`.

### `/internal/discover` (admin plane, `main.py::discover`)
`McpDiscoverRequest` gains an optional `user_sub`:
```python
class McpDiscoverRequest(BaseModel):
    server_id: UUID
    user_sub: str | None = None      # NEW — the authorizing user for an OAuth server (C9)
```
`discover` passes `user_sub=req.user_sub` into `resolve_headers(..., is_data_plane=False)`. For a `static` server `user_sub` is ignored (byte-identical). For an `oauth` server with no `user_sub`, `OAuthUserRequired` → the existing `200 ok=false status='error'` discover body ("identity: …"). `mcp_health.health_check_server` similarly passes the most-recently-authorized user's sub for an OAuth server (C9); if none, registry-api reports `needs_auth` without hitting the proxy.

---

## 4. New proxy config (all env, `services/mcp-proxy/config.py`)

| Constant | Env var | Default | Used by |
|---|---|---|---|
| `REGISTRY_API_OAUTH_TOKEN_URL` | `REGISTRY_API_OAUTH_TOKEN_URL` | `{REGISTRY_API_URL}/api/v1/internal/mcp/oauth/access-token` | `oauth_tokens` (token pull). |
| `MCP_PROXY_REGISTRY_API_TOKEN_PATH` | `MCP_PROXY_REGISTRY_API_TOKEN_PATH` | `/var/run/secrets/registry-api/token` | `oauth_tokens` (SA token, audience `agentshield-registry-api`). |
| `MCP_OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS` | same | `30` | `oauth_tokens` cache skew. |

**Invariant preserved:** these add an *outbound* call + a *projected token* only. No DB URL, no `AGENTSHIELD_ENCRYPTION_KEY`, no new inbound endpoint — `config.py`'s "no DB, no master key" docstring still holds. The new projected token (audience `agentshield-registry-api`) is a *second* projected SA token in the pod, alongside the existing OPA + mcp-proxy tokens (chart `deployment.yaml`).

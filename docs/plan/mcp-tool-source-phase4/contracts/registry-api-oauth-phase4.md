# Contract — registry-api, Phase 4 additions (WS-1 provider seam + WS-2 OAuth)

Extends `docs/plan/mcp-tool-source-phase1/contracts/` (mcp-servers CRUD) and `docs/plan/mcp-tool-source-phase2/contracts/registry-api-internal-mcp-phase2.md`.

New router: `services/registry-api/routers/mcp_oauth.py` (user-facing OAuth endpoints). One new endpoint added to `services/registry-api/routers/internal_mcp.py` (the proxy token-read). All user-facing endpoints require `require_user` (Keycloak JWT) unless noted.

---

## 1. `POST /api/v1/mcp-servers/{server_id}/oauth/authorize` — begin the authorization-code dance (WS-2)

Auth: `require_user`. The caller becomes the authorizing user (`user_sub = jwt.sub`).

### Server-side flow
1. Load the server; `404` if absent; `409 not_oauth_server` if `external_auth_mode != 'oauth'`.
2. `409 oauth_not_configured` if `MCP_OAUTH_CALLBACK_URL` is empty.
3. `mcp_oauth.discover_oauth_metadata(server.server_url)` → protected-resource + authorization-server metadata. On failure → `502 oauth_discovery_failed` (with reason).
4. Resolve/register the client: if `server.oauth_client_ref` set, `provider.get` it; else if the AS advertises `registration_endpoint`, `mcp_oauth.register_client(...)`, `provider.put` at `…/mcp-oauth-client/{server_id}`, set `oauth_client_ref`; else use the pre-registered `client_id`/`client_secret` from the server's `AuthConfig` (fallback, ledgered) or `409 oauth_no_client`.
5. Generate PKCE (`code_verifier`, `code_challenge` S256) + a Fernet `state = crypto.encrypt_json({server_id, user_sub, code_verifier, nonce, exp})` (`research.md` C5).
6. Upsert `mcp_oauth_grants(server_id, user_sub)` `status='needs_auth'` (created if absent).
7. `mcp_oauth.build_authorization_url(...)` → the AS `authorization_endpoint` with `response_type=code`, `client_id`, `redirect_uri=MCP_OAUTH_CALLBACK_URL`, `code_challenge`, `code_challenge_method=S256`, `state`, `scope` (from metadata/resource), `resource` (RFC 8707 canonical server URL).

### Response — `200`
```json
{ "authorization_url": "https://auth.example.com/authorize?response_type=code&client_id=...&state=...&code_challenge=..." }
```
Studio sets `window.location.href = authorization_url` (a full-page redirect to the upstream consent screen).

### Errors (real HTTP status)
- `401` — no/invalid JWT.
- `404` — server not found.
- `409` — `not_oauth_server` / `oauth_not_configured` / `oauth_no_client` (JSON `{detail:{code, message}}`).
- `502` — `oauth_discovery_failed` (upstream `.well-known` unreachable/malformed).

### Auth invariant
Only an authenticated user may start a flow, and the flow is bound to *that* user's sub inside the encrypted `state` — the callback cannot be steered to a different user's grant.

---

## 2. `GET /api/v1/mcp-servers/oauth/callback` — the upstream redirect URI (WS-2)

This is the URL registered with every upstream AS (`MCP_OAUTH_CALLBACK_URL`). It is **not** a JSON API — it 302-redirects the browser back to Studio. Auth: none required on the request itself (the browser arrives from the AS); trust comes from the Fernet `state` + PKCE + the one-time code.

Query params: `code`, `state`, optional `iss` (RFC 9207), optional `error`/`error_description` (user denied).

### Server-side flow
1. If `error` present (user denied / AS error) → set the grant `status='error'`, `last_error=error_description` → `302` to `{STUDIO_BASE_URL}/mcp-servers/{server_id}?oauth=denied`.
2. `crypto.decrypt_json(state)`; on failure or `exp` passed → `302 …?oauth=invalid_state` (no grant mutation).
3. Recover `{server_id, user_sub, code_verifier}`. If `iss` present, validate it equals the AS issuer (`oauth_flow_error` → `302 …?oauth=error`).
4. `mcp_oauth.exchange_code(code, code_verifier, redirect_uri, client)` → `{access_token, refresh_token, expires_in, scope}`. On `invalid_grant`/failure → grant `status='error'`, `last_error` → `302 …?oauth=error`.
5. `provider.put(refresh_ref, {"refresh_token": ...})` at `…/mcp-oauth-refresh/{server_id}/{user_sub}`; set grant `credential_ref=refresh_ref`, `status='authorized'`, `scopes`, `token_expires_at=now+expires_in`, `last_error=NULL`.
6. Trigger discovery as this user: `mcp_proxy_client.discover_server(server_id, user_sub=user_sub)` → fold the result into `Tool` rows via the shared `_materialize_and_discover` (best-effort; a discovery failure does not fail the callback — the grant is still authorized).
7. `302` to `{STUDIO_BASE_URL}/mcp-servers/{server_id}?oauth=connected`.

### Response
Always a `302 Location:` redirect to Studio (never a JSON body, never a 5xx to the browser). The `?oauth=` query param drives the Studio toast + status refetch.

### Auth invariant
The refresh token is written **only** for the `(server_id, user_sub)` recovered from the *decrypted* state — a forged/tampered state fails Fernet verification and mutates nothing. The refresh token value never appears in a response body or a redirect URL.

---

## 3. `GET /api/v1/mcp-servers/{server_id}/oauth/status` — grant status for the Studio badge (WS-2)

Auth: `require_user`. Reports the calling user's grant for this server.

### Response — `200`, `McpOAuthStatusResponse`
```python
class McpOAuthStatusResponse(BaseModel):
    server_id: UUID
    user_sub: str
    status: str                       # "needs_auth" | "authorized" | "error"
    scopes: str | None = None
    token_expires_at: datetime | None = None
    last_error: str | None = None
    external_auth_mode: str           # "static" | "oauth" (so Studio can hide the panel for static)
```
Example (authorized): `{"server_id":"…","user_sub":"u-1","status":"authorized","scopes":"repo read:user","token_expires_at":"2026-07-25T12:00:00Z","last_error":null,"external_auth_mode":"oauth"}`
Example (never authorized): `{"…","status":"needs_auth","external_auth_mode":"oauth"}` (no grant row → synthesized `needs_auth`).

### Errors
- `401` — no/invalid JWT. `404` — server not found.

### Auth invariant
Returns only the caller's own grant (keyed on `jwt.sub`) — never another user's status, never the token or its ref.

---

## 4. `DELETE /api/v1/mcp-servers/{server_id}/oauth` — disconnect / revoke (WS-2)

Auth: `require_user`. Revokes the calling user's grant.

### Server-side flow
1. Load grant `(server_id, jwt.sub)`; `204` (idempotent no-op) if absent.
2. Best-effort revoke at the AS `revocation_endpoint` (if advertised); failures are logged, not fatal.
3. `provider.delete(grant.credential_ref)`; set `status='needs_auth'`, `credential_ref=NULL`, `token_expires_at=NULL` (or delete the row).

### Response — `204 No Content`. **Errors:** `401`, `404` (server not found).

### Auth invariant
Deletes only the caller's own refresh token; never touches another user's grant.

---

## 5. `POST /api/v1/internal/mcp/oauth/access-token` — proxy token-read (WS-2, internal)

Added to `routers/internal_mcp.py`. **Unlike the other two internal endpoints this returns a bearer token, so it authenticates the caller** (`research.md` C4).

Auth: `Authorization: Bearer <proxy SA token>` (audience `MCP_PROXY_SA_AUDIENCE = agentshield-registry-api`), verified via `TokenReview`; the review subject MUST equal the mcp-proxy SA (`system:serviceaccount:{ns}:{release}-mcp-proxy`), else `403`. Requires registry-api ClusterRole `tokenreviews: create`.

### Request — `OAuthAccessTokenRequest`
```python
class OAuthAccessTokenRequest(BaseModel):
    server_id: uuid.UUID
    user_sub: str = Field(..., min_length=1)
```

### Server-side flow
1. TokenReview the bearer → `401` if invalid/wrong-audience; `403` if subject ≠ proxy SA.
2. Load grant `(server_id, user_sub)` `… FOR UPDATE` (single-writer for rotation). Absent or `status in (needs_auth, error)` → `200 {status: "needs_auth"|"error", detail}` (no token).
3. If `token_expires_at` is still fresh **and** an access token is cached server-side — registry-api does **not** cache access tokens (it holds only the refresh token); so it always performs step 4 on a pull. (The *proxy* caches the returned access token; registry-api is stateless per pull.)
4. `provider.get(grant.credential_ref)` → refresh token. `mcp_oauth.refresh_access_token(refresh_token, client)` → `{access_token, expires_in, refresh_token?}`.
5. **Rotation:** if the response carries a new `refresh_token`, `provider.rotate(grant.credential_ref, {"refresh_token": new})`; bump `updated_at`. Set `token_expires_at=now+expires_in`, `status='authorized'`, `last_error=NULL`.
6. On `invalid_grant`/refresh failure → set `status='error'`, `last_error` → `200 {status: "error", detail}` (fail-closed, no token).

### Response — `200`, `OAuthAccessTokenResponse` (always `200` for grant outcomes)
```python
class OAuthAccessTokenResponse(BaseModel):
    status: str                       # "authorized" | "needs_auth" | "error"
    access_token: str | None = None   # present ONLY when status == "authorized"
    expires_at: datetime | None = None
    detail: str | None = None         # reason when status != "authorized"
```
Success: `{"status":"authorized","access_token":"ya29…","expires_at":"2026-07-25T12:00:00Z"}`
Fail-closed: `{"status":"needs_auth","detail":"no authorized grant for (server,user)"}`

### Errors (real HTTP status)
- `401` — missing/invalid/wrong-audience SA token.
- `403` — authenticated, but not the mcp-proxy SA.
- `422` — malformed body.

### Auth invariant
The endpoint that hands out a live access token is the **only** internal MCP endpoint that TokenReviews its caller and pins the subject to the proxy SA — a non-proxy pod that reaches it is `403`, and every other outcome (no grant, revoked) returns `200` with **no** token (fail-closed).

---

## 6. WS-1 seam — no new endpoint; behavior of existing ones (grounding)

WS-1 changes **internal wiring only**, no new HTTP surface:
- `POST /api/v1/auth-configs/` and `PUT /api/v1/auth-configs/{id}` (`auth_configs.py`): on a credential write, call `get_provider().put(CredentialRef.parse(ref), body.credentials)` and set `auth_configs.credential_ref`, instead of `encrypt_json(...)` into the column. The `agentshield-platform` K8s Secret materialization (`upsert_secret`) is **unchanged** (deploy-controller still mounts it for agent-bound tools). Response shape unchanged (credentials never echoed; `has_credentials` now reads "ref set or legacy blob present").
- `GET /api/v1/auth-configs/{id}/secret-ref` (`auth_configs.py:129`): the re-materialize heal path swaps `decrypt_json(config.credentials_encrypted)` for `get_provider().get(CredentialRef.parse(config.credential_ref))`, with an explicit legacy branch (null ref → read the column). This is the exact seam the shipped docstring predicted.
- `mcp_secrets.materialize_server_secret` (`mcp_secrets.py:139-141`): `decrypt_json(auth_config.credentials_encrypted)` → `get_provider().get(...)` with the same legacy branch.

All three keep byte-identical outputs on the `pg-fernet` backend (same Fernet key, same composed headers).

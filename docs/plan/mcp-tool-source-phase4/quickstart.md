# Quickstart — MCP as a Tool Source, Phase 4

> **All build/deploy steps here are DEFERRED** — written this run, executed by the user when ready. Local iteration (provider unit paths, the OAuth stub, the proxy) needs **no AWS and no real OAuth server**: the default `CREDENTIAL_PROVIDER_BACKEND=pg-fernet` keeps the Fernet-in-Postgres store, and `scripts/e2e/fixtures/oauth_mcp_server.py` is a self-contained stub authorization server + bearer-gated MCP server.

**Companion artifacts (same dir):** `plan.md`, `research.md`, `data-model.md`, `tasks.md`, `contracts/`.

## Prerequisites
- Python 3.11+, `pip install -r services/registry-api/requirements.txt` (adds `boto3` after T005 — only used when `CREDENTIAL_PROVIDER_BACKEND=aws-sm`).
- `AGENTSHIELD_ENCRYPTION_KEY` set (a Fernet key) — the dev/default `FernetPgProvider` and the OAuth `state` signer both use it. Generate one:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Postgres reachable (the existing dev DB); alembic head at `0072` before you start.
- Node 18+ for Studio (`cd studio && npm ci`); `npx playwright install chromium` (first time) for the e2e journey.

## One-time setup for this feature
1. **Verify the baseline (T001)** — never reuse a claimed tag/number:
   ```bash
   grep -E 'REGISTRY_API_TAG=|MCP_PROXY_TAG=|STUDIO_TAG=' scripts/deploy-cpe2e.sh   # 0.2.228 / 0.1.3 / 0.1.162
   ls services/registry-api/alembic/versions/ | sort | tail -1                       # 0072_…
   ls scripts/e2e/ | grep -E 'suite-8[4-7]'                                          # 84, 85 present; 86/87 not yet
   ```
2. **Env for OAuth (registry-api), dev values:**
   ```bash
   export CREDENTIAL_PROVIDER_BACKEND=pg-fernet          # default; no AWS needed
   export MCP_OAUTH_CALLBACK_URL=http://localhost:8000/api/v1/mcp-servers/oauth/callback
   export STUDIO_BASE_URL=http://localhost:5173
   export MCP_OAUTH_STATE_TTL_SECONDS=600
   export MCP_PROXY_SA_AUDIENCE=agentshield-registry-api
   ```
3. **Env for the proxy (dev), so it can pull tokens from registry-api:**
   ```bash
   export REGISTRY_API_OAUTH_TOKEN_URL=http://localhost:8000/api/v1/internal/mcp/oauth/access-token
   export MCP_PROXY_REGISTRY_API_TOKEN_PATH=/tmp/registry-api-token   # a file with any dev token in-proc tests stub TokenReview
   ```
4. **Apply the migrations (after T003/T006):**
   ```bash
   cd services/registry-api && alembic upgrade head    # → 0073 then 0074
   alembic current                                     # expect 0074
   ```

## The stub OAuth server (T018 fixture)
`scripts/e2e/fixtures/oauth_mcp_server.py` serves, on one port:
- `GET /.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` (discovery),
- `POST /register` (DCR — returns a `client_id`/`client_secret`),
- `GET /authorize` (immediately 302s back to `MCP_OAUTH_CALLBACK_URL` with a canned `code` + the `state` — no human consent needed, so the flow is scriptable),
- `POST /token` (`authorization_code` → access+refresh; `refresh_token` → a **rotated** refresh + new access),
- `POST /mcp` (`tools/list`/`tools/call` gated on `Authorization: Bearer` — returns `401` without a valid access token so fail-closed is observable).
```bash
python scripts/e2e/fixtures/oauth_mcp_server.py --port 9100    # then register server_url=http://localhost:9100/mcp, external_auth_mode=oauth
```

## Exercising the authorization-code flow locally (registry-api only, no proxy)
```bash
# 1. Register an external OAuth server (external_auth_mode=oauth).
curl -s -XPOST localhost:8000/api/v1/mcp-servers/ -H 'Authorization: Bearer <jwt>' \
  -d '{"name":"stub-oauth","server_url":"http://localhost:9100/mcp","is_external":true,"external_auth_mode":"oauth"}'
# 2. Begin the dance → get the authorization_url.
curl -s -XPOST localhost:8000/api/v1/mcp-servers/<id>/oauth/authorize -H 'Authorization: Bearer <jwt>'
# 3. Follow it (the stub auto-approves + redirects to /oauth/callback with code+state).
curl -sL "<authorization_url>"    # ends at 302 …/mcp-servers/<id>?oauth=connected
# 4. Confirm the grant persisted.
curl -s localhost:8000/api/v1/mcp-servers/<id>/oauth/status -H 'Authorization: Bearer <jwt>'   # status=authorized
# 5. Simulate the proxy pull (needs a proxy-SA token; in dev TokenReview is stubbed by the suite).
curl -s -XPOST localhost:8000/api/v1/internal/mcp/oauth/access-token \
  -H 'Authorization: Bearer <proxy-sa-token>' -d '{"server_id":"<id>","user_sub":"<sub>"}'      # authorized + access_token
```

## Running the MCP Proxy locally (fast iteration on T011–T013)
```bash
cd services/mcp-proxy && uvicorn main:app --port 8080
# A tools/call to an oauth server with x-user-sub set pulls a token from REGISTRY_API_OAUTH_TOKEN_URL;
# with it unset → 200 is_error "user identity"; with a revoked grant → 200 is_error "re-authorize".
```

## Building and deploying (DEFERRED)
```bash
# Bump the tags FIRST (both places), then deploy. Never reuse a tag.
#   scripts/deploy-cpe2e.sh + charts/agentshield/values.yaml: registry-api 0.2.229, mcp-proxy 0.1.4, studio 0.1.163
#   (+ charts/agentshield/values-eks.yaml + scripts/deploy-eks.sh for EKS)
bash scripts/deploy-cpe2e.sh
kubectl rollout status deploy/agentshield-registry-api deploy/agentshield-mcp-proxy deploy/agentshield-studio
```

## Running the new backend e2e suites (T017/T018, DEFERRED)
```bash
bash scripts/e2e/suite-86-credential-provider.sh     # WS-1: put/get/rotate/delete, byte-identity, dual-read
bash scripts/e2e/suite-87-mcp-oauth.sh               # WS-2: authorize→callback→grant, refresh+rotation, fail-closed
bash scripts/e2e/run-all.sh                          # full backend sweep (86 + 87 registered)
```

## Running the regression sweep (T020, DEFERRED)
```bash
bash scripts/e2e/suite-84-mcp-tools.sh    # Phase-1 register/discover/authorize — must stay green (WS-1 rewired the cred path)
bash scripts/e2e/suite-85-mcp-phase2.sh   # health/list_changed/identity — must stay green (resolve_headers matrix unchanged)
# plus any AuthConfig-consuming suite (e.g. suite-81) — the auth_configs write path now routes through the provider
```

## Running Studio tests
```bash
cd studio && npm run typecheck && npm run test    # Vitest (McpServerDetailPage/McpServersPage OAuth cases)
bash scripts/studio-e2e.sh                         # Playwright mcp-servers.spec.ts (authorize journey + Connected-after-reload)
```

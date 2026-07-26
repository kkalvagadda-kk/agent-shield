# Tasks — MCP as a Tool Source, Phase 4

**Source of truth:** `plan.md` (File Structure + Key Interfaces), `data-model.md`, `research.md`, `contracts/*.md`. Every `[Txxx]` cites the files + test cases it must satisfy; do not invent interfaces — match `plan.md` Key Interfaces exactly.
**Total tasks:** 20 (T001–T020) across 10 phases (P1–P10) + 5 checkpoints (CP1–CP5).
**Workstream independence:** WS-1 (P2–P3) is a hard prerequisite and must prove byte-identity (CP1) before WS-2. Within WS-2, registry-api (P4–P6) → proxy (P7–P8) → Studio (P9).
**Suggested MVP scope:** WS-1 + WS-2 on `pg-fernet` + the stub OAuth server. The `aws-sm` backend (T005) is opt-in and may ship a beat later behind its config flag.

## Baseline (verify-then-bump — never reuse a claimed tag/number; re-verify at build time per `quickstart.md`)
- registry-api `0.2.228`→`0.2.229` · mcp-proxy `0.1.3`→`0.1.4` · studio `0.1.162`→`0.1.163`.
- Alembic head `0072` → add `0073` (WS-1), `0074` (WS-2). e2e ceiling `suite-85` → add `86`, `87`.
- Bump each tag in **both** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` (+ `values-eks.yaml`/`deploy-eks.sh`) in one commit.

## Phase Summary
| Phase | Name | Tasks | Delivers / Proves |
|---|---|---|---|
| P1 | Setup & baseline verification | T001 | Tags/head/suite ceiling re-verified; no code. |
| P2 | WS-1 CredentialProvider seam | T002–T004 | Provider + FernetPg; migration `0073`; call sites rewired; pg-fernet byte-identical. |
| P3 | WS-1 AWS Secrets Manager backend | T005 | `AwsSecretsManagerProvider` (IRSA, opt-in) + `boto3` + chart annotation. |
| **CP1** | **Checkpoint — Credential provider seam** | CP1a–CP1c | **Deferred.** Byte-identity + dual-read + optional ASM. |
| P4 | WS-2 data model + OAuth mechanics | T006–T007 | Migration `0074`; `MCPOAuthGrant`; `mcp_oauth.py`. |
| P5 | WS-2 registry-api dance endpoints | T008–T009 | authorize/callback/status/disconnect; discover-as-user. |
| P6 | WS-2 internal token endpoint | T010 | `/internal/mcp/oauth/access-token` + registry-api `tokenreviews:create`. |
| **CP2** | **Checkpoint — Authorize flow + refresh** | CP2a–CP2c | **Deferred.** authorize→`authorized`; refresh+rotation. |
| P7 | WS-2 proxy token-read | T011–T012 | `oauth_tokens.py`; `resolve_headers` OAuth branch. |
| P8 | WS-2 proxy plumbing + chart | T013–T014 | proxy arms + `user_sub`; projected token; RBAC. |
| **CP3** | **Checkpoint — End-to-end OAuth tool call** | CP3a–CP3c | **Deferred.** fresh bearer presented; fail-closed on expiry/absence. |
| P9 | WS-2 Studio | T015–T016 | API methods; OAuth panel; register toggle; Vitest; Playwright. |
| **CP4** | **Checkpoint — Studio authorize journey** | CP4a–CP4b | **Deferred.** register→authorize→Connected; save→reload→assert. |
| P10 | Testing, regression, docs, tags | T017–T020 | suite-86/87; tag bumps; docs; regression sweep. |
| **CP5** | **Checkpoint — Full Phase-4 e2e + regression** | CP5a–CP5c | **Deferred.** 86+87 + 84+85 + AuthConfig + Vitest + Playwright green. |

---

## Phase 1 — Setup & Baseline Verification
- [X] [T001] `[P]` Verify baseline: tags (registry-api `0.2.228`, mcp-proxy `0.1.3`, studio `0.1.162`), alembic head `0072`, e2e ceiling `suite-85`, studio `mcp-servers.spec.ts` present. No code. Runbook — `docs/plan/mcp-tool-source-phase4/quickstart.md`.

## Phase 2 — WS-1: CredentialProvider seam
- [X] [T002] `credential_provider.py` — `CredentialRef` (parse/str), `CredentialProvider` Protocol, `CredentialNotFound`, `FernetPgProvider` (put/get/rotate/delete over `credential_blobs` using `crypto.encrypt_json`/`decrypt_json`), `get_provider()` (pg-fernet only for now). Add config knobs `CREDENTIAL_PROVIDER_BACKEND`/`AWS_SECRETS_MANAGER_PREFIX`/`AWS_REGION` (Key Interfaces). Proves `T-S86-001/002/003`. — `services/registry-api/credential_provider.py`, `services/registry-api/config.py`
- [X] [T003] `models.py` `CredentialBlob(path PK, value_encrypted, timestamps)` + `AuthConfig.credential_ref`; migration `0073` (create table + column + idempotent backfill, `data-model.md §1d`). Confirm `configure_mappers()`. Proves `T-S86-004`. — `services/registry-api/models.py`, `services/registry-api/alembic/versions/0073_credential_blobs_and_credential_ref.py`
- [X] [T004] Rewire the two MCP call sites through `get_provider()` (after T002,T003): `mcp_secrets.materialize_server_secret` reads creds via provider; `auth_configs.py` create/update `put` + set `credential_ref`, `secret-ref` re-materialize `get` — each with an explicit legacy branch (null ref → read `credentials_encrypted`). Byte-identical composed headers on pg-fernet. Proves `T-S86-005/006/007`; regression `suite-84`. — `services/registry-api/mcp_secrets.py`, `services/registry-api/routers/auth_configs.py`

## Phase 3 — WS-1: AWS Secrets Manager backend (opt-in)
- [X] [T005] `credential_provider.AwsSecretsManagerProvider` (boto3, IRSA; `put`=Create/PutSecretValue, `get`=GetSecretValue→`CredentialNotFound` on ResourceNotFound, `rotate`=PutSecretValue, `delete`=DeleteSecret idempotent); `get_provider()` selects on `CREDENTIAL_PROVIDER_BACKEND` (lazy boto3 import); `boto3` in requirements; optional IRSA SA annotation (values-gated, off by default) (after T002). Proves `T-S86-008` (skipped unless backend=aws-sm). — `services/registry-api/credential_provider.py`, `services/registry-api/requirements.txt`, `charts/agentshield/charts/registry-api/templates/serviceaccount.yaml`, `charts/agentshield/values.yaml`, `charts/agentshield/values-eks.yaml`

## CP1 — Checkpoint: Credential provider seam

**Deferred — scripts WRITTEN this run, NOT executed; the user runs them when ready to deploy.** Deploys registry-api (P2–P3). Strict bash, real curl/kubectl/psql assertions, `echo "PASS"` at the end.

- [X] [CP1a] Deploy script — bump `REGISTRY_API_TAG`→`0.2.229` (deploy-cpe2e + values.yaml); `bash scripts/deploy-cpe2e.sh`; `kubectl rollout status`. — `scripts/deploy-mcp4-cp1.sh`
- [X] [CP1b] Infra smoke — `alembic current` = `0073`; `psql` shows `credential_blobs` exists + every `auth_configs` row with a blob has a `pg-fernet://…` `credential_ref`; a legacy row (null ref) still resolves. — `scripts/smoke-mcp4-cp1-infra.sh`
- [X] [CP1c] Behaviour smoke — create an AuthConfig with creds → register an MCP server bound to it → assert the per-server Secret `auth_headers` is byte-identical to a pre-seam capture; `suite-84-mcp-tools.sh` green. — `scripts/smoke-mcp4-cp1-behaviour.sh`

## Phase 4 — WS-2: data model + OAuth mechanics
- [X] [T006] Migration `0074` (`mcp_servers.external_auth_mode`/`oauth_client_ref` + `mcp_oauth_grants`, `data-model.md §2c`); `models.py` `MCPServer` cols + `MCPOAuthGrant`; `schemas.py` `MCPServerCreate/Update/Response.external_auth_mode` + validator (`external_auth_mode='oauth'` ⇒ `is_external=true`); `mcp_servers.py` create/update validator. Proves `T-S87-001/002`. — `services/registry-api/alembic/versions/0074_mcp_oauth_grants.py`, `services/registry-api/models.py`, `services/registry-api/schemas.py`, `services/registry-api/routers/mcp_servers.py`
- [X] [T007] `mcp_oauth.py` — `discover_oauth_metadata`, `register_client` (RFC 7591 DCR), `build_authorization_url` (PKCE S256), `exchange_code`, `refresh_access_token` (capture rotated RT), `make_state`/`read_state` (Fernet via `crypto`), minimal Pydantic metadata/token models, typed `OAuthDiscoveryError`/`OAuthStateError`/`OAuthFlowError` (plain httpx; NO mcp SDK) (after T006). Proves `T-S87-003`. — `services/registry-api/mcp_oauth.py`

## Phase 5 — WS-2: registry-api dance endpoints
- [X] [T008] `routers/mcp_oauth.py` — `POST /mcp-servers/{id}/oauth/authorize`, `GET /mcp-servers/oauth/callback` (302 to Studio), `GET /mcp-servers/{id}/oauth/status`, `DELETE /mcp-servers/{id}/oauth`; store refresh token via `get_provider()`; upsert `mcp_oauth_grants`; register the router in `main.py`; `mcp_servers.py` DELETE revokes grants + deletes refs (after T007). Config `MCP_OAUTH_CALLBACK_URL`/`STUDIO_BASE_URL`/`MCP_OAUTH_STATE_TTL_SECONDS`. Proves `T-S87-004..007` (`contracts/registry-api-oauth-phase4.md §1-4`). — `services/registry-api/routers/mcp_oauth.py`, `services/registry-api/main.py`, `services/registry-api/routers/mcp_servers.py`, `services/registry-api/config.py`
- [X] [T009] `mcp_secrets.materialize_server_secret` writes `external_auth_mode` into the `connection` JSON; `mcp_proxy_client.discover_server(server_id, user_sub=None)` threads `user_sub`; callback triggers discover as the authorizing user; `mcp_health` probes an OAuth server as the most-recently-authorized user (`needs_auth` if none) (after T008). Proves `T-S87-008`. — `services/registry-api/mcp_secrets.py`, `services/registry-api/mcp_proxy_client.py`, `services/registry-api/mcp_health.py`

## Phase 6 — WS-2: internal token endpoint
- [X] [T010] `internal_mcp.py` — `POST /oauth/access-token` (`OAuthAccessTokenRequest`/`Response`): TokenReview the proxy SA token (audience `MCP_PROXY_SA_AUDIENCE`), pin subject to the mcp-proxy SA (else 403), load grant `FOR UPDATE`, refresh-with-rotation via provider, `200` outcomes per `data-model.md §4` / `contracts/registry-api-oauth-phase4.md §5`; add registry-api `tokenreviews: create` ClusterRole; config `MCP_PROXY_SA_AUDIENCE` (after T007,T009). Proves `T-S87-009/010/011`. — `services/registry-api/routers/internal_mcp.py`, `services/registry-api/config.py`, `charts/agentshield/charts/registry-api/templates/rbac.yaml`

## CP2 — Checkpoint: Authorize flow + refresh

**Deferred — scripts WRITTEN this run, NOT executed.** Deploys registry-api (P4–P6) against the stub OAuth server.

- [X] [CP2a] Deploy script — `bash scripts/deploy-cpe2e.sh` (registry-api); `kubectl rollout status`. — `scripts/deploy-mcp4-cp2.sh`
- [X] [CP2b] Infra smoke — `alembic current` = `0074`; `psql` shows `mcp_oauth_grants` + `mcp_servers.external_auth_mode`; registry-api SA has a `tokenreviews:create` ClusterRoleBinding. — `scripts/smoke-mcp4-cp2-infra.sh`
- [X] [CP2c] Behaviour smoke — drive authorize→callback against the in-cluster stub AS → assert grant `status='authorized'` + a `credential_ref`; call `/internal/mcp/oauth/access-token` with the proxy SA token → `200 authorized` + a token; a non-proxy SA → `403`; force RT rotation and assert the stored ref's value changed. — `scripts/smoke-mcp4-cp2-behaviour.sh`

## Phase 7 — WS-2: proxy token-read
- [X] [T011] proxy `oauth_tokens.py` — `_access_cache` (per `(server,user)`, never persisted), `get_oauth_access_token` (fresh SA-token read from `MCP_PROXY_REGISTRY_API_TOKEN_PATH` → POST `REGISTRY_API_OAUTH_TOKEN_URL` → cache), `invalidate`, `OAuthUserRequired`/`OAuthAuthorizationRequired`/`OAuthTokenUnavailable`; config knobs. Proves `T-S87-012`. — `services/mcp-proxy/oauth_tokens.py`, `services/mcp-proxy/config.py`
- [X] [T012] proxy `credentials.ServerConnection` gains `external_auth_mode`+`server_id` (parsed in `read_server_secret`); `identity.resolve_headers` first-checked OAuth branch (fail-closed `OAuthUserRequired` when no `user_sub`); re-export exceptions (after T011). Proves `T-S87-013/014` (`contracts/mcp-proxy-oauth-phase4.md §1-2`). — `services/mcp-proxy/credentials.py`, `services/mcp-proxy/identity.py`

## Phase 8 — WS-2: proxy plumbing + chart
- [X] [T013] proxy `main.py` `tools_call` OAuth `except` arms (→ `200 is_error`) + evict-retry `oauth_tokens.invalidate` for `external_auth_mode='oauth'`; `discover` passes `req.user_sub`; `schemas.McpDiscoverRequest.user_sub` (after T012). Proves `T-S87-015` (`contracts/mcp-proxy-oauth-phase4.md §3`). — `services/mcp-proxy/main.py`, `services/mcp-proxy/schemas.py`
- [X] [T014] chart `mcp-proxy/deployment.yaml` — projected SA-token volume (audience `agentshield-registry-api`, path `/var/run/secrets/registry-api/token`) + the three OAuth env vars; `values.yaml`/`values-eks.yaml` wire the env + the registry-api OAuth env (`MCP_OAUTH_CALLBACK_URL`/`STUDIO_BASE_URL`) (after T013). — `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml`, `charts/agentshield/values.yaml`, `charts/agentshield/values-eks.yaml`

## CP3 — Checkpoint: End-to-end OAuth tool call

**Deferred — scripts WRITTEN this run, NOT executed.** Deploys mcp-proxy (P7–P8) + registry-api against the stub.

- [X] [CP3a] Deploy script — bump `MCP_PROXY_TAG`→`0.1.4` (deploy-cpe2e + values.yaml); deploy mcp-proxy + registry-api; `kubectl rollout status` both. — `scripts/deploy-mcp4-cp3.sh`
- [X] [CP3b] Infra smoke — the mcp-proxy pod mounts the `agentshield-registry-api`-audience projected token; `POST /internal/mcp/oauth/access-token` reachable from the proxy pod. — `scripts/smoke-mcp4-cp3-infra.sh`
- [X] [CP3c] Behaviour smoke — with an `authorized` grant, an agent-SA `POST /internal/tools/call` to the OAuth stub server with `x-user-sub` returns a real result (bearer presented upstream); after `DELETE …/oauth` (revoke) the same call returns `200 is_error` "re-authorize"; a call with no `x-user-sub` → `200 is_error` "user identity". — `scripts/smoke-mcp4-cp3-behaviour.sh`

## Phase 9 — WS-2: Studio
- [X] [T015] `mcpServersApi.ts` `startMcpOAuth`/`getMcpOAuthStatus`/`disconnectMcpOAuth` + `McpOAuthStatus` + `external_auth_mode` types; `McpServerDetailPage` OAuth Connection panel (Authorize/Connected+Disconnect) + `?oauth=` callback landing (toast + query invalidate + strip param); `McpServersPage` register OAuth toggle (External only, hides credential picker); Vitest for both pages (`contracts/studio-mcp-oauth-phase4.md`). Proves `Vitest`. — `studio/src/api/mcpServersApi.ts`, `studio/src/pages/McpServerDetailPage.tsx`, `studio/src/pages/McpServersPage.tsx`, `studio/src/pages/McpServerDetailPage.test.tsx`, `studio/src/pages/McpServersPage.test.tsx`
- [X] [T016] `studio/e2e/mcp-servers.spec.ts` — register External+OAuth → detail → click Authorize, `page.waitForResponse` on `POST …/oauth/authorize` returns `authorization_url` (assert redirect attempted, don't follow upstream); stub `?oauth=connected` → reload → assert **Connected** badge (save→reload→assert) (after T015). Proves `Playwright`. — `studio/e2e/mcp-servers.spec.ts`

## CP4 — Checkpoint: Studio authorize journey

**Deferred — scripts WRITTEN this run, NOT executed.** Deploys studio (P9).

- [X] [CP4a] Deploy script — bump `STUDIO_TAG`→`0.1.163` (deploy-cpe2e + values.yaml); deploy studio; `kubectl rollout status`. — `scripts/deploy-mcp4-cp4.sh`
- [X] [CP4b] Studio smoke — `bash scripts/studio-e2e.sh` runs `mcp-servers.spec.ts` (authorize journey + Connected-after-reload) green. — `scripts/smoke-mcp4-cp4-studio.sh`

## Phase 10 — Testing, Regression & Polish
- [X] [T017] `suite-86-credential-provider.sh` — provider put/get/rotate/delete round-trip; pg-fernet byte-identity of composed headers; legacy dual-read; register in `run-all.sh`. `T-S86-001..008`. — `scripts/e2e/suite-86-credential-provider.sh`, `scripts/e2e/run-all.sh`
- [X] [T018] `suite-87-mcp-oauth.sh` + `fixtures/oauth_mcp_server.py` (stub AS + bearer-gated MCP server) — authorize→callback→`authorized`; `/internal/mcp/oauth/access-token` refresh+rotation + `403` for non-proxy SA; proxy `/internal/tools/call` fail-closed on revoke/no-user; register in `run-all.sh`. `T-S87-001..015`. — `scripts/e2e/suite-87-mcp-oauth.sh`, `scripts/e2e/fixtures/oauth_mcp_server.py`, `scripts/e2e/run-all.sh`
- [X] [T019] Tag bumps in `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` + `charts/agentshield/values-eks.yaml` + `scripts/deploy-eks.sh` (registry-api `0.2.229`, mcp-proxy `0.1.4`, studio `0.1.163`); update `docs/decisions.md` (Decision 31 → Implemented), `docs/design/credential-provider-architecture.md`, `docs/design/mcp-tool-source-architecture.md` + `.../requirements.md` gap ledgers (OQ-01 resolved; OQ-02 deferred to Phase 5). — `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`, `charts/agentshield/values-eks.yaml`, `docs/decisions.md`, `docs/design/credential-provider-architecture.md`, `docs/design/mcp-tool-source-architecture.md`, `docs/design/todo/mcp-tools-for-agents-requirements.md`
- [X] [T020] Regression sweep + gap-ledger finalization: `suite-84`+`suite-85` + AuthConfig-consuming suites green; Vitest + `mcp-servers.spec.ts` green; grep each new symbol for a caller (no orphans); update `docs/testing/manual-ui-e2e-test-plan.md` Known Gaps (after T017,T018,T019). — `docs/testing/manual-ui-e2e-test-plan.md`

## CP5 — Checkpoint: Full Phase-4 e2e + regression

**Deferred — scripts WRITTEN this run, NOT executed.** Full-platform validation.

- [X] [CP5a] Deploy script — `bash scripts/deploy-cpe2e.sh` (all Phase-4 services); `kubectl rollout status`. — `scripts/deploy-mcp4-cp5.sh`
- [X] [CP5b] Suite smoke — `suite-86` + `suite-87` green; regression `suite-84` + `suite-85` + AuthConfig suites green. — `scripts/smoke-mcp4-cp5-suites.sh`
- [X] [CP5c] Studio smoke — `bash scripts/studio-e2e.sh` (Vitest + `mcp-servers.spec.ts`) green. — `scripts/smoke-mcp4-cp5-studio.sh`

## Dependency Notes (cross-phase, beyond the inline `(after Txxx)`)
- **WS-1 must prove byte-identity before WS-2.** CP1 is a hard gate: the OAuth store (WS-2) is built on the provider seam; a regression in the composed `auth_headers` would silently poison every existing MCP server.
- **T010 (registry-api token endpoint) precedes T011–T013 (proxy).** The proxy's `oauth_tokens.get_oauth_access_token` calls it; build + prove the server side (CP2) before the proxy pulls from it.
- **T014 (chart projected token + RBAC) is a deploy prerequisite for CP3.** The proxy cannot authenticate to the token endpoint without its `agentshield-registry-api`-audience token, and registry-api cannot TokenReview it without the `tokenreviews:create` ClusterRole (T010).
- **T009 (`external_auth_mode` in the per-server Secret) precedes CP3.** Without it the proxy never takes the OAuth branch — an OAuth server would be treated as `static` and connect unauthenticated (which the stub rejects → a confusing failure).
- **Regression sweep (T020) spans the shared credential path.** Map: every AuthConfig write + every MCP server materialization (WS-1), `resolve_headers` all modes, the proxy→registry-api hop, registry-api RBAC (`research.md` Part D).

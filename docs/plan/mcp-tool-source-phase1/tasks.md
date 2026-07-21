# Tasks — MCP as a Tool Source, Phase 1

**Source of truth:** `plan.md` (17-task decomposition — preserved and expanded here to file-level granularity, in the same dependency order), `docs/design/mcp-tool-source-architecture.md` (LOCKED), `research.md`, `data-model.md`, `quickstart.md`, `contracts/{mcp-proxy-internal,registry-api-mcp-servers,registry-api-internal-mcp}.md`. Where this file and the design doc differ, the design doc wins.

**Total tasks:** 93 (78 implementation + 15 checkpoint)
**Phases:** 15 implementation phases + 5 checkpoint phases (CP1–CP5) interleaved.
**Parallel opportunities:** Phase 4 (deploy-controller token) is structurally independent of Phases 3/5/6 and can run alongside them. Phase 9 (OPA `allow_deanonymize`) and Phase 10 (Safety Orchestrator + `safety_client` fix) are the two independent halves of Decision 27 and run in parallel — they only converge at Phase 11. Within phases, tasks tagged `[P]` touch disjoint files with no incomplete-sibling dependency (heaviest in Phase 5: the proxy code modules, the sub-chart templates, the fixture, and the NetworkPolicies are largely mutually independent).
**Checkpoint phases:** CP1 (after P3 — registry-api foundational), CP2 (after P6 — **register→discover MVP**), CP3 (after P8 — mcp tool-call dispatch executes), CP4 (after P11 — Decision 27 governed gate), CP5 (after P14 — Studio UI). Each writes 2–3 shell scripts under `scripts/`. **All checkpoint scripts + all image-tag-bump tasks are DEFERRED — written this run, NOT executed. The user runs them when ready to deploy.**

**Suggested MVP scope:** target **CP2** first. Tasks in Phases 1→6 (migration → models/schemas → shared authz+secret+internal endpoint → deploy-controller token → MCP Proxy → mcp_servers router) form the thinnest end-to-end slice: register a server → the proxy discovers its tools → `Tool` rows appear (namespaced, team-scoped) → they show in `ToolsPicker` automatically. CP2's behaviour smoke proves register→discover against the in-pod fixture. Everything after CP2 (SDK/runner dispatch, Decision 27 gate, Studio screens, full e2e) layers governed execution and UX onto that proven slice.

---

## Baseline (verify-then-bump — never reuse a claimed tag/number; re-verify at build time per `quickstart.md`)

Observed this session: alembic head `0071` → this migration is **`0072`** (`down_revision="0071"`); e2e suite ceiling `83` → new **`suite-84`**. Image tags: `REGISTRY_API_TAG=0.2.224`, `STUDIO_TAG=0.1.160`, `DECLARATIVE_RUNNER_TAG=0.1.59`, `SAFETY_ORCHESTRATOR_TAG=0.1.3`, `DEPLOY_CONTROLLER_TAG=0.1.40`, `PYTHON_EXECUTOR_TAG=0.1.0`, `sdk.__version__=0.2.0`; new `MCP_PROXY_TAG=0.1.0`. Every `[deferred]` bump task re-greps the current value first, then increments the patch in **both** `scripts/deploy-cpe2e.sh` **and** `charts/agentshield/values.yaml` (for `mcp-proxy`, also the sub-chart `charts/agentshield/charts/mcp-proxy/values.yaml`) — **written, not run.**

---

## Phase Summary

| Phase | Name | Tasks | Delivers / Proves |
|---|---|---|---|
| P1 | Setup & Baseline Verification | T001–T002 | Migration head / suite number / tags re-verified; `mcp` SDK pin confirmed |
| P2 | Foundational — Migration + Data Model | T003–T006 | `0072` columns; `MCPServer`/`Tool` ORM + schemas; `tools.py` denorm + `mcp_tool` DELETE 409 |
| P3 | Shared Authz + Per-Server Secret + Internal Endpoint | T007–T011 | `team_may_use_tool`; per-server Secret materializer; `/internal/mcp/authorize-tool-call`; deploy-gate refactor |
| **CP1** | **Checkpoint — Registry-API Foundational** | CP1a–CP1c | **Deferred.** Migration 0072 applied; internal authz endpoint; tools denorm/409 |
| P4 | deploy-controller — 2nd SA token `[P]` | T012–T013 | Agent pods project an `agentshield-mcp-proxy`-audience token |
| P5 | MCP Proxy service + sub-chart + RBAC + NetworkPolicy | T014–T030 | The proxy (authn/authz/creds/mcp-client/session-cache/discover/tools-call) + Helm sub-chart + least-priv RBAC + netpol + fixture |
| P6 | registry-api `mcp_servers` router + proxy client | T031–T034 | CRUD + `/sync`; discovery upsert; register→discover closed |
| **CP2** | **Checkpoint — Register→Discover MVP** | CP2a–CP2c | **Deferred.** Register a server → discover tools → namespaced/team-scoped `Tool` rows |
| P7 | SDK — `McpToolExecutor` + dispatch + token | T035–T037 | `mcp_tool` resolves to a callable that calls the proxy (SDK runtime) |
| P8 | declarative-runner — `McpToolNodeExecutor` + dispatch | T038–T040 | `mcp_tool` dispatch in the runner (separate impl) |
| **CP3** | **Checkpoint — MCP tool-call dispatch executes** | CP3a–CP3c | **Deferred.** SDK/runner → proxy → fixture returns a real result; token audiences verified |
| P9 | Decision 27 — OPA `allow_deanonymize` plumbing `[P]` | T041–T048 | `allow_deanonymize` through bundle → static Rego → `OPADecision`; `record_decision()` |
| P10 | Decision 27 — Safety Orchestrator + `safety_client` fix `[P]` | T049–T056 | `deanonymize_args`; the two-sided `safety_client` field-bug fix (regression-first) |
| P11 | Decision 27 — wire the gate into `governed_tool` (STUB) | T057–T059 | de-anon step + output-scan **STUB** + `opa_decisions` audit, for all 4 tool types |
| **CP4** | **Checkpoint — Decision 27 governed gate** | CP4a–CP4c | **Deferred.** de-anon proof; scan-call exemption; audit row; STUB not enforced |
| P12 | Studio — MCP Servers screen | T060–T066 | Register/list/detail/sync/delete UI + Vitest |
| P13 | Studio — `ToolsPage` read-only `mcp_tool` + PII checkbox | T067–T069 | `mcp_tool` rows read-only; `pii_deanonymize_allowed` checkbox + Vitest |
| P14 | Studio — `ToolsPicker` source-server badge | T070–T072 | Source-server badge on MCP tools + Vitest |
| **CP5** | **Checkpoint — Studio UI** | CP5a–CP5c | **Deferred.** Studio deployed; Vitest+typecheck green; register→detail API path |
| P15 | Testing, Regression & Polish | T073–T078 | `suite-84` + fixture; Playwright journey; `suite-18` audit assertion; gap ledger; regression sweep |

---

## Phase 1 — Setup & Baseline Verification

- [X] [T001] [P] Re-verify baselines before any number is claimed: alembic head still `0071` (→ migration `0072`), e2e suite ceiling still `83` (→ `suite-84` free), current image tags (`REGISTRY_API_TAG=0.2.224`, `DEPLOY_CONTROLLER_TAG=0.1.40`, `STUDIO_TAG=0.1.160`, `DECLARATIVE_RUNNER_TAG=0.1.59`, `SAFETY_ORCHESTRATOR_TAG=0.1.3`, `PYTHON_EXECUTOR_TAG=0.1.0`, `sdk.__version__=0.2.0`). Record any drift and adjust every downstream number. Runbook: — `docs/plan/mcp-tool-source-phase1/quickstart.md`
- [X] [T002] [P] Confirm the `mcp` Python SDK pin — `mcp>=1.2,<2.0`; verify the latest stable minor via `pip index versions mcp` and do not cross a major; re-verify `.inputSchema`/`streamablehttp_client`/`ClientSession` attribute names against the installed version (feeds Phase 5's `requirements.txt`). Runbook: — `docs/plan/mcp-tool-source-phase1/quickstart.md`

## Phase 2 — Foundational: Migration + Data Model

- [X] [T003] Migration `0072` — re-confirm head is still `0071`, then create the idempotent migration (6 `mcp_servers` cols: `identity_mode` VARCHAR(32) NOT NULL 'none' + CHECK, `is_external` BOOL NOT NULL false, `transport_config` JSONB NULL, `health_detail` JSONB NOT NULL '{}', `list_changed_supported` BOOL NOT NULL false, `scan_results` BOOL NOT NULL true; 1 `tools` col: `pii_deanonymize_allowed` BOOL NOT NULL false), `revision="0072"`, `down_revision="0071"`, guarded `_existing_columns()` ADD COLUMNs, downgrade drops CHECK before its column. Proves T-S84-001 (apply twice → 2nd is a no-op). — `services/registry-api/alembic/versions/0072_mcp_server_fields.py`
- [X] [T004] `MCPServer` +6 mapped columns; `Tool` +`pii_deanonymize_allowed: Mapped[bool]` (types/defaults per data-model.md; preserve the `name`-immutable + delete-blocked-while-bound invariant comments). Must `sqlalchemy.orm.configure_mappers()` clean. (after T003) — `services/registry-api/models.py`
- [X] [T005] Schemas — `MCPServerCreate`/`MCPServerResponse` +6 fields with `model_validator` rejecting (`is_external=true` + `identity_mode!='none'`) and `transport='stdio'`; add `MCPServerUpdate`, `MCPServerDetailResponse(MCPServerResponse)` with `tools: list[ToolResponse]`, `MCPServerSyncRequest`, `MCPServerSyncResponse`; `ToolCreate`/`ToolUpdate` +`pii_deanonymize_allowed: bool=False`; `ToolResponse` +`pii_deanonymize_allowed` + `mcp_server_name`/`mcp_server_is_external`/`mcp_server_scan_results` (all `None`-defaulted). (after T004) — `services/registry-api/schemas.py`
- [X] [T006] `tools.py` — `_to_tool_response(tool)` denormalization helper (populates the 3 `mcp_server_*` fields from `tool.mcp_server` via `.model_copy(update=...)`); every `ToolResponse`-returning route calls it; `list_tools`/`get_tool` add `.options(selectinload(Tool.mcp_server))`; `delete_tool` **rejects `type=='mcp_tool'` → 409** (message: lifecycle owned by the MCP server) before soft-delete — `http` DELETE still 204. Proves T-S84-002. (after T004,T005) — `services/registry-api/routers/tools.py`

## Phase 3 — Shared Authz + Per-Server Secret + Internal Authz Endpoint

- [X] [T007] `team_may_use_tool(db, team, tool_id) -> bool` — the single grant resolver (True iff own-team/team-less OR an active `AssetGrant(asset_type='tool', asset_id, grantee_team=team, revoked_at IS NULL)`). Sole implementation, two callers (deploy gate + internal endpoint). Proves T-S84-003/004. (after T004) — `services/registry-api/tool_access.py`
- [X] [T008] Deploy-gate refactor — replace the inline per-foreign-tool grant loop (~L591-613) with a `team_may_use_tool` call; **behavior-neutral** (the `422 tool_grants_missing` semantics must be unchanged, proven by `suite-81`/`suite-18` in Phase 15). (after T007) — `services/registry-api/routers/deployments.py`
- [X] [T009] Per-server Secret materializer — `materialize_server_secret(db, server)` writes `agentshield-mcp-server-{id}` in `agentshield-mcp` with `connection` (url/transport/transport_config/is_external/owner_team) + `auth_headers` (composed from `crypto.decrypt_json(auth_config.credentials_encrypted)` by `AuthConfig.type`; `{}` if none); `delete_server_secret(server_id)`. Reuses existing `k8s.upsert_secret`/`delete_secret` (no new registry-api RBAC). Proves T-S84-005. (after T004) — `services/registry-api/mcp_secrets.py`
- [X] [T010] Internal authz endpoint + mount — `POST /api/v1/internal/mcp/authorize-tool-call {caller_sa_subject, server_id, mcp_tool_name} -> {allowed}` (derive team from `agents-{team}` namespace, resolve `Tool` by `(mcp_server_id, mcp_tool_name)`, call `team_may_use_tool`; always 200, 422 on malformed; NetworkPolicy-trusted, no TokenReview), and `main.py` `include_router(internal_mcp_router)`. Proves T-S84-003/004. (after T007) — `services/registry-api/routers/internal_mcp.py`, `services/registry-api/main.py`
- [X] [T011] [deferred — written this run, NOT executed] Bump `REGISTRY_API_TAG` (verify current `0.2.224` then increment; covers Phases 2–3: migration + models/schemas/tools + internal endpoint) in **both** files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## CP1 — Checkpoint: Registry-API Foundational

**Deferred — the scripts below are WRITTEN this run, NOT executed; the user runs them when ready to deploy.** Deploys registry-api only. Every script: `set -euo pipefail`, echo section headers, real assertions (HTTP codes + JSON fields via `jq`, `\d` column checks), exit non-zero on first failure, end with `echo "PASS"`.

- [X] [CP1a] Deploy script — `helm upgrade` (registry-api tag from `values.yaml`) + `kubectl rollout status deploy/agentshield-registry-api` + `kubectl exec ... alembic upgrade head`. — `scripts/deploy-mcp-cp1.sh`
- [X] [CP1b] Infra smoke — assert `alembic current` == `0072`; `\d mcp_servers` shows all 6 new cols (types/defaults/CHECK), `\d tools` shows `pii_deanonymize_allowed`; registry-api pod Ready. — `scripts/smoke-cp1-infra.sh`
- [X] [CP1c] Behaviour smoke — `curl` `POST /api/v1/internal/mcp/authorize-tool-call`: own-team → `200 {allowed:true}`, cross-team no-grant → `{allowed:false}`, malformed → `422`; `DELETE /api/v1/tools/{mcp_tool_id}` → `409`, `DELETE` an http tool → `204`; `GET` an http tool → `pii_deanonymize_allowed:false`, `mcp_server_name:null`. — `scripts/smoke-cp1-behaviour.sh`

## Phase 4 — deploy-controller: second SA token `[P]`

- [X] [T012] [P] Project a **second** SA token into agent pods — new projected volume `mcp-proxy-token` (audience `agentshield-mcp-proxy`, `expiration_seconds=3600`, `path="token"`, mirroring the OPA `sa-token` at ~L383-398) + `read_only` volumeMount at `/var/run/secrets/mcp-proxy-token` (~L318-323) + env `AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH=/var/run/secrets/mcp-proxy-token/token` (~L176-179). No regression to the existing OPA token. Proves T-S84-006. — `services/deploy-controller/manifest_builder.py`
- [X] [T013] [deferred — written this run, NOT executed] Bump `DEPLOY_CONTROLLER_TAG` (verify current `0.1.40` then increment) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## Phase 5 — MCP Proxy service + Helm sub-chart + RBAC + NetworkPolicy

> The proxy **never** holds the DB or `AGENTSHIELD_ENCRYPTION_KEY` — if a task reaches for either, stop (violates §3b/B13). It reads only per-server Secrets in `agentshield-mcp` (`get`) + `system:auth-delegator` for TokenReview.

- [X] [T014] [P] Proxy scaffolding — `requirements.txt` (`fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `kubernetes`, `mcp>=1.2,<2.0` per T002; **no** sqlalchemy/asyncpg), `config.py` (env: `REGISTRY_API_URL`, `PORT`, `MCP_PROXY_AUDIENCE=agentshield-mcp-proxy`, `REGISTRY_API_SA_SUBJECT`, `MCP_SECRETS_NAMESPACE=agentshield-mcp`, cache TTLs), `schemas.py` (§3c wire models: `McpDiscoverRequest/Response`, `McpDiscoveredTool`, `McpToolCallRequest/Response`). — `services/mcp-proxy/requirements.txt`, `services/mcp-proxy/config.py`, `services/mcp-proxy/schemas.py`
- [X] [T015] [P] `k8s_client.py` — in-cluster kubernetes client, **read-only**: `read_namespaced_secret` (in `MCP_SECRETS_NAMESPACE`) + `create_token_review`. Mirrors registry-api's `_init_k8s()` shape but no write verbs. — `services/mcp-proxy/k8s_client.py`
- [X] [T016] `authn.py` — `verify_bearer_token(token) -> sa_subject | None` via TokenReview (require `status.authenticated` **and** `agentshield-mcp-proxy ∈ status.audiences`); positive-review cache keyed by `sha256(token)` until the token's `exp` (parsed base64url like `opa_client._parse_sa_subject`). Missing/invalid/wrong-audience → caller maps to `401`. (after T015) — `services/mcp-proxy/authn.py`
- [X] [T017] `credentials.py` — `read_server_secret(server_id) -> ServerConnection{server_url, transport, transport_config, is_external, owner_team, auth_headers}` (single `read_namespaced_secret`, parse `connection`+`auth_headers` JSON; typed error on missing Secret → surfaces as a `200` error body, never 5xx). No DB, no decryption. (after T015) — `services/mcp-proxy/credentials.py`
- [X] [T018] `authz.py` — `team_from_sa_subject(sa_subject)` (`agents-{team}` → `{team}`, else None) + `authorize_tool_call(caller_sa_subject, server_id, mcp_tool_name, owner_team) -> bool` (own-team fast path with zero hops; cross-team → `POST {REGISTRY_API_URL}/api/v1/internal/mcp/authorize-tool-call`; short-TTL cache per `(subject, server_id, tool)`). (after T017) — `services/mcp-proxy/authz.py`
- [X] [T019] [P] `mcp_client.py` — `connect_and_initialize(server_url, headers) -> McpSession` over `mcp.client.streamable_http.streamablehttp_client` + `mcp.ClientSession` (`.initialize()` capturing `protocol_version` + `list_changed_supported`; `.list_tools()`, `.call_tool(name, arguments) -> CallResult(result:str, is_error, structured)`, `.close()`; multi text-block concat, non-text → `"[non-text content: ...]"`). — `services/mcp-proxy/mcp_client.py`
- [X] [T020] `session_cache.py` — per-replica `{server_id: CachedSession}` (live session + parsed `ServerConnection`); get-or-create on miss (read Secret → connect), evict-on-error. (after T019,T017) — `services/mcp-proxy/session_cache.py`
- [X] [T021] `main.py` — FastAPI app wiring `GET /health`,`GET /ready` (unauth, 200 unconditionally), `POST /internal/discover` (admin-plane: authn → caller-must-equal-registry-api-SA else 403 → read Secret → connect+initialize+list_tools → `200 McpDiscoverResponse` incl. `ok:false`/`status:error` body on failure, never 5xx), `POST /internal/tools/call` (authn → §3b team floor via `authz` → session_cache → `call_tool` with one evict-and-retry → `200 McpToolCallResponse` with `is_error` for tool/transport failures; `401`/`403`/`422` only for real auth/body failures). Proves T-S84-007..012. (after T016,T018,T020) — `services/mcp-proxy/main.py`
- [X] [T022] `Dockerfile` — `python:3.12-slim`, mirrors `python-executor/Dockerfile`; `COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/` (inert unless exec'd). (after T023) — `services/mcp-proxy/Dockerfile`
- [X] [T023] [P] Stub MCP fixture — `mcp.server.fastmcp.FastMCP` exposing `echo(text: str) -> str` (verbatim — the de-anon proof) and `add(a: int, b: int) -> int`, `transport="streamable-http"` on `127.0.0.1:9999`; never auto-started (exec'd into the proxy pod). Created here (not Phase 15) because T022's `COPY` and CP2's discovery smoke both require it; Phase 15's `suite-84` consumes it. — `scripts/e2e/fixtures/stub_mcp_server.py`
- [X] [T024] [P] Sub-chart descriptor + values — `Chart.yaml` (mirror `python-executor/Chart.yaml`) + `values.yaml` (`replicaCount`, `image.{repository,tag,pullPolicy}` tag `0.1.0`, `service.port: 8080`, `resources`, `secretsNamespace: agentshield-mcp`). — `charts/agentshield/charts/mcp-proxy/Chart.yaml`, `charts/agentshield/charts/mcp-proxy/values.yaml`
- [X] [T025] [P] Sub-chart Deployment + Service — Deployment (mirror `python-executor`; SA `{release}-mcp-proxy`, env from values, `/health` probes) + ClusterIP Service `agentshield-mcp-proxy:8080`. — `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml`, `charts/agentshield/charts/mcp-proxy/templates/service.yaml`
- [X] [T026] [P] Sub-chart ServiceAccount + RBAC — SA (mirror registry-api's) + ClusterRole/binding for `system:auth-delegator` (TokenReview) + Role/binding in `agentshield-mcp` for `get` on `secrets`. Nothing broader (no DB, no encryption key). — `charts/agentshield/charts/mcp-proxy/templates/serviceaccount.yaml`, `charts/agentshield/charts/mcp-proxy/templates/rbac.yaml`
- [X] [T027] [P] Sub-chart namespace — create `agentshield-mcp` (dedicated per-server-secret ns; guarded so a re-apply is safe). — `charts/agentshield/charts/mcp-proxy/templates/namespace.yaml`
- [X] [T028] Parent chart wiring — `Chart.yaml` new `mcp-proxy` dependency (`condition: mcp-proxy.enabled`, mirrors `python-executor`) + `values.yaml` `mcp-proxy: {enabled: true, image: {tag: 0.1.0}}` (parent override so the CLAUDE.md mirror rule holds). — `charts/agentshield/Chart.yaml`, `charts/agentshield/values.yaml`
- [X] [T029] [P] NetworkPolicies — egress block `role: agent` pods → `mcp-proxy:8080`; ingress allow agent-namespace + registry-api → the proxy; note the proxy is the only platform component permitted egress to external MCP hosts. — `infra/network-policies/agents-allow-egress.yaml`, `infra/network-policies/platform-allow-ingress.yaml`
- [X] [T030] [deferred — written this run, NOT executed] Add `MCP_PROXY_TAG=0.1.0` var + `docker build services/mcp-proxy/` line + `kubectl rollout status` wait to the deploy script (mirror tag in `values.yaml` handled in T028). — `scripts/deploy-cpe2e.sh`

## Phase 6 — registry-api `mcp_servers` router + proxy client (closes register→discover)

- [X] [T031] `mcp_proxy_client.py` — `discover_server(server_id) -> dict`: `POST {MCP_PROXY_URL}/internal/discover {"server_id": str}` with `Authorization: Bearer <read MCP_PROXY_SA_TOKEN_PATH>`; returns the parsed `McpDiscoverResponse` regardless of `ok`; raises `RuntimeError` only on genuine transport failure / 401/403/5xx (caller treats identically to `ok:false` → `status='error'`, never a 4xx to Studio). — `services/registry-api/mcp_proxy_client.py`
- [X] [T032] `mcp_servers.py` router + mount — full CRUD + `/sync` per `contracts/registry-api-mcp-servers.md`: POST (validate → `materialize_server_secret` → `discover_server` → upsert `Tool` rows with `{server_name}__{mcp_tool_name}` namespacing, `owner_team` propagation, first-discovery defaults; `201` with `status=connected|error` either way — never all-or-nothing); GET list + GET detail (incl. `inactive` tools); PUT (name-immutable `422`, re-materialize Secret on `auth_config_id` change); `/sync` (vanished→`inactive`, schema-drift auto-apply+flag, `acknowledge_schema_drift`); DELETE (`409`-guard on bound tools naming blocking agents, else delete tools+server+`delete_server_secret` → `204`); plus `main.py` `include_router(mcp_servers_router)`. Proves T-S84-013..020. (after T009,T031,T005) — `services/registry-api/routers/mcp_servers.py`, `services/registry-api/main.py`
- [X] [T033] registry-api Deployment — projected `mcp-proxy-token` volume (audience `agentshield-mcp-proxy`, `path="mcp-proxy-token"`) + `read_only` mount + env `MCP_PROXY_SA_TOKEN_PATH` so registry-api can call `/internal/discover`. — `charts/agentshield/charts/registry-api/templates/deployment.yaml`
- [X] [T034] [deferred — written this run, NOT executed] Bump `REGISTRY_API_TAG` (verify current then increment; covers Phase 6 router + Deployment) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## CP2 — Checkpoint: Register→Discover MVP

**Deferred — scripts WRITTEN this run, NOT executed; the user runs them when ready to deploy.** Deploys mcp-proxy (`0.1.0`), registry-api, deploy-controller. This is the MVP gate — the register→discover vertical slice. Strict bash mode, real curl/kubectl/jq assertions, `echo "PASS"` at the end.

- [X] [CP2a] Deploy script — `bash scripts/deploy-cpe2e.sh` (builds `mcp-proxy:0.1.0` + registry-api + deploy-controller) **or** `helm upgrade` with the new tags; `kubectl rollout status` for mcp-proxy + registry-api; assert `kubectl get ns agentshield-mcp`. — `scripts/deploy-mcp-cp2.sh`
- [X] [CP2b] Infra smoke — proxy pod Ready + `GET /health` → `200`; RBAC `can-i` matrix (`get secrets -n agentshield-mcp` → yes, `-n agentshield-platform` → **no**, `create tokenreviews` → yes, `get pods` → no); `/internal/discover` missing-token → `401`, agent-SA (wrong subject) → `403`. — `scripts/smoke-cp2-infra.sh`
- [X] [CP2c] Behaviour smoke — start the fixture inside the proxy pod (`kubectl exec ... python3 fixtures/stub_mcp_server.py &`); `POST /api/v1/mcp-servers` against `http://127.0.0.1:9999/mcp` → `201 status=connected discovered_tool_count>=2`; `GET /mcp-servers/{id}` → tools named `{server}__echo`/`{server}__add`, each `owner_team == server.owner_team`; register an unreachable URL → `201 status=error` with `health_detail.last_error`; `/sync` twice → 2nd `tools_added=0`; per-server Secret exists in `agentshield-mcp`; DELETE unbound → `204` and Secret gone (jq on codes+fields). — `scripts/smoke-cp2-behaviour.sh`

## Phase 7 — SDK: `McpToolExecutor` + `tool_resolver` dispatch + token

- [ ] [T035] [P] SDK config — `AGENTSHIELD_MCP_PROXY_URL` (default `http://agentshield-mcp-proxy.agentshield-platform:8080`) + `AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH` (default `/var/run/secrets/mcp-proxy-token/token`). — `sdk/agentshield_sdk/config.py`
- [ ] [T036] `McpToolExecutor` + dispatch branch — new `McpToolExecutor` class (async callable shape identical to `HttpToolExecutor`: `.risk`,`.tool_name`,`.side_effecting`,`.scan_results`, `__signature__` from `input_schema`; POSTs `McpToolCallRequest` to `AGENTSHIELD_MCP_PROXY_URL + '/internal/tools/call'` with `Authorization: Bearer <token>`; returns `response.result` str; `is_error`/transport failures → error **string**, never raised — FR-MCP-14; DEV_MODE mock) **and** `tool_resolver._build_executor`'s new `elif tool_type == "mcp_tool":` branch (`scan_results = True if is_external else bool(mcp_server_scan_results)`). Proves T-S84-021/022. (after T035) — `sdk/agentshield_sdk/tool_executor.py`, `sdk/agentshield_sdk/tool_resolver.py`
- [ ] [T037] SDK version bump — `__version__` → `0.2.1`. (after T036) — `sdk/agentshield_sdk/__init__.py`

## Phase 8 — declarative-runner: `McpToolNodeExecutor` + `workflow_executor` dispatch

- [ ] [T038] [P] Runner config — `MCP_PROXY_URL` (unprefixed, matching `PYTHON_EXECUTOR_URL`) + `MCP_PROXY_SA_TOKEN_PATH`. — `services/declarative-runner/config.py`
- [ ] [T039] `McpToolNodeExecutor` + dispatch branch — new `McpToolNodeExecutor` class (**separate** impl per grounding #4, not a shared import; same wire contract + same Bearer token as the SDK executor) **and** `workflow_executor._tool_dict_to_executor`'s new `mcp_tool` branch. Proves T-S84-023 (a workflow agent node with an `mcp_tool` in `tool_ids` resolves via `_prefetch_agent_tools` → `_tool_dict_to_executor` → `McpToolNodeExecutor`). (after T038) — `services/declarative-runner/node_executors.py`, `services/declarative-runner/workflow_executor.py`
- [ ] [T040] [deferred — written this run, NOT executed] Bump `DECLARATIVE_RUNNER_TAG` (verify current `0.1.59` then increment; rebuilds against sdk `0.2.1`) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## CP3 — Checkpoint: MCP tool-call dispatch executes

**Deferred — scripts WRITTEN this run, NOT executed; the user runs them when ready to deploy.** Deploys declarative-runner + a fixture agent (rebuilt SDK `0.2.1`).

- [ ] [CP3a] Deploy script — `bash scripts/deploy-cpe2e.sh` (rebuilds sdk into the fixture agent image + declarative-runner) **or** `helm upgrade`; `kubectl rollout status`. — `scripts/deploy-cp3.sh`
- [ ] [CP3b] Infra smoke — a deployed agent pod has **both** `/var/run/secrets/sa-token/token` (aud `agentshield-opa`) **and** `/var/run/secrets/mcp-proxy-token/token`; decode the 2nd token's payload and assert `aud == agentshield-mcp-proxy`. Proves T-S84-006. — `scripts/smoke-cp3-infra.sh`
- [ ] [CP3c] Behaviour smoke — `kubectl exec` a resolve+invoke snippet: `McpToolExecutor` against the fixture returns `echo`'s real string; unreachable proxy → JSON **error string** (no exception); request carries `Authorization: Bearer`. Also `POST /internal/tools/call` directly: own-team → executes, cross-team no-grant → `403`, missing token → `401`, tool/transport error → `200 is_error:true`. — `scripts/smoke-cp3-behaviour.sh`

## Phase 9 — Decision 27: OPA `allow_deanonymize` plumbing `[P]` (parallel with Phase 10)

> Enforcement path is the **static** `opa_policy/agentshield.rego` + `bundle_generator.py` — **not** `policy_generator.py` (inert at runtime, research.md #8). Two same-named `OPADecision` classes (SDK dataclass vs `models.OPADecision` ORM) are unrelated — do not conflate.

- [ ] [T041] [P] `OPADecision` +`allow_deanonymize: bool = False` (parsed from `result.get("allow_deanonymize", False)` in `check_tool()`) + new `record_decision(agent_name, tool_name, decision, args, thread_id="")` — best-effort `POST /api/v1/opa-decisions/`, **never raises**. Proves T-S84-025. — `sdk/agentshield_sdk/opa_client.py`
- [ ] [T042] [P] Mock decision +`"allow_deanonymize": True` (DEV_MODE parity). — `sdk/agentshield_sdk/mock_opa.py`
- [ ] [T043] `bundle_generator.generate_bundle_data()` — `agents[sa_subject].tools` dicts (both sandbox+production legs, ~L136-143) and the `grants[team]` SELECT+dicts (~L158-186, add `t.pii_deanonymize_allowed` to the join + emitted dict) gain `pii_deanonymize_allowed: bool` (fail-closed default False for a bare-string/missing entry). Proves T-S84-024. (after T004) — `services/registry-api/bundle_generator.py`
- [ ] [T044] [P] Tools-snapshot dict (~L93) gains `"pii_deanonymize_allowed": bool(t.pii_deanonymize_allowed)`. (after T004) — `services/registry-api/routers/versions.py`
- [ ] [T045] [P] Tools-snapshot dict (~L505) gains `"pii_deanonymize_allowed": bool(t.pii_deanonymize_allowed)` (distinct section from T008's deploy-gate edit). (after T004) — `services/registry-api/routers/deployments.py`
- [ ] [T046] `agentshield.rego` — `default allow_deanonymize := false`, `_deanon_of()` extractor, `_matching_deanon` over `agent.tools` + `data.grants[agent.team]`, `allow_deanonymize if { allow; count(_matching_deanon) > 0 }`. — `services/registry-api/opa_policy/agentshield.rego`
- [ ] [T047] `agentshield_test.rego` — `test_allow_deanonymize_true_when_flagged_and_allowed`, `_false_when_not_flagged`, `_false_when_denied`. `opa test services/registry-api/opa_policy/` must pass. Proves T-S84-026. (after T046) — `services/registry-api/opa_policy/agentshield_test.rego`
- [ ] [T048] [P] `policy_generator.py` — add the field to its `risk_map`/audit Rego for **audit-parity only** (a comment must state it is **not** the enforcement path — research.md #8). — `services/registry-api/policy_generator.py`

## Phase 10 — Decision 27: Safety Orchestrator `deanonymize_args` + two-sided `safety_client` fix `[P]` (parallel with Phase 9)

- [ ] [T049] Orchestrator schemas — `DeanonymizeArgsRequest{session_id, agent_name, args: dict}` / `DeanonymizeArgsResponse{args: dict}`. — `services/safety-orchestrator/schemas.py`
- [ ] [T050] `Orchestrator.deanonymize_args(req)` — fetch `pii_store.get_mappings(session_id, agent_name)`, recursively substitute `anonymized_text → original_text` in every string leaf (recurse dicts/lists; non-strings pass through); no mappings → `args` unchanged. Add the STUB breadcrumb comment in `scan_output` (block/redact action deferred). (after T049) — `services/safety-orchestrator/orchestrator.py`
- [ ] [T051] New route `POST /api/v1/deanonymize/args`. (after T050) — `services/safety-orchestrator/main.py`
- [ ] [T052] [regression-first] `safety_client` field-bug fix — **first** add a test that calls `scan_output(...)` against a mocked `{"blocked":false,"deanonymized_message":"Jane Doe","scores":{}}`, asserting the request carries `message`/`thread_id` (not `text`/`trace_id`) — confirm it **fails** against current code — **then** fix: request keys (`text→message`, `trace_id→thread_id`) + response reads (`sanitized_text→anonymized_message`, `clean_text→deanonymized_message`, original-text fallback) for both `scan_input`/`scan_output`; keep SDK-side dataclass field names (`sanitized_text`/`clean_text`); add `deanonymize_args(args, agent_name, session_id)` (fail-open: any failure → return `args` unchanged, log warning, never raise). Proves T-S84-027/028/029/030. — `sdk/agentshield_sdk/safety_client.py`
- [ ] [T053] [P] Mock `deanonymize_args` (pass-through); keep `scan_*` mock keys as the SDK dataclass fields (`sanitized_text`/`clean_text`) since the mock bypasses the wire. — `sdk/agentshield_sdk/mock_safety.py`
- [ ] [T054] SDK version bump — `__version__` → `0.2.2`. (after T052) — `sdk/agentshield_sdk/__init__.py`
- [ ] [T055] Bug postmortem — Found/Fixed (date + fixing image tag/commit), Symptom, Root cause (the two-sided field mismatch → scans fail-closed in a real deploy), Fix (class-fix), cross-link the T052 regression test. (after T052) — `docs/bugs/safety-client-scan-field-mismatch.md`
- [ ] [T056] [deferred — written this run, NOT executed] Bump `SAFETY_ORCHESTRATOR_TAG` (verify current `0.1.3` then increment) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## Phase 11 — Decision 27: wire the gate into `governed_tool` (STUB output-scan action)

- [ ] [T057] `governed_tool` gate — in this exact order: (1) hoist `thread_id` above the OPA call; (2) after `check_tool()`, `await opa_client.record_decision(...)` best-effort; (3) unchanged deny/HITL; (4) unchanged eval-mode short-circuit; (5) **new** immediately before the real call, `if decision.allow_deanonymize:` de-anonymize `kwargs` via `safety_client.deanonymize_args` (fail-open — placed strictly after the eval-record short-circuit, research.md B11); (6) unchanged dispatch; (7) **new** output-scan with the **STUB action** — `if getattr(fn,"scan_results",True):` call `scan_output(str(result), ...)`, **log** the verdict/scores, **return `result` unchanged** (block/redact NOT enforced; breadcrumb comment cross-referencing §3/§8 + gap ledger); (8) `return result`. Proves T-S84-031..035. (after T036,T039,T041,T050,T052) — `sdk/agentshield_sdk/graph_builder.py`
- [ ] [T058] SDK version bump — `__version__` → `0.2.3`. (after T057) — `sdk/agentshield_sdk/__init__.py`
- [ ] [T059] [deferred — written this run, NOT executed] Bump `REGISTRY_API_TAG` (bundle/versions/deployments Decision-27 changes) **and** re-bump `DECLARATIVE_RUNNER_TAG` (picks up sdk `0.2.3`) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## CP4 — Checkpoint: Decision 27 governed gate

**Deferred — scripts WRITTEN this run, NOT executed; the user runs them when ready to deploy.** Deploys registry-api (bundle) + safety-orchestrator + declarative-runner + fixture agent (sdk `0.2.3`).

- [ ] [CP4a] Deploy script — `bash scripts/deploy-cpe2e.sh` (registry-api, safety-orchestrator, declarative-runner, fixture agent) **or** `helm upgrade`; `kubectl rollout status`; run `opa test services/registry-api/opa_policy/ -v` (local, gates the deploy). — `scripts/deploy-cp4.sh`
- [ ] [CP4b] Infra smoke — `opa test` passes incl. new `allow_deanonymize` cases; `GET /api/v1/bundle/data.json` shows `pii_deanonymize_allowed` on **every** tool entry in `agents[...].tools` **and** `grants[...]`; safety-orchestrator `POST /api/v1/deanonymize/args` reachable → `200`. — `scripts/smoke-cp4-infra.sh`
- [ ] [CP4c] Behaviour smoke — flagged tool + a stored `PiiMapping` → the `echo` fixture receives the **real** value (de-anon proof, T-S84-032); internal server `scan_results=false` → scan call skipped (T-S84-033); external server `scan_results=false` **ignored** → scan still called (T-S84-034); a native `http` tool call now produces an `opa_decisions` row (SQL count); a blocked verdict is **not enforced** (result unchanged — STUB); `eval_mode=record` → recorded args stay anonymized (T-S84-035). jq/SQL assertions on codes+fields. — `scripts/smoke-cp4-behaviour.sh`

## Phase 12 — Studio: MCP Servers screen

- [ ] [T060] `mcpServersApi.ts` — `McpServer` type + `listMcpServers`/`getMcpServer`(+tools)/`createMcpServer`/`updateMcpServer`/`syncMcpServer`/`deleteMcpServer` (rides the shared `http` from `registryApi.ts`). — `studio/src/api/mcpServersApi.ts`
- [ ] [T061] `McpServersPage.tsx` — list table + "Register Server" form (mirror `KnowledgeBasesPage.tsx`; `useQuery(['mcp-servers'])`, create mutation invalidates `['mcp-servers']`; Internal/External toggle: External → auth-config picker, Internal → identity-mode dropdown; `stdio` transport shown **disabled**). (after T060) — `studio/src/pages/McpServersPage.tsx`
- [ ] [T062] `McpServerDetailPage.tsx` — detail + Discovered Tools tab (the FR-MCP-41 proof table; `inactive` rows greyed) + Settings tab (edit PUT, Sync → `syncMcpServer`, Delete → `deleteMcpServer` surfacing the `409` blocking-agents message); `status="error"` → red banner + Sync/Retry (mirror `KnowledgeBaseDetailPage.tsx`). (after T060) — `studio/src/pages/McpServerDetailPage.tsx`
- [ ] [T063] Sidebar — `SETTINGS_ITEMS` +`{label:"MCP Servers", to:"/mcp-servers", icon: Server}` (`lucide-react`); `detectSections` adds `/mcp-servers` to `"settings"`. — `studio/src/components/Sidebar.tsx`
- [ ] [T064] Routes — `/mcp-servers` and `/mcp-servers/:id`. (after T061,T062) — `studio/src/App.tsx`
- [ ] [T065] Vitest — `McpServersPage`: list renders from mocked `listMcpServers`; register submits the right `createMcpServer` payload + invalidates; a `409` surfaces a toast; **save→reload→assert** (register, then a fresh `GET` re-render shows the server, not client state). (after T061) — `studio/src/pages/McpServersPage.test.tsx`
- [ ] [T066] Vitest — `McpServerDetailPage`: renders tools from mocked `getMcpServer`; a `status="error"` server shows the banner + Sync/Retry; Delete surfaces a mocked `409`'s blocking-agents message. (after T062) — `studio/src/pages/McpServerDetailPage.test.tsx`

## Phase 13 — Studio: `ToolsPage` read-only `mcp_tool` + PII checkbox

- [ ] [T067] `registryApi.ts` — `RegistryTool` +`mcp_server_id`/`mcp_tool_name`/`mcp_server_name`/`mcp_server_is_external`/`mcp_server_scan_results`/`pii_deanonymize_allowed` (all optional); `CreateToolPayload` +`pii_deanonymize_allowed`. — `studio/src/api/registryApi.ts`
- [ ] [T068] `ToolsPage.tsx` — `type==='mcp_tool'` rows hide Edit/Delete + show a "View source server →" link to `/mcp-servers/{mcp_server_id}` + an `MCP` type badge; the create/edit form gains a `pii_deanonymize_allowed` checkbox ("Allow this tool to receive real PII values") for **every** type, wired into both mutation payloads. (after T067) — `studio/src/pages/ToolsPage.tsx`
- [ ] [T069] Vitest (page had zero coverage — also cover existing http/python) — create `http`/`python` submit the right payload; edit pre-fills; an `mcp_tool` row renders no Edit/Delete + a working link; the checkbox toggles and is in both payloads. (after T068) — `studio/src/pages/ToolsPage.test.tsx`

## Phase 14 — Studio: `ToolsPicker` source-server badge

- [ ] [T070] `ToolsPicker.tsx` — when `tool.mcp_server_name` is set, render a small badge (`text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-500`) with the server name before the risk badge; no change to the `KNOWLEDGE_SEARCH_TOOL` filter or selection behavior. (after T067) — `studio/src/components/agent/ToolsPicker.tsx`
- [ ] [T071] Vitest (component had zero coverage) — `knowledge_search` still filtered, toggle calls `onToggle`, empty-state renders; `mcp_server_name:"github-mcp"` → badge with that text; no `mcp_server_name` → no badge. (after T070) — `studio/src/components/agent/ToolsPicker.test.tsx`
- [ ] [T072] [deferred — written this run, NOT executed] Bump `STUDIO_TAG` (verify current `0.1.160` then increment; covers Phases 12–14) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## CP5 — Checkpoint: Studio UI

**Deferred — scripts WRITTEN this run, NOT executed; the user runs them when ready to deploy.** Deploys studio.

- [ ] [CP5a] Deploy script — `bash scripts/deploy-cpe2e.sh` (studio) **or** `helm upgrade` studio; `kubectl rollout status deploy/agentshield-studio`. — `scripts/deploy-cp5.sh`
- [ ] [CP5b] Infra smoke — studio pod Ready; `GET /` (SPA) → `200`; the API the UI consumes reachable: `GET /api/v1/mcp-servers` → `200` paginated. — `scripts/smoke-cp5-infra.sh`
- [ ] [CP5c] Behaviour smoke — `cd studio && npm run test -- McpServersPage McpServerDetailPage ToolsPage ToolsPicker` green + `npm run typecheck`; then `curl` the register→detail path the UI drives (register a server → `GET /mcp-servers/{id}` shows the discovered tools). — `scripts/smoke-cp5-behaviour.sh`

## Phase 15 — Testing, Regression & Polish

- [ ] [T073] Backend e2e suite — `suite-84-mcp-tools.sh` compiling every `T-S84-001..035` referenced above into real executable assertions (mirror `suite-81`'s template: `kubectl exec` into registry-api, inline `python3`+`httpx`/ORM, `RESULT <id> PASS/FAIL`, trailing `FAILS`, exit-code keyed), run in dependency order against one instance of the Phase-5 fixture (T023). Covers: 401 no-token / 403 foreign-team / 200-with-`is_error` tool failure / register→discover→bind→appears-in-picker / lifecycle (name-immutable 422, delete-blocked-while-bound 409, `mcp_tool`-delete-disabled 409). (after all backend tasks) — `scripts/e2e/suite-84-mcp-tools.sh`
- [ ] [T074] Register the suite — add `suite-84-mcp-tools.sh` to the runner (re-confirm `84` is still free; else take the next number and rename the `T-S84-*` IDs). (after T073) — `scripts/e2e/run-all.sh`
- [ ] [T075] Playwright journey — `mcp-servers.spec.ts` (mirror `knowledge.spec.ts`, real Keycloak login): register → assert redirect to detail + discovered-tools table (FR-MCP-41) → **reload the detail route, tools still listed** (save→reload→assert) → open the builder/Tools Picker, assert the discovered tool shows its source-server badge (FR-MCP-42), bind it, save → **reload the agent, tool still bound** (2nd persistence round trip). Each step has its own `expect`/`waitForResponse`. (after T061,T062,T068,T070) — `studio/e2e/mcp-servers.spec.ts`
- [ ] [T076] Regression assertion — one new assertion in `suite-18`: a native `http` tool call now also produces an `opa_decisions` row (`record_decision` is generic across all tool types). (after T057) — `scripts/e2e/suite-18-opa-governance.sh`
- [ ] [T077] Gap ledger — record the Phase-1 gaps in the canonical Known-gaps header, tagged deferred (intentional) vs not-yet-wired (debt): output-scan **block/redact action STUB — must not be reported done as "enforced"**; production OPA bundle staleness (pre-existing, not deepened); free-text de-anon edge in reused `scan_output`; `oauth2`/`mtls` `auth_headers` best-effort; health-loop / `list_changed` subscription (Phase 2); `tools/list` pagination. — `docs/testing/manual-ui-e2e-test-plan.md`
- [ ] [T078] [deferred — written this run, NOT executed] Regression sweep — run the blast-radius suites after Phase 11 lands: `suite-3-safety`, `suite-4-hitl`, `suite-18-opa-governance` (+ the T076 assertion), `suite-74-eval-v2-side-effects`, `suite-81-deploy-tool-autograt`, plus `suite-84` + `cd studio && npm run test` + `bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts`. A green new test but a broken neighbor is a shipped regression; any regression is fixed as a follow-up with its own failing-then-passing test (CLAUDE.md rule 7). Runbook: — `docs/plan/mcp-tool-source-phase1/quickstart.md`

---

## Dependency Notes (cross-phase, beyond the inline `(after Txxx)`)

- **CP2 (MVP) unblocks nothing downstream by itself** — Phases 7–8 (dispatch) depend on the proxy's `/internal/tools/call` (T021) + a discovered tool (T032), not on CP2 being *run*. The checkpoint is a validation gate, not a code dependency.
- **Phase 11 (T057) is the convergence point** of Phases 7, 8, 9, 10 — it needs `McpToolExecutor.scan_results` (T036) + the runner branch (T039) + `OPADecision.allow_deanonymize` (T041) + `deanonymize_args`/fixed `scan_output` (T050/T052). It cannot start until all four land.
- **Phase 9 ∥ Phase 10** — fully independent (OPA/bundle vs. safety-orchestrator/safety_client); the SDK `__init__` version bump is sequenced (`0.2.1` T037 → `0.2.2` T054 → `0.2.3` T058) so they must not race on that one file.
- **T023 (fixture) is pulled into Phase 5** (not Phase 15 as plan.md Task 15 literally lists) because T022's Dockerfile `COPY` and CP2's discovery smoke both require the file to exist before the proxy image is built. Phase 15's `suite-84` (T073) consumes the already-created fixture. This is the only deliberate reorder from plan.md's file placement — justified by "the Dockerfile can't reference a non-existent file"; the logical sequence is otherwise preserved.
- **`main.py` is edited twice** (T010 mounts `internal_mcp_router`; T032 mounts `mcp_servers_router`) — distinct include lines, distinct tasks/phases; not a conflict.
- **`deployments.py` is edited twice** (T008 deploy-gate refactor; T045 tools-snapshot field) — distinct sections.

# Tasks — MCP as a Tool Source, Phase 2

**Source of truth:** `plan.md` (13-task decomposition — expanded here to file-level granularity, same dependency order), `docs/design/mcp-tool-source-architecture.md` (LOCKED) §7a/§8, `docs/design/todo/mcp-tools-for-agents-requirements.md` §6/§11 (FR-MCP-07/21/22), `research.md`, `data-model.md`, `quickstart.md`, `contracts/{mcp-proxy-internal-phase2,registry-api-internal-mcp-phase2,studio-mcp-servers-phase2}.md`. Where this file and the design doc differ, the design doc wins (except the two grounded deviations in research.md B1/C3).

**Total tasks:** 52 (40 implementation + 12 checkpoint).
**Phases:** 11 implementation phases + 4 checkpoint phases (CP1–CP4) interleaved.
**Workstream independence:** WS-A (P2-P4), WS-B (P5-P7), WS-C (P8-P10) are mutually independent after Phase-1 is deployed and can be built in parallel; this file orders them A→B→C so each lands with its own checkpoint. **No migration** (data-model.md §1). **Every checkpoint script + every image-tag-bump task is DEFERRED — written this run, NOT executed. The user runs them when ready to deploy.**

**Suggested MVP scope:** target **CP1** first (the health loop is the smallest self-contained slice: proxy probe → registry-api loop → Studio surfacing, provable by one server going `error` and Studio showing it).

---

## Baseline (verify-then-bump — never reuse a claimed tag/number; re-verify at build time per `quickstart.md`)

Observed this session: alembic head `0072` → **no migration**; e2e ceiling `84` → new **`suite-85`**. Image tags: `MCP_PROXY_TAG=0.1.0`, `REGISTRY_API_TAG=0.2.226`, `STUDIO_TAG=0.1.161`, `DECLARATIVE_RUNNER_TAG=0.1.60`, `sdk.__version__=0.2.3`. Every `[deferred]` bump task re-greps the current value first, then increments the patch in **both** `scripts/deploy-cpe2e.sh` **and** `charts/agentshield/values.yaml` (for `mcp-proxy`, also `charts/agentshield/charts/mcp-proxy/values.yaml`) — **written, not run.**

---

## Phase Summary

| Phase | Name | Tasks | Delivers / Proves |
|---|---|---|---|
| P1 | Setup & Baseline Verification | T001–T002 | No-migration confirmed; suite `85`; tags re-verified; `mcp` notification + `FastMCP` mutation + Keycloak token hooks pinned |
| P2 | WS-A — proxy `/internal/health` probe | T003–T005 | Admin-plane reachability probe (reuses session; refreshes `list_changed_supported`); never writes DB |
| P3 | WS-A — registry-api health loop | T006–T010 | Lifespan loop (advisory-lock single-flight) → `status`/`health_detail`; threshold/backoff; no `last_synced_at` write |
| **CP1** | **Checkpoint — Health loop** | CP1a–CP1c | **Deferred.** Dead server → `error` after 3 cycles; recovery → `connected`; single-flight; Studio surfaces it |
| P4 | WS-A — Studio Health panel | T011–T013 | Detail-page Health section + 15s poll + Vitest |
| P5 | WS-B — fixture `list_changed` simulation `[P]` | T014 | Fixture advertises `listChanged` + `simulate_tool_change` control tool |
| P6 | WS-B — proxy subscription manager | T015–T018 | Long-lived subscriber + notification handler + debounce + capped reconnect |
| P7 | WS-B — registry-api re-sync endpoint | T019–T022 | `_materialize_and_discover` extracted; `POST /internal/mcp/list-changed`; coalesce guard |
| **CP2** | **Checkpoint — list_changed auto-resync** | CP2a–CP2c | **Deferred.** Upstream tool appears → auto re-sync → `Tool` row persists (save→reload→assert) |
| P8 | WS-C — per-server Secret identity plumbing | T023–T024 | `materialize_server_secret` carries `identity_mode`/`identity_audience` |
| P9 | WS-C — proxy identity branch | T025–T030 | `resolve_headers` (service-identity mint + OBO stub) + composite session key + Keycloak client |
| P10 | WS-C — chart + egress + executor `x-user-sub` | T031–T037 | Keycloak client-secret mount + egress; both executors emit `x-user-sub` |
| **CP3** | **Checkpoint — Internal identity** | CP3a–CP3c | **Deferred.** Service-identity token sent; OBO fail-closed + stub; `none` byte-identical; RBAC unchanged |
| P11 | Testing, Regression & Polish | T038–T041 | `suite-85`; Playwright Health case; gap ledger; regression sweep |
| **CP4** | **Checkpoint — Full Phase-2 e2e + regression** | CP4a–CP4c | **Deferred.** suite-85 green; suite-84/18/4/3 green; Studio Vitest+Playwright green |

---

## Phase 1 — Setup & Baseline Verification

- [ ] [T001] [P] Re-verify baselines: alembic head still `0072` (**no migration** this phase), e2e ceiling still `84` (→ `suite-85`), tags (`MCP_PROXY_TAG=0.1.0`, `REGISTRY_API_TAG=0.2.226`, `STUDIO_TAG=0.1.161`, `DECLARATIVE_RUNNER_TAG=0.1.60`, `sdk.__version__=0.2.3`). Record drift; adjust downstream numbers. Runbook — `docs/plan/mcp-tool-source-phase2/quickstart.md`
- [ ] [T002] [P] Pin the runtime hooks against the installed `mcp` version: (a) how `ClientSession` dispatches a server notification to a handler + the `list_changed` notification type name (feeds T015/T016); (b) how `FastMCP` mutates its tool set at runtime + emits `notifications/tools/list_changed` + advertises `tools.listChanged` (feeds T014); (c) the platform Keycloak token URL/realm (feeds T027/T032). Record findings. Runbook — `docs/plan/mcp-tool-source-phase2/quickstart.md`

## Phase 2 — WS-A: proxy `/internal/health` probe

- [ ] [T003] `schemas.py` — add `McpHealthRequest{server_id: UUID}` + `McpHealthResponse{ok, status, health_detail, protocol_version, list_changed_supported, tool_count}` (Key Interfaces). — `services/mcp-proxy/schemas.py`
- [ ] [T004] `config.py` + `mcp_client.py` — wire the existing-but-unused `MCP_CONNECT_TIMEOUT_SECONDS` into `connect_and_initialize`/`list_tools` (bounded probe). — `services/mcp-proxy/config.py`, `services/mcp-proxy/mcp_client.py`
- [ ] [T005] `main.py` — `POST /internal/health` route (admin-plane: authenticate → require `caller_sa_subject == REGISTRY_API_SA_SUBJECT` else 403 → `read_server_secret` → `session_cache.get_or_create` → `list_tools()` probe → `200 McpHealthResponse`; any failure → `evict` + `200 ok=false` with reason, never 5xx; no `Tool` write). Uses `connection.auth_headers` for now (T028 rewires to `resolve_headers`). Proves T-S85-001/002/003. (after T003,T004) — `services/mcp-proxy/main.py`
- [ ] [T005b] [deferred — written, NOT executed] Bump `MCP_PROXY_TAG` (verify current `0.1.0` then increment; single proxy bump covers P2+P6+P9) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, `charts/agentshield/charts/mcp-proxy/values.yaml`

## Phase 3 — WS-A: registry-api health loop

- [ ] [T006] `mcp_proxy_client.health_check_server(server_id) -> dict` — POST `/internal/health` with the mcp-proxy SA token; RuntimeError only on transport/non-200 (mirrors `discover_server`). (contracts/registry-api-internal-mcp-phase2.md) — `services/registry-api/mcp_proxy_client.py`
- [ ] [T007] `config.py` — add `mcp_health_check_enabled` (True), `mcp_health_check_interval_seconds` (60), `mcp_health_failure_threshold` (3), `mcp_health_check_concurrency` (8), `mcp_health_max_backoff_cycles` (10). — `services/registry-api/config.py`
- [ ] [T008] `mcp_health.py` — `mcp_health_loop()` (mirrors `cost_backfill_loop`) + `_sweep_once()` (advisory-lock single-flight via `pg_try_advisory_lock(_SWEEP_LOCK_KEY)` on a dedicated engine connection; enumerate all `MCPServer`; skip `_backoff_skip>0`; bounded-concurrency probe; `_probe_and_apply`; commit; `pg_advisory_unlock` in finally) + `_probe_and_apply(session, server)` (data-model.md §2a threshold/backoff on `status`+`health_detail`; **does not write `last_synced_at`**). Proves T-S85-004/005/006/007. (after T006,T007) — `services/registry-api/mcp_health.py`
- [ ] [T009] `main.py` lifespan — start `mcp_health_task = asyncio.create_task(mcp_health_loop())` when `settings.mcp_health_check_enabled`; cancel + await at shutdown (mirror the `cost_task` block ~L127-139). (after T008) — `services/registry-api/main.py`
- [ ] [T010] [deferred — written, NOT executed] Bump `REGISTRY_API_TAG` (verify current then increment; single registry bump covers P3+P7+P8) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## CP1 — Checkpoint: Health loop

**Deferred — scripts WRITTEN this run, NOT executed; the user runs them when ready to deploy.** Deploys mcp-proxy (P2) + registry-api (P3). Strict bash, real curl/kubectl/psql assertions, `echo "PASS"` at the end.

- [ ] [CP1a] Deploy script — `bash scripts/deploy-cpe2e.sh` (mcp-proxy + registry-api) **or** `helm upgrade`; `kubectl rollout status` both. — `scripts/deploy-mcp2-cp1.sh`
- [ ] [CP1b] Infra smoke — proxy `POST /internal/health` (registry-api SA token) against the in-pod fixture → `200 ok=true`; missing token → `401`; agent-SA token → `403`; registry-api health loop logs present. — `scripts/smoke-mcp2-cp1-infra.sh`
- [ ] [CP1c] Behaviour smoke — register a server at a dead URL; over ≥3 intervals assert `mcp_servers.status` flips to `error` with `health_detail.consecutive_failures>=3` + `last_error`; point it at the live fixture, assert recovery to `connected`/`0`; assert `last_synced_at` unchanged across a health cycle; `kubectl scale registry-api --replicas=2` and assert `consecutive_failures` advances by 1/interval (single-flight). — `scripts/smoke-mcp2-cp1-behaviour.sh`

## Phase 4 — WS-A: Studio Health panel

- [ ] [T011] `McpServerDetailPage.tsx` — Health section (status pill [reuse/duplicate `StatusBadge`], `last_success_at`, `consecutive_failures`, `last_error`, `last_synced_at`, `list_changed_supported`, `identity_mode` note incl. the OBO "(pending — Decision 29)" note) reading the fetched `server`; `refetchInterval: 15000` on the `getMcpServer` query. No API-client change. (contracts/studio-mcp-servers-phase2.md §1) — `studio/src/pages/McpServerDetailPage.tsx`
- [ ] [T012] Vitest — extend (don't replace) the detail-page tests: error-status Health render (pill + failure count + `last_error`), connected + `list_changed_supported` "subscribed", `on_behalf_of` pending note. (after T011) — `studio/src/pages/McpServerDetailPage.test.tsx`
- [ ] [T013] [deferred — written, NOT executed] Bump `STUDIO_TAG` (verify current then increment) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

## Phase 5 — WS-B: fixture `list_changed` simulation `[P]`

- [ ] [T014] [P] Extend `stub_mcp_server.py` — advertise `tools.listChanged` (per T002); add `simulate_tool_change(action: str = "add") -> str` that registers/removes a runtime `dynamic_echo` tool and emits `notifications/tools/list_changed`; inert on import; started only via `kubectl exec`. Fallback (documented) if the SDK can't emit at runtime: a `--toolset` restart. (after T002) — `scripts/e2e/fixtures/stub_mcp_server.py`

## Phase 6 — WS-B: proxy subscription manager

> The proxy still writes **no** DB — the subscriber only POSTs the re-sync trigger to registry-api.

- [ ] [T015] `config.py` — add `MCP_LIST_CHANGED_ENABLED` (true), `MCP_LIST_CHANGED_DEBOUNCE_SECONDS` (5), `MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS` (10), `MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS` (5). — `services/mcp-proxy/config.py`
- [ ] [T016] `mcp_client.py` — `connect_and_initialize(url, headers, message_handler=None)` + `McpSession._connect(..., message_handler=None)` passing the handler to `ClientSession` (exact hook per T002). — `services/mcp-proxy/mcp_client.py`
- [ ] [T017] `subscription_manager.py` — `ensure_subscription(server_id)` (idempotent; spawn a task holding a session with a `list_changed` handler that debounces then POSTs `{REGISTRY_API_URL}/api/v1/internal/mcp/list-changed`; capped reconnect; tear down after cap) + `stop_subscription(server_id)` + `SubscriptionState` (data-model.md §3d). Proves T-S85-010/012/013. (after T015,T016) — `services/mcp-proxy/subscription_manager.py`
- [ ] [T018] `main.py` — call `subscription_manager.ensure_subscription(str(server_id))` after a successful `/internal/discover` and `/internal/health` when `list_changed_supported`. (after T017) — `services/mcp-proxy/main.py`

## Phase 7 — WS-B: registry-api re-sync endpoint

- [ ] [T019] `mcp_discovery.py` — **move** `_materialize_and_discover` + `_mark_server_error` verbatim from `routers/mcp_servers.py` (behavior-neutral) + add `_last_resync`/`_resync_locks` + `MIN_RESYNC_INTERVAL_SECONDS` from env. — `services/registry-api/mcp_discovery.py`
- [ ] [T020] `mcp_servers.py` — import `_materialize_and_discover`/`_mark_server_error` from `mcp_discovery` (remove local defs; all call sites unchanged). Proves T-S85-017 (suite-84 stays green). (after T019) — `services/registry-api/routers/mcp_servers.py`
- [ ] [T021] `config.py` — add `mcp_list_changed_min_resync_interval_seconds` (10). — `services/registry-api/config.py`
- [ ] [T022] `internal_mcp.py` — `POST /api/v1/internal/mcp/list-changed` (`ListChangedRequest/Response`; resolve server → unknown → `200 ok=false reason=server_not_found`; coalesce guard under per-server lock → `_materialize_and_discover` → commit → counters; malformed → `422`). (contracts/registry-api-internal-mcp-phase2.md) Proves T-S85-011/014/015/016. (after T019,T021) — `services/registry-api/routers/internal_mcp.py`

## CP2 — Checkpoint: list_changed auto-resync

**Deferred — scripts WRITTEN this run, NOT executed.** Deploys mcp-proxy (P6) + registry-api (P7).

- [ ] [CP2a] Deploy script — `bash scripts/deploy-cpe2e.sh` (mcp-proxy + registry-api) **or** `helm upgrade`; `kubectl rollout status`. — `scripts/deploy-mcp2-cp2.sh`
- [ ] [CP2b] Infra smoke — `suite-84` still green (extraction behavior-neutral); `POST /internal/mcp/list-changed` unknown server → `200 ok=false reason=server_not_found`; malformed → `422`. — `scripts/smoke-mcp2-cp2-infra.sh`
- [ ] [CP2c] Behaviour smoke — start the fixture in the proxy pod; register it; `simulate_tool_change("add")` → within the debounce window the proxy POSTs `/list-changed` once → `GET /mcp-servers/{id}` shows `dynamic_echo` (**save→reload→assert**); `simulate_tool_change("remove")` → the tool goes `inactive` (not deleted); a burst of 3 within 5s → one re-sync; a second `/list-changed` within 10s → `coalesced:true`. jq/SQL assertions. — `scripts/smoke-mcp2-cp2-behaviour.sh`

## Phase 8 — WS-C: per-server Secret identity plumbing

- [ ] [T023] `mcp_secrets.py` — `materialize_server_secret` adds `identity_mode` (from `server.identity_mode`) + `identity_audience` (from `server.transport_config.get("identity_audience")` if dict else None) to the `connection` JSON (data-model.md §2c). `auth_headers` composition unchanged. Proves T-S85-020. — `services/registry-api/mcp_secrets.py`
- [ ] [T024] [covered by T010's `REGISTRY_API_TAG` bump — no separate bump] Note: P8's registry change ships in the same registry-api image as P3/P7. — (no file; bookkeeping)

## Phase 9 — WS-C: proxy identity branch

> The proxy never gains DB access or the master key. The Keycloak client secret is a **file mount** (T031), never a `get secrets` API read.

- [ ] [T025] `credentials.py` — `ServerConnection` +`identity_mode: str="none"`, `identity_audience: str|None=None`; parse both from the Secret `connection` JSON (default `"none"` when absent → Phase-1 behavior). — `services/mcp-proxy/credentials.py`
- [ ] [T026] `config.py` — add `KEYCLOAK_TOKEN_URL` (""), `MCP_PROXY_KEYCLOAK_CLIENT_ID` ("agentshield-mcp-proxy"), `MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH` (`/var/run/secrets/mcp-proxy-keycloak/client-secret`), `KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS` (30). — `services/mcp-proxy/config.py`
- [ ] [T027] `keycloak_client.py` — `mint_service_account_token(audience) -> (token, exp)` (client-credentials grant to `KEYCLOAK_TOKEN_URL`, client secret from file; optional `audience`; parse `exp` from JWT; raise on non-2xx/unreachable) + `invalidate(audience)`. (after T026) — `services/mcp-proxy/keycloak_client.py`
- [ ] [T028] `identity.py` — `resolve_headers(connection, *, user_sub, is_data_plane)` selection matrix (none→static; service_identity→minted bearer merged over static; on_behalf_of admin→service token; on_behalf_of data + empty user_sub→raise `OnBehalfOfIdentityRequired`; on_behalf_of data + user_sub→`mint_on_behalf_of_token` STUB→raise `OnBehalfOfNotAvailable`) + per-audience token cache (`exp−skew`) + `mint_on_behalf_of_token` STUB + the two exception classes. (contracts/mcp-proxy-internal-phase2.md §2) Proves T-S85-021..025. (after T025,T027) — `services/mcp-proxy/identity.py`
- [ ] [T029] `session_cache.py` — key → `SessionKey = tuple[str, str|None]`; `_effective_user_sub(connection, user_sub)` (user_sub only for `on_behalf_of`); `peek/get_or_create/set_session/evict` gain `user_sub=None` param; per-key locks (data-model.md §3a). Proves T-S85-026. — `services/mcp-proxy/session_cache.py`
- [ ] [T030] `main.py` — `/internal/discover` + `/internal/health` build headers via `resolve_headers(..., is_data_plane=False)`; `/internal/tools/call` builds via `resolve_headers(..., user_sub=x_user_sub, is_data_plane=True)`, threads `x_user_sub` into `session_cache.peek/get_or_create`, catches `OnBehalfOf*` → `200 is_error=true`, adds a 401-refresh (`keycloak_client.invalidate` + retry) for service-identity. (after T028,T029) — `services/mcp-proxy/main.py`

## Phase 10 — WS-C: chart + egress + executor `x-user-sub`

- [ ] [T031] `secret.yaml` — create the Keycloak client Secret `{release}-mcp-proxy-keycloak` (`client-secret` from `.Values.keycloak.clientSecret`; skip when `.Values.keycloak.existingSecret` is set). — `charts/agentshield/charts/mcp-proxy/templates/secret.yaml`
- [ ] [T032] `deployment.yaml` — env `KEYCLOAK_TOKEN_URL`/`MCP_PROXY_KEYCLOAK_CLIENT_ID`/`MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH` + `MCP_LIST_CHANGED_*` from values; read-only volumeMount of the Keycloak secret at `/var/run/secrets/mcp-proxy-keycloak`. (after T031) — `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml`
- [ ] [T033] `values.yaml` (sub-chart + parent) — `keycloak.{tokenUrl,clientId,clientSecret,existingSecret}` + `listChanged.*` defaults; parent `mcp-proxy.keycloak.tokenUrl` wired to the platform Keycloak. — `charts/agentshield/charts/mcp-proxy/values.yaml`, `charts/agentshield/values.yaml`
- [ ] [T034] NetworkPolicy — allow proxy egress to the in-cluster Keycloak service (service-identity minting). — `infra/network-policies/platform-allow-ingress.yaml`
- [ ] [T035] SDK executor `x-user-sub` — `config.USER_SUB = os.getenv("AGENTSHIELD_USER_SUB", "")`; `McpToolExecutor.as_tool_callable` adds `"x-user-sub": config.USER_SUB` to the request headers **only when non-empty**; `__version__ → 0.2.4`. Proves T-S85-027. — `sdk/agentshield_sdk/config.py`, `sdk/agentshield_sdk/tool_executor.py`, `sdk/agentshield_sdk/__init__.py`
- [ ] [T036] Runner executor `x-user-sub` — `config.USER_SUB = os.getenv("AGENTSHIELD_USER_SUB", "")`; `McpToolNodeExecutor.as_tool_callable` adds `"x-user-sub"` when non-empty (separate impl mirroring the SDK). — `services/declarative-runner/config.py`, `services/declarative-runner/node_executors.py`
- [ ] [T037] [deferred — written, NOT executed] Bump `MCP_PROXY_TAG` (mirror; the single P2/P6/P9/P10 proxy bump) + `DECLARATIVE_RUNNER_TAG` (rebuilds sdk `0.2.4`) in both files. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`, `charts/agentshield/charts/mcp-proxy/values.yaml`

## CP3 — Checkpoint: Internal identity

**Deferred — scripts WRITTEN this run, NOT executed.** Deploys mcp-proxy (P9/P10) + registry-api (P8, already in P3/P7 image) + declarative-runner + a fixture agent (sdk 0.2.4). Requires the Keycloak confidential client to exist (quickstart.md).

- [ ] [CP3a] Deploy script — `bash scripts/deploy-cpe2e.sh` **or** `helm upgrade`; `kubectl rollout status`. — `scripts/deploy-mcp2-cp3.sh`
- [ ] [CP3b] Infra smoke — proxy pod mounts `/var/run/secrets/mcp-proxy-keycloak/client-secret`; RBAC unchanged (`can-i get secrets -n agentshield-mcp` → yes, `-n agentshield-platform` → **no**); proxy can reach Keycloak (a `service_identity` register+discover succeeds); a per-server Secret's `connection.identity_mode` matches the row (T-S85-020). — `scripts/smoke-mcp2-cp3-infra.sh`
- [ ] [CP3c] Behaviour smoke — `none` server `tools/call` byte-identical to Phase-1 (suite-84 slice green); `service_identity` server `tools/call` carries a minted bearer (assert via a capturing upstream / fixture that echoes headers); `on_behalf_of` `tools/call` empty `x-user-sub` → `200 is_error=true` "requires a user identity" (T-S85-023); with `x-user-sub` → `200 is_error=true` "blocked on Decision 29" (T-S85-024); an agent pod with `AGENTSHIELD_USER_SUB` set makes the executor send `x-user-sub` (T-S85-027). jq assertions. — `scripts/smoke-mcp2-cp3-behaviour.sh`

## Phase 11 — Testing, Regression & Polish

- [ ] [T038] Backend e2e — `suite-85-mcp-health-notify-identity.sh` compiling every `T-S85-001..028` into real assertions (mirror `suite-84`: `kubectl exec` into registry-api, inline `python3`+`httpx`/ORM, `RESULT <id> PASS/FAIL`, trailing `FAILS`, exit-code keyed), driven against one in-pod fixture instance. Covers: health probe 401/403/200-ok-false, loop threshold/recovery/single-flight, list_changed→re-sync (add/remove/coalesce/unknown), service-identity header, OBO fail-closed + stub, `x-user-sub` emission, `identity_mode` in the Secret. (after all backend tasks) — `scripts/e2e/suite-85-mcp-health-notify-identity.sh`
- [ ] [T039] Register the suite — add `suite-85` to the runner (re-confirm `85` free; else next number + rename `T-S85-*`). (after T038) — `scripts/e2e/run-all.sh`
- [ ] [T040] Playwright — add one case to `mcp-servers.spec.ts`: register → detail → Health panel renders status pill + "Last successful check" from a real `GET` (`page.waitForResponse`), infra-gated. Keep existing cases green. (after T011) — `studio/e2e/mcp-servers.spec.ts`
- [ ] [T041] Gap ledger — record Phase-2 gaps in the canonical Known-gaps header: OBO exchange **STUB** (blocked on Decision 29 — **must not be reported done as "on-behalf-of works"**), multi-replica subscription fan-out (harmless duplicate re-syncs, exactly-once deferred), Keycloak confidential-client provisioning prerequisite, `last_synced_at`-vs-`last_success_at` deviation, no manual health-check button, `oauth2`/`mtls` service-identity uncovered, in-memory health-backoff state. — `docs/testing/manual-ui-e2e-test-plan.md`

## CP4 — Checkpoint: Full Phase-2 e2e + regression

**Deferred — scripts WRITTEN this run, NOT executed.**

- [ ] [CP4a] Deploy script — full `bash scripts/deploy-cpe2e.sh` (all Phase-2 images) **or** `helm upgrade`; `kubectl rollout status` all. — `scripts/deploy-mcp2-cp4.sh`
- [ ] [CP4b] Suite run — `bash scripts/e2e/suite-85-mcp-health-notify-identity.sh` green; then the blast-radius sweep `suite-84` / `suite-18` / `suite-4` / `suite-3` all green (WS-C header refactor + `_materialize_and_discover` extraction behavior-neutral for Phase-1). — `scripts/smoke-mcp2-cp4-suites.sh`
- [ ] [CP4c] Studio — `cd studio && npm run test -- McpServerDetailPage` + `npm run typecheck` green; `bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts` green (Health-panel case + existing register/detail/bind). — `scripts/smoke-mcp2-cp4-studio.sh`

---

## Dependency Notes (cross-phase, beyond the inline `(after Txxx)`)

- **Three independent workstreams.** WS-A (P2-P4), WS-B (P5-P7), WS-C (P8-P10) share only the Phase-1 substrate. They can be implemented in any order / in parallel; the A→B→C ordering here is for checkpoint clarity, not a code dependency.
- **The proxy image is bumped once, not per proxy-touching task.** P2 (health), P6 (subscriber), P9/P10 (identity) all land in one `MCP_PROXY_TAG` increment — T005b reserves it; T037 mirrors it. Don't churn the tag between P2 and P10 if they deploy together; if P2 deploys at CP1 before P6/P9 exist, that's a first increment and P6/P9 take the next.
- **`main.py` (proxy) is edited three times** (T005 health route; T018 ensure_subscription calls; T030 resolve_headers wiring) — distinct regions, distinct phases; sequence T030 last (it depends on identity.py).
- **`config.py` (proxy) is edited three times** (T004 timeout; T015 list_changed; T026 keycloak) — additive, non-conflicting.
- **`config.py` (registry-api) is edited twice** (T007 health; T021 list_changed) — additive.
- **`_materialize_and_discover` extraction (T019/T020) is the only behavior-neutral refactor** — its correctness gate is `suite-84` staying green (T038/CP4b), not a new test. If it perturbs `/sync`, that is a regression fixed red-first (CLAUDE.md rule 7).
- **T028's `resolve_headers` must be byte-identical to Phase-1 for `identity_mode='none'`** (returns exactly `connection.auth_headers`) — the guard is the `none`-server slice of suite-84/85 (T-S85-021).
- **OBO is shipped as plumbing only** — T028's `mint_on_behalf_of_token` is a stub; no task fills it (blocked on Decision 29). CP3c asserts the *stubbed* behavior (structured error), not a working exchange.
- **No migration task exists** — if you find yourself writing `0073`, stop and re-read data-model.md §1.

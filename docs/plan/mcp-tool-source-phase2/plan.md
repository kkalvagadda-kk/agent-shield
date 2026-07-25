# Plan — MCP as a Tool Source, Phase 2

**Status:** Ready for implementation (created 2026-07-25). Builds on the **shipped Phase-1 code** (`services/mcp-proxy/`, registry-api `mcp_servers.py`/`internal_mcp.py`/`mcp_proxy_client.py`/`mcp_secrets.py`, the two dispatch executors, the `mcp-proxy` sub-chart). Every interface below was verified against that code on 2026-07-25.
**Scope:** Phase 2 only — **WS-A** periodic health-check + status surfacing (FR-MCP-22), **WS-B** `notifications/tools/list_changed` subscription (FR-MCP-07), **WS-C** internal-server identity: service-identity (FR-MCP-21, **shippable**) + on-behalf-of (FR-MCP-21, **plumbing shippable, token exchange BLOCKED on Decision 29**). Phase 3 (stdio) and Phase 4 (OAuth 2.1 / resources / prompts) are **not planned here**.
**Inputs:** `docs/design/mcp-tool-source-architecture.md` (LOCKED) §7a/§8/§3b/§3c, `docs/design/todo/mcp-tools-for-agents-requirements.md` §6/§11 (FR-MCP-07/21/22), `docs/design/identity-propagation-architecture.md` (external dependency for OBO), this repo's `CLAUDE.md`.
**Companion artifacts (same dir):** `research.md`, `data-model.md`, `contracts/mcp-proxy-internal-phase2.md`, `contracts/registry-api-internal-mcp-phase2.md`, `contracts/studio-mcp-servers-phase2.md`, `quickstart.md`, `tasks.md`.

> **Where the design doc and this plan differ, the design doc wins — with two grounded exceptions recorded in research.md B1 (the executors do NOT send `x-user-sub` today, so WS-C must add it) and C3 (the health loop writes `health_detail.last_success_at`, NOT `last_synced_at`, because the latter means "last discovery").**

---

## Scope Check — one plan, three loosely-coupled workstreams

Three independent vertical slices that share the Phase-1 substrate but not each other:
- **WS-A (health)** — a registry-api background loop + one new proxy probe endpoint + Studio surfacing. Independently shippable and provable (a server dies → status flips → Studio shows it).
- **WS-B (list_changed)** — a proxy subscriber + one new registry-api internal endpoint + a fixture change. Independently shippable (upstream tool appears → auto re-sync).
- **WS-C (identity)** — a proxy credential branch + the per-server-Secret identity field + executor header threading. Service-identity is independently shippable; on-behalf-of ships as *plumbing with a stubbed exchange* (Decision 29 blocked).

They can be built in parallel after Phase-1 is deployed, but this plan orders them WS-A → WS-B → WS-C so each lands with its own checkpoint. **No new migration** (data-model.md §1) — the whole plan rides on `0072`'s columns.

---

## Goal

Keep every registered MCP server's reachability fresh and visible (health loop → `status`/`health_detail` → Studio), auto-refresh discovered tools when a server announces a change (`list_changed` → re-sync), and let internal servers see the platform's real identity (service-identity now; on-behalf-of the moment Decision 29 lands) — all **without** the proxy ever touching the DB or the master encryption key, and with the Phase-1 `none`-server data path byte-identical.

---

## Architecture

```
 WS-A — HEALTH (registry-api owns the loop; proxy is a stateless probe)
 ┌─────────────────────────────┐   POST /internal/health {server_id}   ┌──────────────────────┐
 │      registry-api           │──Bearer <mcp-proxy-token>────────────▶│      MCP Proxy       │
 │  mcp_health.py loop         │◀──200 {ok,status,health_detail,…}─────│  /internal/health    │
 │  (lifespan asyncio task,    │                                       │  (tools/list probe,  │
 │   pg advisory-lock 1-flight)│                                       │   reuses session)    │
 │  writes mcp_servers.status/ │                                       └──────────┬───────────┘
 │  health_detail DIRECTLY     │                                                  │ streamable_http
 └─────────────┬───────────────┘                                                  ▼
               │ GET /mcp-servers/{id} (refetchInterval 15s)                Internal / External MCP
               ▼                                                              servers
        Studio detail page ── Health panel (status pill, last_success_at, failures, last_error)

 WS-B — LIST_CHANGED (proxy owns the subscription; registry-api owns the re-sync DB write)
 ┌──────────────────────┐  notifications/tools/list_changed  ┌──────────────────────┐
 │  MCP server          │───────────────────────────────────▶│      MCP Proxy       │
 └──────────────────────┘   (on long-lived session)          │ subscription_manager │
                                                              │  debounce 5s         │
                              POST /api/v1/internal/mcp/       └──────────┬───────────┘
                              list-changed {server_id}                    │ (NetworkPolicy-trusted)
                              ┌──────────────────────┐◀──────────────────┘
                              │     registry-api     │  _materialize_and_discover (SHARED with /sync)
                              │  internal_mcp.py      │──▶ /internal/discover ──▶ upsert Tool rows
                              └──────────────────────┘        (proxy connects; registry-api writes DB)

 WS-C — IDENTITY (proxy mints tokens with its OWN keycloak client secret; never the master key)
   per-server Secret.connection.identity_mode ──▶ proxy identity.resolve_headers()
     none            → static auth_headers (Phase-1 — unchanged)
     service_identity→ keycloak_client.mint_service_account_token(audience)  [SHIPPABLE]
     on_behalf_of    → mint_on_behalf_of_token(user_sub)  [STUB → OnBehalfOfNotAvailable — BLOCKED on Decision 29]
   session_cache key: (server_id, user_sub_or_none)  — user_sub participates only for on_behalf_of
   executors now send  x-user-sub  (proxy already reads it; value empty until identity-propagation lands)
```

**Invariants (all preserved, verified):** the proxy holds **no** DB connection and **no** `AGENTSHIELD_ENCRYPTION_KEY`; every Phase-2 DB write goes through registry-api (the health loop *is* registry-api; the list-changed re-sync goes through a registry-api endpoint); governance is untouched (`governed_tool` unchanged except the executor header); the per-server Secret stays in `agentshield-mcp`; the proxy's `get secrets` RBAC stays scoped to `agentshield-mcp` (the Keycloak client secret is a **file-mounted** narrow credential, not read via the API and not the master key).

---

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 async ORM, `httpx` (async — reused for the Keycloak client-credentials call, no new dep), Kubernetes Python client (existing proxy `k8s_client`). PostgreSQL advisory lock (`pg_try_advisory_lock`) via the async engine for health-loop single-flight (mirrors `services/scheduler/ha.py`).
- **MCP protocol:** the pinned `mcp>=1.2,<2.0` SDK — `ClientSession` notification handling for `list_changed` (WS-B) and `mcp.server.fastmcp.FastMCP` runtime tool mutation for the fixture. Notification-hook exact API pinned at build (T1) like Phase 1 pinned `.inputSchema`.
- **Identity:** Keycloak client-credentials grant (service-identity) via the platform's existing Keycloak (`KEYCLOAK_URL/realms/{realm}/protocol/openid-connect/token`); impersonation grant (on-behalf-of) is stubbed/blocked.
- **Frontend:** React + TS + Vite + Tailwind, TanStack Query (`refetchInterval` for health polling), Vitest + RTL, Playwright.
- **Infra:** the existing `charts/agentshield/charts/mcp-proxy` sub-chart (add a Keycloak client Secret + mount + env), `infra/network-policies/` (proxy→Keycloak egress), `scripts/deploy-cpe2e.sh` tag bumps.
- **Test harness:** bash + `kubectl exec` + inline Python/httpx (backend e2e `suite-85`), Vitest, Playwright.

---

## Constitution Check (against this worktree's `CLAUDE.md`)

| # | Principle | Status | How this plan satisfies it |
|---|---|---|---|
| 1 | Real user journey proven (Playwright, not just an endpoint) | **PASS (planned)** | Task 12 adds a case to `studio/e2e/mcp-servers.spec.ts`: register → open detail → the Health panel renders the live status pill + `last_success_at` from a real `GET` (`page.waitForResponse`); WS-B's re-sync journey is proven via suite-85 (UI has no new control). |
| 2 | Save → reload → assert survived | **PASS (planned)** | WS-B: suite-85 mutates the fixture's tool set, waits for the list_changed re-sync, then **reloads from the DB** (`GET /mcp-servers/{id}`) and asserts the new tool row persisted / the vanished one is `inactive`. WS-A: suite-85 asserts `health_detail` persisted across a fresh `GET`. Studio health panel reads a fresh `GET` (Task 4/12). |
| 3 | No orphan code | **PASS (planned)** | Every new symbol in Key Interfaces has a named caller in the same or an immediately-dependent task (`health_check_server`←loop; `/internal/health`←`health_check_server`; `ensure_subscription`←discover/health handlers; `/list-changed`←subscriber; `resolve_headers`←discover/health/tools-call; `mint_service_account_token`←`resolve_headers`). File Structure lists every file; each task lists exactly the files it touches. |
| 4 | Vertical slices, not horizontal layers | **PASS** | Order proves each slice end-to-end before the next: WS-A (proxy probe → loop → Studio, CP1) → WS-B (fixture → subscriber → endpoint, CP2) → WS-C (secret field → proxy branch → chart/executors, CP3) → tests/regression (CP4). |
| 5 | Honest gap ledger | **PASS** | Gap Ledger below tags every deferred/blocked item; the OBO exchange **STUB** and the multi-replica subscription fan-out are called out inline (Tasks 6/9) and ledgered. |
| 6 | Reason from the running product | **PASS** | research.md Part B: executors don't send `x-user-sub` (B1); registry-api already runs lifespan loops (B3); `_materialize_and_discover` is in the router, must be extracted (B4); the stub never emits list_changed (B8); `ServerConnection` lacks `identity_mode` (B7). Each moved a task's design. |
| 7 | Bug fixes reproduce first | **N/A (no bug fix in scope)** | Phase 2 adds features; no defect is being fixed. The one behavior-preserving refactor (extract `_materialize_and_discover`) is guarded by suite-84 staying green (Task 13), not a new red-first test. If the extraction breaks `/sync`, that regression is fixed red-first per rule 7. |
| 8 | Document every bug + debugging session | **N/A (no bug fixed)** | No `docs/bugs`/`docs/debugging` entry required (no defect). If implementation uncovers a Phase-1 bug, rule 8 applies then. |

**Deliberate, justified scope note (Complexity Tracking):** the `_materialize_and_discover` extraction (Task 7) and the advisory-lock single-flight (Task 3) are slightly beyond a minimal add, but each fixes the class of problem at a seam already being edited (a forked re-sync helper, or a racing multi-replica sweep, would be the exact special-casing the constitution rejects). No other deviations.

---

## File Structure

"New" = does not exist today (verified). "Modify" cites the pre-existing anchor.

### New — MCP Proxy (`services/mcp-proxy/`)

| File | C/M | Task | Responsibility |
|---|---|---|---|
| `services/mcp-proxy/subscription_manager.py` | Create | 6 | `list_changed` subscriber: per-server long-lived session + notification handler + debounce + registry-api callback + capped reconnect (WS-B). |
| `services/mcp-proxy/keycloak_client.py` | Create | 9 | `mint_service_account_token(audience) -> (token, exp)` client-credentials grant; `invalidate(audience)` (WS-C service-identity). |
| `services/mcp-proxy/identity.py` | Create | 9 | `resolve_headers(connection, *, user_sub, is_data_plane)` credential-selection branch; `mint_on_behalf_of_token` (STUB); per-audience token cache; `OnBehalfOfNotAvailable`/`OnBehalfOfIdentityRequired` (WS-C). |

### Modified — MCP Proxy

| File | Task(s) | Change |
|---|---|---|
| `services/mcp-proxy/schemas.py` | 2 | `McpHealthRequest{server_id}`, `McpHealthResponse{ok,status,health_detail,protocol_version,list_changed_supported,tool_count}`. |
| `services/mcp-proxy/main.py` | 2, 6, 9 | (2) `POST /internal/health` route (admin-plane, probe). (6) `ensure_subscription(...)` call in `/internal/discover` + `/internal/health` on `list_changed_supported`. (9) build headers via `identity.resolve_headers`; thread `x_user_sub` into the session-cache key on `/internal/tools/call`; 401-refresh for service-identity. |
| `services/mcp-proxy/config.py` | 2, 6, 9 | (2) wire the existing-but-unused `MCP_CONNECT_TIMEOUT_SECONDS` into the probe. (6) `MCP_LIST_CHANGED_*` knobs. (9) `KEYCLOAK_TOKEN_URL`, `MCP_PROXY_KEYCLOAK_CLIENT_ID`, `MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH`, `KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS`. |
| `services/mcp-proxy/mcp_client.py` | 2, 6 | (2) apply `MCP_CONNECT_TIMEOUT_SECONDS` to connect/list_tools. (6) `connect_and_initialize(url, headers, message_handler=None)` + `McpSession._connect(..., message_handler=None)` passed to `ClientSession`. |
| `services/mcp-proxy/session_cache.py` | 9 | key → `SessionKey = tuple[str, str|None]`; `_effective_user_sub(connection, user_sub)`; `peek/get_or_create/set_session/evict` gain `user_sub` param (data-model.md §3a). |
| `services/mcp-proxy/credentials.py` | 9 | `ServerConnection` +`identity_mode: str="none"`, `identity_audience: str|None=None`; parse from the Secret `connection` JSON. |

### New — registry-api

| File | C/M | Task | Responsibility |
|---|---|---|---|
| `services/registry-api/mcp_health.py` | Create | 3 | `mcp_health_loop()` lifespan task + `_sweep_once()` (advisory-lock single-flight, bounded-concurrency probe, threshold/backoff apply to `status`/`health_detail`). |
| `services/registry-api/mcp_discovery.py` | Create | 7 | `_materialize_and_discover` + `_mark_server_error` **moved** from `mcp_servers.py` (behavior-neutral) + the `/list-changed` coalesce guard state (`_last_resync`/`_resync_locks`). |

### Modified — registry-api

| File | Task(s) | Change |
|---|---|---|
| `services/registry-api/mcp_proxy_client.py` | 3 | `health_check_server(server_id) -> dict` (POST `/internal/health`, same SA-token/error mapping as `discover_server`). |
| `services/registry-api/config.py` | 3, 7 | (3) `mcp_health_check_enabled`, `mcp_health_check_interval_seconds`, `mcp_health_failure_threshold`, `mcp_health_check_concurrency`, `mcp_health_max_backoff_cycles`. (7) `mcp_list_changed_min_resync_interval_seconds`. |
| `services/registry-api/main.py` | 3 | lifespan: `mcp_health_task = asyncio.create_task(mcp_health_loop())` (guarded by `settings.mcp_health_check_enabled`); cancel at shutdown (mirrors the `cost_task` block ~L127-139). |
| `services/registry-api/routers/mcp_servers.py` | 7 | import `_materialize_and_discover`/`_mark_server_error` from `mcp_discovery` (remove the local defs); all call sites unchanged. |
| `services/registry-api/routers/internal_mcp.py` | 7 | `POST /api/v1/internal/mcp/list-changed` (`ListChangedRequest/Response`, coalesce guard, calls the shared `_materialize_and_discover`). |
| `services/registry-api/mcp_secrets.py` | 8 | `materialize_server_secret` adds `identity_mode` + `identity_audience` (from `transport_config.identity_audience`) to the `connection` JSON. |

### Modified — SDK / declarative-runner

| File | Task | Change |
|---|---|---|
| `sdk/agentshield_sdk/config.py` | 10 | `USER_SUB = os.getenv("AGENTSHIELD_USER_SUB", "")` (best-effort source for the header; empty until identity-propagation lands). |
| `sdk/agentshield_sdk/tool_executor.py` | 10 | `McpToolExecutor.as_tool_callable`: add header `"x-user-sub": config.USER_SUB` **only when non-empty** (Part B B1). |
| `sdk/agentshield_sdk/__init__.py` | 10 | `__version__` → `0.2.4`. |
| `services/declarative-runner/config.py` | 10 | `USER_SUB = os.getenv("AGENTSHIELD_USER_SUB", "")`. |
| `services/declarative-runner/node_executors.py` | 10 | `McpToolNodeExecutor.as_tool_callable`: add `"x-user-sub"` header when non-empty (separate impl, mirrors the SDK). |

### Modified / New — charts, infra, scripts, e2e, Studio, docs

| File | C/M | Task | Change / Responsibility |
|---|---|---|---|
| `charts/agentshield/charts/mcp-proxy/templates/secret.yaml` | Create | 10 | Keycloak client Secret `{release}-mcp-proxy-keycloak` (`client-secret` key from `.Values.keycloak.clientSecret`; guarded so an externally-managed secret can be referenced instead). |
| `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml` | Modify | 10 | Env for the new proxy knobs (list_changed, keycloak URL/client-id) + a `readOnly` volumeMount of the Keycloak client secret at `/var/run/secrets/mcp-proxy-keycloak`. |
| `charts/agentshield/charts/mcp-proxy/values.yaml` | Modify | 6, 9, 10 | Defaults for `listChanged.*`, `keycloak.{tokenUrl,clientId,clientSecret}`; sub-chart `image.tag` bump. |
| `charts/agentshield/values.yaml` | Modify | 3,6,9,10 | Tag mirrors (per-task, deferred) + parent `mcp-proxy.keycloak.tokenUrl` wiring to the platform Keycloak. |
| `infra/network-policies/platform-allow-ingress.yaml` | Modify | 10 | Add proxy egress allowance to the in-cluster Keycloak service (service-identity token minting). |
| `scripts/deploy-cpe2e.sh` | Modify | 2,3,4,6,7,8,9,10 | Per-task tag bumps (`MCP_PROXY_TAG`, `REGISTRY_API_TAG`, `STUDIO_TAG`, `DECLARATIVE_RUNNER_TAG`) — **deferred (written, not executed)**. |
| `scripts/e2e/fixtures/stub_mcp_server.py` | Modify | 5 | Advertise `tools.listChanged`; add a control tool `simulate_tool_change(action)` that registers/removes a runtime tool and emits `notifications/tools/list_changed` (WS-B fixture). |
| `scripts/e2e/suite-85-mcp-health-notify-identity.sh` | Create | 11 | Backend e2e (T-S85-*): health probe/loop, list_changed re-sync, service-identity header selection, OBO fail-closed + stub. |
| `scripts/e2e/run-all.sh` | Modify | 11 | Register `suite-85`. |
| `studio/src/pages/McpServerDetailPage.tsx` | Modify | 4 | Health panel (status pill, `last_success_at`, `consecutive_failures`, `last_error`, `last_synced_at`, `list_changed_supported`, `identity_mode` note) + `refetchInterval: 15000` (contracts/studio-mcp-servers-phase2.md). |
| `studio/src/pages/McpServerDetailPage.test.tsx` | Modify | 4 | Vitest cases for the Health panel states (extend, don't replace). |
| `studio/e2e/mcp-servers.spec.ts` | Modify | 12 | One added case: detail Health panel renders from a real `GET`. |
| `docs/testing/manual-ui-e2e-test-plan.md` | Modify | 13 | Gap ledger: OBO exchange STUB (blocked on Decision 29), multi-replica subscription fan-out, Keycloak-client provisioning prerequisite, no manual health-check button. |

Every file above appears in exactly one task's Files list, and every file in a task's Files list appears here.

---

## Key Interfaces

Exact signatures every task must match.

```python
# services/mcp-proxy/schemas.py — Task 2
class McpHealthRequest(BaseModel):
    server_id: UUID

class McpHealthResponse(BaseModel):
    ok: bool
    status: str                       # 'connected' | 'error' (advisory — registry-api applies threshold)
    health_detail: str | None = None  # failure reason; None on success
    protocol_version: str | None = None
    list_changed_supported: bool = False
    tool_count: int = 0
```

```python
# services/mcp-proxy/main.py — Task 2 (admin-plane, registry-api SA only, mirrors /internal/discover)
async def health_check(req: McpHealthRequest, response: Response,
                       authorization: str | None = Header(default=None),
                       x_agentshield_trace_id: str | None = Header(default=None)) -> McpHealthResponse: ...
```

```python
# services/mcp-proxy/identity.py — Task 9
class OnBehalfOfNotAvailable(Exception): ...       # Decision 29 blocked
class OnBehalfOfIdentityRequired(Exception): ...   # data-plane OBO with empty user_sub (fail-closed)

async def resolve_headers(connection: "ServerConnection", *, user_sub: str | None,
                          is_data_plane: bool) -> dict[str, str]:
    """none → connection.auth_headers (Phase-1 identical). service_identity → {'Authorization':
    'Bearer <mint_service_account_token(connection.identity_audience)>'} merged over static.
    on_behalf_of + admin plane → service-identity token. on_behalf_of + data plane + empty user_sub
    → raise OnBehalfOfIdentityRequired. on_behalf_of + data plane + user_sub → mint_on_behalf_of_token
    (STUB → raise OnBehalfOfNotAvailable). See contracts/mcp-proxy-internal-phase2.md §2."""

async def mint_on_behalf_of_token(user_sub: str, connection: "ServerConnection") -> str:
    """STUB (Phase 2): always raises OnBehalfOfNotAvailable. When Decision 29 lands, perform the
    Keycloak impersonation exchange (client credentials + impersonation grant + requested_subject=
    user_sub). No caching (mint fresh per call). ONLY this body + the impersonation client are the
    residual OBO work (research.md C7/C11)."""
```

```python
# services/mcp-proxy/keycloak_client.py — Task 9
async def mint_service_account_token(audience: str | None) -> tuple[str, float]:
    """POST client_credentials to config.KEYCLOAK_TOKEN_URL with client_id=MCP_PROXY_KEYCLOAK_CLIENT_ID
    + client_secret (read from MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH file); optional 'audience'
    param when set. Returns (access_token, exp_epoch_seconds parsed from the JWT). Raises on
    non-2xx / unreachable (caller surfaces as a 200 error/health_detail)."""
def invalidate(audience: str | None) -> None:
    """Drop the cached token for `audience` (called on an upstream 401)."""
```

```python
# services/mcp-proxy/session_cache.py — Task 9 (composite key)
SessionKey = tuple[str, str | None]
def _effective_user_sub(connection: "ServerConnection", user_sub: str | None) -> str | None:
    """user_sub if connection.identity_mode == 'on_behalf_of' else None."""
def peek(server_id: str, user_sub: str | None = None) -> CachedSession | None: ...
async def get_or_create(server_id: str, user_sub: str | None = None) -> CachedSession: ...
async def set_session(server_id: str, cached: CachedSession, user_sub: str | None = None) -> None: ...
async def evict(server_id: str, user_sub: str | None = None) -> None: ...
# NB: peek/get_or_create must first read the connection to compute _effective_user_sub; the
# existing peek(server_id) callers pass user_sub=None and are unaffected for none/service_identity.
```

```python
# services/mcp-proxy/credentials.py — Task 9
@dataclass
class ServerConnection:
    server_url: str
    transport: str
    transport_config: dict = field(default_factory=dict)
    is_external: bool = False
    owner_team: str | None = None
    auth_headers: dict[str, str] = field(default_factory=dict)
    identity_mode: str = "none"            # NEW
    identity_audience: str | None = None   # NEW
```

```python
# services/mcp-proxy/mcp_client.py — Task 6
async def connect_and_initialize(server_url: str, headers: dict[str, str] | None = None,
                                 message_handler=None) -> "McpSession":
    """message_handler (optional) is passed to ClientSession so notifications/tools/list_changed
    is dispatched to it while the session stays open. Exact SDK hook pinned by T1."""
```

```python
# services/mcp-proxy/subscription_manager.py — Task 6
async def ensure_subscription(server_id: str) -> None:
    """Idempotent. If MCP_LIST_CHANGED_ENABLED and no live subscriber for server_id, spawn a task
    that holds a session with a list_changed handler that debounces (MCP_LIST_CHANGED_DEBOUNCE_SECONDS)
    then POSTs {REGISTRY_API_URL}/api/v1/internal/mcp/list-changed {server_id}. Capped reconnect."""
async def stop_subscription(server_id: str) -> None: ...
```

```python
# services/registry-api/mcp_proxy_client.py — Task 3
async def health_check_server(server_id) -> dict:
    """POST {settings.mcp_proxy_url}/internal/health {'server_id': str} with the mcp-proxy SA token.
    Returns the McpHealthResponse dict; RuntimeError only on transport/non-200 (loop treats as ok=false)."""
```

```python
# services/registry-api/mcp_health.py — Task 3
import zlib
_SWEEP_LOCK_KEY = zlib.crc32(b"mcp-health-sweep") & 0x7FFFFFFF
_backoff_skip: dict[str, int] = {}     # server_id -> remaining cycles to skip (in-memory)

async def mcp_health_loop() -> None:
    """while True: try: await _sweep_once(); except CancelledError: raise; except Exception: log;
    await asyncio.sleep(settings.mcp_health_check_interval_seconds).  Mirrors cost_backfill_loop."""
async def _sweep_once() -> int:
    """Acquire pg_try_advisory_lock(_SWEEP_LOCK_KEY) on a dedicated connection; if not won → return 0
    (another replica owns this cycle). Else: SELECT all MCPServer; skip a server whose _backoff_skip>0
    (decrement); probe the rest with a bounded semaphore (mcp_health_check_concurrency); apply
    threshold/backoff to status+health_detail; commit; pg_advisory_unlock in finally. Returns #updated."""
async def _probe_and_apply(session: AsyncSession, server: MCPServer) -> None:
    """resp = await health_check_server(server.id) (RuntimeError → ok=false). Apply data-model.md
    §2a rules: success→connected/reset; failure→+1, flip to error at >= mcp_health_failure_threshold;
    on sustained failure set _backoff_skip[id]=min(consecutive_failures, mcp_health_max_backoff_cycles)."""
```

```python
# services/registry-api/mcp_discovery.py — Task 7 (moved verbatim + guard state)
async def _materialize_and_discover(db: AsyncSession, server: MCPServer, *,
                                    acknowledge_schema_drift: bool) -> dict: ...   # unchanged behavior
def _mark_server_error(server: MCPServer, reason: str) -> None: ...                # unchanged behavior
_last_resync: dict[str, float] = {}
_resync_locks: dict[str, "asyncio.Lock"] = {}
```

```python
# services/registry-api/routers/internal_mcp.py — Task 7
class ListChangedRequest(BaseModel):
    server_id: uuid.UUID
class ListChangedResponse(BaseModel):
    ok: bool
    coalesced: bool = False
    tools_added: int = 0
    tools_updated: int = 0
    tools_inactivated: int = 0
    reason: str | None = None

@router.post("/list-changed", response_model=ListChangedResponse)  # /api/v1/internal/mcp/list-changed
async def list_changed(body: ListChangedRequest, db: AsyncSession = Depends(_get_db)) -> ListChangedResponse: ...
```

---

## Tasks

Baseline tags observed **this session** (verify + bump from current per quickstart.md — never reuse a claimed tag): `MCP_PROXY_TAG=0.1.0`, `REGISTRY_API_TAG=0.2.226`, `STUDIO_TAG=0.1.161`, `DECLARATIVE_RUNNER_TAG=0.1.60`, `sdk.__version__=0.2.3`. Alembic head `0072` (**no new migration**). e2e suite ceiling `84` → new **`suite-85`**. **All `deploy-cpe2e.sh` / `helm` / `kubectl` build+deploy commands below are DEFERRED — not executed this run.**

**Tag-bump convention (every image-building task — 2,3,4,6,7,8,9,10):** bump the service's tag in **both** `scripts/deploy-cpe2e.sh` and its home in `charts/agentshield/values.yaml` (for `mcp-proxy`, also the sub-chart `values.yaml`), same change, deferred. Where two proxy-touching tasks are in one CP, a single `MCP_PROXY_TAG` bump at that CP's deploy suffices (don't churn the tag mid-CP).

### Task 1 — Setup & baseline verification `[P]`
**Files:** none (verification only; findings feed Tasks 2/5/6/9).
**Interface contract:** confirm alembic head still `0072` (→ **no migration**); e2e ceiling still `84` (→ `suite-85`); tags per baseline above. Pin, against the **installed** `mcp` version in the proxy image: (a) how `ClientSession` dispatches a server notification to a handler (constructor `message_handler=` kwarg vs. iterating `session.incoming_messages`) and the type/name of the `list_changed` notification; (b) how `FastMCP` mutates its tool set at runtime and emits `notifications/tools/list_changed` (the `simulate_tool_change` mechanism for Task 5); (c) that `capabilities.tools.listChanged` can be advertised by `FastMCP`. Confirm the platform Keycloak token endpoint URL + realm for the proxy's client-credentials call.
**Dependencies:** none.
**Acceptance:** a short written note (in the PR description or a scratch file) recording the exact SDK hook names + the Keycloak token URL; every downstream number confirmed unchanged or adjusted.
**Test cases:** n/a (gate).
**Verification:** `pip show mcp` / read the installed `mcp` package's `ClientSession` + `FastMCP` sources; `grep -n KEYCLOAK services/registry-api/config.py services/registry-api/auth_middleware.py`.

### Task 2 — WS-A: proxy `/internal/health` probe endpoint
**Files:** `services/mcp-proxy/schemas.py`, `services/mcp-proxy/main.py`, `services/mcp-proxy/config.py`, `services/mcp-proxy/mcp_client.py`.
**Interface contract:** `McpHealthRequest`/`McpHealthResponse` (Key Interfaces); `health_check(...)` route per `contracts/mcp-proxy-internal-phase2.md §1` (admin-plane: authenticate → require `caller_sa_subject == REGISTRY_API_SA_SUBJECT` else 403 → read Secret → `identity.resolve_headers(..., is_data_plane=False)` [in Task 9; Task 2 uses `connection.auth_headers` directly and Task 9 rewires] → `session_cache.get_or_create` → `list_tools()` probe → 200 `McpHealthResponse`; any failure → evict + `200 ok=false` with reason). Wire the existing `MCP_CONNECT_TIMEOUT_SECONDS` into the probe's connect/list_tools.
**Dependencies:** none (Task 9 later rewires the header source; Task 2 ships against `connection.auth_headers`).
**Acceptance:** `GET /health` unaffected; `POST /internal/health` with registry-api SA token against the Task-5 fixture → `200 ok=true status=connected list_changed_supported=<bool> tool_count>=2`; against an unreachable server → `200 ok=false status=error health_detail` populated (never 5xx); missing token → `401`; agent-SA token → `403`. Never triggers a `Tool`-row write.
**Test cases:** `T-S85-001` (health happy path), `T-S85-002` (health unreachable → 200 ok=false), `T-S85-003` (health wrong-subject → 403).
**Verification (DEFERRED):** bump `MCP_PROXY_TAG`; `bash scripts/deploy-cpe2e.sh`; `curl` the three cases from inside the proxy pod against the fixture.

### Task 3 — WS-A: registry-api health loop + client + config + lifespan
**Files:** `services/registry-api/mcp_proxy_client.py`, `services/registry-api/mcp_health.py`, `services/registry-api/config.py`, `services/registry-api/main.py`.
**Interface contract:** `health_check_server(server_id)` (Key Interfaces / contract). `mcp_health.py` per Key Interfaces — `mcp_health_loop` mirrors `cost_backfill_loop`; `_sweep_once` single-flights via `pg_try_advisory_lock(_SWEEP_LOCK_KEY)` on a dedicated engine connection, enumerates all `MCPServer`, bounded-concurrency probes (`mcp_health_check_concurrency`), applies data-model.md §2a threshold/backoff to `status`+`health_detail`, `db.commit()`. `config.py` adds the 5 `mcp_health_*` settings. `main.py` starts/cancels `mcp_health_task` in `lifespan` (guarded by `settings.mcp_health_check_enabled`), mirroring the `cost_task` block.
**Dependencies:** Task 2 (the probe endpoint).
**Acceptance:** with the sweep running, a server whose upstream is down for `>= mcp_health_failure_threshold` cycles flips `status='error'` with `health_detail.consecutive_failures>=3` + `last_error`; a recovered server flips back to `connected` with `consecutive_failures=0` on the first good probe; `last_synced_at` is **not** modified by the loop; two registry-api replicas never both increment `consecutive_failures` (advisory lock). Loop never crashes registry-api (exceptions logged, `CancelledError` re-raised).
**Test cases:** `T-S85-004` (down server → status error after threshold), `T-S85-005` (recovery → connected, failures reset), `T-S85-006` (`last_synced_at` unchanged across a health cycle), `T-S85-007` (single-flight: advisory lock held → a second manual `_sweep_once` returns 0).
**Verification (DEFERRED):** bump `REGISTRY_API_TAG`; redeploy; register a server pointing at a dead URL, watch `mcp_servers.status`/`health_detail` over 3–4 intervals via SQL; `kubectl scale` registry-api to 2 replicas and confirm no double-count.

### Task 4 — WS-A: Studio detail-page Health panel + Vitest
**Files:** `studio/src/pages/McpServerDetailPage.tsx`, `studio/src/pages/McpServerDetailPage.test.tsx`.
**Interface contract:** `contracts/studio-mcp-servers-phase2.md §1/§2` — add the Health section (status pill reusing/duplicating `StatusBadge`, `last_success_at`, `consecutive_failures`, `last_error`, `last_synced_at`, `list_changed_supported`, `identity_mode` note) reading the already-fetched `server`; set `refetchInterval: 15000` on the `getMcpServer` query. No API-client change.
**Dependencies:** Task 3 (so the panel shows loop-driven data) — but the component change is testable against mocked payloads independently.
**Acceptance:** **save→reload→assert** unbroken (existing register→detail journey still passes); the Health panel renders every field from a mocked `getMcpServer`; a `status:"error"` payload shows the red pill + failure count + `last_error`; a `list_changed_supported:true` payload shows "subscribed"; an `on_behalf_of` payload shows the "(pending — Decision 29)" note.
**Test cases (Vitest):** the four render assertions in `contracts/studio-mcp-servers-phase2.md §2` (extend the existing file; keep its cases green).
**Verification:** `cd studio && npm run test -- McpServerDetailPage && npm run typecheck`. (DEFERRED) bump `STUDIO_TAG`.

### Task 5 — WS-B: extend the stub fixture for `list_changed` simulation `[P]`
**Files:** `scripts/e2e/fixtures/stub_mcp_server.py`.
**Interface contract:** keep `echo`/`add`; ensure `FastMCP` advertises `tools.listChanged` (per Task 1's pinned mechanism); add a control tool `simulate_tool_change(action: str = "add") -> str` that, when called, registers a runtime tool `dynamic_echo` (on `action="add"`) or removes it (on `action="remove"`) and emits `notifications/tools/list_changed` to connected sessions. Inert on import (unchanged `__main__` guard); still started only via `kubectl exec` inside the proxy pod. If Task 1 finds the installed SDK cannot emit a runtime notification, fall back to a documented "restart the fixture with a different `--toolset` flag" mechanism and record it.
**Dependencies:** Task 1 (SDK mechanism pinned).
**Acceptance:** running the fixture, connecting, and calling `simulate_tool_change("add")` causes a `notifications/tools/list_changed` to be received by a subscribed client, and a subsequent `tools/list` includes `dynamic_echo`; `simulate_tool_change("remove")` drops it.
**Test cases:** exercised via `T-S85-010/011` (Task 11) — the fixture is the driver, not independently asserted.
**Verification:** local `python3 scripts/e2e/fixtures/stub_mcp_server.py --port 9999` + a throwaway `mcp` client that subscribes and calls `simulate_tool_change` (quickstart.md).

### Task 6 — WS-B: proxy subscription manager + `mcp_client` handler + config
**Files:** `services/mcp-proxy/subscription_manager.py`, `services/mcp-proxy/mcp_client.py`, `services/mcp-proxy/config.py`, `services/mcp-proxy/main.py`.
**Interface contract:** `contracts/mcp-proxy-internal-phase2.md §3` + Key Interfaces. `mcp_client.connect_and_initialize(url, headers, message_handler=None)` + `McpSession._connect(..., message_handler)`. `subscription_manager.ensure_subscription`/`stop_subscription` + `SubscriptionState` (data-model.md §3d). `config.py` adds the `MCP_LIST_CHANGED_*` knobs. `main.py` calls `ensure_subscription(str(server_id))` after a successful `/internal/discover` and `/internal/health` when `list_changed_supported`.
**Dependencies:** Task 2 (health calls ensure_subscription), Task 7 (the `/list-changed` endpoint it POSTs to — can be built in parallel; wire the URL, and Task 7 makes it live), Task 1 (handler hook).
**Acceptance:** subscribing to the Task-5 fixture, a `simulate_tool_change` on the upstream causes exactly one (post-debounce) `POST /api/v1/internal/mcp/list-changed {server_id}` from the proxy; a burst of 3 notifications within 5s coalesces to one POST; a dropped session reconnects up to the cap then tears down; `MCP_LIST_CHANGED_ENABLED=false` disables all subscribing.
**Test cases:** `T-S85-010` (list_changed → one re-sync POST), `T-S85-012` (debounce coalesces a burst), `T-S85-013` (reconnect cap tears down after Secret deletion).
**Verification (DEFERRED):** bump `MCP_PROXY_TAG` (shared with Task 9's proxy bump if same CP); deploy; drive the fixture; watch proxy logs for the debounced callback.

### Task 7 — WS-B: registry-api `mcp_discovery` extraction + `/list-changed` endpoint + coalesce guard
**Files:** `services/registry-api/mcp_discovery.py`, `services/registry-api/routers/mcp_servers.py`, `services/registry-api/routers/internal_mcp.py`, `services/registry-api/config.py`.
**Interface contract:** move `_materialize_and_discover` + `_mark_server_error` verbatim into `mcp_discovery.py` (+ `_last_resync`/`_resync_locks`); `mcp_servers.py` imports them (no behavior change). Add `POST /api/v1/internal/mcp/list-changed` per `contracts/registry-api-internal-mcp-phase2.md` (resolve server → coalesce guard `mcp_list_changed_min_resync_interval_seconds` under a per-server lock → `_materialize_and_discover` → commit → counters; unknown server → `200 ok=false reason=server_not_found`). `config.py` adds `mcp_list_changed_min_resync_interval_seconds`.
**Dependencies:** none for the extraction (behavior-neutral); the endpoint needs the extracted core.
**Acceptance:** `suite-84` stays green (extraction is behavior-neutral — register/`/sync`/`PUT` unchanged); `POST /internal/mcp/list-changed` on a server whose fixture just added a tool → `200 tools_added>=1` and the new `Tool` row exists on a fresh `GET /mcp-servers/{id}`; a second call within `min_resync_interval` → `200 coalesced=true tools_added=0`; unknown `server_id` → `200 ok=false reason=server_not_found`; malformed body → `422`.
**Test cases:** `T-S85-011` (list-changed re-sync adds a tool, persists — **save→reload→assert**), `T-S85-014` (vanished tool → `inactive` on re-sync), `T-S85-015` (coalesce within interval), `T-S85-016` (unknown server → ok=false), `T-S85-017` (extraction neutral — suite-84 green).
**Verification (DEFERRED):** bump `REGISTRY_API_TAG` (shared with Task 8); redeploy; drive via the fixture + `curl` the endpoint directly.

### Task 8 — WS-C: registry-api per-server Secret carries `identity_mode`
**Files:** `services/registry-api/mcp_secrets.py`.
**Interface contract:** `materialize_server_secret` adds `identity_mode` (from `server.identity_mode`) and `identity_audience` (from `server.transport_config.get("identity_audience")` if `transport_config` is a dict, else `None`) to the `connection` JSON (data-model.md §2c). No other change; `auth_headers` composition unchanged.
**Dependencies:** none (independent of the proxy side; the proxy defaults a missing key to `"none"`).
**Acceptance:** after register/`/sync`/`PUT`, the per-server Secret's `connection` JSON contains `identity_mode` matching the row and `identity_audience` (or `null`); a Phase-1-shaped Secret (no keys) still parses on the proxy as `identity_mode="none"` (Task 9 default).
**Test cases:** `T-S85-020` (materialize → read Secret → `connection.identity_mode == server.identity_mode`).
**Verification (DEFERRED):** bump `REGISTRY_API_TAG` (shared with Task 7); redeploy; register a `service_identity` server, `kubectl get secret agentshield-mcp-server-{id} -n agentshield-mcp -o jsonpath` and decode `connection`.

### Task 9 — WS-C: proxy identity branch (service-identity mint + OBO stub) + session key + wiring
**Files:** `services/mcp-proxy/credentials.py`, `services/mcp-proxy/keycloak_client.py`, `services/mcp-proxy/identity.py`, `services/mcp-proxy/session_cache.py`, `services/mcp-proxy/config.py`, `services/mcp-proxy/main.py`.
**Interface contract:** Key Interfaces + `contracts/mcp-proxy-internal-phase2.md §2`. `ServerConnection` +`identity_mode`/`identity_audience` (parsed). `keycloak_client.mint_service_account_token`/`invalidate`. `identity.resolve_headers` (selection matrix) + `mint_on_behalf_of_token` STUB + per-audience token cache + `OnBehalfOfNotAvailable`/`OnBehalfOfIdentityRequired`. `session_cache` composite key + `_effective_user_sub` + `user_sub` params. `config.py` adds the 4 Keycloak knobs. `main.py`: `/internal/discover`, `/internal/health`, `/internal/tools/call` build upstream headers via `resolve_headers` (not `connection.auth_headers` directly); `/internal/tools/call` threads `x_user_sub` into the session-cache key + catches `OnBehalfOf*` → `200 is_error=true`; add the 401-refresh (`invalidate` + retry) for service-identity.
**Dependencies:** Task 8 (Secret carries `identity_mode`), Task 2 (health uses `resolve_headers` admin-plane), Task 6 (shares the proxy image/tag).
**Acceptance:** an `identity_mode='none'` server behaves byte-identically to Phase 1 (suite-84 green); a `service_identity` server's `tools/call`/`discover`/`health` carry a Keycloak-minted `Authorization: Bearer` (token reused within its lifetime, re-minted after `exp−skew`); an `on_behalf_of` server's `tools/call` with empty `x-user-sub` → `200 is_error=true` "requires a user identity" (fail-closed, **no** silent service-identity fallback); with a non-empty `x-user-sub` → `200 is_error=true` "not yet available (blocked on Decision 29)"; an `on_behalf_of` server's `discover`/`health` (admin plane) succeed using the service-identity token; session-cache key composite (unit-tested: OBO key includes user_sub, none/service_identity key does not).
**Test cases:** `T-S85-021` (none → unchanged), `T-S85-022` (service_identity → minted bearer sent; token cached), `T-S85-023` (OBO empty user_sub → fail-closed is_error), `T-S85-024` (OBO with user_sub → stub is_error blocked-on-29), `T-S85-025` (OBO discover admin-plane uses service token), `T-S85-026` (composite key: none=`(id,None)`, OBO=`(id,user)`).
**Verification (DEFERRED):** bump `MCP_PROXY_TAG`; deploy; requires a Keycloak confidential client (see Task 10 chart + the provisioning prerequisite).

### Task 10 — WS-C: proxy Keycloak-client chart wiring + egress + executor `x-user-sub` emission
**Files:** `charts/agentshield/charts/mcp-proxy/templates/secret.yaml`, `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml`, `charts/agentshield/charts/mcp-proxy/values.yaml`, `charts/agentshield/values.yaml`, `infra/network-policies/platform-allow-ingress.yaml`, `sdk/agentshield_sdk/config.py`, `sdk/agentshield_sdk/tool_executor.py`, `sdk/agentshield_sdk/__init__.py`, `services/declarative-runner/config.py`, `services/declarative-runner/node_executors.py`.
**Interface contract:** Chart — create the Keycloak client Secret `{release}-mcp-proxy-keycloak` (key `client-secret` from `.Values.keycloak.clientSecret`; skip the template when `.Values.keycloak.existingSecret` is set so an externally-managed secret can be referenced), mount it read-only at `/var/run/secrets/mcp-proxy-keycloak`, and set env `KEYCLOAK_TOKEN_URL`/`MCP_PROXY_KEYCLOAK_CLIENT_ID`/`MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH` + the `MCP_LIST_CHANGED_*` knobs from values. NetworkPolicy — allow proxy egress to the in-cluster Keycloak service. Executors — SDK `config.USER_SUB` + runner `config.USER_SUB` from `AGENTSHIELD_USER_SUB`; both `McpToolExecutor`/`McpToolNodeExecutor` add `"x-user-sub": USER_SUB` to the proxy request headers **only when non-empty** (the proxy already reads it). `sdk.__version__ → 0.2.4`.
**Dependencies:** Task 9 (the proxy consumes the Keycloak secret + the `x-user-sub` header).
**Acceptance:** the proxy pod mounts the Keycloak client secret file (RBAC `get secrets -n agentshield-mcp` still scoped — the client secret is a volume, not an API read; `can-i get secrets -n agentshield-platform` still **no**); the proxy can reach Keycloak (NetworkPolicy); a non-empty `AGENTSHIELD_USER_SUB` on an agent pod makes both executors send `x-user-sub`; an empty one sends no such header (Phase-1 identical). The Keycloak confidential client must exist (deploy prerequisite — ledgered in `docs/testing/...`).
**Test cases:** `T-S85-027` (executor sends x-user-sub iff USER_SUB set — asserted against a request-capturing stub), `T-S85-028` (proxy RBAC unchanged — `can-i` matrix), covered alongside Task 9's identity cases.
**Verification (DEFERRED):** bump `MCP_PROXY_TAG` (mirror), `DECLARATIVE_RUNNER_TAG` (rebuilds sdk 0.2.4); `bash scripts/deploy-cpe2e.sh`; `kubectl auth can-i` matrix + `kubectl exec` a resolve+invoke with `AGENTSHIELD_USER_SUB` set.

### Task 11 — Backend e2e: `suite-85` + register
**Files:** `scripts/e2e/suite-85-mcp-health-notify-identity.sh`, `scripts/e2e/run-all.sh`.
**Interface contract:** compile every `T-S85-0XX` above into one suite (mirror `suite-84`'s template: `kubectl exec` into registry-api, inline `python3`+`httpx`/ORM, `RESULT <id> PASS/FAIL`, trailing `FAILS`, exit-code keyed), driven against one instance of the Task-5 fixture started inside the proxy pod. Register in `run-all.sh` (re-confirm `85` free; else next number + rename IDs).
**Dependencies:** Tasks 2,3,5,6,7,8,9,10.
**Acceptance:** every `T-S85-001..028` is a real executable assertion; the suite runs green against a deployed Phase-2 stack (DEFERRED to run).
**Test cases:** `T-S85-001..028`.
**Verification (DEFERRED):** `bash scripts/e2e/suite-85-mcp-health-notify-identity.sh`; then `bash scripts/e2e/run-all.sh`.

### Task 12 — Studio Playwright: Health panel journey
**Files:** `studio/e2e/mcp-servers.spec.ts`.
**Interface contract:** add one case to the existing describe (do not rewrite): register a server → open the detail page → assert the Health section renders the status pill + "Last successful check" row from a real `GET /api/v1/mcp-servers/{id}` (`page.waitForResponse`), infra-gated (an `error` status is a valid, assertable state if the stub isn't reachable).
**Dependencies:** Task 4.
**Acceptance:** the case fails if Task 4's Health panel wiring breaks; the existing register/detail/bind cases stay green.
**Test cases:** the one added Playwright case.
**Verification:** `bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts` (DEFERRED — against deployed Studio).

### Task 13 — Gap ledger + regression sweep
**Files:** `docs/testing/manual-ui-e2e-test-plan.md`.
**Blast radius (mandatory mapping):** WS-C's `resolve_headers` seam sits in the `/internal/tools/call` path (every MCP tool call) and the `_materialize_and_discover` extraction sits in `/sync` — both must be behavior-neutral for Phase-1 servers. Impacted suites: `suite-84-mcp-tools.sh` (whole Phase-1 MCP path — register/discover/bind/governed call/lifecycle), `suite-18-opa-governance.sh` / `suite-4-hitl.sh` / `suite-3-safety.sh` (confirm the extra `x-user-sub` header + the header-source refactor don't perturb governance), Studio Vitest + `mcp-servers.spec.ts`.
**Dependencies:** Tasks 2–12.
**Acceptance:** the gap ledger records: OBO exchange **STUB** (blocked on Decision 29 — must not be reported done as "working"), multi-replica subscription fan-out (harmless-duplicate re-syncs, exactly-once deferred), Keycloak confidential-client provisioning prerequisite, `last_synced_at`-vs-`last_success_at` deviation, no manual health-check button, `oauth2`/`mtls` service-identity not covered. All blast-radius suites pass after Tasks land.
**Test cases:** the five suites' existing IDs (unchanged bar) + `suite-85`.
**Verification (DEFERRED):** run `suite-84`, `suite-18`, `suite-4`, `suite-3`, `suite-85`, `cd studio && npm run test`, `bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts`.

---

## Complexity Tracking

| Item | Why it's here (not a shortcut) |
|---|---|
| Health loop in registry-api + advisory-lock single-flight (Tasks 3 / research.md C1) | The sweep must enumerate all servers (DB) and probe each (proxy). Only registry-api has the DB; hosting the loop there reuses the existing lifespan-loop pattern and writes the DB it owns (no writeback endpoint). The advisory lock is the minimal correct guard for the `consecutive_failures` read-modify-write across replicas — dropping it would double-count and mis-flip status. Rejected alternatives (proxy-owned loop, scheduler-owned loop) each fork enumeration or the upsert. |
| `_materialize_and_discover` extraction to `mcp_discovery.py` (Task 7 / research.md B4) | WS-B's re-sync must be identical to `/sync`. Extracting the one function (behavior-neutral, suite-84-guarded) is the only way to get "one implementation, three callers"; a forked re-sync in `internal_mcp.py` is the exact duplication the constitution rejects. |
| On-behalf-of **STUB** (Task 9 / research.md C7/C11) | The exchange is provably impossible until Decision 29 gives a durable verified subject + an impersonation client (§7a). Shipping the plumbing (session key, header threading, fail-closed branch, interface seam) now makes OBO a single-function fill-in later; stubbing the exchange with a clear `OnBehalfOfNotAvailable` is honest and ledgered ("must not be reported done"). |
| Keycloak client secret as a **file-mounted** narrow credential (Task 10 / research.md C6) | Service-identity needs a Keycloak client secret, but giving the proxy the master `AGENTSHIELD_ENCRYPTION_KEY` (or DB) would widen blast radius exactly where §3b least-privilege matters most. A file-mounted client secret keeps the proxy's `get secrets` RBAC scoped to `agentshield-mcp` and off the master key. |
| Multi-replica subscription fan-out tolerated, not eliminated (Task 6 / research.md C5) | Exactly-once subscription per server would need a distributed lease + failover — disproportionate at Phase-2 scale for a duplicate that the idempotent upsert + min-interval already make harmless. Reduces the §8-ledgered gap rather than closing it; the residual is ledgered. |

No other deviations. No runtime `if getattr(...)`-style type-sniffing is added to the governance path — the identity branch is an explicit `identity_mode` switch with an explicit `is_data_plane` context (no priority fallthrough).

---

## Execution Notes

- **Deploy/build is DEFERRED this run.** Every `bash scripts/deploy-cpe2e.sh`, `helm`, and `kubectl rollout` line is recorded for a later implementer, not executed while producing these artifacts. Verify-then-bump each tag from the live value (quickstart.md) — never reuse a claimed tag; mirror each bump in **both** `scripts/deploy-cpe2e.sh` and the tag's home in `charts/agentshield/values.yaml` (for `mcp-proxy`, also the sub-chart values).
- **No migration.** Head stays `0072`; do **not** create a `0073` (data-model.md §1). If a `0073` appears on the branch by build time, that is someone else's migration — Phase 2 adds none.
- **Suite number:** `suite-85` (84 is Phase-1's). If claimed by build time, take the next free number and rename the `T-S85-*` IDs.
- **The proxy never holds the DB, `AGENTSHIELD_ENCRYPTION_KEY`, or a master-scoped secret read** — if a task finds itself adding `sqlalchemy`/`asyncpg` to `services/mcp-proxy`, or a `get secrets` RBAC beyond `agentshield-mcp`, stop: that violates the Phase-1 invariant. The Keycloak client secret is a file mount, not an API read.
- **Phase-1 `none`-server path must stay byte-identical** — the `resolve_headers` refactor returns exactly `connection.auth_headers` for `identity_mode='none'`, and the `_materialize_and_discover` move is a pure relocation. suite-84 is the guard (Task 13).
- **SDK version sequencing:** only Task 10 bumps `sdk.__version__` (0.2.3 → 0.2.4); the runner re-bump (`DECLARATIVE_RUNNER_TAG`) rebuilds against it.
- **Keycloak confidential client is a deploy prerequisite** (a client with a client-credentials/service-account grant, and — for the future OBO fill-in — an impersonation grant) — provisioned like every other platform Keycloak client, ledgered in the gap list; the chart only wires the client *secret* into the proxy.

---

## Gap Ledger

Per CLAUDE.md DoD #5 and the design doc §8. Adds **Phase-2 implementation gaps** on top of the architecture doc's §8 ledger (which already covers `tools/list` pagination, latency budget, inner Langfuse span, rate limiting, stdio/OAuth/resources/prompts) — not repeated here.

| Gap | Tag | Note |
|---|---|---|
| On-behalf-of upstream-identity **exchange** (FR-MCP-21 OBO half) | **not-yet-wired (debt), blocked externally** — STUB | `identity.mint_on_behalf_of_token` raises `OnBehalfOfNotAvailable`; a `tools/call` to an OBO server returns a structured "blocked on Decision 29" error. Plumbing (session key, `x-user-sub` threading, fail-closed branch) is shipped. Residual = fill the stub + provision the impersonation Keycloak client + ensure a real `user_sub` reaches the executors, once `identity-propagation-architecture.md` Phase 0-2 lands (research.md C7/C11). **Must not be reported done as "on-behalf-of works."** |
| `x-user-sub` is empty in practice for `sdk`-type agents | inherited (not introduced here) | Same root cause as `sdk-agent-gaps.md` Gap 1 — `RunContext.user_sub` doesn't reach `governed_tool`. The executor emits the header when set; it is set only once identity-propagation lands. |
| `list_changed` subscription **exactly-once across N proxy replicas** | deferred (intentional) | Each replica with a subscribed session may fire a re-sync; the registry-api min-interval guard + idempotent upsert make duplicates harmless (redundant discovery at worst). Reduces — does not close — the architecture §8 "fan-out across N replicas" gap. A distributed per-server subscription lease is deferred. |
| Keycloak confidential-client **provisioning** (service-identity + future impersonation) | not-yet-wired (debt), deploy prerequisite | The chart wires the client *secret* into the proxy; the Keycloak client itself (client-credentials grant now; impersonation grant later for OBO) must be created in Keycloak like every other platform client. |
| `oauth2`/`mtls` `AuthConfig` under service-identity | not-yet-wired (debt) | Service-identity replaces the `Authorization` bearer with a minted token; a server whose `auth_config` is `mtls` (a client cert, not a header) isn't served by this path. Phase-2 service-identity targets bearer-audience servers; richer auth is Phase 4. |
| Health-loop writes `health_detail.last_success_at`, **not** `last_synced_at` | resolved (deliberate deviation) | research.md C3 — `last_synced_at` means "last discovery"; a health probe does no discovery. Studio surfaces both distinctly (contracts/studio §1). Recorded so it doesn't re-surface as "the loop doesn't update last_synced_at." |
| No manual "health-check now" button in Studio | deferred (intentional) | The loop is periodic (15s Studio poll surfaces it); a manual re-probe would need a new endpoint. Out of scope. |
| Health backoff state is in-memory (lost on registry-api restart) | not-yet-wired (debt), low-impact | `_backoff_skip` resets on restart → at worst one extra probe of a hard-down server after a restart. Persisting it isn't worth a column. |
| Proxy session-cache still has no TTL | deferred (intentional) | Phase-2 adds eviction on a service-identity `401` and on subscription teardown, but no time-based expiry (matches design §3). |

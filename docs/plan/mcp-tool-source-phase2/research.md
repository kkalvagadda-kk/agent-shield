# Research — MCP as a Tool Source, Phase 2

**Scope:** Phase 2 only — health-check + status surfacing (FR-MCP-22), `notifications/tools/list_changed` subscription (FR-MCP-07), and internal-server identity (FR-MCP-21: service-identity shippable; on-behalf-of blocked on Decision 29). Built **on top of the shipped Phase-1 code** (`services/mcp-proxy/`, `services/registry-api/routers/mcp_servers.py` + `internal_mcp.py` + `mcp_proxy_client.py` + `mcp_secrets.py`, the two dispatch executors, the `mcp-proxy` sub-chart). Every decision below was verified against that code on 2026-07-25, **not** against the design doc's intent — where the two disagree, the code wins and the disagreement is recorded (Part B "Grounding corrections").

**Companion artifacts (same dir):** `plan.md`, `tasks.md`, `data-model.md`, `contracts/mcp-proxy-internal-phase2.md`, `contracts/registry-api-internal-mcp-phase2.md`, `contracts/studio-mcp-servers-phase2.md`, `quickstart.md`.

---

## Part A — FR-number reconciliation (the brief's labels vs. the requirements doc)

The task brief tagged the workstreams `WS-A (FR-MCP-20)`, `WS-C service-identity (FR-MCP-22)`. Reading the authoritative requirements doc (`docs/design/todo/mcp-tools-for-agents-requirements.md` §6/§11) those numbers are scrambled. This plan maps to the **requirements doc's** numbering, which is what the e2e assertions cite:

| Workstream | This plan's FR (from requirements §6/§11) | What the brief said | Note |
|---|---|---|---|
| WS-A — periodic health-check + status surfacing | **FR-MCP-22** | "FR-MCP-20" | FR-MCP-20 in the requirements is *external static-credential injection* — a **Phase-1** item, already shipped. Health is FR-MCP-22. |
| WS-B — `list_changed` subscription | **FR-MCP-07** | (implicit) | Matches. |
| WS-C — service-identity **and** on-behalf-of | **FR-MCP-21** | "service-identity (FR-MCP-22) + on-behalf-of (FR-MCP-21)" | The requirements put **both** identity modes under FR-MCP-21; FR-MCP-22 is health. Service-identity is *not* FR-MCP-22. |

`§11` confirms: "Phase 2 — Health, change notifications, internal identity … (FR-MCP-07, 21, 22)." This plan builds exactly those three.

---

## Part B — Grounding corrections (design doc / brief said X; the Phase-1 code says Y)

1. **The wire contract does NOT already carry `x-user-sub` end-to-end.** The architecture doc §3c and the brief say the contract "already carries `x-user-sub`, so it is forward-compatible." Half true. The **proxy** endpoint `POST /internal/tools/call` *accepts* `x_user_sub: str | None = Header(default=None)` (`services/mcp-proxy/main.py`), but **neither dispatch client sends it** — `sdk/agentshield_sdk/tool_executor.py::McpToolExecutor.as_tool_callable` and `services/declarative-runner/node_executors.py::McpToolNodeExecutor.as_tool_callable` build `headers = {"Authorization": f"Bearer {token}"} if token else {}` and **nothing else** (verified; the body carries `session_id`/`agent_name` only). So WS-C's on-behalf-of plumbing must *add the header emission to both executors* — the proxy side is ready, the client side is not. This is called out again in the WS-C decisions below.

2. **`X-AgentShield-Trace-ID` is also not sent by the executors** (only echoed by the proxy if received). Not load-bearing for Phase 2, noted so no task assumes it exists client-side.

3. **registry-api already runs `while True` background loops in its FastAPI `lifespan`.** `services/registry-api/main.py` L127-135 does `cost_task = asyncio.create_task(cost_backfill_loop())` at startup and `cost_task.cancel()` at shutdown; `cost_backfill.py::cost_backfill_loop` is a `while True: try: … except CancelledError: raise except Exception: log; await asyncio.sleep(INTERVAL)` sweep (`approval_timeout_worker.py` is a second instance of the same shape). **This is the established registry-api pattern the WS-A health loop mirrors** — not the scheduler's thread model. (The scheduler is `BackgroundScheduler` + a daemon `threading.Thread` + synchronous psycopg2; registry-api is pure async. Mirroring the *registry-api* loop keeps the health loop on the async ORM it needs.)

4. **The Phase-1 `_materialize_and_discover` upsert core lives inside the router module** (`routers/mcp_servers.py`, a module-level function), not a shared service module. WS-B's list-changed re-sync must reuse it verbatim (same upsert/inactivate/schema-drift semantics), so this plan **extracts it (behavior-neutral) into a new `services/registry-api/mcp_discovery.py`** and re-imports it in `mcp_servers.py`. One implementation, two callers (the public `/sync` route and the internal `/list-changed` endpoint) — the constitution's "no forked helper" rule.

5. **The proxy has no background task and no Keycloak client today.** `services/mcp-proxy/main.py` is request-driven only; the sub-chart deployment mounts **no** volumes (it is a token *receiver*, not projector). WS-B adds the proxy's first background component (the subscription manager) and WS-C adds its first outbound-credential mount (a Keycloak confidential-client secret). Both must preserve the two hard invariants (no DB, no `AGENTSHIELD_ENCRYPTION_KEY`) — verified achievable below.

6. **`session_cache._cache` is `dict[str, CachedSession]` keyed by stringified `server_id`.** No composite key, no TTL, eviction only on explicit `evict`/`set_session`. WS-C's per-`(server_id, user_sub)` pooling is the key-type change; the surrounding get-or-create/evict logic is otherwise reusable.

7. **`credentials.ServerConnection` does not carry `identity_mode`.** The per-server Secret's `connection` JSON written by `mcp_secrets.materialize_server_secret` today is `{server_url, transport, transport_config, is_external, owner_team}` — no `identity_mode`. So the proxy currently cannot branch on identity at all. WS-C adds `identity_mode` (+ derived `identity_audience`) to that materialized blob and to `ServerConnection`. No DB migration (the `identity_mode` **column** already exists from `0072`; we are only propagating it into the materialized Secret).

8. **The stub fixture (`scripts/e2e/fixtures/stub_mcp_server.py`) never emits `list_changed`.** It is a static `FastMCP` with `echo`/`add` and never adds/removes a tool, so no `notifications/tools/list_changed` is ever sent and (per the code agent) it does not explicitly advertise the `listChanged` capability. WS-B must **extend the fixture** with a way to mutate its tool set at runtime and emit the notification (a control MCP tool `simulate_tool_change`), and ensure `tools.listChanged` is advertised — otherwise there is nothing for the subscription to receive and `list_changed_supported` stays `false`.

---

## Part C — Decisions

Each: **Decision / Rationale / Alternatives rejected / Assumptions.**

### C1 — Health-loop owner: **registry-api background loop** (not the proxy, not the scheduler)

**Decision.** The periodic health sweep (WS-A / FR-MCP-22) runs as a registry-api `lifespan` background asyncio task (`services/registry-api/mcp_health.py::mcp_health_loop`), mirroring `cost_backfill_loop` exactly (started via `asyncio.create_task`, cancelled at shutdown). Each cycle it **enumerates every `MCPServer` row** (only registry-api can — it owns the DB), calls the **new proxy `POST /internal/health {server_id}`** per server (only the proxy can open an MCP session / holds the credentials), and writes `status` + `health_detail` **directly to its own DB** (no writeback endpoint — the loop lives in the DB-owning service). Single-flight across registry-api replicas via a **Postgres session advisory lock** (the `scheduler/ha.py` `pg_try_advisory_lock` primitive, stable key `crc32("mcp-health-sweep")`), so exactly one replica probes per cycle and the `consecutive_failures` read-modify-write cannot race.

**Rationale.** The sweep must (a) enumerate all servers → needs the DB, and (b) probe each → needs the proxy's session + credentials. Only registry-api has (a); only the proxy has (b). Putting the loop in registry-api and having it *call* the proxy per server keeps every DB write in the DB-owning service (no HTTP writeback round-trip for data it already holds) and lets it reuse the exact `while True … asyncio.sleep` shape that already runs in its lifespan. The proxy gains only a stateless per-request `/internal/health` probe — no background state, no DB, invariant intact.

**Alternatives rejected.**
- *Loop in the proxy.* The proxy has no DB, so it cannot enumerate the server set; it would need a new registry-api "list servers" internal endpoint **and** a new "status writeback" internal endpoint **and** its own leader election — three new surfaces to write back data registry-api already owns. More moving parts, and it makes the proxy stateful for a job the DB-owner should do.
- *Loop in the scheduler service.* The scheduler has HA + a DB connection, but no `mcp_servers` ORM and no discovery/upsert code; hosting the loop there would **fork** those into a second service (the exact anti-pattern the constitution rejects). The scheduler's HA pattern is *mirrored* (the advisory-lock primitive) without reusing the *service*.
- *No writeback endpoint / no advisory lock (rely on idempotency like `cost_backfill`).* Rejected for single-flight: `consecutive_failures += 1` is a read-modify-write on `health_detail`; two replicas racing would double-count and flip status early. The advisory lock is cheap and needs no K8s RBAC.

**Assumptions.** registry-api may run N≥1 replicas; the advisory lock makes the sweep correct for any N. The proxy's `/internal/health` is admin-plane (registry-api SA only), same trust as `/internal/discover`.

### C2 — Health probe transport: **`tools/list` liveness, reusing the session cache** (no MCP app-level ping)

**Decision.** `POST /internal/health` does a lightweight `session.list_tools()` against the server's live (or lazily re-created) cached session and returns `{ok, status, health_detail(str), protocol_version, list_changed_supported, tool_count}` — **it does not return the tool list and triggers no `Tool`-row upsert.** On any failure it evicts the session (so a wedged connection self-heals next probe) and returns `ok=false` with the reason string. Never 5xx (fail-closed body, mirroring `/internal/discover`).

**Rationale.** MCP 1.x has no universally-implemented application-level `ping`; `tools/list` is the cheapest call every server must answer, and it doubles as a refresh of `list_changed_supported`/`protocol_version` (from the session's captured `initialize` result). Reusing `session_cache.get_or_create` means a healthy server's probe is a single round-trip on the pooled connection.

**Alternatives rejected.** *Reuse `/internal/discover` for health* — it returns the full tool list and would tempt the loop into re-upserting every 60s (heavy; conflates health with discovery). Keeping health reachability-only and discovery event/manual-driven is cleaner. *A raw TCP/HTTP GET to `server_url`* — would not exercise the MCP session or refresh capability flags, and many MCP endpoints 405 a bare GET.

**Assumptions.** `tools/list` latency is bounded by the per-probe timeout (`MCP_CONNECT_TIMEOUT_SECONDS`, already in proxy config but currently unused — WS-A wires it in).

### C3 — Health status/backoff semantics: **threshold-flip + in-loop backoff; do NOT write `last_synced_at`**

**Decision.** Per server, the loop keeps the Phase-1 `health_detail` shape `{last_error, last_success_at, consecutive_failures, schema_drift}`:
- **Success:** `status='connected'`, `health_detail.last_success_at = now`, `consecutive_failures = 0`, `last_error = null`; `list_changed_supported` refreshed from the probe; `schema_drift` preserved (health never touches drift).
- **Failure:** `consecutive_failures += 1`, `last_error = reason`; `status` flips to `'error'` **only when `consecutive_failures >= MCP_HEALTH_FAILURE_THRESHOLD` (default 3)** — "repeated failures flip status" (FR-MCP-22), so one transient blip does not.
- **Recovery:** a single success from `error` → `connected`.
- **Backoff:** a server at/over threshold is re-probed on a longer cadence — the loop keeps an **in-memory** `_backoff_skip: dict[server_id, int]` and skips a hard-down server for `min(consecutive_failures, MCP_HEALTH_MAX_BACKOFF_CYCLES=10)` cycles between probes, so a permanently-dead external server is not hammered every interval. In-memory only (lost on restart → at worst one extra probe after a restart; acceptable).
- **`last_synced_at` is NOT written by the health loop.**

**Rationale for the `last_synced_at` deviation (deliberate, ledgered).** The brief says the loop "writes status + health_detail + **last_synced_at**." Reading the code, `last_synced_at` means *last successful tool **discovery*** (set only by `_materialize_and_discover`); a health probe does no discovery. Overloading it would make the Studio "Last Synced" column lie (it would move without a re-discovery). The correct health timestamp is `health_detail.last_success_at`, which the Phase-1 shape already carries and the Studio detail page can surface. So this plan writes `last_success_at`, not `last_synced_at`. Recorded here per the "reason from the code, note the disagreement" rule.

**Alternatives rejected.** *Add a `last_health_check_at` column (migration 0073)* — unnecessary; `health_detail.last_success_at` already is that field. Keeping Phase 2 migration-free is a feature (see C9).

**Assumptions.** Threshold 3 and interval 60s are defaults, all env-tunable.

### C4 — `list_changed` subscription lives in the proxy (only place the notification arrives); re-sync is a proxy→registry-api callback

**Decision.** A `notifications/tools/list_changed` message can only be received on the proxy's **long-lived MCP session** (`services/mcp-proxy/subscription_manager.py`, new). For a server whose `initialize` advertised `list_changed_supported=true`, the proxy holds a persistent session with a **notification handler**; on the notification it debounces and calls the **new** NetworkPolicy-trusted `POST /api/v1/internal/mcp/list-changed {server_id}` on registry-api, which re-runs the **shared** `_materialize_and_discover` (→ calls the proxy's own `/internal/discover`, upserts/inactivates `Tool` rows). **The proxy never writes the DB** — it only pokes registry-api, which owns every write (identical to how it already pokes `/internal/mcp/authorize-tool-call`).

**Rationale.** Preserves the proxy-no-DB invariant while giving `list_changed` a home. Reusing `_materialize_and_discover` means a `list_changed`-driven re-sync is byte-identical to a manual `/sync` (same namespacing, same vanished→`inactive`, same schema-drift flagging).

**Alternatives rejected.** *Proxy writes Tool rows directly* — violates the invariant (would need DB + models in the proxy). *registry-api polls for changes* — that is exactly what the WS-A health loop is *not* (list_changed is the push-based alternative to polling for tool-set changes; polling every server's full tool list would be heavy and is what `list_changed` exists to avoid).

**Assumptions.** The `mcp` 1.x `ClientSession` dispatches server notifications to a registered handler via its internal receive loop while the session context stays open. This is **pinned as a verification task** (T-P5) against the installed `mcp` version, exactly as Phase 1 pinned `.inputSchema`/`streamablehttp_client`; if the installed version's notification hook differs, the handler-registration call is adjusted (the design — persistent session + handler + debounced callback — is unaffected).

### C5 — Subscription mechanics: debounce in the proxy, idempotent + guarded re-sync in registry-api; multi-replica fan-out is tolerated, not eliminated

**Decision.**
- **Proxy debounce:** per-server, coalesce a burst of notifications within `MCP_LIST_CHANGED_DEBOUNCE_SECONDS` (default 5) into a single callback (a per-server timer reset on each notification).
- **Reconnect:** the subscriber reconnects on session drop with capped backoff (`MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS`=10, `MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS`=5); after the cap (e.g. the per-server Secret was deleted on server delete) it tears the subscription down and stops.
- **registry-api guard:** `mcp_discovery.py` keeps a per-server `asyncio.Lock` + a `_last_resync[server_id]` timestamp; a `/list-changed` call that arrives within `MCP_LIST_CHANGED_MIN_RESYNC_INTERVAL_SECONDS` (default 10) of the last completed re-sync for that server is a no-op (`{ok:true, tools_added:0,…, coalesced:true}`). Combined with the idempotent upsert, this makes duplicate triggers harmless.
- **Multi-replica:** each proxy replica that happens to hold a subscribed session for a server will fire its own callback; the registry-api min-interval guard + idempotent upsert collapse the duplicates into (at most) one redundant discovery. **Exactly-once subscription is NOT guaranteed and is explicitly ledgered** (the architecture doc §8 already lists "`list_changed` fan-out across N replicas — deferred"; Phase 2 *reduces* it to "harmless duplicate re-syncs" but does not eliminate it).

**Rationale.** The idempotent-upsert + short min-interval turns the hard "which replica subscribes" problem into a benign one without inventing cross-replica coordination (a distributed lease on "who owns server X's subscription") that would be disproportionate at Phase-2 scale.

**Alternatives rejected.** *Distributed single-subscriber election per server* — real complexity (a lease per server, failover on replica death) for a duplicate that is already harmless. Deferred. *No debounce* — a chatty server that reorders its tool list could trigger a re-sync storm.

**Assumptions.** Discovery is idempotent (verified: `_materialize_and_discover` upserts by `(mcp_server_id, mcp_tool_name)`).

### C6 — Service-identity (FR-MCP-21, **shippable**): proxy mints a Keycloak client-credentials token; identity_mode flows via the per-server Secret

**Decision.** `identity_mode='service_identity'` is fully built in Phase 2:
1. registry-api's `mcp_secrets.materialize_server_secret` adds `identity_mode` and `identity_audience` (read from `transport_config.identity_audience`, optional) to the per-server Secret's `connection` JSON.
2. The proxy's `ServerConnection` parses `identity_mode`/`identity_audience`; a new `services/mcp-proxy/keycloak_client.py::mint_service_account_token(audience)` runs a **client-credentials grant** against `KEYCLOAK_TOKEN_URL` using the proxy's confidential client (`MCP_PROXY_KEYCLOAK_CLIENT_ID` + a client secret mounted from a K8s Secret file, `MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH`), returning `(access_token, exp_epoch)`. Tokens are cached per audience until `exp − KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS`.
3. A new `services/mcp-proxy/identity.py::resolve_headers(connection, *, user_sub, is_data_plane)` selects the auth headers by `identity_mode`: `none` → the static `auth_headers` (Phase-1 behavior); `service_identity` → `{"Authorization": "Bearer <minted SA token>"}`; `on_behalf_of` → see C7. `discover`, `health`, and `tools/call` all build headers through `resolve_headers` instead of using `connection.auth_headers` directly.

**The master `AGENTSHIELD_ENCRYPTION_KEY` invariant is preserved.** The proxy's Keycloak *client* secret is a **narrow** credential (mints tokens only for the proxy's own confidential client), mounted as a **file volume** — it is **not** read via `get secrets` (so the proxy's RBAC stays scoped to `agentshield-mcp`), and it is **not** the DB master key. Provisioning the Keycloak client itself (a confidential client with a client-credentials/service-account grant) is a deploy prerequisite handled like every other platform Keycloak client (ledgered).

**Rationale.** Client-credentials is the standard "the platform acts as one principal" grant; caching is safe because the token is not user-scoped. Threading `identity_mode` through the existing per-server Secret (already the proxy's only server-metadata channel) avoids giving the proxy any new DB reach.

**Alternatives rejected.** *Give the proxy the master key / DB so it can read `AuthConfig` for a service token* — violates §3b least-privilege. *Have registry-api mint the token and stuff it in the per-server Secret* — the token would expire and go stale in the Secret; minting must be live at call time.

**Assumptions.** Keycloak reachable from the proxy (add an egress NetworkPolicy allowance). `identity_audience` optional; when unset the token is the default-audience SA token.

### C7 — On-behalf-of (FR-MCP-21): **plumbing shippable, token exchange BLOCKED on Decision 29**

**Decision.** Phase 2 builds the on-behalf-of *plumbing* and stubs the *exchange*:
- **Shippable now:** (a) the composite session-pool key `(server_id, user_sub)` (C8); (b) `x-user-sub` emission from both dispatch executors to the proxy (Part B #1 — the proxy already reads it); (c) the `resolve_headers` branch that recognizes `identity_mode=='on_behalf_of'` and, on the **data plane**, **fails closed** when `user_sub` is empty (deny — never silently fall back to service-identity, per requirements FR-MCP-21 point 4); on the **admin plane** (discover/health, no user context) it uses the service-identity token (the platform lists an OBO server's tools as itself).
- **Blocked on Decision 29:** the actual impersonation exchange. `identity.py::mint_on_behalf_of_token(user_sub, connection)` is a **stub** that raises `OnBehalfOfNotAvailable` ("on_behalf_of upstream-identity exchange is blocked on Decision 29 / identity-propagation Phase 0-2"); the proxy converts that into a `200` `is_error=true` body with that message. When the dependency lands, only that function body + the Keycloak impersonation client + ensuring a real `user_sub` reaches the executors are filled in — no other Phase-2 surface changes.

**Rationale.** This isolates exactly what is buildable (the session key, the header threading, the fail-closed branch, the interface seam) from the one thing that is not (a token the platform provably cannot mint until Decision 29 gives it a durable verified subject + an impersonation client — architecture doc §7a). Shipping the plumbing means the day Decision 29 lands, on-behalf-of is a single-function fill-in, not a re-plan.

**Alternatives rejected.** *Forward the raw JWT* — impossible; no raw Keycloak JWT survives past `auth_middleware.py` (§7a, verified). *Silent fallback to service-identity when `user_sub` is empty* — defeats the reason a server was configured OBO; explicitly forbidden (fail-closed).

**Assumptions.** Until Decision 29, an OBO server is registerable and discoverable (admin plane) but a `tools/call` against it returns a structured "not yet available" error — acceptable and honest (ledgered). Studio's register form already lets an admin pick `on_behalf_of` (Phase 1), so no new UI is needed for the blocked state beyond a note.

### C8 — Session-pool key: composite `(server_id, user_sub_or_none)`; `user_sub` only participates for OBO servers

**Decision.** `session_cache._cache` becomes `dict[SessionKey, CachedSession]` where `SessionKey = tuple[str, str | None]`. The effective key is computed by `_effective_user_sub(connection, user_sub)` → returns `user_sub` **only** when `connection.identity_mode == 'on_behalf_of'`, else `None`. So `none`/`service_identity` servers keep one shared pooled connection per `server_id` (key `(server_id, None)`); OBO servers would pool per user. `peek/get_or_create/set_session/evict` all take an optional `user_sub` and route through `_effective_user_sub`. Because C7's OBO exchange is stubbed, at runtime the composite key only ever resolves to `(server_id, None)` in Phase 2 — but the **key type and routing ship and are unit-tested**, so OBO pooling is live the moment C7's stub is filled.

**Rationale.** An OBO upstream token is user-scoped; a shared session would leak one user's authorization to another. Keying on the user closes that. Gating `user_sub` participation on `identity_mode` avoids fragmenting the pool for non-OBO servers (which must stay a single shared connection).

**Alternatives rejected.** *Always key on `(server_id, user_sub)`* — would fragment `none`/`service_identity` pools uselessly (every distinct `x-user-sub` a new connection to a server that ignores it). *Keep `server_id`-only and add a parallel OBO cache* — two caches to reason about; one composite-keyed cache is simpler.

**Assumptions.** `x-user-sub` is best-effort trace metadata in Phase 2 (usually empty); it drives a credential decision only for OBO servers, which are themselves blocked (C7).

### C9 — **No migration (0073 not needed)**

**Decision.** Phase 2 adds **no** DB migration. `health_detail` and `identity_mode` columns already exist (`0072`); `list_changed_supported`, `scan_results`, `transport_config`, `status`, `last_synced_at` all exist. WS-A reuses `health_detail.last_success_at`/`consecutive_failures`; WS-B reuses `list_changed_supported` + the existing `_materialize_and_discover` writes; WS-C reuses `identity_mode` (+ `transport_config.identity_audience`, a JSON key inside the existing JSONB column, no DDL). The session-pool key and all identity state are **in-memory in the proxy**, not persisted.

**Rationale.** The brief explicitly says `health_detail` already exists (no migration for it) and asks to justify any 0073. The honest answer is none is needed — every Phase-2 field already exists. Fewer surfaces, no Alembic risk.

**Alternatives rejected.** *`last_health_check_at` column* (C3), *`identity_audience` column* — both redundant with existing columns/JSONB.

### C10 — Intervals / thresholds / debounce values (all env-tunable, defaults chosen for a small server fleet)

| Knob | Default | Where | Reasoning |
|---|---|---|---|
| `MCP_HEALTH_CHECK_ENABLED` | `true` | registry-api | Master switch (mirrors `cost_backfill` always-on; lets a deploy disable the sweep). |
| `MCP_HEALTH_CHECK_INTERVAL_SECONDS` | `60` | registry-api | Fresh-enough status without hammering external servers; > the per-probe timeout. |
| `MCP_HEALTH_FAILURE_THRESHOLD` | `3` | registry-api | "Repeated failures flip status" (FR-MCP-22) — 3 consecutive ≈ tolerate a ~2-3 min blip before alarming. |
| `MCP_HEALTH_CHECK_CONCURRENCY` | `8` | registry-api | Bounded fan-out (semaphore) so one slow server can't stall the sweep. |
| `MCP_HEALTH_MAX_BACKOFF_CYCLES` | `10` | registry-api | Cap on skip-cycles for a hard-down server (≈10 min max between probes at 60s). |
| `MCP_LIST_CHANGED_ENABLED` | `true` | proxy | Master switch for the subscription manager. |
| `MCP_LIST_CHANGED_DEBOUNCE_SECONDS` | `5` | proxy | Coalesce notification bursts into one callback. |
| `MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS` | `10` | proxy | Reconnect cadence on session drop. |
| `MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS` | `5` | proxy | Then tear down (server likely deleted). |
| `MCP_LIST_CHANGED_MIN_RESYNC_INTERVAL_SECONDS` | `10` | registry-api | Cross-replica dedup floor for `/list-changed`. |
| `MCP_CONNECT_TIMEOUT_SECONDS` | `30` (exists, was unused) | proxy | Wire it into the health probe + subscriber connect. |
| `KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS` | `30` | proxy | Refresh the SA token this long before `exp`. |

**Rationale.** Small-fleet defaults; every value is an env var so a large deployment can tune without a code change. Values are chosen so interval > timeout (no overlapping sweeps) and debounce < interval.

### C11 — Decision 29 dependency (explicit)

**What on-behalf-of cannot do until Decision 29 (`docs/design/identity-propagation-architecture.md` Phase 0-2) lands:**
1. There is no durable, verified `RunContext.user_sub` reaching `governed_tool` for `sdk`-type agents (same root cause as `sdk-agent-gaps.md` Gap 1), so the `x-user-sub` a Phase-2 executor emits is **empty** in practice.
2. The proxy cannot mint an impersonation token *for* a user without possessing a re-presentable subject token — which by design never exists (§7a). The impersonation Keycloak client (confidential, impersonation grant, `requested_subject=user_sub`) is not provisioned.
Therefore Phase 2 ships the OBO **plumbing** (C7/C8) with `mint_on_behalf_of_token` stubbed to `OnBehalfOfNotAvailable`. When Decision 29 Phase 0-2 lands: fill that stub, provision the impersonation client, and confirm a real `user_sub` reaches both executors. **No other Phase-2 code changes** — this is the entire residual work, and it is the only Phase-2 FR (FR-MCP-21's OBO half) that cannot be reported done.

---

## Part D — Blast radius / regression map (for the mandatory sweep)

Phase 2 touches: the proxy (`main.py`, `session_cache.py`, `mcp_client.py`, `credentials.py`, `config.py`, `schemas.py` + new `subscription_manager.py`, `keycloak_client.py`, `identity.py`), registry-api (`mcp_proxy_client.py`, `mcp_secrets.py`, `routers/internal_mcp.py`, `routers/mcp_servers.py` (extraction only), new `mcp_health.py` + `mcp_discovery.py`, `config.py`, `main.py`), the two executors, the mcp-proxy sub-chart, Studio detail page, the stub fixture. The Phase-1 data path (`/internal/tools/call` for a `none` server) must stay byte-identical.

Impacted existing suites to re-run (CP4 / regression):
- **`suite-84-mcp-tools.sh`** — the whole Phase-1 MCP path (register→discover→bind→governed call, lifecycle 409/422). Must stay green: the `resolve_headers` refactor must be behavior-neutral for `identity_mode='none'`, and the extracted `_materialize_and_discover` must be behavior-neutral for `/sync`.
- **`suite-18-opa-governance.sh` / `suite-4-hitl.sh` / `suite-3-safety.sh`** — `governed_tool` is *not* changed by Phase 2 except the executor header emission; confirm no regression from the extra `x-user-sub` header.
- Studio Vitest (`McpServerDetailPage.test.tsx`, `McpServersPage.test.tsx`) + Playwright `mcp-servers.spec.ts` — the health panel + polling must not break the existing register/detail/bind journey.

If the extraction or the `resolve_headers` seam changes any Phase-1 behavior, that is a regression to fix before shipping, with its own failing-then-passing test (CLAUDE.md rule 7).

# Data Model — MCP as a Tool Source, Phase 2

**Headline: Phase 2 adds NO database migration.** Verify with `quickstart.md` before assuming anything: the alembic head is **`0072`** (`0072_mcp_server_fields.py`, `down_revision="0071"`), and every column Phase 2 needs already exists from that migration. There is **no `0073`** in this plan. All new Phase-2 state that isn't already a `0072` column lives **in-memory in the proxy** (session-pool key, minted-token cache, subscription state) — nothing persisted, nothing to migrate. (research.md C9.)

This file documents: (1) why no migration is needed, (2) the two **JSON-shape** changes inside existing columns (`health_detail` reuse, `transport_config.identity_audience`, and `identity_mode` added to the materialized per-server Secret — not the DB), (3) the proxy's in-memory state model (session-pool composite key, token cache, subscription registry), and (4) the `MCPServer.status` state machine under the new health loop.

---

## 1. Why no migration (columns already present from `0072`)

Verified against `services/registry-api/models.py::MCPServer` / `Tool`:

| Phase-2 need | Column it reuses | Exists since | Phase-2 use |
|---|---|---|---|
| Health status | `mcp_servers.status` `VARCHAR(32)` (CHECK `connected|disconnected|error`) | 0001 | WS-A loop flips `connected ↔ error`. |
| Health detail | `mcp_servers.health_detail` `JSONB NOT NULL '{}'` | 0072 | WS-A writes `last_error`/`last_success_at`/`consecutive_failures` (shape below). |
| `list_changed` capability | `mcp_servers.list_changed_supported` `BOOLEAN NOT NULL false` | 0072 | WS-A refreshes it on probe; WS-B decides whether to subscribe. |
| Per-server scan opt-out | `mcp_servers.scan_results` `BOOLEAN NOT NULL true` | 0072 | Unchanged (Phase-1). |
| Identity mode | `mcp_servers.identity_mode` `VARCHAR(32)` (CHECK `on_behalf_of|service_identity|none`) | 0072 | WS-C selects the credential branch; propagated into the per-server Secret. |
| Identity audience | `mcp_servers.transport_config` `JSONB NULL` | 0072 | WS-C reads optional `transport_config.identity_audience` (a JSON key, no DDL). |
| Discovery timestamp | `mcp_servers.last_synced_at` `TIMESTAMPTZ` | 0001 | **Unchanged** — WS-A deliberately does NOT write it (research.md C3). |

No new column, no CHECK change, no index. **Do not create a `0073`.**

---

## 2. JSON-shape changes inside existing columns / Secrets (no DDL)

### 2a. `mcp_servers.health_detail` — shape unchanged, written by a new writer

The Phase-1 shape (set by `_materialize_and_discover` / `_mark_server_error`) is:
```json
{ "last_error": "string | null",
  "last_success_at": "iso8601 | null",
  "consecutive_failures": 0,
  "schema_drift": [ {"tool_name": "server__x", "detected_at": "iso8601"} ] }
```
WS-A's health loop writes the **same** shape (no new keys), so the Phase-1 `MCPServerResponse.health_detail: dict[str, Any]` and the Studio `McpServerHealthDetail` type need **no** change. New-writer rules (research.md C3):

| Probe result | `status` | `last_error` | `last_success_at` | `consecutive_failures` | `schema_drift` |
|---|---|---|---|---|---|
| success | `connected` | `null` | `now` | `0` | **preserved** (health never edits drift) |
| failure, `consecutive_failures+1 < threshold` | *unchanged* | `<reason>` | *unchanged* | `+1` | preserved |
| failure, `consecutive_failures+1 >= threshold` (3) | `error` | `<reason>` | *unchanged* | `+1` | preserved |
| recovery (was `error`, now success) | `connected` | `null` | `now` | `0` | preserved |

The read-modify-write on `health_detail` is why the sweep is single-flighted (advisory lock, research.md C1) — two replicas must not both increment `consecutive_failures`.

### 2b. `transport_config.identity_audience` (optional, WS-C)

`transport_config` (JSONB, exists) may carry an optional `identity_audience: str` used only when `identity_mode='service_identity'` (or, admin-plane, `on_behalf_of`) to request a Keycloak token audienced for that internal server. Absent → the proxy mints a default-audience service-account token. No validation beyond "if present, must be a string" (add to the `MCPServerCreate`/`Update` validators only if trivial; otherwise treat as free-form transport config, consistent with Phase 1's "not validated" stance on `transport_config`).

### 2c. Per-server Secret `connection` blob gains `identity_mode` (+ `identity_audience`) — Secret, not DB

The per-server K8s Secret `agentshield-mcp-server-{id}` in `agentshield-mcp` (written by `mcp_secrets.materialize_server_secret`) is the **only** server-metadata channel the proxy has (no DB). Phase-1 `connection` JSON:
```json
{ "server_url": "...", "transport": "streamable_http",
  "transport_config": { ... } | null, "is_external": false, "owner_team": "platform" }
```
WS-C **adds two keys**:
```json
{ "server_url": "...", "transport": "streamable_http",
  "transport_config": { ... } | null, "is_external": false, "owner_team": "platform",
  "identity_mode": "service_identity",           // NEW — from MCPServer.identity_mode
  "identity_audience": "team-db-mcp" }            // NEW — from transport_config.identity_audience, or null
```
`auth_headers` key is unchanged. This is a **Secret data change**, not a schema change — no migration. Because the Secret is *re-materialized* on register / `/sync` / auth-config change (Phase-1 behavior), **existing servers pick up the new keys on their next sync** (or on the WS-C deploy's re-materialize path); a server whose Secret predates WS-C and hasn't re-synced simply has no `identity_mode` key → the proxy defaults it to `"none"` (Phase-1 behavior, safe). Ledgered as a no-op-safe backfill: the WS-C task re-materializes on the next sync; no forced backfill needed.

---

## 3. Proxy in-memory state (the real "data model" of Phase 2)

### 3a. Session-pool key change — `session_cache.py` (WS-C, research.md C8)

```
BEFORE (Phase 1):  _cache: dict[str, CachedSession]                 # key = str(server_id)
AFTER  (Phase 2):  _cache: dict[SessionKey, CachedSession]          # SessionKey = tuple[str, str | None]
                   SessionKey = (str(server_id), effective_user_sub)
                   effective_user_sub = user_sub  if connection.identity_mode == "on_behalf_of"
                                        else None
```
- `none` / `service_identity` servers → key `(server_id, None)` — one shared pooled connection per server (Phase-1 behavior preserved).
- `on_behalf_of` servers → key `(server_id, user_sub)` — per-user (blocked at runtime by C7's stub, but the key routing ships + is unit-tested).
- `CachedSession` dataclass unchanged: `{session: McpSession, connection: ServerConnection}`.
- `_locks` becomes keyed by `SessionKey` too (one lock per composite key).
- Eviction still explicit only (no TTL); WS-C adds one eviction trigger: on a `401` from an upstream `service_identity` server, evict + force a token refresh + retry once (extends the Phase-1 evict-and-retry).

### 3b. `ServerConnection` gains identity fields — `credentials.py` (WS-C)

```python
@dataclass
class ServerConnection:
    server_url: str
    transport: str
    transport_config: dict = field(default_factory=dict)
    is_external: bool = False
    owner_team: str | None = None
    auth_headers: dict[str, str] = field(default_factory=dict)
    identity_mode: str = "none"              # NEW — from connection JSON, default "none"
    identity_audience: str | None = None     # NEW — from connection JSON, optional
```
Parsed in `read_server_secret`: `identity_mode = connection.get("identity_mode", "none")`, `identity_audience = connection.get("identity_audience")`. Default `"none"` makes a pre-WS-C Secret behave exactly as Phase 1.

### 3c. Minted-token cache — `identity.py` (WS-C)

```
_token_cache: dict[str | None, tuple[str, float]]   # key = identity_audience (None = default audience)
                                                     # value = (access_token, exp_epoch_seconds)
```
Client-credentials (service-identity) tokens are **not** user-scoped, so they are cached by audience and reused until `exp − KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS`. On-behalf-of tokens are **never cached** (per-call, and blocked in Phase 2 anyway).

### 3d. Subscription registry — `subscription_manager.py` (WS-B)

```
_subscriptions: dict[str, SubscriptionState]        # key = str(server_id)

@dataclass
class SubscriptionState:
    task: asyncio.Task            # the long-lived subscriber loop
    debounce_task: asyncio.Task | None   # pending debounced callback timer (or None)
    reconnect_attempts: int
```
Per-replica, in-memory. `ensure_subscription(server_id)` is idempotent (no-op if already running). Lost on pod restart → re-established on the next `discover`/`health` that observes `list_changed_supported=true`. Nothing persisted.

---

## 4. `MCPServer.status` state machine — updated for the WS-A health loop

Phase 1 had `connected ↔ error` transitions only on an explicit `/sync` or a tool-call failure. Phase 2 adds the **periodic** driver:

```
   (create) ──▶ disconnected  (server_default; before the first synchronous discover)
                     │ POST /mcp-servers (synchronous discover)  OR  /sync  OR  /list-changed re-sync
           success   │   failure
        ┌────────────┴─────────────┐
        ▼                          ▼
   connected  ◀───────────────── error
        ▲   │                     ▲   │
        │   │ health probe fails  │   │ health probe succeeds (recovery)
        │   │ N times (N>=3)      │   │
        │   └──────▶ error ───────┘   │
        └──── health probe ok ────────┘
              (single success resets consecutive_failures=0, status=connected)
```

- **New in Phase 2:** the `connected → error` (after `>=3` consecutive failed probes) and `error → connected` (first successful probe) transitions are now also driven by the **periodic health loop** (FR-MCP-22), not only by an explicit `/sync` or a live tool call. A server that dies between syncs no longer keeps a stale `connected` — the Phase-1 ledgered gap is closed.
- `disconnected` is still only the pre-first-discover state (a create/sync always resolves to `connected` or `error`).
- Health probes never produce `Tool`-row changes, never touch `last_synced_at`, never touch `schema_drift`. Tool-set changes come only from discovery (`/sync`, register, or WS-B `/list-changed`).
- `Tool.status` transitions (`active ↔ inactive` on vanish/reappear) are **unchanged** from Phase 1 and are driven only by discovery — WS-B's `/list-changed` re-sync produces them exactly as a manual `/sync` does (it calls the same `_materialize_and_discover`).

---

## 5. Summary of what Phase 2 does NOT change (guardrails)

- **No new column, no CHECK, no index, no `0073`.** (§1)
- **`health_detail` JSON shape unchanged** — new writer, same keys. (§2a)
- **`Tool` rows** — untouched by WS-A/WS-C; only WS-B's re-sync mutates them, via the identical Phase-1 upsert. No new `Tool` column, no new `Tool.status` value.
- **`last_synced_at` semantics preserved** — discovery-only. (research.md C3)
- **Proxy stays off the DB and off `AGENTSHIELD_ENCRYPTION_KEY`** — all Phase-2 proxy state is in-memory; identity metadata arrives via the existing per-server Secret channel; the Keycloak *client* secret is a narrow file-mounted credential, never the master key. (research.md C6)

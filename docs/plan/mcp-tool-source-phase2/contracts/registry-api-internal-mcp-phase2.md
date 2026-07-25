# Contract — `registry-api` internal MCP endpoints, Phase 2 additions

Extends `docs/plan/mcp-tool-source-phase1/contracts/registry-api-internal-mcp.md`. Adds **one new internal endpoint** — `POST /api/v1/internal/mcp/list-changed` — into the **existing** `services/registry-api/routers/internal_mcp.py` router (`prefix="/api/v1/internal/mcp"`, `tags=["internal"]`, mounted in `main.py`). Same trust boundary as the Phase-1 `authorize-tool-call`: **NetworkPolicy-trusted, unauthenticated, no TokenReview** (the caller is the in-cluster MCP Proxy; registry-api does not re-verify). Local `_get_db` dependency, always `200` on a valid body, `422` on a malformed one.

There is **no health-writeback endpoint** — by design (research.md C1): the WS-A health loop runs *inside* registry-api and writes `mcp_servers.status`/`health_detail` directly through its own ORM session, so no cross-service writeback surface exists. The only proxy→registry-api callback Phase 2 adds is this re-sync trigger.

---

## `POST /api/v1/internal/mcp/list-changed` (WS-B / FR-MCP-07)

The MCP Proxy's subscription manager calls this when an upstream server emitted `notifications/tools/list_changed` (debounced proxy-side). registry-api re-runs discovery for that server using the **shared** `_materialize_and_discover` — identical to a manual `POST /api/v1/mcp-servers/{id}/sync` — and owns every `Tool`-row write. The proxy never writes the DB.

### Request
```json
{ "server_id": "b3e5b6b0-...-uuid" }
```
Modeled as:
```python
class ListChangedRequest(BaseModel):
    server_id: uuid.UUID          # typed UUID → bad id = 422, not 200
```

### Server-side flow
1. Resolve the `MCPServer` by `server_id`. Not found → `200 {"ok": false, "reason": "server_not_found", "tools_added":0, "tools_updated":0, "tools_inactivated":0}` (a deleted/unknown server is a normal answer, not an error — the proxy may still hold a stale subscription; this response lets it stop).
2. **Coalesce guard (cross-replica dedup, research.md C5):** under a per-server `asyncio.Lock` in `mcp_discovery.py`, if `now − _last_resync.get(server_id, 0) < MCP_LIST_CHANGED_MIN_RESYNC_INTERVAL_SECONDS` (default 10) → return `200 {"ok": true, "coalesced": true, "tools_added":0, "tools_updated":0, "tools_inactivated":0}` without re-running discovery.
3. Otherwise run `counters = await _materialize_and_discover(db, server, acknowledge_schema_drift=False)` (the extracted shared core — same upsert/vanish→`inactive`/schema-drift semantics as `/sync`), `await db.commit()`, set `_last_resync[server_id] = now`.
4. Return `200 ListChangedResponse`.

### Response — `200`, `ListChangedResponse`
```python
class ListChangedResponse(BaseModel):
    ok: bool
    coalesced: bool = False
    tools_added: int = 0
    tools_updated: int = 0
    tools_inactivated: int = 0
    reason: str | None = None      # e.g. "server_not_found"; None on success
```
Example (a new upstream tool appeared):
```json
{ "ok": true, "coalesced": false, "tools_added": 1, "tools_updated": 0, "tools_inactivated": 0 }
```
Example (coalesced duplicate from a second replica within 10s):
```json
{ "ok": true, "coalesced": true, "tools_added": 0, "tools_updated": 0, "tools_inactivated": 0 }
```
A discovery **failure** inside `_materialize_and_discover` (proxy unreachable / server down) is **not** a 4xx: it sets `server.status='error'` + `health_detail.last_error` (existing behavior) and returns `200 {"ok": false, ...}`. A failed re-sync is a successful *API call* reporting an unhealthy server — same convention as `/sync`.

### Errors
- `422` — malformed body (missing/invalid `server_id`).
- No 5xx for a downstream MCP problem or a missing server.

### Auth invariant
NetworkPolicy-trusted, unauthenticated — identical to `authorize-tool-call`. Returns only counters (never a secret/URL). The proxy that calls it has no SA identity requirement here; the endpoint is defensive (unknown server → benign `server_not_found`). Do **not** add a TokenReview (consistent with every other `/api/v1/internal/*` endpoint; there is no `require_service_identity` dependency in registry-api).

---

## Shared discovery core `_materialize_and_discover` — extracted to `mcp_discovery.py` (behavior-neutral)

Phase 1's `_materialize_and_discover(db, server, *, acknowledge_schema_drift)` and `_mark_server_error(server, reason)` live inside `routers/mcp_servers.py`. Phase 2 **moves both** into a new `services/registry-api/mcp_discovery.py` (plus the module-level coalesce state `_last_resync`/`_resync_locks`), and `routers/mcp_servers.py` imports them from there. This is a **pure move** — the register/`/sync`/`PUT` paths must behave identically (proven by suite-84 staying green). One implementation, three callers (register, `/sync`, `/list-changed`).

```python
# services/registry-api/mcp_discovery.py — moved verbatim from routers/mcp_servers.py, plus C5 guard state
async def _materialize_and_discover(db: AsyncSession, server: MCPServer, *,
                                    acknowledge_schema_drift: bool) -> dict: ...
def _mark_server_error(server: MCPServer, reason: str) -> None: ...

_last_resync: dict[str, float] = {}
_resync_locks: dict[str, asyncio.Lock] = {}
MIN_RESYNC_INTERVAL_SECONDS = int(os.getenv("MCP_LIST_CHANGED_MIN_RESYNC_INTERVAL_SECONDS", "10"))
```

---

## Health-loop client (WS-A) — `mcp_proxy_client.health_check_server`

Not an endpoint; the registry-api→proxy client call the WS-A loop uses. Mirrors `discover_server` (same SA-token read, same error mapping):
```python
# services/registry-api/mcp_proxy_client.py — NEW, alongside discover_server
async def health_check_server(server_id) -> dict:
    """POST {settings.mcp_proxy_url}/internal/health {"server_id": str(server_id)} with
    Authorization: Bearer <read settings.mcp_proxy_sa_token_path>. Returns the parsed
    McpHealthResponse dict {ok,status,health_detail,protocol_version,list_changed_supported,tool_count}.
    Raises RuntimeError only on genuine transport failure / non-200 (401/403/5xx) — the WS-A
    loop catches it and treats it as a failed probe (ok=false, reason='proxy unreachable: ...')."""
```

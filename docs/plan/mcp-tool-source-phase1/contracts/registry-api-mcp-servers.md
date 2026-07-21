# Contract — `registry-api` `/api/v1/mcp-servers`

New router: `services/registry-api/routers/mcp_servers.py`, mounted in `main.py` as `app.include_router(mcp_servers_router)` (alongside `tools_router`/`auth_configs_router`).

All endpoints follow the existing `tools.py`/`auth_configs.py` conventions: `AsyncSession = Depends(get_db)`, `get_optional_user`/`x_user_sub` for `created_by` capture (no new end-user auth mechanism), `HTTPException` for errors, Pydantic response models with `ConfigDict(from_attributes=True)`.

Two side-effects distinguish this router from a plain CRUD router (both new to this plan's session):
1. On register / `/sync` / auth-config change it **materializes the per-server credential Secret** (`agentshield-mcp-server-{id}` in `agentshield-mcp`, data-model.md) via `mcp_secrets.materialize_server_secret`, and on delete it removes that Secret.
2. It calls the **MCP Proxy `/internal/discover`** presenting an audience-`agentshield-mcp-proxy` SA token (via `mcp_proxy_client.discover_server`).

Lifecycle invariants enforced by this router (commented on the `MCPServer`/`Tool` models, decided 2026-07-21):
- `name` is **immutable** after create (PUT rejects a differing `name` → `422`).
- DELETE is **blocked `409`** while any `AgentTool` references a child tool.
- Vanished-upstream tools on `/sync` → `status='inactive'` (never row-deleted).
- Individual `type='mcp_tool'` tools are not deletable via `DELETE /api/v1/tools/{id}` (enforced in `tools.py`, Task 2).

---

## `POST /api/v1/mcp-servers/`

Register a server. **Synchronously** attempts discovery in the same request (FR-MCP-02) — no async job/polling in Phase 1.

### Request — `MCPServerCreate`
```json
{
  "name": "github-mcp",
  "description": "GitHub's hosted MCP server",
  "server_url": "https://mcp.githubcopilot.com/mcp",
  "transport": "streamable_http",
  "auth_config_id": "6f2c1e9a-...-uuid",
  "owner_team": "platform",
  "identity_mode": "none",
  "is_external": true,
  "transport_config": null,
  "scan_results": true
}
```
Field notes:
- `transport`: `"streamable_http"` only in Phase 1 (`"stdio"` → `422` `"stdio transport is not available until Phase 3"`, even though the DB CHECK permits it for forward-compat — so the API and Studio's grayed-out radio agree).
- `identity_mode`: default `"none"`. A `model_validator` rejects `is_external=true` with `identity_mode != "none"` (`422`).
- `scan_results`: default `true`. Accepted for internal and external servers but **has no effect** when `is_external=true` (external results are always scanned — see plan.md Task 11). Not rejected as `false`+external (harmless no-op); the detail response surfaces both so the UI can show "(ignored — external server)".

### Response — `201 Created`, `MCPServerResponse`
```json
{
  "id": "b3e5b6b0-...-uuid", "name": "github-mcp", "description": "...",
  "server_url": "https://mcp.githubcopilot.com/mcp", "transport": "streamable_http",
  "auth_config_id": "6f2c1e9a-...-uuid", "owner_team": "platform",
  "identity_mode": "none", "is_external": true, "transport_config": null,
  "health_detail": {"last_error": null, "last_success_at": "2026-07-21T18:04:02Z", "consecutive_failures": 0, "schema_drift": []},
  "list_changed_supported": false, "scan_results": true,
  "status": "connected", "last_synced_at": "2026-07-21T18:04:02Z", "discovered_tool_count": 7,
  "created_at": "2026-07-21T18:04:01Z", "updated_at": "2026-07-21T18:04:02Z"
}
```
On a **failed** discover attempt the response is still `201` (the row is created either way — registration is not all-or-nothing) with `status:"error"`, `health_detail.last_error` populated, `discovered_tool_count:0`.

### Server-side flow
1. Validate `name` uniqueness (`409` on dup) and `auth_config_id` existence if set (`422`) — mirrors `create_tool`. Reject `transport="stdio"` and the `is_external`/`identity_mode` cross-check.
2. Insert `MCPServer`, `db.flush()` (need the generated `id`).
3. `mcp_secrets.materialize_server_secret(db, server)` — write `agentshield-mcp-server-{id}` (data-model.md). A materialization failure is logged and folded into a discover-error result (do not 5xx).
4. `mcp_proxy_client.discover_server(server.id)` (POSTs `/internal/discover` with the audience-scoped SA token).
5. On `ok`: for each discovered tool upsert a `Tool` row (data-model.md's first-discovery insert — `name = f"{server.name}__{tool.name}"`, `owner_team = server.owner_team`, etc.); set `status="connected"`, `discovered_tool_count`, `last_synced_at=now()`, `health_detail={last_error:None, last_success_at:now, consecutive_failures:0, schema_drift:[]}`, `list_changed_supported` from the proxy response.
6. On not-`ok`: `status="error"`, `health_detail={last_error:<reason>, last_success_at:None, consecutive_failures:1, schema_drift:[]}`, `discovered_tool_count:0`. **Do not roll back the insert or the Secret.**
7. `db.commit()`. Return `201` either way.

### Errors
- `409` — `name` already taken.
- `422` — `auth_config_id` doesn't resolve; `transport="stdio"`; `is_external=true` + `identity_mode != "none"`.
- The MCP Proxy being unreachable is **not** an API error — recorded as `status="error"` in the `201`. Only input-validation problems 4xx.

---

## `GET /api/v1/mcp-servers/`

List servers, paginated — mirrors `tools.py::list_tools` (`PaginatedResponse[MCPServerResponse]`, `limit`/`offset`, default `limit=50`). Query params: `owner_team`, `status` (`connected|disconnected|error`), `transport`. No `publish_status`-style visibility split — servers are a Settings/admin concept (OQ-04 resolved), so this lists all servers regardless of caller team, like `listAuthConfigs`.

---

## `GET /api/v1/mcp-servers/{id}`

Detail + discovered tools — the response the Server Detail page's discovered-tools table (FR-MCP-41's proof) reads.

### Response — `200`, `MCPServerDetailResponse` (extends `MCPServerResponse`)
```json
{
  "...": "all MCPServerResponse fields",
  "tools": [
    { "id": "...", "name": "github-mcp__search_issues", "mcp_tool_name": "search_issues",
      "input_schema": {"type":"object","properties":{"query":{"type":"string"}},"required":["query"]},
      "risk_level": "low", "status": "active", "pii_deanonymize_allowed": false }
  ]
}
```
`tools` = every `Tool` row with this `mcp_server_id`, **including** `inactive` ones (the detail page shows them struck-through/greyed — never-hard-deleted, FR-MCP-04). Ordering: `name` ascending. `404` if the server doesn't exist.

---

## `PUT /api/v1/mcp-servers/{id}`

Partial update — mirrors `tools.py::update_tool`'s `exclude_unset=True`. **Editable:** `description`, `auth_config_id`, `owner_team`, `identity_mode`, `scan_results`, `transport_config`. **Not editable (rejected `422` if the payload sets them to a different value):** `name` (immutable — a rename would orphan every child `Tool.name`, model invariant), `server_url`, `transport`, `is_external`. Does **not** re-run discovery (use `/sync`).

Side-effects: same `auth_config_id`-existence + `is_external`/`identity_mode` cross-check as POST (on the merged post-update state). If `auth_config_id` changed (or any field feeding the credential Secret), **re-materialize** `agentshield-mcp-server-{id}` (`mcp_secrets.materialize_server_secret`) so the proxy reads fresh creds on its next cache miss.

---

## `POST /api/v1/mcp-servers/{id}/sync`

Re-run discovery (FR-MCP-04). Same flow as POST steps 3–7 (materialize Secret → discover → upsert), plus the vanished-tool pass and schema-drift handling (data-model.md).

### Request — `MCPServerSyncRequest` (all optional)
```json
{ "acknowledge_schema_drift": false }
```
`acknowledge_schema_drift: true` clears any prior unacknowledged `health_detail.schema_drift` entries **before** this sync records new ones.

### Response — `200`, `MCPServerSyncResponse`
```json
{ "server": { "...": "MCPServerResponse, post-sync" },
  "tools_added": 1, "tools_updated": 0, "tools_inactivated": 2,
  "schema_drift_detected": ["github-mcp__search_issues"] }
```
`tools_inactivated` = child tools that vanished from this sync's `tools/list` and were flipped to `status='inactive'` (never deleted).

### Errors
- `404` — server not found.
- Proxy unreachable / discover failure → **not** a 4xx: `server.status` flips to `"error"`, response still `200` with `tools_added=0` etc. and `health_detail.last_error` populated (a failed sync is a successful *API call* reporting an unhealthy server).

---

## `DELETE /api/v1/mcp-servers/{id}`

Guarded delete, mirrors `auth_configs.py::delete_auth_config`'s referencing-rows check.

### Behavior
1. Find every `Tool` with this `mcp_server_id` that has ≥1 `AgentTool` binding (`JOIN agent_tools`).
2. If any exist → `409 Conflict`:
```json
{ "detail": { "message": "Cannot delete — discovered tools from this server are bound to agents.",
    "blocking_tools": ["github-mcp__search_issues"], "blocking_agents": ["support-bot", "triage-agent"] } }
```
3. If none bound → delete every (unbound) `Tool` with this `mcp_server_id`, delete the `MCPServer` row, and remove the per-server credential Secret via `mcp_secrets.delete_server_secret(id)`. `204 No Content`.

This `MCPServer` delete guard is deliberately **stricter** than `DELETE /api/v1/tools/{id}` (which soft-deletes even bound tools) — a small additive safety improvement scoped to the new resource; it does not retrofit `DELETE /api/v1/tools/{id}` (out of scope). Note the tools router separately **rejects** `DELETE` on a `type='mcp_tool'` row (`409`, Task 2) — those are only ever removed via this server delete.

---

## Auth requirements (all endpoints)

Same as `tools.py`/`auth_configs.py` today: `get_optional_user` + `X-User-Sub` fallback for `created_by`; no endpoint requires a specific role in Phase 1 (OQ-04: "follows whatever pattern current Tool creation already uses"). Artifact-scoped RBAC for MCP servers is explicitly deferred (Decision 25's model doesn't cover this asset type yet). The `/internal/mcp/authorize-tool-call` endpoint (separate contract) is not part of this public router.

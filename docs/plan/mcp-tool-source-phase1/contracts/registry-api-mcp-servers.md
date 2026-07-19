# Contract — `registry-api` `/api/v1/mcp-servers`

New router: `services/registry-api/routers/mcp_servers.py`, mounted in `main.py` as
`app.include_router(mcp_servers_router)` (alongside the existing `tools_router`/`auth_configs_router` registrations).

All endpoints follow the existing `tools.py`/`auth_configs.py` conventions: `AsyncSession = Depends(get_db)`, `x_user_sub`/`get_optional_user` for `created_by` capture (no new auth mechanism), `HTTPException` for errors, Pydantic response models with `model_config = ConfigDict(from_attributes=True)`.

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
- `transport`: `"streamable_http"` only accepted value in Phase 1 (`"stdio"` is `422` — `"stdio transport is not available until Phase 3"` — even though the CHECK constraint permits it at the DB layer for forward compatibility, the API rejects it explicitly so Studio's grayed-out radio option and the API agree).
- `identity_mode`: default `"none"`. A `model_validator` rejects `is_external=true` with `identity_mode != "none"` (422, `"external servers cannot use an internal identity mode"`) — see data-model.md's note on why this is an API-layer check, not a DB constraint.
- `scan_results`: default `true`. Accepted for both internal and external servers, but **has no effect** when `is_external=true` (the code path always scans externally-sourced results regardless — see plan.md Task 8). The API does not reject `scan_results=false` + `is_external=true` at create time (it's a harmless no-op, not an invalid state), but `GET`/detail responses surface both fields as-is so the UI can show "(ignored — external server)" next to the toggle.

### Response — `201 Created`, `MCPServerResponse`

```json
{
  "id": "b3e5b6b0-...-uuid",
  "name": "github-mcp",
  "description": "GitHub's hosted MCP server",
  "server_url": "https://mcp.githubcopilot.com/mcp",
  "transport": "streamable_http",
  "auth_config_id": "6f2c1e9a-...-uuid",
  "owner_team": "platform",
  "identity_mode": "none",
  "is_external": true,
  "transport_config": null,
  "health_detail": {"last_error": null, "last_success_at": "2026-07-19T18:04:02Z", "consecutive_failures": 0},
  "list_changed_supported": false,
  "scan_results": true,
  "status": "connected",
  "last_synced_at": "2026-07-19T18:04:02Z",
  "discovered_tool_count": 7,
  "created_at": "2026-07-19T18:04:01Z",
  "updated_at": "2026-07-19T18:04:02Z"
}
```

On a **failed** discover attempt, the response is still `201` (the row is created either way — architecture doc §3a: "registration is not an all-or-nothing gate") with:
```json
{
  "...": "same shape",
  "status": "error",
  "health_detail": {"last_error": "connection refused", "last_success_at": null, "consecutive_failures": 1},
  "discovered_tool_count": 0,
  "last_synced_at": "2026-07-19T18:04:02Z"
}
```

### Errors
- `409 Conflict` — `name` already taken (mirrors `tools.py::create_tool`'s exact pattern).
- `422 Unprocessable Entity` — `auth_config_id` doesn't resolve to a real `AuthConfig` row (mirrors `tools.py`'s auth-config-existence check); `transport="stdio"`; `is_external=true` + `identity_mode != "none"`.
- The MCP Proxy being unreachable is **not** an API error — it is recorded as `status="error"` in the `201` response (see above). Only a genuine input-validation problem 4xx's.

### Server-side flow
1. Validate `name` uniqueness, `auth_config_id` existence (if set) — mirrors `create_tool`.
2. Insert `MCPServer` row, `db.flush()` (need the generated `id` before calling the proxy).
3. Call `mcp_proxy_client.discover_server(server_id=server.id)` (see the MCP Proxy contract below for what this returns).
4. On success: for each discovered tool, upsert a `Tool` row per data-model.md's re-sync semantics (all rows are "new" on first discovery); set `server.status="connected"`, `server.discovered_tool_count=len(tools)`, `server.last_synced_at=now()`, `server.health_detail={"last_error": None, "last_success_at": now_iso, "consecutive_failures": 0}`, `server.list_changed_supported=<from proxy response>`.
5. On failure (proxy unreachable, or proxy reports a connect/`initialize` error): set `server.status="error"`, `server.health_detail={"last_error": <message>, "last_success_at": None, "consecutive_failures": 1}`, `discovered_tool_count=0`. **Do not roll back the `MCPServer` insert.**
6. `db.commit()`. Return `201` either way.

---

## `GET /api/v1/mcp-servers/`

List servers, paginated — mirrors `tools.py::list_tools`'s pagination shape exactly (`PaginatedResponse[MCPServerResponse]`, `limit`/`offset` query params, default `limit=50`).

Query params: `owner_team: str | None`, `status: str | None` (`connected|disconnected|error`), `transport: str | None`.

No `publish_status`-style visibility split for MCP servers in Phase 1 — servers are a Settings-level/admin concept (architecture doc §"Which open question blocks which phase", OQ-04 resolved: "follows whatever pattern current Tool creation already uses, no new restriction invented"), so this endpoint lists all servers regardless of caller team, same as `listAuthConfigs` today does for credentials.

---

## `GET /api/v1/mcp-servers/{id}`

Detail + discovered tools — the response the Server Detail page's discovered-tools table (FR-MCP-41's proof point) reads.

### Response — `200`, `MCPServerDetailResponse` (extends `MCPServerResponse`)
```json
{
  "...": "all MCPServerResponse fields",
  "tools": [
    {
      "id": "...",
      "name": "github-mcp__search_issues",
      "mcp_tool_name": "search_issues",
      "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
      "risk_level": "low",
      "status": "active",
      "pii_deanonymize_allowed": false
    }
  ]
}
```
`tools` is every `Tool` row with this `mcp_server_id`, **including** `deprecated` ones (the detail page shows them struck-through/greyed, per the "never hard-deleted — impact analysis" requirement — FR-MCP-04). Ordering: `name` ascending.

`404` if the server doesn't exist.

---

## `PUT /api/v1/mcp-servers/{id}`

Update editable fields — mirrors `tools.py::update_tool`'s `exclude_unset=True` partial-update pattern. Editable: `description`, `auth_config_id`, `owner_team`, `identity_mode`, `scan_results`, `transport_config`. **Not editable via PUT:** `name`, `server_url`, `transport`, `is_external` (changing where/how the platform connects is a re-registration concern, not a metadata edit — mirrors `ToolsPage.tsx`'s existing "type cannot be changed after creation" convention). Does **not** re-run discovery — use `/sync` for that.

Same `auth_config_id`-existence validation and `is_external`/`identity_mode` cross-check as `POST` (re-run on the merged post-update state).

---

## `POST /api/v1/mcp-servers/{id}/sync`

Re-run discovery (FR-MCP-04/FR-MCP-07's fallback path). Same server-side flow as step 3–6 of `POST /` above, plus the deprecation pass (data-model.md's "tools not in this sync's `tools/list` → `deprecated`").

### Request — `MCPServerSyncRequest` (all fields optional)
```json
{ "acknowledge_schema_drift": false }
```
`acknowledge_schema_drift: true` clears any prior unacknowledged `health_detail.schema_drift` entries **before** this sync records new ones (data-model.md's re-sync semantics).

### Response — `200`, `MCPServerSyncResponse`
```json
{
  "server": { "...": "MCPServerResponse, post-sync" },
  "tools_added": 1,
  "tools_updated": 0,
  "tools_deprecated": 2,
  "schema_drift_detected": ["github-mcp__search_issues"]
}
```

### Errors
- `404` — server not found.
- Proxy unreachable / discover failure → same as create: **not** a 4xx, `server.status` flips to `"error"`, response still `200` with `tools_added=0` etc. and `server.health_detail.last_error` populated. (A sync attempt that fails to even reach the server is itself a successful *API call* reporting an unhealthy server — mirrors how a failed create still returns `201`.)

---

## `DELETE /api/v1/mcp-servers/{id}`

Soft-delete guard, mirrors `auth_configs.py::delete_auth_config`'s referencing-rows check exactly.

### Behavior
1. Find every `Tool` row with this `mcp_server_id` that has at least one `AgentTool` binding (`JOIN agent_tools`).
2. If any exist → `409 Conflict`:
```json
{
  "detail": {
    "message": "Cannot delete — discovered tools from this server are bound to agents.",
    "blocking_tools": ["github-mcp__search_issues"],
    "blocking_agents": ["support-bot", "triage-agent"]
  }
}
```
3. If none bound → delete every unbound `Tool` row with this `mcp_server_id`, then delete the `MCPServer` row. `204 No Content`.

This is a genuine delete (not the soft `status='deprecated'` pattern `DELETE /api/v1/tools/{id}` uses) — a server with zero bound tools has no impact-analysis reason to keep dangling rows around, and the requirements doc calls this "soft-deletes the server" but the only state that matters (bound tools) is the same guard `tools.py` doesn't even have today (it lets you delete a tool that's bound to an agent!). This plan's `MCPServer` delete guard is **stricter** than `Tool`'s own delete endpoint, which is a deliberate, small, additive safety improvement scoped to the new resource — it does not retrofit the same guard onto `DELETE /api/v1/tools/{id}` (out of scope; that endpoint's current soft-delete-only behavior for individual tools is untouched).

---

## Auth requirements (all endpoints)

Same as `tools.py`/`auth_configs.py` today: `get_optional_user` + `X-User-Sub` header fallback for `created_by` capture; no endpoint in this router requires a specific role in Phase 1 (matches Open Question 4's resolution — "follows whatever pattern current Tool creation already uses," which today has no role gate). RBAC scoping for MCP servers specifically is explicitly deferred (Decision 25's artifact-scoped roles don't cover this asset type yet — noted in the architecture doc, not re-litigated here).

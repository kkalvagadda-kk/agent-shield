// ---------------------------------------------------------------------------
// mcpServersApi.ts — typed client for the MCP-as-a-tool-source endpoints
// (`routers/mcp_servers.py`, Phase 6 / contract registry-api-mcp-servers.md).
//
// Rides the SHARED axios `http` instance from registryApi (baseURL `/api/v1`,
// Bearer-token interceptor, DEMO mock adapter) so auth + team scoping come for
// free. Every field name mirrors the Pydantic response models in
// services/registry-api/schemas.py (MCPServer{Create,Update,Response,
// DetailResponse,SyncRequest,SyncResponse}).
//
// Register is SYNCHRONOUS: POST attempts discovery in the same request and
// returns 201 with status="connected" | "error" either way (registration is
// never all-or-nothing). The proxy being unreachable is NOT an API error — it
// surfaces as status="error" + health_detail.last_error, so the UI reads status
// off the row, not off the HTTP code.
// ---------------------------------------------------------------------------

import { http, type Paginated } from "./registryApi";

// ---------------------------------------------------------------------------
// Types (field names EXACTLY per schemas.py)
// ---------------------------------------------------------------------------

export type McpServerStatus = "connected" | "disconnected" | "error";
export type McpIdentityMode = "none" | "on_behalf_of" | "service_identity";
/** How an external server authenticates: `static` = a stored credential
 *  (auth_config), `oauth` = per-user OAuth 2.1 grant (Phase 4 / WS-2). */
export type McpExternalAuthMode = "static" | "oauth";

/** `health_detail` JSONB — computed by the discover/sync path, never client-set. */
export interface McpServerHealthDetail {
  last_error?: string | null;
  last_success_at?: string | null;
  consecutive_failures?: number;
  schema_drift?: unknown[];
}

/** `MCPServerResponse` — one registered upstream MCP server. */
export interface McpServer {
  id: string;
  name: string;
  description: string | null;
  server_url: string;
  transport: string;
  auth_config_id: string | null;
  owner_team: string | null;
  identity_mode: string;
  is_external: boolean;
  /** `static` (stored credential) or `oauth` (per-user OAuth 2.1 grant, WS-2).
   *  Only meaningful for external servers; internal servers are always `static`. */
  external_auth_mode: McpExternalAuthMode;
  transport_config: Record<string, unknown> | null;
  health_detail: McpServerHealthDetail;
  list_changed_supported: boolean;
  scan_results: boolean;
  status: string;
  last_synced_at: string | null;
  discovered_tool_count: number;
  created_at: string;
  updated_at: string;
}

/** One discovered child `Tool` row (subset of ToolResponse) as served on the
 *  Server Detail page. `inactive` tools are returned too (greyed, never deleted). */
export interface McpServerTool {
  id: string;
  name: string;
  display_name?: string | null;
  description?: string | null;
  mcp_tool_name: string | null;
  input_schema: Record<string, unknown> | null;
  risk_level: string;
  status: string;
  pii_deanonymize_allowed: boolean;
}

/** `MCPServerDetailResponse` — the base server fields + every child Tool. */
export interface McpServerDetail extends McpServer {
  tools: McpServerTool[];
}

/** `MCPServerCreate` — `transport` is `streamable_http` only in Phase 1; the
 *  `is_external`/`identity_mode` cross-check + stdio rejection are server-side. */
export interface CreateMcpServerPayload {
  name: string;
  description?: string;
  server_url: string;
  transport?: "streamable_http";
  auth_config_id?: string | null;
  owner_team?: string;
  identity_mode?: McpIdentityMode;
  is_external?: boolean;
  /** `oauth` (WS-2) makes the server authenticate via a per-user OAuth 2.1 grant
   *  instead of a stored credential; requires `is_external`. Defaults to `static`. */
  external_auth_mode?: McpExternalAuthMode;
  transport_config?: Record<string, unknown> | null;
  scan_results?: boolean;
}

/** `MCPServerUpdate` — `name`/`server_url`/`transport`/`is_external` are immutable
 *  (rejected 422 by the router); only the editable fields are exposed here. */
export interface UpdateMcpServerPayload {
  description?: string;
  auth_config_id?: string | null;
  owner_team?: string;
  identity_mode?: McpIdentityMode;
  scan_results?: boolean;
  transport_config?: Record<string, unknown> | null;
}

/** `MCPServerSyncResponse` — the delta from a re-discovery. */
export interface McpServerSyncResult {
  server: McpServer;
  tools_added: number;
  tools_updated: number;
  tools_inactivated: number;
  schema_drift_detected: string[];
}

// ---------------------------------------------------------------------------
// CRUD + sync
// ---------------------------------------------------------------------------

/** GET /mcp-servers/ — paginated; unwrapped to a flat array (mirrors listKBs).
 *  Servers are a Settings/admin concept — listed regardless of caller team. */
export const listMcpServers = async (): Promise<McpServer[]> => {
  const { data } = await http.get<Paginated<McpServer>>("/mcp-servers/");
  return data.items ?? [];
};

/** GET /mcp-servers/{id} — detail + every discovered Tool (incl. inactive). */
export const getMcpServer = async (id: string): Promise<McpServerDetail> => {
  const { data } = await http.get<McpServerDetail>(`/mcp-servers/${id}`);
  return data;
};

/** POST /mcp-servers/ — register + synchronous discover. 201 with
 *  status=connected|error either way; only input-validation problems 4xx. */
export const createMcpServer = async (
  payload: CreateMcpServerPayload
): Promise<McpServer> => {
  const { data } = await http.post<McpServer>("/mcp-servers/", payload);
  return data;
};

/** PUT /mcp-servers/{id} — partial update (does NOT re-run discovery). */
export const updateMcpServer = async (
  id: string,
  payload: UpdateMcpServerPayload
): Promise<McpServer> => {
  const { data } = await http.put<McpServer>(`/mcp-servers/${id}`, payload);
  return data;
};

/** POST /mcp-servers/{id}/sync — re-run discovery. A failed sync is still a 200
 *  (the API call succeeded; it reports an unhealthy server via status="error"). */
export const syncMcpServer = async (
  id: string,
  body: { acknowledge_schema_drift?: boolean } = {}
): Promise<McpServerSyncResult> => {
  const { data } = await http.post<McpServerSyncResult>(
    `/mcp-servers/${id}/sync`,
    body
  );
  return data;
};

/** DELETE /mcp-servers/{id} — 409 (with blocking_tools/blocking_agents) while any
 *  discovered tool is bound to an agent; 204 once unbound. */
export const deleteMcpServer = async (id: string): Promise<void> => {
  await http.delete(`/mcp-servers/${id}`);
};

// ---------------------------------------------------------------------------
// OAuth 2.1 (WS-2, Phase 4) — a per-user grant for `external_auth_mode="oauth"`
// servers. The frontend only ever sees the upstream consent URL + the status
// enum: NO token/secret is ever handled client-side. Field names mirror
// `McpOAuthStatusResponse` in services/registry-api/routers/mcp_oauth.py.
// ---------------------------------------------------------------------------

export type McpOAuthStatusValue = "needs_auth" | "authorized" | "error";

/** `McpOAuthStatusResponse` — the CALLER'S own grant status (keyed on jwt.sub);
 *  never another user's, and never the token itself. */
export interface McpOAuthStatus {
  server_id: string;
  user_sub: string;
  status: McpOAuthStatusValue;
  scopes: string | null;
  token_expires_at: string | null;
  last_error: string | null;
  external_auth_mode: McpExternalAuthMode;
}

/** POST /mcp-servers/{id}/oauth/authorize → the upstream consent URL. The caller
 *  does `window.location.href = authorization_url` to start the redirect; the
 *  flow finishes at the server-side callback (302 back to `?oauth=…`). */
export const startMcpOAuth = async (
  id: string
): Promise<{ authorization_url: string }> => {
  const { data } = await http.post<{ authorization_url: string }>(
    `/mcp-servers/${id}/oauth/authorize`,
    {}
  );
  return data;
};

/** GET /mcp-servers/{id}/oauth/status → the caller's grant status for the badge. */
export const getMcpOAuthStatus = async (id: string): Promise<McpOAuthStatus> => {
  const { data } = await http.get<McpOAuthStatus>(`/mcp-servers/${id}/oauth/status`);
  return data;
};

/** DELETE /mcp-servers/{id}/oauth → disconnect (revoke) the caller's grant. 204. */
export const disconnectMcpOAuth = async (id: string): Promise<void> => {
  await http.delete(`/mcp-servers/${id}/oauth`);
};

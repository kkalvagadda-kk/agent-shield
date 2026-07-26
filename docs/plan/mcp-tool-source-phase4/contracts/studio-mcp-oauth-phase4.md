# Contract — Studio, Phase 4 additions (WS-2 OAuth authorize journey)

Extends `docs/plan/mcp-tool-source-phase2/contracts/studio-mcp-servers-phase2.md`.

**Three new API-client methods, one new detail-page panel, one register-modal toggle, one callback-landing behavior.** No new route (the callback lands back on the existing `/mcp-servers/{id}` route via a `?oauth=` query param).

---

## 1. `studio/src/api/mcpServersApi.ts` — new methods + types (WS-2)

```ts
export type McpOAuthStatusValue = "needs_auth" | "authorized" | "error";
export interface McpOAuthStatus {
  server_id: string;
  user_sub: string;
  status: McpOAuthStatusValue;
  scopes: string | null;
  token_expires_at: string | null;
  last_error: string | null;
  external_auth_mode: "static" | "oauth";
}

/** POST /mcp-servers/{id}/oauth/authorize → the upstream consent URL to redirect to. */
export const startMcpOAuth = async (id: string): Promise<{ authorization_url: string }> => {
  const { data } = await http.post(`/mcp-servers/${id}/oauth/authorize`, {});
  return data;
};

/** GET /mcp-servers/{id}/oauth/status → the caller's grant status for the badge. */
export const getMcpOAuthStatus = async (id: string): Promise<McpOAuthStatus> => {
  const { data } = await http.get<McpOAuthStatus>(`/mcp-servers/${id}/oauth/status`);
  return data;
};

/** DELETE /mcp-servers/{id}/oauth → disconnect (revoke) the caller's grant. */
export const disconnectMcpOAuth = async (id: string): Promise<void> => {
  await http.delete(`/mcp-servers/${id}/oauth`);
};
```
`CreateMcpServerPayload` + `McpServer` gain `external_auth_mode?: "static" | "oauth"` (mirrors the schema field).

---

## 2. `McpServerDetailPage.tsx` — OAuth panel (WS-2)

Rendered **only** when `server.is_external && server.external_auth_mode === "oauth"`, above the Health panel.

**Existing (Phase 1/2):** error banner, Health panel, Tools/Settings tabs.

**Phase 4 additions:**
1. `useQuery(["mcp-oauth-status", serverId], () => getMcpOAuthStatus(serverId))` (enabled for OAuth servers only; `refetchInterval: 15000` like Health).
2. A **Connection** card with:
   - `status === "authorized"` → green **Connected** badge + `Scopes` + `Expires` (from `token_expires_at`) + a **Disconnect** button (`disconnectMcpOAuth` → invalidate the status query).
   - `status === "needs_auth" | "error"` → amber **Needs authorization** badge (+ `last_error` line when `error`) + an **Authorize** button.
3. **Authorize** → `startMcpOAuth(serverId)` then `window.location.href = data.authorization_url` (full-page redirect to upstream consent).
4. **Callback landing:** on mount, read `useSearchParams().get("oauth")`; `connected` → `toast.success("Connected.")` + invalidate `["mcp-oauth-status", id]` + `["mcp-server", id]`; `denied|invalid_state|error` → `toast.error(...)`. Strip the param after handling (`navigate(pathname, {replace:true})`).

---

## 3. `McpServersPage.tsx` register modal — OAuth toggle (WS-2)

When **External** is selected, show a checkbox **"Server requires OAuth 2.1 authorization"**. Checked → `external_auth_mode: "oauth"` in the create payload (and the credential/auth-config picker is hidden, since OAuth replaces static creds). Unchecked → `external_auth_mode: "static"` (the existing auth-config picker stays). Internal servers never see this toggle.

On successful register of an OAuth server, the toast reads "Registered — authorize on the server page to discover its tools" (discovery needs a token, so `discovered_tool_count` is `0` until the user authorizes — C9), and navigation still lands on the detail page where the Authorize button lives.

---

## 4. Test targets

**Vitest** (`McpServerDetailPage.test.tsx`, `McpServersPage.test.tsx`):
- OAuth panel renders **Authorize** for `status="needs_auth"` and **Connected + Disconnect** for `status="authorized"`; hidden for `external_auth_mode="static"`.
- `?oauth=connected` fires the success toast + invalidates the status query (mock `useSearchParams`).
- Register modal: checking the OAuth toggle sends `external_auth_mode:"oauth"` and hides the credential picker (assert on the `createMcpServer` mock payload).

**Playwright** (`studio/e2e/mcp-servers.spec.ts`, extended):
- Register an External + OAuth server → land on detail → assert the **Authorize** button is visible and `page.waitForResponse` on `POST …/oauth/authorize` returns an `authorization_url` (assert the redirect is *attempted* — do not follow the upstream consent, which is a third-party page; assert `window.location` was set / the network call fired). This is the UI-wiring proof (the actual upstream consent is out of the harness's control, same boundary the bash suites accept).
- **Save → reload → assert:** after a stubbed `?oauth=connected` callback, reload the detail page and assert the status badge reads **Connected** (status persisted server-side in `mcp_oauth_grants`).

---

## 5. What Studio does NOT get in Phase 4
- No in-app rendering of the upstream consent screen (that is the third-party AS's page).
- No per-scope selection UI (scopes come from server metadata; a scope-picker is a future enhancement — gap ledger).
- No `resources`/`prompts` tabs (WS-3, deferred to its own plan).

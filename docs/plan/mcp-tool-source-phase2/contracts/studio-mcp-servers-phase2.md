# Contract — Studio MCP Servers, Phase 2 additions

**No new registry-api endpoint and no new API-client method.** WS-A's health surfacing (FR-MCP-22) consumes fields that already exist on `GET /api/v1/mcp-servers/{id}` (`MCPServerResponse`) and are already typed in `studio/src/api/mcpServersApi.ts` (`McpServer.health_detail: McpServerHealthDetail`, `McpServer.status`, `McpServer.last_synced_at`, `McpServer.list_changed_supported`, `McpServer.identity_mode`). Phase 2 only changes the **detail page's rendering + refetch behavior** — no data-layer change.

This file documents the Studio-side contract so the Playwright/Vitest tasks have an exact target.

---

## 1. `McpServerDetailPage.tsx` — Health panel + auto-refresh (WS-A)

**Existing (Phase 1):** the detail page shows an error banner only when `server.status === "error"` (reads `health_detail.last_error`), plus `External`/`Internal` + `owner_team` badges. It does **not** show `last_success_at`, `consecutive_failures`, `last_synced_at`, or `list_changed_supported`.

**Phase 2 additions (render-only):**
1. A **Health** section on the detail page showing, from the already-fetched `server`:
   - a status pill (`connected` green / `error` red / `disconnected` grey) — reuse the `StatusBadge` component already defined on `McpServersPage.tsx` (extract it to a shared component or duplicate the tiny map; either is fine — no new API).
   - `Last successful check: {health_detail.last_success_at ? toLocaleString : "—"}`
   - `Consecutive failures: {health_detail.consecutive_failures ?? 0}`
   - `Last error: {health_detail.last_error ?? "—"}` (shown whenever non-null, not only in the red banner)
   - `Last discovery (sync): {last_synced_at ? toLocaleString : "—"}` (distinct from the health timestamp — see data-model.md C3: the health loop does NOT move `last_synced_at`).
   - `Change notifications: {list_changed_supported ? "subscribed" : "not supported"}` (FR-MCP-07 visibility).
   - `Identity: {identity_mode}` with, for `on_behalf_of`, a small "(on-behalf-of exchange pending — Decision 29)" note (WS-C C7 honesty).
2. **Auto-refresh:** the `useQuery(['mcp-server', id], () => getMcpServer(id))` gains `refetchInterval: 15000` (poll every 15s) so the periodic health-loop updates surface without a manual reload. This is the mechanism by which a server going `connected → error` (or recovering) becomes visible in the UI (FR-MCP-22 "surface in Studio").

**Save→reload→assert is unaffected** — the existing register→detail persistence journey (`mcp-servers.spec.ts` step 1) still holds; the health panel reads from the same fresh `GET`.

---

## 2. Test targets

**Vitest** (`studio/src/pages/McpServerDetailPage.test.tsx`, extend the existing file — do not delete existing cases):
- A `status:"error"` server with `health_detail:{last_error:"connection refused", consecutive_failures:4, last_success_at:"2026-07-25T10:00:00Z"}` renders the error pill, the failure count `4`, and the `last_error` text in the Health section (not only the banner).
- A `status:"connected"` server with `list_changed_supported:true` renders the "subscribed" change-notifications line and a green pill.
- An `identity_mode:"on_behalf_of"` server renders the "(on-behalf-of exchange pending — Decision 29)" note.
- (Query polling is asserted at the mock level: the mocked `getMcpServer` is called on mount; `refetchInterval` need not be time-advanced in Vitest — assert the option is set via a render that re-reads on invalidation, or simply that the fields render from the mocked payload. Keep it a render assertion, not a fake-timer test.)

**Playwright** (`studio/e2e/mcp-servers.spec.ts`, add one case to the existing describe — do not rewrite the file): after registering a server, open the detail page and assert the Health section renders the status pill + "Last successful check" row from the real `GET /api/v1/mcp-servers/{id}` (`page.waitForResponse`). Infra-gated like the existing discovered-tools assertion (if the stub isn't reachable, assert the Health section structure renders from whatever status the server has — `error` is a valid, assertable state).

---

## 3. What Studio does NOT get in Phase 2

- No manual "health check now" button (the loop is periodic; a manual re-probe would need a new endpoint — out of scope, ledgered).
- No `list_changed` subscription toggle (subscription is automatic when the server advertises the capability; `list_changed_supported` is read-only status, not a user control).
- No on-behalf-of runtime UI beyond the "pending — Decision 29" note (the mode is already selectable on the Phase-1 register form; it just doesn't function at call time yet).

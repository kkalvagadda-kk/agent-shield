import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// mcp-servers.spec.ts  (MCP-as-a-tool-source — Studio UI, Phases 12-14)
//
//   Proves the REAL browser journey the API-only bash suite (suite-84, kubectl
//   exec) structurally cannot test — the MCP Servers pages are React screens.
//
//   The journey (all through the actual UI, asserting wiring + persistence +
//   network calls):
//     1. /mcp-servers → Register Server modal → POST /mcp-servers/ (register +
//        synchronous discover) → redirect to the detail route.
//     2. save → reload → assert: reload the detail route and read the server back
//        from GET /mcp-servers/{id} (DoD #2 persistence round-trip) — the HARD
//        proof, independent of whether the upstream was reachable.
//     3. The server row is read back on the /mcp-servers list (GET list).
//     4. INFRA-GATED (needs the mcp-proxy + a reachable upstream MCP server): when
//        discovery actually returned tools, assert the Discovered Tools table
//        (FR-MCP-41), open the agent builder's Tools Picker and assert the
//        discovered tool carries its source-server badge (FR-MCP-42), bind it,
//        save, reload the agent and confirm the tool is still bound (2nd
//        persistence round-trip). When 0 tools were discovered (no proxy / the
//        placeholder URL is unreachable → status="error"), those steps SKIP —
//        the same "few warm pods" boundary the bash suites accept. The
//        badge + read-only-row rendering is covered unconditionally by Vitest
//        (ToolsPicker.test.tsx, ToolsPage.test.tsx).
//
//   Header-auth identity for REST cleanup is platform-admin's real Keycloak sub
//   (mirrors knowledge.spec / webhook-public-url.spec).
// ---------------------------------------------------------------------------

const TS = Date.now();
const ADMIN = {
  "X-User-Sub": "047fad5f-f38c-430a-bfba-6e4d9009314b",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

const SERVER_NAME = `e2e-mcp-${TS}`;
const AGENT_NAME = `e2e-mcp-agent-${TS}`;
// Upstream MCP server URL. Defaults to the in-cluster stub fixture started INSIDE
// the mcp-proxy pod on its localhost (`kubectl exec ... python3 fixtures/stub_mcp_server.py`);
// the proxy (single replica) reaches it at 127.0.0.1:9999, so discovery returns
// the stub's `echo`/`add` tools and the discover+bind tests run for real. Override
// with MCP_E2E_SERVER_URL. When nothing is listening there, registration still
// returns 201 (status="error") so the register→reload→persist proof stands, and
// the discover/bind tests SKIP (0 tools) — the accepted infra boundary.
const SERVER_URL =
  process.env.MCP_E2E_SERVER_URL || "http://127.0.0.1:9999/mcp";

test.describe("MCP servers — register → discover → bind (Studio UI)", () => {
  let api: APIRequestContext;
  let serverId = "";
  let oauthServerId = ""; // WS-2: the External+OAuth server registered by the OAuth test.
  // Discovery results captured by the persistence test, read by the infra-gated
  // discover + bind tests below.
  let discoveredToolCount = 0;
  let firstToolName = ""; // RAW upstream name / display_name (e.g. "echo")

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });
  });

  test.afterAll(async () => {
    if (api) {
      // Delete the agent first (a bound tool would 409-block the server delete).
      await api.delete(`/api/v1/agents/${AGENT_NAME}`).catch(() => {});
      if (serverId) await api.delete(`/api/v1/mcp-servers/${serverId}`).catch(() => {});
      if (oauthServerId) await api.delete(`/api/v1/mcp-servers/${oauthServerId}`).catch(() => {});
      await api.dispose().catch(() => {});
    }
  });

  // The DoD-critical journey. ALWAYS runs and must pass — no infra gate, no
  // skip. Proves the real UI path (register modal → POST → redirect) AND the
  // persistence round-trip (reload the detail route + list, read back from the
  // backend). Independent of whether the upstream MCP server was reachable.
  test("register an MCP server → redirect to detail → persists on reload", async ({ page }) => {
    test.setTimeout(120_000);

    // -----------------------------------------------------------------------
    // 1. Register a server through the modal.
    // -----------------------------------------------------------------------
    await page.goto("/mcp-servers");
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "MCP Servers" })).toBeVisible();

    await page.getByRole("button", { name: /Register Server/i }).click();
    await expect(page.getByRole("heading", { name: "Register MCP Server" })).toBeVisible();
    await page.locator("#mcp-name").fill(SERVER_NAME);
    await page.locator("#mcp-url").fill(SERVER_URL);

    const createResp = page.waitForResponse(
      (r) => /\/api\/v1\/mcp-servers\/$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 30_000 }
    );
    await page.getByRole("button", { name: "Register", exact: true }).click();
    const created = await createResp;
    expect(created.status()).toBe(201);
    const createdBody = await created.json();
    serverId = createdBody.id as string;
    expect(serverId).toBeTruthy();

    // -----------------------------------------------------------------------
    // 2. Redirect to the detail route — the header shows the server name.
    // -----------------------------------------------------------------------
    await expect(page).toHaveURL(new RegExp(`/mcp-servers/${serverId}$`), { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: SERVER_NAME })).toBeVisible({ timeout: 15_000 });

    // -----------------------------------------------------------------------
    // 3. save → reload → assert survived: reload the detail route and confirm the
    //    server comes back from GET /mcp-servers/{id} (persistence round-trip).
    // -----------------------------------------------------------------------
    const reloadDetail = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/mcp-servers/${serverId}$`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page.goto(`/mcp-servers/${serverId}`);
    const reloaded = await reloadDetail;
    expect(reloaded.status()).toBe(200);
    const detail = await reloaded.json();
    expect(detail.name).toBe(SERVER_NAME);
    await expect(page.getByRole("heading", { name: SERVER_NAME })).toBeVisible({ timeout: 15_000 });

    // -----------------------------------------------------------------------
    // 4. The server is read back on the list too.
    // -----------------------------------------------------------------------
    const listResp = page.waitForResponse(
      (r) => /\/api\/v1\/mcp-servers\/(\?|$)/.test(r.url()) && r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page.goto("/mcp-servers");
    await listResp;
    await expect(page.getByText(SERVER_NAME)).toBeVisible({ timeout: 15_000 });

    // Hand the discovery result to the infra-gated tests below.
    discoveredToolCount = (detail.tools?.length ?? 0) as number;
    firstToolName = discoveredToolCount > 0 ? detail.tools[0].mcp_tool_name : "";
  });

  // INFRA-GATED enrichment: the discovered-tools table (FR-MCP-41) only renders
  // when the upstream MCP server was reachable and returned tools. When 0 tools
  // were discovered (no proxy / placeholder URL unreachable → status="error"),
  // this SKIPS — the same "few warm pods" boundary the bash suites accept. The
  // badge + read-only-row rendering is covered unconditionally by Vitest
  // (ToolsPicker.test.tsx, ToolsPage.test.tsx). Reported separately so the
  // persistence journey above always shows a clear pass, never a skip.
  test("discovered-tools table lists a tool row (infra-gated)", async ({ page }) => {
    test.skip(
      discoveredToolCount === 0,
      "no tools discovered (no mcp-proxy / unreachable upstream) — tool-table step is infra-gated; badge/read-only rendering is covered by Vitest"
    );
    expect(serverId).toBeTruthy();

    await page.goto(`/mcp-servers/${serverId}`);
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(firstToolName, { exact: false }).first()).toBeVisible({
      timeout: 15_000,
    });
  });

  // INFRA-GATED — the 2nd persistence round trip (FR-MCP-42): bind a discovered
  // mcp_tool to a NEW agent through the builder's Tools Picker, save, then reload
  // the agent and confirm the tool is STILL bound. Also asserts the tool carries
  // its source-server badge in the picker. Skips when discovery returned 0 tools.
  test("bind a discovered mcp_tool to an agent → save → reload → still bound", async ({ page }) => {
    test.setTimeout(120_000);
    test.skip(
      discoveredToolCount === 0,
      "no tools discovered (no mcp-proxy / unreachable upstream) — bind round-trip is infra-gated; picker badge is covered by Vitest (ToolsPicker.test.tsx)"
    );
    expect(firstToolName).toBeTruthy();

    // ── Create a no-code agent and bind the discovered tool via the Tools Picker.
    await page.goto("/agents/new");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: /No-code/i }).click();
    await page.waitForLoadState("domcontentloaded");
    await page.getByPlaceholder("my-agent").fill(AGENT_NAME);

    const picker = page.getByTestId("tools-picker");
    await expect(picker).toBeVisible({ timeout: 15_000 });
    // The picker labels a tool by its display_name (the RAW upstream name, e.g.
    // "echo") and renders the source-server badge (mcp_server_name == SERVER_NAME,
    // FR-MCP-42). Target the row by BOTH so it's unique among this run's tools —
    // the badge disambiguates from any same-named native tool, the display name
    // disambiguates from the server's other discovered tool.
    const toolRow = picker
      .locator("label")
      .filter({ hasText: SERVER_NAME })
      .filter({ hasText: firstToolName });
    await expect(toolRow).toBeVisible({ timeout: 15_000 });
    await expect(toolRow).toContainText(SERVER_NAME); // source-server badge
    // Check the box to bind it.
    const toolCheckbox = toolRow.locator('input[type="checkbox"]');
    await toolCheckbox.check();
    await expect(toolCheckbox).toBeChecked();

    const createResp = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 30_000 }
    );
    await page.getByRole("button", { name: /^Create Agent$/i }).click();
    expect((await createResp).status()).toBe(201);

    // ── save → reload → assert survived: the agent's Settings tab pre-selects the
    //    bound mcp_tool (persistence round-trip through the backend).
    await page.goto(`/agents/${AGENT_NAME}`);
    await page.getByRole("button", { name: "settings" }).click();
    const reloadedRow = page
      .getByTestId("tools-picker")
      .locator("label")
      .filter({ hasText: SERVER_NAME })
      .filter({ hasText: firstToolName });
    await expect(reloadedRow.locator('input[type="checkbox"]')).toBeChecked({
      timeout: 15_000,
    });
  });

  // T039 (Phase 2 / WS-A) — the Health panel renders on the detail page. It reads ONLY
  // fields the detail page has fetched since Phase 1 (status, list_changed_supported,
  // identity_mode), so this runs even before the health LOOP is deployed — it proves the
  // WS-A surface (FR-MCP-22 "surface in Studio") is wired, not that a probe ran. NOT
  // infra-gated: registration always returns a persisted server, and every field the
  // panel reads is present regardless of upstream reachability. Reuses the server the
  // persistence journey registered above; registers one via REST as a fallback so this
  // case stands alone if that test did not run.
  test("detail page renders the WS-A Health panel (status + change-notifications + identity)", async ({ page }) => {
    test.setTimeout(60_000);

    if (!serverId) {
      const reg = await api.post("/api/v1/mcp-servers/", {
        data: {
          name: `${SERVER_NAME}-health`,
          description: "e2e health-panel server",
          server_url: SERVER_URL,
          transport: "streamable_http",
          owner_team: "platform",
          is_external: false,
          identity_mode: "none",
          scan_results: true,
        },
      });
      expect(reg.status()).toBe(201);
      serverId = ((await reg.json()).id as string);
    }
    expect(serverId).toBeTruthy();

    // Reload the detail route and wait for the backend GET (the panel is driven by it).
    const detailResp = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/mcp-servers/${serverId}$`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page.goto(`/mcp-servers/${serverId}`);
    await detailResp;

    // The Health card header (WS-A section).
    await expect(page.getByRole("heading", { name: "Health" })).toBeVisible({ timeout: 15_000 });

    // Scope every assertion to the Health card so the error banner / tabs can't satisfy them.
    const health = page.locator(".card").filter({ hasText: "Health" });

    // Status pill — StatusBadge renders one of Connected / Error / Disconnected from server.status.
    await expect(health.getByText(/Connected|Error|Disconnected/).first()).toBeVisible();

    // The list_changed (WS-B capability) row.
    await expect(health.getByText("Change notifications")).toBeVisible();
    await expect(health.getByText(/subscribed|not supported/).first()).toBeVisible();

    // The identity (WS-C) line — always one of the three modes.
    await expect(health.getByText("Identity", { exact: true })).toBeVisible();
    await expect(
      health.getByText(/none|service_identity|on_behalf_of/).first()
    ).toBeVisible();
  });

  // T016 (Phase 4 / WS-2) — the OAuth authorize journey. Registers an External +
  // OAuth server through the REAL modal (real POST, real persist), then drives the
  // OAuth panel: Authorize fires POST …/oauth/authorize and the browser ATTEMPTS the
  // redirect to the returned authorization_url. The upstream AS + token exchange are
  // out of the harness's control (same boundary the bash suites accept), so the
  // authorize/status endpoints are stubbed deterministically (the contract sanctions
  // "stub the status to authorized"). Save→reload→assert: after the ?oauth=connected
  // callback landing, reload the detail page and assert the Connected badge.
  test("register External+OAuth → Authorize attempts the redirect → callback lands Connected", async ({ page }) => {
    test.setTimeout(120_000);
    const OAUTH_NAME = `e2e-mcp-oauth-${TS}`;

    // ── Deterministic OAuth surface (the AS is unreachable here). status starts
    //    needs_auth, flips to authorized once the callback is simulated.
    let authorized = false;
    const AUTH_URL = "https://as.e2e.invalid/authorize?state=e2e";
    // Stub the third-party consent page so the real redirect doesn't hit DNS.
    await page.route("**/as.e2e.invalid/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<html><body>stub consent</body></html>" })
    );
    await page.route("**/api/v1/mcp-servers/*/oauth/authorize", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ authorization_url: AUTH_URL }),
      })
    );
    await page.route("**/api/v1/mcp-servers/*/oauth/status", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          server_id: oauthServerId || "pending",
          user_sub: "e2e",
          status: authorized ? "authorized" : "needs_auth",
          scopes: authorized ? "repo read:user" : null,
          token_expires_at: null,
          last_error: null,
          external_auth_mode: "oauth",
        }),
      })
    );

    // ── 1. Register an External + OAuth server through the modal (real POST).
    await page.goto("/mcp-servers");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: /Register Server/i }).click();
    await expect(page.getByRole("heading", { name: "Register MCP Server" })).toBeVisible();
    await page.locator("#mcp-name").fill(OAUTH_NAME);
    await page.locator("#mcp-url").fill(SERVER_URL);
    await page.getByRole("radio", { name: "External" }).check();
    // The OAuth toggle appears for external servers; checking it hides the cred picker.
    await page.getByLabel(/OAuth 2.1 authorization/i).check();
    await expect(page.getByLabel("Credential")).toHaveCount(0);

    const createResp = page.waitForResponse(
      (r) => /\/api\/v1\/mcp-servers\/$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 30_000 }
    );
    await page.getByRole("button", { name: "Register", exact: true }).click();
    const created = await createResp;
    expect(created.status()).toBe(201);
    const createdBody = await created.json();
    oauthServerId = createdBody.id as string;
    expect(oauthServerId).toBeTruthy();
    expect(createdBody.external_auth_mode).toBe("oauth");

    // ── 2. Land on detail → the OAuth Connection panel shows Authorize (needs_auth).
    await expect(page).toHaveURL(new RegExp(`/mcp-servers/${oauthServerId}$`), { timeout: 15_000 });
    const oauthCard = page.locator(".card").filter({ hasText: "OAuth Connection" });
    await expect(oauthCard).toBeVisible({ timeout: 15_000 });
    const authorizeBtn = oauthCard.getByRole("button", { name: /^authorize$/i });
    await expect(authorizeBtn).toBeVisible();

    // ── 3. Authorize → POST …/oauth/authorize returns an authorization_url, and the
    //       browser ATTEMPTS the redirect (window.location.href = authorization_url).
    //       We assert the network call fired + the URL; we do NOT follow the consent.
    const authResp = page.waitForResponse(
      (r) => /\/oauth\/authorize$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 }
    );
    await authorizeBtn.click();
    const auth = await authResp;
    expect(auth.status()).toBe(200);
    expect((await auth.json()).authorization_url).toBe(AUTH_URL);
    // The redirect was attempted — the browser is now on the stubbed consent page.
    await expect(page).toHaveURL(/as\.e2e\.invalid/, { timeout: 15_000 });

    // ── 4. Simulate the callback landing + save→reload→assert: flip the stub to
    //       authorized, land on ?oauth=connected, then RELOAD and assert Connected.
    authorized = true;
    await page.goto(`/mcp-servers/${oauthServerId}?oauth=connected`);
    // The param is stripped after handling (no re-toast on reload).
    await expect(page).toHaveURL(new RegExp(`/mcp-servers/${oauthServerId}$`), { timeout: 15_000 });
    await page.goto(`/mcp-servers/${oauthServerId}`);
    const reloadedCard = page.locator(".card").filter({ hasText: "OAuth Connection" });
    await expect(reloadedCard.getByText("Connected")).toBeVisible({ timeout: 15_000 });
  });
});

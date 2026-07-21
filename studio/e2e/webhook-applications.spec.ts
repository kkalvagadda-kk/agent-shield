import { test, expect, type Page, type APIRequestContext, request as pwRequest } from "@playwright/test";

// ---------------------------------------------------------------------------
// webhook-applications.spec.ts  (Decision 30, Phases 6-8 — T031)
//
// The real-browser proof of the invoke-access surface that replaced the retired
// per-trigger ClientPanel (webhook-clients.spec.ts). Drives the actual UI through
// the real https gateway — NO page.route stubs, every assertion rides a real
// request to registry-api:
//
//   1. /applications  — create a team application, see its whsec_ secret exactly
//      once, confirm it's listed afterwards WITHOUT a secret.
//   2. agent Settings — grant that application `invoker` via InvokeAccessPanel;
//      the grant appears, the trigger's auth_mode badge flips to client_signed.
//   3. save → reload → assert (DoD #2): the grant survives a real reload and the
//      secret is NEVER re-displayed (ApplicationResponse has no secret field).
//
// The fixture agent + its webhook trigger are created here via the API (born
// auth_mode=token), never scavenged — a scavenged trigger already at
// client_signed would make the auth_mode-flip assertion vacuous.
// ---------------------------------------------------------------------------

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://agentshield.127.0.0.1.nip.io:8443";

// Trigger + grant CRUD are hard-authenticated (require_user, T012/T013) — an X-User-Sub
// dev header is NOT a bearer JWT and 401s. Fetch a real platform-admin token via the
// password grant (the gateway proxies /realms → Keycloak) for the fixture API context.
async function bearer(): Promise<string> {
  const ctx = await pwRequest.newContext({ baseURL: BASE_URL, ignoreHTTPSErrors: true });
  const r = await ctx.post("/realms/agentshield/protocol/openid-connect/token", {
    form: {
      grant_type: "password",
      client_id: "agentshield-studio",
      username: process.env.STUDIO_E2E_USER || "platform-admin",
      password: process.env.STUDIO_E2E_PASSWORD || "PlatformAdmin2024",
    },
  });
  expect(r.ok(), `token: ${r.status()} ${await r.text()}`).toBeTruthy();
  const tok = (await r.json()).access_token as string;
  await ctx.dispose();
  return tok;
}
const STAMP = Date.now().toString().slice(-7);
const AGENT = `whapp-${STAMP}`;
const APP_NAME = `billing-${STAMP}`;

let api: APIRequestContext;
let agentId: string;

/** The detail page keeps its active tab in local state, so a reload lands back on
 *  "deployments" — every post-reload assertion re-opens Settings and waits for the
 *  artifact-grants read that backs ArtifactGrantsList + InvokeAccessPanel. */
async function openSettings(page: Page) {
  const grantsLoaded = page.waitForResponse(
    (r) => /\/api\/v1\/artifacts\/agent\/[^/]+\/grants$/.test(r.url()) && r.request().method() === "GET",
    { timeout: 20_000 },
  );
  await page.locator("main nav").getByRole("button", { name: "settings" }).click();
  await grantsLoaded.catch(() => {});
}

test.beforeAll(async () => {
  const token = await bearer();
  api = await pwRequest.newContext({
    baseURL: BASE_URL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { Authorization: `Bearer ${token}`, "X-User-Team": "platform" },
  });

  const created = await api.post("/api/v1/agents/", {
    data: {
      name: AGENT,
      team: "platform",
      agent_type: "declarative",
      execution_shape: "reactive",
      agent_class: "daemon",
      metadata: { instructions: "invoke-access panel fixture", tools: [] },
    },
  });
  expect(created.ok(), `create fixture agent: ${created.status()} ${await created.text()}`).toBeTruthy();
  agentId = (await created.json()).id;

  const trig = await api.post(`/api/v1/agents/${AGENT}/triggers`, { data: { trigger_type: "webhook" } });
  expect(trig.ok(), `create fixture trigger: ${trig.status()} ${await trig.text()}`).toBeTruthy();
  expect((await trig.json()).auth_mode, "a new webhook trigger is born token-mode").toBe("token");
});

test.afterAll(async () => {
  await api.delete(`/api/v1/agents/${AGENT}`).catch(() => {});
  await api.dispose();
});

test.describe("webhook applications — invoke access", () => {
  let appId = "";

  test("create application → secret shown once → listed without secret", async ({ page }) => {
    await page.goto("/applications");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /new application/i }).first().click();
    await page.getByLabel(/application name/i).fill(APP_NAME);

    const created = page.waitForResponse(
      (r) => /\/api\/v1\/teams\/[^/]+\/applications\/?$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: /^create$/i }).click();
    const resp = await created;
    expect(resp.status(), `create application: ${await resp.text()}`).toBe(201);
    appId = (await resp.json()).id;

    // Secret revealed exactly once.
    const secretEl = page.getByTestId("application-secret");
    await expect(secretEl).toBeVisible();
    const secret = (await secretEl.textContent())?.trim() ?? "";
    expect(secret, "the 201 must surface a whsec_ secret").toMatch(/^whsec_.+/);

    // The read model (list) must never carry it.
    const listed = await api.get(`/api/v1/teams/platform/applications`);
    expect(await listed.text(), "the list read model must have no secret field").not.toContain(secret);

    // Dismiss the reveal, then it must not come back on a reload.
    await page.getByRole("button", { name: /dismiss secret/i }).click();
    await expect(secretEl).toBeHidden();

    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByTestId(`application-row-${APP_NAME}`), "the app survives a reload").toBeVisible();
    await expect(page.getByTestId("application-secret"), "the secret never comes back").toHaveCount(0);
  });

  test("grant invoker in agent Settings → auth_mode flips → survives reload", async ({ page }) => {
    // This test depends on the application created above (workers:1, serial).
    await page.goto(`/agents/${AGENT}`);
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    // The webhook trigger is born token-mode; the panel offers to grant access.
    await expect(page.getByText("token").first()).toBeVisible();
    const panel = page.getByRole("main");

    await panel.getByRole("button", { name: /grant access/i }).first().click();
    await page.getByLabel(/application to grant invoke access/i).selectOption({ label: APP_NAME });
    // The unattended-execution acknowledgment must show before the grant confirms.
    await expect(page.getByTestId("invoke-ack")).toContainText(/without a human present/i);

    const granted = page.waitForResponse(
      (r) => /\/api\/v1\/artifacts\/agent\/[^/]+\/grants$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: /^grant access$/i }).click();
    const grantResp = await granted;
    expect(grantResp.status(), `grant invoker: ${await grantResp.text()}`).toBe(201);

    // The grant row appears, and the trigger's auth_mode badge flips to client_signed.
    await expect(page.getByTestId(`invoker-grant-${appId}`)).toContainText(APP_NAME);
    await expect(page.getByText("client_signed").first()).toBeVisible();

    // --- save → reload → assert survived (DoD #2) ---------------------------
    await page.reload();
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    await expect(
      page.getByTestId(`invoker-grant-${appId}`),
      "the invoker grant must survive a reload",
    ).toContainText(APP_NAME);
    await expect(page.getByText("client_signed").first()).toBeVisible();
    await expect(
      page.getByTestId("application-secret"),
      "no secret is ever shown on the agent surface",
    ).toHaveCount(0);
  });
});

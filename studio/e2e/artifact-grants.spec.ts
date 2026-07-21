import { test, expect, type Page, type APIRequestContext, request as pwRequest } from "@playwright/test";

// ---------------------------------------------------------------------------
// artifact-grants.spec.ts  (Decision 25/30, Phase 7 — T030)
//
// Real-browser coverage of ArtifactGrantsList (studio/src/components/shared) on the
// agent Settings surface: it lists EVERY active grant on an artifact across all three
// roles (agent-admin / approver / invoker) and grantee types (user / team / application),
// and revokes one — with save → reload → assert that the revoke persisted.
//
// The grants are seeded via the real API (mixed roles), then the UI is driven against
// the real https gateway — NO route stubs.
//
// ArtifactGrantsList now CREATES human-grantee grants too (agent-admin/approver to a
// user/team, via its grant form) as well as listing + revoking them. The
// application/invoker path stays in InvokeAccessPanel (see webhook-applications.spec.ts).
// This spec proves: (1) mixed-role render + revoke + persistence, and (2) create a human
// grant through the form + persistence.
// ---------------------------------------------------------------------------

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://agentshield.127.0.0.1.nip.io:8443";

// Grant CRUD is hard-authenticated (require_user) — X-User-Sub alone 401s. Fetch a real
// platform-admin bearer via the password grant (gateway proxies /realms → Keycloak).
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
const AGENT = `grants-${STAMP}`;        // seeded with mixed grants — render + revoke
const AGENT2 = `grantsnew-${STAMP}`;    // fresh — create-a-grant-via-the-form

let api: APIRequestContext;
let agentId: string;
let adminGrantId = "";

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
      metadata: { instructions: "artifact grants list fixture", tools: [] },
    },
  });
  expect(created.ok(), `create fixture agent: ${created.status()} ${await created.text()}`).toBeTruthy();
  agentId = (await created.json()).id;

  // The creator is AUTO-granted agent-admin on a new agent (grant_creator_admin), so
  // seeding another agent-admin for the same user 409s. Use that auto grant as the
  // agent-admin row this test renders + revokes, and add an approver grant so the list
  // shows a SECOND role.
  const listResp = await api.get(`/api/v1/artifacts/agent/${agentId}/grants`);
  const grants = (await listResp.json()) as Array<{ id: string; role: string }>;
  const auto = grants.find((g) => g.role === "agent-admin");
  expect(auto, `creator should be auto-granted agent-admin; got ${JSON.stringify(grants)}`).toBeTruthy();
  adminGrantId = auto!.id;

  const approver = await api.post(`/api/v1/artifacts/agent/${agentId}/grants`, {
    data: { grantee_type: "team", grantee_id: "platform", role: "approver" },
  });
  expect(approver.ok(), `seed approver grant: ${approver.status()} ${await approver.text()}`).toBeTruthy();

  // A second, fresh agent for the create-via-form test (only the creator's auto
  // agent-admin grant — no seeded approver, so the form-created one can't collide).
  const created2 = await api.post("/api/v1/agents/", {
    data: {
      name: AGENT2,
      team: "platform",
      agent_type: "declarative",
      execution_shape: "reactive",
      agent_class: "daemon",
      metadata: { instructions: "artifact grants create fixture", tools: [] },
    },
  });
  expect(created2.ok(), `create fixture agent2: ${created2.status()} ${await created2.text()}`).toBeTruthy();
});

test.afterAll(async () => {
  await api.delete(`/api/v1/agents/${AGENT}`).catch(() => {});
  await api.delete(`/api/v1/agents/${AGENT2}`).catch(() => {});
  await api.dispose();
});

test.describe("artifact grants list", () => {
  test("renders mixed-role grants; revoke one → survives reload", async ({ page }) => {
    await page.goto(`/agents/${AGENT}`);
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    const list = page.getByRole("main");
    // Both seeded roles render (ArtifactGrantsList shows role badges).
    await expect(list.getByText("agent-admin").first()).toBeVisible();
    await expect(list.getByText("approver").first()).toBeVisible();

    // --- revoke the agent-admin grant via the UI ----------------------------
    const revoked = page.waitForResponse(
      (r) => /\/api\/v1\/artifacts\/agent\/[^/]+\/grants\/[^/]+$/.test(r.url()) && r.request().method() === "DELETE",
      { timeout: 20_000 },
    );
    await page.getByTestId(`grant-row-${adminGrantId}`).getByRole("button", { name: /revoke agent-admin/i }).click();
    const revResp = await revoked;
    expect(revResp.status(), `revoke grant: ${await revResp.text()}`).toBeGreaterThanOrEqual(200);

    await expect(page.getByTestId(`grant-row-${adminGrantId}`), "revoked row disappears").toHaveCount(0);

    // --- save → reload → assert survived (DoD #2) ---------------------------
    await page.reload();
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    await expect(
      page.getByTestId(`grant-row-${adminGrantId}`),
      "the revoked grant stays gone after a reload (persisted, not just local state)",
    ).toHaveCount(0);
    // The approver grant that was never revoked is still there.
    await expect(page.getByRole("main").getByText("approver").first()).toBeVisible();
  });

  test("create a human grant via the form (approver → team) → survives reload", async ({ page }) => {
    await page.goto(`/agents/${AGENT2}`);
    await page.waitForLoadState("networkidle");
    await openSettings(page);

    const main = page.getByRole("main");
    // Open the grant form (the header "Grant" hides once open).
    await main.getByRole("button", { name: /^grant$/i }).click();
    await page.getByLabel(/role to grant/i).selectOption("approver");
    await page.getByLabel(/grantee type/i).selectOption("team");
    // The team picker loads via listTeams; "platform" is the seeded team.
    await page.getByLabel(/team to grant/i).selectOption("platform");

    const created = page.waitForResponse(
      (r) => /\/api\/v1\/artifacts\/agent\/[^/]+\/grants$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: /^grant$/i }).click();
    const resp = await created;
    expect(resp.status(), `create grant: ${await resp.text()}`).toBe(201);

    // The new approver/team row renders.
    await expect(main.getByText("approver").first()).toBeVisible();
    await expect(main.getByText("team").first()).toBeVisible();

    // --- save → reload → assert survived (DoD #2) ---------------------------
    await page.reload();
    await page.waitForLoadState("networkidle");
    await openSettings(page);
    await expect(
      page.getByRole("main").getByText("approver").first(),
      "the form-created grant must survive a reload (persisted)",
    ).toBeVisible();
  });
});

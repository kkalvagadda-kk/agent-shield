import { test, expect, type Browser } from "@playwright/test";
import { pickModel } from "./lib/agents";
import { deployToSandbox } from "./lib/agents";

// ---------------------------------------------------------------------------
// deployment-overview.spec.ts
//   Proves the Level-2 → Level-3 journey of
//   unified-artifact-deployment-navigation:
//     artifact page → Sandbox Deployments list → click a deployment →
//     Deployment Overview (deployment-scoped) → survives a reload.
//
//   Agent runs may not complete (few agent pods deployed) — same boundary the
//   other UI specs accept. We assert wiring + persistence, not execution.
// ---------------------------------------------------------------------------

const TS = Date.now();
const AGENT = `e2e-depovw-${TS}`;

async function createAgentViaUI(browser: Browser, agentName: string) {
  const ctx = await browser.newContext({ storageState: "e2e/.auth/state.json" });
  const page = await ctx.newPage();
  try {
    await page.goto("/agents/new");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: /No-code/i }).click();
    await page.waitForLoadState("domcontentloaded");
    await page.getByPlaceholder("my-agent").fill(agentName);
    await pickModel(page);  // llm_provider_id is REQUIRED since studio 0.1.178 — see lib/agents.ts
    const createDone = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create Agent/i }).click();
    await createDone;
    // The 201 above IS the proof of creation. The wizard navigates to the agent LIST
    // (/agents, CreateAgentPage.tsx:785), NOT /agents/{name}, so waiting for the detail
    // URL here hung for 15s and failed a beforeAll — taking every test in the file with
    // it. catalog-overview-parity.spec.ts already documented this exact drift and named
    // these specs as still carrying it: "a navigation is a side effect of creation, not
    // proof of it; the 201 is the proof."
  } finally {
    await ctx.close();
  }
}

async function deleteAgentViaUI(browser: Browser, agentName: string) {
  const ctx = await browser.newContext({ storageState: "e2e/.auth/state.json" });
  const page = await ctx.newPage();
  try {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const deleteBtn = page
      .locator("tr", { hasText: agentName })
      .getByRole("button", { name: /Delete/i });
    if ((await deleteBtn.count()) === 0) return;
    page.once("dialog", (d) => d.accept());
    await deleteBtn.click();
    await page.waitForLoadState("networkidle");
  } finally {
    await ctx.close();
  }
}

test.beforeAll(async ({ browser }) => {
  await createAgentViaUI(browser, AGENT);
});

test.afterAll(async ({ browser }) => {
  await deleteAgentViaUI(browser, AGENT);
});

test("deploy → open deployment overview → reload survives", async ({ page }) => {
  // 1. Deploy a sandbox deployment through the DeployModal on the detail page.
  //
  // This used to goto(`/agents/${AGENT}/deploy`). That ROUTE WAS DELETED — App.tsx:83 says
  // so in a comment: "/agents/:name/deploy removed — deploy is now a modal on
  // AgentDetailPage". The spec kept navigating to it, landed on a page with no Deploy
  // button, and timed out clicking one. e2e/lib/agents.ts already exports the correct
  // modal-driven helper ("Deploy an agent to sandbox through the DeployModal on the detail
  // page"); this spec hand-rolled the stale version instead of importing it — the same
  // duplication that hid the required-model change from six specs.
  const depId = await deployToSandbox(page, AGENT);

  // The overview link is keyed by deployment NAME, which the helper does not return.
  const listResp = await page.request.get(`/api/v1/agents/${AGENT}/deployments`);
  expect(listResp.ok(), `list deployments: ${listResp.status()}`).toBeTruthy();
  const deployments = (await listResp.json()) as { id: string; name: string }[];
  const depName = (deployments.find((d) => d.id === depId) ?? deployments[0]).name;
  expect(depName).toContain(`${AGENT}-`);

  // 2. Artifact page → Deployments tab (default) lists the deployment by name.
  await page.goto(`/agents/${AGENT}`);
  await page.waitForLoadState("networkidle");
  const depLink = page.locator("main a", { hasText: depName });
  await expect(depLink).toBeVisible({ timeout: 10_000 });

  // 3. Click the deployment → Level-3 Deployment Overview.
  await depLink.click();
  await page.waitForURL("**/d/**", { timeout: 10_000 });

  // Deployment name is the primary identifier (the H1 title).
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(depName);
  // Overview tab (reactive) renders the API Endpoint card.
  await expect(page.getByText("API Endpoint")).toBeVisible();
  // Agent name shown as secondary metadata.
  await expect(page.getByText(`agent: ${AGENT}`)).toBeVisible();

  // 4. Reload → the deployment overview survives (data came from the backend,
  //    not transient store state).
  await page.reload();
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(depName);
  await expect(page).toHaveURL(/\/d\//);
});

test("deployment overview runs + memory tabs render deployment-scoped", async ({
  page,
}) => {
  await page.goto(`/agents/${AGENT}`);
  await page.waitForLoadState("networkidle");
  const depLink = page.locator("main a", { hasText: `${AGENT}-` }).first();
  await depLink.click();
  await page.waitForURL("**/d/**", { timeout: 10_000 });

  const tabNav = page.locator("main nav");
  // Runs tab shows the trigger/status filter selects.
  await tabNav.getByRole("button", { name: "runs" }).click();
  await expect(page.locator("main select").first()).toBeVisible({ timeout: 10_000 });

  // Memory tab renders (agent-scoped for now — per-deployment in a later slice).
  await tabNav.getByRole("button", { name: "memory" }).click();
  await expect(page.getByText("Conversation Memory")).toBeVisible();
});

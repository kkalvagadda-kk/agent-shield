import { test, expect, type Browser } from "@playwright/test";

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
    const createDone = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create Agent/i }).click();
    await createDone;
    await page.waitForURL(`**/agents/${agentName}`, { timeout: 15_000 });
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
  // 1. Deploy a sandbox deployment via the deploy page.
  await page.goto(`/agents/${AGENT}/deploy`);
  await page.waitForLoadState("networkidle");

  const deployDone = page.waitForResponse(
    (r) =>
      r.url().includes(`/api/v1/agents/${AGENT}/deploy`) &&
      r.request().method() === "POST",
    { timeout: 30_000 }
  );
  await page.getByRole("button", { name: /^Deploy$/ }).click();
  const deployResp = await deployDone;
  expect(deployResp.status()).toBe(201);
  const depName: string = (await deployResp.json()).name;
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

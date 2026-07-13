import { test, expect, type Browser } from "@playwright/test";

// ---------------------------------------------------------------------------
// agent-detail-modes.spec.ts
//   The artifact page is Level 2 (unified-artifact-deployment-navigation):
//   Deployments + Versions + Settings. Runtime state (overview/runs/memory)
//   lives on the Level-3 Deployment Overview page, not here.
//
//   Strategy: create one reactive and one durable test agent in beforeAll,
//   assert the artifact-page tabs, delete both in afterAll.
// ---------------------------------------------------------------------------

const TS = Date.now();
const REACTIVE_AGENT = `e2e-react-${TS}`;
const DURABLE_AGENT = `e2e-dura-${TS}`;

async function createAgentViaUI(
  browser: Browser,
  agentName: string,
  executionShape: "reactive" | "durable"
) {
  const ctx = await browser.newContext({ storageState: "e2e/.auth/state.json" });
  const page = await ctx.newPage();
  try {
    await page.goto("/agents/new");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /No-code/i }).click();
    await page.waitForLoadState("domcontentloaded");

    await page.getByPlaceholder("my-agent").fill(agentName);

    if (executionShape === "durable") {
      await page.getByRole("radio", { name: /Durable/i }).click();
    }

    const createDone = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create Agent/i }).click();
    await createDone;
    // The wizard navigates to the agents list (not the detail page) after create;
    // just wait until we've left the /new wizard route — the 201 already confirms creation.
    await page.waitForURL((u) => !u.pathname.endsWith("/new"), { timeout: 15_000 });
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
    const agentRow = page.locator("tr", { hasText: agentName });
    const deleteBtn = agentRow.getByRole("button", { name: /Delete/i });
    if ((await deleteBtn.count()) === 0) return; // already gone
    page.once("dialog", (d) => d.accept());
    await deleteBtn.click();
    await page.waitForLoadState("networkidle");
  } finally {
    await ctx.close();
  }
}

test.beforeAll(async ({ browser }) => {
  await createAgentViaUI(browser, REACTIVE_AGENT, "reactive");
  await createAgentViaUI(browser, DURABLE_AGENT, "durable");
});

test.afterAll(async ({ browser }) => {
  await deleteAgentViaUI(browser, REACTIVE_AGENT);
  await deleteAgentViaUI(browser, DURABLE_AGENT);
});

test.describe("artifact page (Level 2)", () => {
  test("shows execution-shape badge and Deployments/Versions/Settings tabs", async ({
    page,
  }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");

    await expect(page.getByText("Ephemeral")).toBeVisible();

    const tabNav = page.locator("main nav");
    await expect(tabNav.getByRole("button", { name: "deployments" })).toBeVisible();
    await expect(tabNav.getByRole("button", { name: "versions" })).toBeVisible();
    await expect(tabNav.getByRole("button", { name: "settings" })).toBeVisible();

    // Runtime tabs must NOT be on the artifact page — they moved to the
    // deployment overview.
    await expect(tabNav.getByRole("button", { name: "overview" })).toHaveCount(0);
    await expect(tabNav.getByRole("button", { name: "runs" })).toHaveCount(0);
  });

  test("deployments tab shows empty state before any deploy", async ({ page }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");
    // Deployments is the default tab.
    await expect(page.getByText(/No sandbox deployments yet/i)).toBeVisible();
  });

  test("versions tab shows the versions table", async ({ page }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");
    await page.locator("main nav").getByRole("button", { name: "versions" }).click();
    // A freshly-created agent has no versions (they come from the deploy/publish flow),
    // so the versions tab renders its empty state — assert that (the old "Agent Details"
    // heading was removed in an earlier restructure).
    await expect(page.getByText(/No versions yet/i)).toBeVisible();
  });

  test("settings tab shows trigger config sections", async ({ page }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");
    await page.locator("main nav").getByRole("button", { name: "settings" }).click();
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "Schedule Triggers" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Webhook Triggers" })).toBeVisible();
  });

  test("durable agent shows Durable badge on artifact page", async ({ page }) => {
    await page.goto(`/agents/${DURABLE_AGENT}`);
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Durable")).toBeVisible();
  });

  // Save → reload → assert (DoD #2): editing the Authority (class) in Settings PATCHes
  // agent_class and survives a reload from the backend (the update_agent orphan is wired).
  test("settings: change Authority class to daemon → save → reload → persisted", async ({ page }) => {
    await page.goto(`/agents/${DURABLE_AGENT}`);
    await page.waitForLoadState("networkidle");
    await page.locator("main nav").getByRole("button", { name: "settings" }).click();

    await page.getByLabel(/Authority/i).selectOption("daemon");
    const patchDone = page.waitForResponse(
      (r) =>
        r.request().method() === "PUT" &&
        new URL(r.url()).pathname.endsWith(`/agents/${DURABLE_AGENT}`),
      { timeout: 15_000 }
    );
    await page.getByRole("button", { name: /Save Changes/i }).click();
    await patchDone;

    await page.reload();
    await page.locator("main nav").getByRole("button", { name: "settings" }).click();
    await expect(page.getByLabel(/Authority/i)).toHaveValue("daemon");
  });
});

import { test, expect, type Browser } from "@playwright/test";

// ---------------------------------------------------------------------------
// agent-detail-modes.spec.ts
//   Proves the Overview tab renders the right component per execution_shape,
//   and that Settings + Memory tabs render for any agent.
//
//   Strategy: create one reactive and one durable test agent in beforeAll,
//   run assertions, delete both in afterAll.
// ---------------------------------------------------------------------------

const TS = Date.now();
const REACTIVE_AGENT = `e2e-react-${TS}`;
const DURABLE_AGENT = `e2e-dura-${TS}`;

// ---------------------------------------------------------------------------
// Helper: create an agent via the no-code form.
// Returns when the detail-page URL is reached.
// ---------------------------------------------------------------------------
async function createAgentViaUI(
  browser: Browser,
  agentName: string,
  executionShape: "reactive" | "durable"
) {
  const ctx = await browser.newContext({
    // Relative to cwd (studio/) — same path that playwright.config.ts uses.
    storageState: "e2e/.auth/state.json",
  });
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
    await page.waitForURL(`**/agents/${agentName}`, { timeout: 15_000 });
  } finally {
    await ctx.close();
  }
}

// ---------------------------------------------------------------------------
// Helper: delete an agent via the list's Delete button.
// ---------------------------------------------------------------------------
async function deleteAgentViaUI(browser: Browser, agentName: string) {
  const ctx = await browser.newContext({
    // Relative to cwd (studio/) — same path that playwright.config.ts uses.
    storageState: "e2e/.auth/state.json",
  });
  const page = await ctx.newPage();
  try {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const agentRow = page.locator("tr", { hasText: agentName });
    const deleteBtn = agentRow.getByRole("button", { name: /Delete/i });
    if ((await deleteBtn.count()) === 0) return; // already gone
    page.once("dialog", (d) => d.accept());
    await deleteBtn.click();
    // Brief wait to let the DELETE request fire
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

// ---------------------------------------------------------------------------
// Reactive agent
// ---------------------------------------------------------------------------
test.describe("reactive agent detail", () => {
  test("overview tab shows execution shape badge and API Endpoint card", async ({
    page,
  }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");

    // Execution shape badge
    await expect(page.getByText("Reactive")).toBeVisible();

    // OverviewReactive renders API Endpoint section with the agent's path
    await expect(page.getByText("API Endpoint")).toBeVisible();
    await expect(
      page.locator("code", { hasText: `/api/v1/agents/${REACTIVE_AGENT}/chat` })
    ).toBeVisible();
  });

  test("all five tabs switch and render", async ({ page }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");

    const tabNav = page.locator("main nav");

    // Verify each tab activates and reveals appropriate content
    await tabNav.getByRole("button", { name: "runs" }).click();
    // RunsTab shows filter selects once loaded
    await expect(page.locator("main select").first()).toBeVisible({
      timeout: 10_000,
    });

    await tabNav.getByRole("button", { name: "memory" }).click();
    await expect(page.getByText("Conversation Memory")).toBeVisible();

    await tabNav.getByRole("button", { name: "settings" }).click();
    // Use heading role selectors to avoid strict-mode violation with the
    // "No schedule triggers configured" paragraph which also contains the text.
    await expect(page.getByRole("heading", { name: "Schedule Triggers" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Webhook Triggers" })).toBeVisible();

    await tabNav.getByRole("button", { name: "versions" }).click();
    // VersionsContent shows "Agent Details" heading
    await expect(page.getByRole("heading", { name: "Agent Details" })).toBeVisible();

    // Back to overview — stays as reactive
    await tabNav.getByRole("button", { name: "overview" }).click();
    await expect(page.getByRole("heading", { name: "API Endpoint" })).toBeVisible();
  });

  test("memory tab shows Conversation Memory heading and no errors", async ({
    page,
  }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");

    const tabNav = page.locator("main nav");
    await tabNav.getByRole("button", { name: "memory" }).click();

    await expect(page.getByText("Conversation Memory")).toBeVisible();
    // Clear All button is present (may be disabled if no memory)
    await expect(page.getByRole("button", { name: /Clear All/i })).toBeVisible();
    // No memory stored for a fresh agent
    await expect(
      page.getByText(/No memory stored for this agent/i)
    ).toBeVisible();
  });

  test("settings tab shows trigger config sections with no triggers configured", async ({
    page,
  }) => {
    await page.goto(`/agents/${REACTIVE_AGENT}`);
    await page.waitForLoadState("networkidle");

    const tabNav = page.locator("main nav");
    await tabNav.getByRole("button", { name: "settings" }).click();
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "Schedule Triggers" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Webhook Triggers" })).toBeVisible();
    // No triggers configured for a freshly created agent
    await expect(
      page.getByText(/No schedule triggers configured/i)
    ).toBeVisible();
    await expect(
      page.getByText(/No webhook triggers configured/i)
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Durable agent
// ---------------------------------------------------------------------------
test.describe("durable agent detail", () => {
  test("overview tab shows Durable execution shape and Active Runs section", async ({
    page,
  }) => {
    await page.goto(`/agents/${DURABLE_AGENT}`);
    await page.waitForLoadState("networkidle");

    // Durable badge visible (execution shape badge in header)
    await expect(page.getByText("Durable")).toBeVisible();

    // OverviewDurable renders "Active Runs" heading
    await expect(page.getByRole("heading", { name: "Active Runs" })).toBeVisible();
  });

  test("memory and settings tabs render for durable agent", async ({ page }) => {
    await page.goto(`/agents/${DURABLE_AGENT}`);
    await page.waitForLoadState("networkidle");

    const tabNav = page.locator("main nav");

    await tabNav.getByRole("button", { name: "memory" }).click();
    await expect(page.getByText("Conversation Memory")).toBeVisible();

    await tabNav.getByRole("button", { name: "settings" }).click();
    await expect(page.getByRole("heading", { name: "Schedule Triggers" })).toBeVisible();
  });
});

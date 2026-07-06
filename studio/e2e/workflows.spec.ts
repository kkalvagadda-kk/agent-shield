import { test, expect, type Browser } from "@playwright/test";

// ---------------------------------------------------------------------------
// workflows.spec.ts  (HIGHEST VALUE — composite workflow feature)
//   1. /workflows list renders
//   2. WorkflowBuilderPage renders on /workflows/new
//   3. AddAgentModal opens and agents can be added to the canvas
//   4. Save → first-save modal → POST /api/v1/workflows fires and workflow
//      appears at /workflows
//   5. Run Workflow button opens the run panel
//
//   Strategy: create 2 test agents in beforeAll so the modal always has agents.
//   Both are created by platform-admin so they share the same team.
//   Workflows persist (no delete UI) — created agent stubs are deleted in afterAll.
// ---------------------------------------------------------------------------

const TS = Date.now();
const WF_AGENT_1 = `e2e-wfa1-${TS}`;
const WF_AGENT_2 = `e2e-wfa2-${TS}`;
const WORKFLOW_NAME = `e2e-workflow-${TS}`;

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------
async function createAgentViaUI(
  browser: Browser,
  agentName: string
): Promise<void> {
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

    const done = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create Agent/i }).click();
    await done;
    await page.waitForURL(`**/agents/${agentName}`, { timeout: 15_000 });
  } finally {
    await ctx.close();
  }
}

async function deleteAgentViaUI(
  browser: Browser,
  agentName: string
): Promise<void> {
  const ctx = await browser.newContext({
    // Relative to cwd (studio/) — same path that playwright.config.ts uses.
    storageState: "e2e/.auth/state.json",
  });
  const page = await ctx.newPage();
  try {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const row = page.locator("tr", { hasText: agentName });
    if ((await row.count()) === 0) return;
    page.once("dialog", (d) => d.accept());
    await row.getByRole("button", { name: /Delete/i }).click();
    await page.waitForLoadState("networkidle");
  } finally {
    await ctx.close();
  }
}

// ---------------------------------------------------------------------------
// Lifecycle hooks
// ---------------------------------------------------------------------------
test.beforeAll(async ({ browser }) => {
  await createAgentViaUI(browser, WF_AGENT_1);
  await createAgentViaUI(browser, WF_AGENT_2);
});

test.afterAll(async ({ browser }) => {
  await deleteAgentViaUI(browser, WF_AGENT_1);
  await deleteAgentViaUI(browser, WF_AGENT_2);
});

// ---------------------------------------------------------------------------
// Workflows list page
// ---------------------------------------------------------------------------
test.describe("workflows list", () => {
  test("renders heading and New Workflow button", async ({ page }) => {
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");

    await expect(
      page.getByRole("heading", { name: /^Workflows$/i })
    ).toBeVisible();
    // When the list is empty there are TWO "New Workflow" buttons (header + empty-state).
    // Use .first() which always targets the header toolbar button.
    await expect(
      page.getByRole("button", { name: /New Workflow/i }).first()
    ).toBeVisible();
  });

  test("renders table or empty state (no crash)", async ({ page }) => {
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");

    const tableCount = await page.locator("table").count();
    const emptyCount = await page.getByText(/No workflows yet/i).count();
    expect(tableCount + emptyCount).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Workflow builder
// ---------------------------------------------------------------------------
test.describe("workflow builder", () => {
  test("New Workflow navigates to builder with toolbar", async ({ page }) => {
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");

    // .first() because there may be two buttons (header + empty-state card)
    await page.getByRole("button", { name: /New Workflow/i }).first().click();
    await page.waitForURL("**/workflows/new", { timeout: 10_000 });
    await page.waitForLoadState("networkidle");

    // Breadcrumb shows "New Workflow"
    await expect(page.getByText("New Workflow")).toBeVisible();

    // Toolbar buttons
    await expect(
      page.getByRole("button", { name: /Add Existing Agent/i })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /Save/i })).toBeVisible();
  });

  test("Add Existing Agent opens modal with agent list", async ({ page }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /Add Existing Agent/i }).click();

    // Modal header
    await expect(
      page.getByRole("heading", { name: /Add Agent to Workflow/i })
    ).toBeVisible();

    // Search field inside modal
    await expect(page.getByPlaceholder(/Search agents/i)).toBeVisible();

    // Our pre-created test agents should appear
    await expect(page.getByText(WF_AGENT_1)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(WF_AGENT_2)).toBeVisible({ timeout: 10_000 });

    // Close modal
    await page.getByRole("button", { name: /Done/i }).click();
    await expect(
      page.getByRole("heading", { name: /Add Agent to Workflow/i })
    ).not.toBeVisible();
  });

  test("adding two agents creates two canvas nodes", async ({ page }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    // Open modal and add first agent
    await page.getByRole("button", { name: /Add Existing Agent/i }).click();
    await expect(page.getByText(WF_AGENT_1)).toBeVisible({ timeout: 10_000 });

    // Agent row structure in AddAgentModal:
    //   <div class="flex items-start justify-between gap-3 p-3...">   ← ROW (3 levels up from p)
    //     <div class="flex items-start gap-2 min-w-0">               ← inner-left (2 up)
    //       <svg/>
    //       <div class="min-w-0">                                    ← text-container (1 up)
    //         <p class="text-sm font-medium...">agent-name</p>       ← p (found by XPath)
    //       </div>
    //     </div>
    //     <button>+ Add</button>                                     ← sibling of inner-left in ROW
    //   </div>
    // XPath: from p → up 3 → down to button sibling.
    const addBtn1 = page.locator(
      `xpath=//p[normalize-space()="${WF_AGENT_1}"]/../../../button`
    );
    await addBtn1.click();
    // After adding, the button text changes to "Added"
    await expect(addBtn1).toContainText("Added");

    // Add second agent using the same XPath pattern
    const addBtn2 = page.locator(
      `xpath=//p[normalize-space()="${WF_AGENT_2}"]/../../../button`
    );
    await addBtn2.click();

    await page.getByRole("button", { name: /Done/i }).click();

    // ReactFlow renders each node inside a .react-flow__node wrapper.
    // WorkflowMemberNode shows the agent_name in a <span>.
    await expect(
      page.locator(".react-flow__node", { hasText: WF_AGENT_1 })
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.locator(".react-flow__node", { hasText: WF_AGENT_2 })
    ).toBeVisible({ timeout: 10_000 });
  });

  test("Save opens first-save modal; filling name and confirming fires POST /api/v1/workflows", async ({
    page,
  }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    // Add at least one agent so Save doesn't error
    await page.getByRole("button", { name: /Add Existing Agent/i }).click();
    await expect(page.getByText(WF_AGENT_1)).toBeVisible({ timeout: 10_000 });
    // Use XPath to navigate from the agent name <p> to its sibling Add button
    await page
      .locator(`xpath=//p[normalize-space()="${WF_AGENT_1}"]/../../../button`)
      .click();
    await page.getByRole("button", { name: /Done/i }).click();

    // Click Save — should open the "Save Workflow" modal (first save)
    await page.getByRole("button", { name: /^Save$/i }).click();
    await expect(
      page.getByRole("heading", { name: /Save Workflow/i })
    ).toBeVisible();

    // Fill workflow name
    await page.locator("input#wfb-name").fill(WORKFLOW_NAME);

    // Capture the POST before clicking the confirm button
    const wfPostPromise = page.waitForResponse(
      (r) =>
        /\/api\/v1\/workflows$/.test(r.url()) &&
        r.request().method() === "POST",
      { timeout: 20_000 }
    );

    await page.getByRole("button", { name: /Save Workflow/i }).click();

    const wfResp = await wfPostPromise;
    // 201 Created
    expect(wfResp.status()).toBe(201);

    // App navigates to /workflows/{id}/builder after save
    await page.waitForURL(/\/workflows\/.+\/builder/, { timeout: 15_000 });

    // Breadcrumb now shows the workflow name
    await expect(page.getByText(WORKFLOW_NAME)).toBeVisible();
  });

  test("saved workflow appears at /workflows list", async ({ page }) => {
    // Navigate to the list and find the workflow we created in the previous test.
    // This test depends on the POST test running first (fullyParallel: false, workers: 1).
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");

    await expect(page.getByText(WORKFLOW_NAME)).toBeVisible({ timeout: 10_000 });
  });

  test("Run Workflow button opens run panel on a saved workflow", async ({
    page,
  }) => {
    // Navigate to /workflows list, find our workflow, open it
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");

    // Click the "Open" button for our workflow
    const wfRow = page.locator("tr", { hasText: WORKFLOW_NAME });
    await wfRow.getByRole("button", { name: /Open/i }).click();
    await page.waitForURL(/\/workflows\/.+\/builder/, { timeout: 10_000 });
    await page.waitForLoadState("networkidle");

    // "Run Workflow" button should be visible (workflow is already saved)
    await expect(
      page.getByRole("button", { name: /Run Workflow/i })
    ).toBeVisible();

    // Click it — run panel slides in
    await page.getByRole("button", { name: /Run Workflow/i }).click();
    await expect(
      page.getByRole("heading", { name: /Run Workflow/i })
    ).toBeVisible();

    // Input textarea for the run message is present
    await expect(
      page.getByPlaceholder(/Enter the message to pass/i)
    ).toBeVisible();

    // "Start Run" button is present (disabled until input is provided)
    await expect(
      page.getByRole("button", { name: /Start Run/i })
    ).toBeVisible();
  });
});

import { test, expect, type Browser } from "@playwright/test";
import { pickModel } from "./lib/agents";

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
    await pickModel(page);  // llm_provider_id is REQUIRED since studio 0.1.178 — see lib/agents.ts

    const done = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create Agent/i }).click();
    await done;
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
      page.getByRole("button", { name: /^Add Agent$/i })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /Save/i })).toBeVisible();
  });

  test("Add Existing Agent opens modal with agent list", async ({ page }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /^Add Agent$/i }).click();

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

    // "Add Agent", not "Add Existing Agent": Decision 24 unified the Workflow builder so a
    // SINGLE modal handles both existing and newly-created members (AddAgentModal has
    // "Existing Agent" / "Create New Agent" tabs, default 'existing'). The old name only made
    // sense while those were separate flows. The row markup this spec's XPath depends on is
    // unchanged — only the entry point was renamed.
    // Open modal and add first agent
    await page.getByRole("button", { name: /^Add Agent$/i }).click();
    // SEARCH FIRST. ExistingTab fetches listAgents(100, 0, …, {composable:true}) — a
    // hard cap of 100 (AddAgentModal.tsx:34) — and this cluster holds far more, so a
    // freshly-created fixture is simply off the end of the list and its row never
    // renders. The modal ships a search box for exactly this; using it removes the
    // dependency on where the fixture happens to land in an unfiltered page.
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
    // ONE ADD PER MODAL. handleAddAgent closes it on every add — "Close the modal after
    // adding so the new node is visible" (WorkflowBuilderPage.tsx:257). This spec was
    // written against a multi-add modal: it added agent 1, asserted the button flipped to
    // "Added" in a still-open modal, added agent 2, then clicked Done. The first assertion
    // now fails against a modal that is already gone, and the canvas node — which is the
    // thing actually under test — was correct all along. Reopen per agent and assert the
    // NODE, not the button state of a dismissed dialog.
    const addFromModal = async (name: string) => {
      await page.getByPlaceholder("Search agents…").fill(name);
      const addBtn = page.locator(`xpath=//p[normalize-space()="${name}"]/../../../button`);
      await expect(addBtn).toBeVisible({ timeout: 10_000 });
      await addBtn.click();
      // The modal self-dismisses; wait for that rather than clicking a Done that is gone.
      await expect(page.getByPlaceholder("Search agents…")).toBeHidden({ timeout: 10_000 });
    };

    await addFromModal(WF_AGENT_1);
    await page.getByRole("button", { name: /^Add Agent$/i }).click();
    await addFromModal(WF_AGENT_2);

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
    await page.getByRole("button", { name: /^Add Agent$/i }).click();
    // SEARCH FIRST. ExistingTab fetches listAgents(100, 0, …, {composable:true}) — a
    // hard cap of 100 (AddAgentModal.tsx:34) — and this cluster holds far more, so a
    // freshly-created fixture is simply off the end of the list and its row never
    // renders. The modal ships a search box for exactly this; using it removes the
    // dependency on where the fixture happens to land in an unfiltered page.
    await page.getByPlaceholder("Search agents…").fill(WF_AGENT_1);
    await expect(page.getByText(WF_AGENT_1)).toBeVisible({ timeout: 10_000 });
    // Use XPath to navigate from the agent name <p> to its sibling Add button
    await page
      .locator(`xpath=//p[normalize-space()="${WF_AGENT_1}"]/../../../button`)
      .click();
    // No Done click: handleAddAgent dismisses the modal on every add
    // (WorkflowBuilderPage.tsx:257). Wait for that instead of clicking a button that is
    // already gone — same stale multi-add assumption as the two-node test above.
    await expect(page.getByPlaceholder("Search agents…")).toBeHidden({ timeout: 10_000 });

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

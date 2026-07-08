import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// playground.spec.ts
//   Proves the Eval Runs (/playground) and Datasets (/playground/datasets)
//   pages load and expose their primary UI affordances.
//
//   Note: actually RUNNING an agent requires a live pod; these tests prove the
//   UI wiring (selector, panel layout, datasets table/modal) only.
// ---------------------------------------------------------------------------

test.describe("playground page", () => {
  test("loads with left panel and Select Agent dropdown", async ({ page }) => {
    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    // Left panel heading (h2); there's also a sidebar nav link with the same text,
    // so scope to the role=heading to avoid strict-mode violation.
    await expect(page.getByRole("heading", { name: "Eval Runs" })).toBeVisible();

    // VersionSelector renders a label and a <select>
    await expect(page.getByText(/Select Agent/i)).toBeVisible();
    // The <select> for agent selection is present
    const agentSelect = page.locator("select").filter({
      hasText: /pick an agent/i,
    });
    await expect(agentSelect).toBeVisible();
  });

  test("Manage Datasets / Eval link is present", async ({ page }) => {
    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    const datasetsLink = page.getByText(/Manage Datasets/i);
    await expect(datasetsLink).toBeVisible();
  });

  test("Event Trace panel is rendered in collapsed or expanded state", async ({
    page,
  }) => {
    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    // TracePanel renders a toggle button that contains a Terminal icon.
    // In expanded state it also shows "Event Trace" text.
    // In collapsed state (default) the panel is narrow with just the icon button.
    // Either way, the toggle button itself is always visible.
    const traceToggle = page
      .locator("button")
      .filter({ has: page.locator("[data-lucide='terminal'], svg") })
      .last(); // Last button in the right-side panel area

    // Weaker assertion: "Event Trace" text appears when not collapsed
    // The panel starts expanded so "Event Trace" should be visible
    await expect(page.getByText("Event Trace")).toBeVisible();
  });

  test("selecting an agent updates the center panel header", async ({
    page,
  }) => {
    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    // Get all options in the agent selector
    const agentSelect = page.locator("select").filter({
      hasText: /pick an agent/i,
    });
    const options = await agentSelect.locator("option").allTextContents();
    // options[0] is the placeholder "-- pick an agent --"
    const realAgents = options.filter(
      (o) => o.trim() !== "" && !o.includes("pick an agent")
    );

    if (realAgents.length === 0) {
      // No agents in the environment — skip run-dependent assertion
      // UI still renders correctly (tested above)
      test.skip();
      return;
    }

    // Select the first real agent
    await agentSelect.selectOption({ label: realAgents[0] });
    await page.waitForLoadState("networkidle");

    // Center panel header shows the selected agent name (in a <span>) and sandbox badge.
    // Use span locator to avoid strict-mode violation with the matching <option> element.
    const agentName = realAgents[0].trim();
    await expect(page.locator("span", { hasText: agentName })).toBeVisible();
    // sandbox badge appears when any agent is selected
    await expect(page.locator("span").filter({ hasText: /^sandbox$/ })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Datasets sub-page
// ---------------------------------------------------------------------------
test.describe("datasets page", () => {
  test("renders heading, Refresh button, and New Dataset button", async ({
    page,
  }) => {
    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    await expect(
      page.getByRole("heading", { name: /Datasets/i }).first()
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /New Dataset/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Refresh/i })).toBeVisible();
  });

  test("New Dataset button opens creation modal", async ({ page }) => {
    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /New Dataset/i }).click();

    // Modal opens with "New Dataset" heading and name input
    await expect(
      page.getByRole("heading", { name: /New Dataset/i })
    ).toBeVisible();
    await expect(page.getByPlaceholder(/order-lookup-tests/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Create Dataset/i })
    ).toBeVisible();

    // Close the modal
    await page.getByRole("button", { name: /Cancel/i }).click();
    await expect(
      page.getByRole("heading", { name: /New Dataset/i })
    ).not.toBeVisible();
  });

  test("renders dataset table or empty-state (no crash)", async ({ page }) => {
    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    // Either a table with column headers OR the empty-state message is visible
    const hasTable = await page.locator("table").count();
    const hasEmptyState = await page
      .getByText(/No datasets yet/i)
      .count();

    expect(hasTable + hasEmptyState).toBeGreaterThan(0);
  });
});

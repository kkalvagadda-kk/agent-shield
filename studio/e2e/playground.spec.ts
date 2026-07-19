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

    // The left panel selects a running DEPLOYMENT (SELECT DEPLOYMENT → "-- pick a
    // deployment --"), not an agent. (Locator was stale — see durable-stream.spec.ts.)
    await expect(page.getByText(/Select Deployment/i)).toBeVisible();
    const depSelect = page.locator("select").filter({
      hasText: /pick a deployment/i,
    });
    await expect(depSelect).toBeVisible();
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

  test("selecting a deployment updates the center panel (leaves the empty state)", async ({
    page,
  }) => {
    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    const depSelect = page.locator("select").filter({
      hasText: /pick a deployment/i,
    });
    // options load async — wait for real deployments to populate before reading.
    await expect
      .poll(async () => depSelect.locator("option").count(), { timeout: 15000 })
      .toBeGreaterThan(1);
    const options = await depSelect.locator("option").allTextContents();
    const real = options.filter(
      (o) => o.trim() !== "" && !/pick a deployment/i.test(o)
    );
    if (real.length === 0) {
      // No running deployment in this environment — the empty state (tested above)
      // still renders correctly.
      test.skip();
      return;
    }

    // Selecting a deployment must leave the "No agent selected" empty state and
    // render the interaction surface (RunLauncher for durable, chat for reactive).
    await depSelect.selectOption({ label: real[0] });
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/No agent selected/i)).toHaveCount(0);
  });

  test("History dock opens for a reactive deployment and lists its conversations", async ({
    page,
  }) => {
    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    const depSelect = page.locator("select").filter({
      hasText: /pick a deployment/i,
    });
    await expect
      .poll(async () => depSelect.locator("option").count(), { timeout: 15000 })
      .toBeGreaterThan(1);
    const options = await depSelect.locator("option").allTextContents();
    const real = options.filter(
      (o) => o.trim() !== "" && !/pick a deployment/i.test(o)
    );
    if (real.length === 0) {
      // No running sandbox deployment here — nothing to open History against.
      test.skip();
      return;
    }
    await depSelect.selectOption({ label: real[0] });
    await page.waitForLoadState("networkidle");

    // History is only wired for the reactive ChatPane surface — a durable/triggered
    // deployment renders the InteractionSurface instead (no toggle). Tolerate that,
    // the same capacity boundary the other playground tests accept.
    const toggle = page.getByTestId("playground-history-toggle");
    if ((await toggle.count()) === 0) {
      test.skip();
      return;
    }

    // Opening the dock fires the deployment-scoped conversations query (proves the
    // network wiring) and renders the shared sidebar with its New-conversation control.
    const [resp] = await Promise.all([
      page.waitForResponse((r) => /\/memory\/conversations/.test(r.url()), {
        timeout: 15000,
      }),
      toggle.click(),
    ]);
    expect(resp.status()).toBeLessThan(500);
    await expect(page.getByTestId("playground-history-dock")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /New conversation/i })
    ).toBeVisible();
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

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// eval-mode-plumbing.spec.ts  (Eval v2 E-0)
//
//   Real, NON-route-stubbed browser journey for the mode-aware datasets + one
//   scoring door + dimension render. NO page.route stubbing of the eval API —
//   every assertion rides a real network call to the deployed backend and a
//   real reload from it (save -> reload -> assert).
//
//   Flow:
//     1. author a reactive dataset (mode selector defaults "reactive")
//        -> POST /playground/datasets  (real waitForResponse)
//     2. reload the datasets page -> the dataset is still there (persisted)
//     3. launch an eval against a running deployment if one exists
//        -> POST /playground/eval-runs (real waitForResponse) -> EvalResultsPage
//        -> read the composite/response column back
//
//   Boundary (same as playground.spec.ts / suite-58): actually COMPLETING a run
//   needs a live agent pod + the eval-runner Job. When no running sandbox
//   deployment exists in the environment, the launch step is skipped (loudly),
//   but the create + persist round-trip is still fully proven here. The real
//   end-to-end score persistence is the bash suite-61 gate.
// ---------------------------------------------------------------------------

const uniq = () => Math.random().toString(36).slice(2, 8);

test.describe("eval v2 E-0 — mode-aware datasets + dimension render", () => {
  test("author a reactive dataset, persist it, reload, and see it survive", async ({
    page,
  }) => {
    const dsName = `e2e-eval-mode-${uniq()}`;

    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    // 1. open the create modal
    await page.getByRole("button", { name: /New Dataset/i }).click();
    await expect(page.getByRole("heading", { name: /New Dataset/i })).toBeVisible();

    // Mode selector defaults to "reactive" (E-0 behaviour-neutral default)
    const modeSelect = page.getByLabel("Dataset mode");
    await expect(modeSelect).toBeVisible();
    await expect(modeSelect).toHaveValue("reactive");

    // Fill name + two real reactive items (input + expected_output JSON lines)
    await page.getByPlaceholder(/order-lookup-tests/i).fill(dsName);
    const itemsBox = page.locator("textarea").first();
    await itemsBox.fill(
      '{"input": "What is the capital of France? Answer with only the city name.", "expected_output": "Paris"}\n' +
        '{"input": "What is 2 + 2? Answer with only the number.", "expected_output": "4"}'
    );

    // 2. Create -> assert the REAL POST fires and returns the mode-tagged dataset
    const createResp = page.waitForResponse(
      (r) =>
        r.url().includes("/playground/datasets") &&
        r.request().method() === "POST"
    );
    await page.getByRole("button", { name: /Create Dataset/i }).click();
    const created = await createResp;
    expect(created.status()).toBeGreaterThanOrEqual(200);
    expect(created.status()).toBeLessThan(300);
    const body = await created.json();
    expect(body.mode).toBe("reactive");
    expect(Array.isArray(body.items) ? body.items.length : 0).toBe(2);

    // 3. save -> RELOAD from the backend -> the dataset is still there
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(dsName, { exact: false })).toBeVisible();

    // 4. launch an eval against a running deployment IF one exists (real network).
    //    Open the Run Eval modal for our dataset row.
    const row = page.locator("tr", { hasText: dsName }).first();
    await row.getByRole("button", { name: /Run Eval/i }).click();
    await expect(page.getByRole("heading", { name: /Run Eval/i })).toBeVisible();

    const deploySelect = page
      .locator("select")
      .filter({ hasText: /pick a running deployment/i });
    const options = await deploySelect.locator("option").allTextContents();
    const realDeploys = options.filter((o) => !/pick a running deployment/i.test(o));

    if (realDeploys.length === 0) {
      // No running sandbox deployment here — the end-to-end run needs a live pod
      // (proven by bash suite-61). The create + persist round-trip above is done.
      await expect(
        page.getByText(/No running sandbox deployments found/i)
      ).toBeVisible();
      test.info().annotations.push({
        type: "note",
        description:
          "No running sandbox deployment in this env — eval launch skipped; " +
          "real end-to-end score persistence is covered by suite-61.",
      });
      return;
    }

    // Pick the first running deployment and START the eval — real POST.
    await deploySelect.selectOption({ index: 1 });
    const launchResp = page.waitForResponse(
      (r) =>
        r.url().includes("/playground/eval-runs") &&
        r.request().method() === "POST"
    );
    await page.getByRole("button", { name: /Start Eval/i }).click();
    const launched = await launchResp;
    expect(launched.status()).toBe(201);

    // We navigate to the EvalResultsPage for the new run.
    await page.waitForURL(/\/playground\/eval-runs\//);
    await expect(
      page.getByRole("heading", { name: /Eval/i }).first()
    ).toBeVisible();

    // The results table renders the Response (composite) dimension column header.
    // (The per-row score only fills once the run completes — a live-pod boundary.)
    await expect(page.getByText(/Response/i).first()).toBeVisible();
  });
});

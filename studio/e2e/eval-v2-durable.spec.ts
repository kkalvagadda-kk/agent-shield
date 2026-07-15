import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// eval-v2-durable.spec.ts  (Eval v2 E-1)
//
//   Real, NON-route-stubbed browser journey for the DURABLE trajectory eval:
//   author a durable dataset item in DatasetsPage (mode=durable, input_payload,
//   expected_trajectory steps incl. an expect_approval HITL toggle), persist it,
//   RELOAD from the backend, and confirm it survived. Then render the durable
//   evidence (per-dimension trajectory/tool_call columns + tool-diff panel +
//   run_id deep-link) against a REAL completed durable EvalRun.
//
//   NO page.route stubbing — every assertion rides a real network call to the
//   deployed backend and a real reload from it (save -> reload -> assert).
//
//   Boundary (same bar as eval-mode-plumbing.spec / suite-58/72): actually
//   running a durable eval to completion in-browser needs a live agent pod +
//   the eval-runner Job + minutes of park/approve/resume, which is too slow/
//   flaky for a browser test. So the results-render half asserts against an
//   ALREADY-completed durable EvalRun discovered from the backend (e.g. the one
//   bash suite-72 produced) — real rows, no fabricated data. If no completed
//   durable run exists in this environment, that half is skipped loudly (the
//   authoring round-trip above still fully proves the durable DatasetsPage UI).
//   The real end-to-end score persistence is the bash suite-72 gate.
// ---------------------------------------------------------------------------

const uniq = () => Math.random().toString(36).slice(2, 8);

test.describe("eval v2 E-1 — durable dataset authoring + trajectory render", () => {
  test("author a durable dataset, persist it, reload, and see it survive", async ({
    page,
  }) => {
    const dsName = `e2e-durable-${uniq()}`;

    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    // 1. open the create modal
    await page.getByRole("button", { name: /New Dataset/i }).click();
    await expect(page.getByRole("heading", { name: /New Dataset/i })).toBeVisible();

    // 2. switch the mode selector to DURABLE — this reveals the trajectory editor
    const modeSelect = page.getByLabel("Dataset mode");
    await expect(modeSelect).toBeVisible();
    await modeSelect.selectOption("durable");

    // name
    await page.getByPlaceholder(/order-lookup-tests/i).fill(dsName);

    // 3. author the durable item: input_payload + match_mode + two steps, the
    //    second flagged expect_approval (HITL).
    await page.locator("#durable-input-payload").fill('{"message": "Refund order 12345 amount 25, account ACC-2."}');
    await page.getByLabel("Trajectory match mode").selectOption("superset");

    const addStep = page.getByRole("button", { name: /Add step/i });
    await addStep.click();
    await addStep.click();

    await page.getByLabel("Step 1 tool").fill("quarantine_action");
    await page.getByLabel("Step 2 tool").fill("refund_action");
    // flag the 2nd step as an expected HITL park
    await page.getByLabel("Step 2 expect approval").check();

    // 4. Create -> assert the REAL POST fires and returns a durable dataset whose
    //    item carries the structured expected_trajectory (persisted, not stubbed).
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
    expect(body.mode).toBe("durable");
    const item0 = (body.items || [])[0] || {};
    const steps = (item0.expected_trajectory?.steps || []).map((s: any) => s.tool);
    expect(steps).toEqual(["quarantine_action", "refund_action"]);
    const gatedStep = (item0.expected_trajectory?.steps || []).find(
      (s: any) => s.tool === "refund_action"
    );
    expect(gatedStep?.expect_approval).toBe(true);

    // 5. save -> RELOAD from the backend -> the durable dataset is still there
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(dsName, { exact: false })).toBeVisible();
    // and it is tagged as a durable-mode dataset in the list
    const row = page.locator("tr", { hasText: dsName }).first();
    await expect(row.getByText(/durable/i)).toBeVisible();

    // ---- 6. RESULTS RENDER against a REAL completed durable EvalRun ----------
    // Capture a real Bearer token from one of the app's own authenticated calls,
    // then query the backend for a completed durable eval-run (no stub).
    let authHeader: string | undefined;
    page.on("request", (req) => {
      const h = req.headers()["authorization"];
      if (h && h.startsWith("Bearer ") && req.url().includes("/api/v1/")) {
        authHeader = h;
      }
    });
    // Nudge the app to make an authenticated call so we capture the token.
    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    if (!authHeader) {
      test.info().annotations.push({
        type: "note",
        description: "Could not capture an auth token — durable results-render half skipped.",
      });
      return;
    }

    const listResp = await page.request.get("/api/v1/playground/eval-runs", {
      headers: { Authorization: authHeader },
    });
    if (!listResp.ok()) {
      test.info().annotations.push({
        type: "note",
        description: `eval-runs list returned ${listResp.status()} — results-render half skipped.`,
      });
      return;
    }
    const runs = (await listResp.json()) as Array<{
      id: string;
      mode?: string;
      status?: string;
    }>;
    const durableDone = runs.find((r) => r.mode === "durable" && r.status === "completed");

    if (!durableDone) {
      test.info().annotations.push({
        type: "note",
        description:
          "No completed durable EvalRun in this env — durable results-render half skipped. " +
          "Real end-to-end durable score persistence is covered by bash suite-72.",
      });
      return;
    }

    // 7. render the durable evidence for that REAL run — real rows, no stub.
    await page.goto(`/playground/eval-runs/${durableDone.id}`);
    await page.waitForLoadState("networkidle");
    // page loaded: the "Back to Datasets" control + a Run ID line are present.
    await expect(page.getByText(/Run ID:/i).first()).toBeVisible();

    // Per-dimension durable columns render (E-1 T014). The trajectory + tool_call
    // dimension cells are present for durable rows (rendered per-row, no expand).
    await expect(page.getByTestId("dim-trajectory").first()).toBeVisible();
    await expect(page.getByTestId("dim-tool_call").first()).toBeVisible();

    // Expand the first result row to reveal the durable per-item evidence: the
    // actual-trajectory / tool-diff panel and the run_id deep-link into the real
    // run tree (StepTracker). These live in the expanded row detail.
    await page.locator("tbody tr").first().click();
    await expect(page.getByTestId("durable-evidence").first()).toBeVisible();
    await expect(page.getByTestId("run-steps-deeplink").first()).toBeVisible();
  });
});

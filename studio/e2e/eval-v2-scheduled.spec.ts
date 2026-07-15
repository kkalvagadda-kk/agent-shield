import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// eval-v2-scheduled.spec.ts  (Eval v2 E-3)
//
//   Real, NON-route-stubbed browser journey for the SCHEDULED job-spec eval:
//   author a scheduled dataset item in DatasetsPage (mode=scheduled, job_spec,
//   an expected-trajectory step, and the HEADLINE expected_side_effects
//   assertion), persist it through the REAL POST, RELOAD from the backend, and
//   confirm it survived. Then render the job-spec evidence + the recorded
//   side-effect panel + the side_effect dimension against a REAL completed
//   scheduled EvalRun.
//
//   NO page.route stubbing — every assertion rides a real network call to the
//   deployed backend and a real reload from it (save -> reload -> assert).
//
//   Boundary (the eval-v2-durable.spec / suite-58/72/74 bar): actually running a
//   scheduled eval to completion in-browser needs a live daemon agent pod + an
//   armed schedule trigger + the eval-runner Job + minutes of real LLM tool
//   calls, which is too slow/flaky for a browser test. So the results-render
//   half asserts against an ALREADY-completed scheduled EvalRun discovered from
//   the backend (the one bash suite-75 produces) — real rows, no fabricated
//   data. If none exists in this environment, that half is skipped LOUDLY (the
//   authoring round-trip above still fully proves the scheduled DatasetsPage
//   UI). The real end-to-end recorded-not-delivered + score persistence is the
//   bash suite-75 gate (T-S75-003/004/005).
// ---------------------------------------------------------------------------

const uniq = () => Math.random().toString(36).slice(2, 8);

test.describe("eval v2 E-3 — scheduled job-spec authoring + evidence render", () => {
  test("author a scheduled dataset (job spec + side-effect assertion), persist it, reload, and see it survive", async ({
    page,
  }) => {
    const dsName = `e2e-scheduled-${uniq()}`;
    const jobSpec = {
      message: "Refund order 12345 amount 25.",
      report: "nightly-refunds",
    };

    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    // 1. open the create modal
    await page.getByRole("button", { name: /New Dataset/i }).click();
    await expect(page.getByRole("heading", { name: /New Dataset/i })).toBeVisible();

    // 2. switch the mode selector to SCHEDULED — this reveals the job-spec editor.
    //    Before E-3 this option created an EMPTY dataset (no editor), so the editor
    //    being here at all is the T012 claim.
    const modeSelect = page.getByLabel("Dataset mode");
    await expect(modeSelect).toBeVisible();
    await modeSelect.selectOption("scheduled");

    await page.getByPlaceholder(/order-lookup-tests/i).fill(dsName);

    // 3. INVALID JSON blocks save with an inline error (the editor validates before
    //    any POST — a malformed job spec never reaches the API).
    await page.locator("#scheduled-job-spec").fill("{not json");
    await page.getByRole("button", { name: /Create Dataset/i }).click();
    await expect(page.getByText(/Job spec is not valid JSON/i)).toBeVisible();
    // still on the modal — nothing was created
    await expect(page.getByRole("heading", { name: /New Dataset/i })).toBeVisible();

    // 4. author the real scheduled item: job_spec + expected output + one expected
    //    trajectory step (durable-inner) + the HEADLINE side-effect assertion.
    await page.locator("#scheduled-job-spec").fill(JSON.stringify(jobSpec));
    await page
      .locator("#scheduled-expected-output")
      .fill("The refund for order 12345 was processed.");
    await page.getByLabel("Trajectory match mode").selectOption("superset");

    await page.getByRole("button", { name: /Add step/i }).click();
    await page.getByLabel("Step 1 tool").fill("refund_action");

    // The side-effect assertion is what makes the runner fire this item under
    // eval_mode=record — the whole point of a scheduled eval.
    await page.getByRole("button", { name: /Add side effect/i }).click();
    await page.getByLabel("Side effect 1 tool").fill("refund_action");
    await page.getByLabel("Side effect 1 occurs").selectOption("exactly");
    await page.getByLabel("Side effect 1 count").fill("1");
    // the record-mode warning is surfaced to the author, not hidden
    await expect(page.getByText(/will run in.*record.*mode/i)).toBeVisible();

    // 5. Create -> assert the REAL POST fires and returns a scheduled dataset whose
    //    item carries job_spec + expected_side_effects (persisted, not stubbed).
    const createResp = page.waitForResponse(
      (r) =>
        r.url().includes("/playground/datasets") && r.request().method() === "POST"
    );
    await page.getByRole("button", { name: /Create Dataset/i }).click();
    const created = await createResp;
    expect(created.status()).toBeGreaterThanOrEqual(200);
    expect(created.status()).toBeLessThan(300);
    const body = await created.json();
    expect(body.mode).toBe("scheduled");
    const item0 = (body.items || [])[0] || {};
    expect(item0.kind).toBe("scheduled");
    expect(item0.job_spec).toEqual(jobSpec);
    expect((item0.expected_side_effects || [])[0]).toMatchObject({
      tool: "refund_action",
      occurs: "exactly",
      count: 1,
    });
    expect((item0.expected_trajectory?.steps || []).map((s: any) => s.tool)).toEqual([
      "refund_action",
    ]);

    // 6. save -> RELOAD from the backend -> the scheduled dataset is still there,
    //    tagged scheduled in the list (DoD #2: the round-trip, not the store).
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(dsName, { exact: false })).toBeVisible();
    const row = page.locator("tr", { hasText: dsName }).first();
    await expect(row.getByText(/scheduled/i)).toBeVisible();

    // ---- 7. RESULTS RENDER against a REAL completed scheduled EvalRun ----------
    // Capture a real Bearer token from one of the app's own authenticated calls,
    // then query the backend for a completed scheduled eval-run (no stub).
    let authHeader: string | undefined;
    page.on("request", (req) => {
      const h = req.headers()["authorization"];
      if (h && h.startsWith("Bearer ") && req.url().includes("/api/v1/")) {
        authHeader = h;
      }
    });
    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    if (!authHeader) {
      test.info().annotations.push({
        type: "note",
        description:
          "Could not capture an auth token — scheduled results-render half skipped.",
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

    // Select the run by the PRECONDITION these assertions require — a completed
    // scheduled run whose results actually carry recorded side-effect evidence —
    // not merely the first `mode=scheduled && status=completed` row.
    //
    // Why: "scheduled + completed" is satisfied by runs that CANNOT have evidence.
    // suite-75's own reactive fail-closed run is scheduled+completed with 1 item,
    // dimension_scores=null and recorded_side_effects=[] — refusing to fire is the
    // correct behaviour there, so nothing renders. Picking whichever row sorted
    // first made this test's verdict depend on unrelated leftover state (observed:
    // it landed on the fail-closed run and failed on `side-effect-evidence`, while
    // the UI was correct). Ambient-state dependence is the same disease as a stub —
    // the outcome stops tracking the code under test.
    //
    // This is not circular. That the API HAS evidence is the precondition; that the
    // SCREEN renders it is the claim, and only a browser can prove that.
    let scheduledDone: { id: string } | undefined;
    for (const r of runs.filter(
      (x) => x.mode === "scheduled" && x.status === "completed"
    )) {
      const res = await page.request.get(
        `/api/v1/playground/eval-runs/${r.id}/results`,
        { headers: { Authorization: authHeader } }
      );
      if (!res.ok()) continue;
      const rows = (await res.json()) as Array<{
        eval_detail?: { recorded_side_effects?: unknown[] };
        dimension_scores?: Record<string, number> | null;
      }>;
      const hasEvidence = rows.some(
        (row) =>
          (row.eval_detail?.recorded_side_effects || []).length > 0 &&
          row.dimension_scores?.side_effect !== undefined
      );
      if (hasEvidence) {
        scheduledDone = { id: r.id };
        break;
      }
    }

    if (!scheduledDone) {
      test.info().annotations.push({
        type: "note",
        description:
          "No completed scheduled EvalRun WITH recorded side-effect evidence in this " +
          "env — scheduled results-render half skipped. Run bash suite-75 first; it " +
          "produces exactly this fixture. Real end-to-end recorded-not-delivered + " +
          "score persistence is covered by suite-75 (T-S75-003/004/005).",
      });
      return;
    }

    // 8. render the evidence for that REAL run — real rows, no stub.
    await page.goto(`/playground/eval-runs/${scheduledDone.id}`);
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/Run ID:/i).first()).toBeVisible();

    // The side_effect dimension is the headline column for a scheduled run (E-2's
    // chip, reused). Present per-row, no expand needed.
    await expect(page.getByTestId("dim-side_effect").first()).toBeVisible();

    // Expand the first result row: the JOB SPEC that was fed as input_payload
    // (T013 — closes the eval_run_results.trigger_payload orphan) renders alongside
    // the reused recorded-not-delivered side-effect evidence panel.
    await page.locator("tbody tr").first().click();
    await expect(page.getByTestId("job-spec-evidence").first()).toBeVisible();
    await expect(page.getByTestId("side-effect-evidence").first()).toBeVisible();
  });
});

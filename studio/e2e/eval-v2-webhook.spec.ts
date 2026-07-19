import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// eval-v2-webhook.spec.ts  (Eval v2 E-4)
//
//   Real, NON-route-stubbed browser journey for the WEBHOOK eval: author a webhook
//   dataset item in DatasetsPage (mode=webhook, the synthetic `trigger_payload`, the
//   `expected_match` filter decision, and an `injection_probe`), persist it through
//   the REAL POST, RELOAD from the backend, and confirm all three survived. Then
//   render the filter verdict + the synthetic event + BOTH halves of the injection
//   report (ASR *and* utility) against a REAL completed webhook EvalRun.
//
//   NO page.route stubbing — every assertion rides a real network call to the
//   deployed backend and a real reload from it (save -> reload -> assert).
//
//   Boundary (the eval-v2-durable/scheduled spec + suite-58/72/74/75 bar): actually
//   running a webhook eval to completion in-browser needs a live daemon agent pod +
//   an armed webhook trigger + the eval-runner Job + minutes of real LLM tool calls,
//   which is too slow/flaky for a browser test. So the results-render half asserts
//   against an ALREADY-completed webhook EvalRun discovered from the backend (the one
//   bash suite-77 produces) — real rows, no fabricated data. If none exists in this
//   environment, that half is skipped LOUDLY. The real end-to-end filter decision +
//   recorded-not-delivered + injection scoring is the bash suite-77 gate
//   (T-S77-003/004/006/007/008).
// ---------------------------------------------------------------------------

const uniq = () => Math.random().toString(36).slice(2, 8);

test.describe("eval v2 E-4 — webhook event authoring + filter/injection evidence", () => {
  test("author a webhook dataset (synthetic event + expected_match + injection probe), persist it, reload, and see it survive", async ({
    page,
  }) => {
    const dsName = `e2e-webhook-${uniq()}`;
    const triggerPayload = {
      event_type: "payment.fail",
      order_id: "12345",
      amount: "25",
    };

    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    // 1. open the create modal
    await page.getByRole("button", { name: /New Dataset/i }).click();
    await expect(page.getByRole("heading", { name: /New Dataset/i })).toBeVisible();

    // 2. switch the mode selector to WEBHOOK — this reveals the synthetic-event
    //    editor. Before E-4 this option fell through to the "editor is coming later"
    //    path and created an EMPTY dataset, so the editor being here at all is the
    //    T015 claim.
    const modeSelect = page.getByLabel("Dataset mode");
    await expect(modeSelect).toBeVisible();
    await modeSelect.selectOption("webhook");
    await expect(page.getByTestId("webhook-trigger-payload")).toBeVisible();

    await page.getByPlaceholder(/order-lookup-tests/i).fill(dsName);

    // 3. INVALID JSON blocks save with an inline error (the editor validates before
    //    any POST — a malformed synthetic event never reaches the API).
    await page.locator("#webhook-trigger-payload").fill("{not json");
    await page.getByRole("button", { name: /Create Dataset/i }).click();
    await expect(page.getByText(/Synthetic event is not valid JSON/i)).toBeVisible();
    // still on the modal — nothing was created
    await expect(page.getByRole("heading", { name: /New Dataset/i })).toBeVisible();

    // 4. the filter-decision toggle drives the reason field: `expected_filter_reason`
    //    is meaningless for an event that SHOULD match (a match's reason just names
    //    which rule fired), so it is only offered for an expected MISS.
    await expect(page.getByLabel("Expected filter reason")).toHaveCount(0);
    await page.getByLabel("Expected match").uncheck();
    await expect(page.getByLabel("Expected filter reason")).toBeVisible();
    await page.getByLabel("Expected match").check();
    await expect(page.getByLabel("Expected filter reason")).toHaveCount(0);

    // 5. author the real webhook item: the synthetic event + expected match + the
    //    injection probe (the attacker-controlled half — the payload arrives from the
    //    public internet, so a forbidden tool must stay unreachable).
    await page.locator("#webhook-trigger-payload").fill(JSON.stringify(triggerPayload));
    await page
      .locator("#webhook-expected-output")
      .fill("The on-call engineer was paged about order 12345.");

    await page.getByTestId("webhook-must-not-call-input").fill("wire_transfer");
    await page.getByTestId("webhook-add-must-not-call").click();
    await expect(page.getByTestId("webhook-must-not-call-list")).toContainText(
      "wire_transfer"
    );

    // 6. Create -> assert the REAL POST fires and returns a webhook dataset whose item
    //    carries trigger_payload + expected_match + injection_probe (persisted, not
    //    stubbed).
    const createResp = page.waitForResponse(
      (r) =>
        r.url().includes("/playground/datasets") && r.request().method() === "POST"
    );
    await page.getByRole("button", { name: /Create Dataset/i }).click();
    const created = await createResp;
    expect(created.status()).toBeGreaterThanOrEqual(200);
    expect(created.status()).toBeLessThan(300);
    const body = await created.json();
    expect(body.mode).toBe("webhook");
    const item0 = (body.items || [])[0] || {};
    expect(item0.kind).toBe("webhook");
    expect(item0.trigger_payload).toEqual(triggerPayload);
    expect(item0.expected_match).toBe(true);
    expect(item0.injection_probe).toMatchObject({ must_not_call: ["wire_transfer"] });

    // 7. save -> RELOAD from the backend -> the webhook dataset is still there, tagged
    //    webhook in the list (DoD #2: the round-trip, not the store).
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(dsName, { exact: false })).toBeVisible();
    const row = page.locator("tr", { hasText: dsName }).first();
    await expect(row.getByText(/webhook/i)).toBeVisible();

    // 8. The RELOAD half of save->reload->assert, read back through the REAL API: all
    //    THREE fields survived the round-trip to the DB. The list row above only
    //    proves the dataset exists; this proves its authored content did too — most
    //    past rework was an unclosed persistence round-trip.
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
          "Could not capture an auth token — webhook reload-verify + results-render halves skipped.",
      });
      return;
    }

    const reloaded = await page.request.get(
      `/api/v1/playground/datasets/${body.id}`,
      { headers: { Authorization: authHeader } }
    );
    expect(reloaded.ok()).toBeTruthy();
    const reloadedBody = await reloaded.json();
    const reloadedItem = (reloadedBody.items || [])[0] || {};
    expect(reloadedItem.trigger_payload).toEqual(triggerPayload);
    expect(reloadedItem.expected_match).toBe(true);
    expect(reloadedItem.injection_probe).toMatchObject({
      must_not_call: ["wire_transfer"],
    });

    // ---- 9. RESULTS RENDER against a REAL completed webhook EvalRun -------------
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
    // webhook run that actually recorded a filter decision — not merely the first
    // `mode=webhook && status=completed` row.
    //
    // Why (the eval-v2-scheduled.spec lesson, which cost a real debugging cycle):
    // "webhook + completed" is satisfied by runs that CANNOT show a verdict. A run
    // whose every item fail-closed BEFORE firing is webhook+completed with
    // matched=null — refusing to fire is the correct behaviour there, so no decision
    // renders. Picking whichever row sorted first would make this test's verdict
    // depend on unrelated leftover state; ambient-state dependence is the same disease
    // as a stub — the outcome stops tracking the code under test.
    //
    // This is not circular. That the API HAS a decision is the precondition; that the
    // SCREEN renders it is the claim, and only a browser can prove that.
    let webhookDone: { id: string; hasInjection: boolean } | undefined;
    for (const r of runs.filter(
      (x) => x.mode === "webhook" && x.status === "completed"
    )) {
      const res = await page.request.get(
        `/api/v1/playground/eval-runs/${r.id}/results`,
        { headers: { Authorization: authHeader } }
      );
      if (!res.ok()) continue;
      const rows = (await res.json()) as Array<{
        matched?: boolean | null;
        trigger_payload?: Record<string, unknown> | null;
        eval_detail?: { asr?: number | null; utility?: number | null } | null;
        dimension_scores?: Record<string, number> | null;
      }>;
      const hasDecision = rows.some(
        (row) =>
          (row.matched === true || row.matched === false) &&
          row.dimension_scores?.filter !== undefined
      );
      if (!hasDecision) continue;
      webhookDone = {
        id: r.id,
        hasInjection: rows.some((row) => row.eval_detail?.asr !== undefined && row.eval_detail?.asr !== null),
      };
      break;
    }

    if (!webhookDone) {
      test.info().annotations.push({
        type: "note",
        description:
          "No completed webhook EvalRun WITH a recorded filter decision in this env — " +
          "webhook results-render half skipped. Run bash suite-77 first; it produces " +
          "exactly this fixture. Real end-to-end filter decision + recorded-not-" +
          "delivered + injection scoring is covered by suite-77 " +
          "(T-S77-003/004/006/007/008).",
      });
      return;
    }

    // 10. render the evidence for that REAL run — real rows, no stub.
    await page.goto(`/playground/eval-runs/${webhookDone.id}`);
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/Run ID:/i).first()).toBeVisible();

    // The filter dimension is the headline column for a webhook run. Present per-row.
    await expect(page.getByTestId("dim-filter").first()).toBeVisible();

    // Expand the first result row: the FILTER VERDICT (T016 — closes the read side of
    // the `eval_run_results.matched` orphan, which had neither a writer nor a reader
    // before E-4) renders alongside the SYNTHETIC EVENT that was fired.
    await page.locator("tbody tr").first().click();
    await expect(page.getByTestId("filter-verdict").first()).toBeVisible();
    await expect(page.getByTestId("synthetic-event-evidence").first()).toBeVisible();
    // The event is labelled an EVENT, never a "job spec" — both ride the same
    // `trigger_payload` column and are told apart by the run's explicit mode.
    await expect(page.getByTestId("job-spec-evidence")).toHaveCount(0);

    // 11. the injection report: ASR *and* utility, both on screen. Asserting only ASR
    //     would let a refuse-everything agent (ASR 0, useless) read as flawless — the
    //     side-by-side is the point of the panel, so the test pins both.
    if (!webhookDone.hasInjection) {
      test.info().annotations.push({
        type: "note",
        description:
          "The discovered webhook run recorded no injection probe — ASR/utility render " +
          "half skipped. suite-77's T-S77-007/008 are the real gate for it.",
      });
      return;
    }
    const rowsLoc = page.locator("tbody tr");
    const rowCount = await rowsLoc.count();
    let sawInjection = false;
    for (let i = 0; i < rowCount; i++) {
      await rowsLoc.nth(i).click();
      const asr = page.getByTestId("injection-asr");
      if ((await asr.count()) > 0) {
        await expect(asr.first()).toBeVisible();
        await expect(page.getByTestId("injection-utility").first()).toBeVisible();
        sawInjection = true;
        break;
      }
    }
    expect(
      sawInjection,
      "a webhook run carrying an injection probe must render BOTH ASR and utility"
    ).toBeTruthy();
  });
});

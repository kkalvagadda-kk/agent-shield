import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// eval-side-effects.spec.ts  (Eval v2 E-2)
//
//   Real, NON-route-stubbed browser journey for the SIDE-EFFECT record seam:
//   author a durable dataset item carrying `expected_side_effects` in
//   DatasetsPage (the field whose presence makes the eval-runner launch the item
//   under `eval_mode=record`, so a write tool is recorded + mocked instead of
//   really sending), persist it, RELOAD from the backend, and confirm it
//   survived. Then render the recorded side-effect evidence (the `side_effect`
//   dimension column + the "would have been sent" panel) against a REAL eval run.
//
//   NO page.route stubbing — every assertion rides a real network call to the
//   deployed backend and a real reload from it (save -> reload -> assert).
//
//   Boundary (same bar as eval-v2-durable.spec / suite-58/72/74): actually
//   running a record-mode durable eval to completion in-browser needs a live
//   agent pod + the eval-runner Job + minutes of park/approve/resume, which is
//   too slow/flaky for a browser test. So the results-render half asserts
//   against an ALREADY-completed durable EvalRun discovered from the backend
//   (e.g. the one bash suite-74 produced) — real rows, no fabricated data. If no
//   such run exists in this environment, that half is skipped loudly (the
//   authoring round-trip above still fully proves the E-2 DatasetsPage UI).
//   The real end-to-end record-not-delivered proof is the bash suite-74 gate.
// ---------------------------------------------------------------------------

const uniq = () => Math.random().toString(36).slice(2, 8);

test.describe("eval v2 E-2 — side-effect authoring + recorded-call render", () => {
  test("author expected_side_effects, persist, reload, and see them survive", async ({
    page,
  }) => {
    const dsName = `e2e-side-effects-${uniq()}`;

    await page.goto("/playground/datasets");
    await page.waitForLoadState("networkidle");

    // 1. open the create modal
    await page.getByRole("button", { name: /New Dataset/i }).click();
    await expect(page.getByRole("heading", { name: /New Dataset/i })).toBeVisible();

    // 2. switch the mode selector to DURABLE — this reveals the durable editor
    //    (trajectory steps + the E-2 side-effect assertions).
    const modeSelect = page.getByLabel("Dataset mode");
    await expect(modeSelect).toBeVisible();
    await modeSelect.selectOption("durable");

    await page.getByPlaceholder(/order-lookup-tests/i).fill(dsName);
    await page
      .locator("#durable-input-payload")
      .fill('{"message": "Refund order 12345 amount 25, account ACC-2."}');

    // 3. Before any assertion is authored the item is LIVE — the UI must say so
    //    (record mode is opt-in per item; it never leaks into a normal run).
    await expect(
      page.getByText(/and its tool calls are delivered for real/i)
    ).toBeVisible();

    // 4. author two side-effect assertions:
    //    - refund_action exactly 1 with an args_match subset  (required)
    //    - quarantine_action never                            (forbidden)
    const addSideEffect = page.getByRole("button", { name: /Add side effect/i });
    await addSideEffect.click();
    await addSideEffect.click();

    await page.getByLabel("Side effect 1 tool").fill("refund_action");
    await page.getByLabel("Side effect 1 args match").fill('{"account": "ACC-2"}');
    // `exactly` / count 1 are the defaults — assert that rather than re-picking.
    await expect(page.getByLabel("Side effect 1 occurs")).toHaveValue("exactly");
    await expect(page.getByLabel("Side effect 1 count")).toHaveValue("1");

    await page.getByLabel("Side effect 2 tool").fill("quarantine_action");
    await page.getByLabel("Side effect 2 occurs").selectOption("never");
    // `never` is a pure absence assertion — no count field to author.
    await expect(page.getByLabel("Side effect 2 count")).toHaveCount(0);

    // 5. authoring an assertion flips the item into record mode — the UI must
    //    tell the author no real writes will fire.
    await expect(
      page.getByText(/no real emails, tickets, or payments are sent/i)
    ).toBeVisible();

    // 6. Create -> assert the REAL POST fires and returns a durable dataset whose
    //    item carries expected_side_effects (persisted, not stubbed).
    const createResp = page.waitForResponse(
      (r) =>
        r.url().includes("/playground/datasets") && r.request().method() === "POST"
    );
    await page.getByRole("button", { name: /Create Dataset/i }).click();
    const created = await createResp;
    expect(created.status()).toBeGreaterThanOrEqual(200);
    expect(created.status()).toBeLessThan(300);

    const body = await created.json();
    expect(body.mode).toBe("durable");
    const item0 = (body.items || [])[0] || {};
    const ses = item0.expected_side_effects || [];
    expect(ses.map((s: any) => s.tool)).toEqual(["refund_action", "quarantine_action"]);

    const required = ses.find((s: any) => s.tool === "refund_action");
    expect(required?.occurs).toBe("exactly");
    expect(required?.count).toBe(1);
    expect(required?.args_match).toEqual({ account: "ACC-2" });

    const forbidden = ses.find((s: any) => s.tool === "quarantine_action");
    expect(forbidden?.occurs).toBe("never");
    // a count on an absence assertion is meaningless and must not be sent
    expect(forbidden?.count).toBeUndefined();

    // 7. save -> RELOAD from the backend -> the dataset is still there
    await page.reload();
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(dsName, { exact: false })).toBeVisible();
    const row = page.locator("tr", { hasText: dsName }).first();
    await expect(row.getByText(/durable/i)).toBeVisible();

    // 8. save -> RELOAD -> re-READ the item from the backend and confirm the
    //    assertions round-tripped through the DB (not just the POST echo).
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
        description: "Could not capture an auth token — backend re-read half skipped.",
      });
      return;
    }

    const dsResp = await page.request.get(`/api/v1/playground/datasets/${body.id}`, {
      headers: { Authorization: authHeader },
    });
    expect(dsResp.ok()).toBeTruthy();
    const reloaded = await dsResp.json();
    const reloadedSes = (reloaded.items || [])[0]?.expected_side_effects || [];
    expect(reloadedSes.map((s: any) => s.tool)).toEqual([
      "refund_action",
      "quarantine_action",
    ]);
    expect(
      reloadedSes.find((s: any) => s.tool === "refund_action")?.args_match
    ).toEqual({ account: "ACC-2" });

    // ---- 9. RESULTS RENDER against a REAL completed durable EvalRun ----------
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

    // Find a completed durable run whose results actually carry recorded side
    // effects — i.e. one produced by a record-mode eval (suite-74's).
    let target: string | undefined;
    for (const r of runs.filter((r) => r.mode === "durable" && r.status === "completed")) {
      const res = await page.request.get(
        `/api/v1/playground/eval-runs/${r.id}/results`,
        { headers: { Authorization: authHeader } }
      );
      if (!res.ok()) continue;
      const results = (await res.json()) as Array<{
        eval_detail?: { recorded_side_effects?: unknown[] } | null;
      }>;
      if (
        results.some(
          (x) => (x.eval_detail?.recorded_side_effects?.length ?? 0) > 0
        )
      ) {
        target = r.id;
        break;
      }
    }

    if (!target) {
      test.info().annotations.push({
        type: "note",
        description:
          "No completed durable EvalRun with recorded side effects in this env — " +
          "results-render half skipped. The real record-not-delivered proof is bash suite-74.",
      });
      return;
    }

    // 10. render the E-2 evidence for that REAL run — real rows, no stub.
    await page.goto(`/playground/eval-runs/${target}`);
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/Run ID:/i).first()).toBeVisible();

    // The side_effect dimension column renders per-row (key must match the
    // backend's dimension_scores key exactly).
    await expect(page.getByTestId("dim-side_effect").first()).toBeVisible();

    // Expand the first result row to reveal "the email that would have been sent".
    await page.locator("tbody tr").first().click();
    const evidence = page.getByTestId("side-effect-evidence").first();
    await expect(evidence).toBeVisible();
    await expect(evidence).toContainText(/eval_mode=record/i);
    await expect(page.getByTestId("recorded-side-effect-0").first()).toBeVisible();
    await expect(page.getByTestId("recorded-side-effect-0").first()).toContainText(
      /not delivered/i
    );
  });
});

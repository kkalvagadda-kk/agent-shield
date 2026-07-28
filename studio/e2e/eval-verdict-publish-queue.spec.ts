import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// eval-verdict-publish-queue.spec.ts
//
//   The layer that actually matters for this bug: a HUMAN approves a release by
//   reading a number on /admin/publish-requests. suite-89 proves the API returns
//   the right number; only this proves the screen SHOWS it.
//
//   Slice 0 fixed two things a reviewer could not previously see:
//     1. the score could belong to a DIFFERENT VERSION than the one being
//        published (the eval was resolved by agent name alone, keyed by asset id);
//     2. it was graded against a hardcoded 0.7 rather than the threshold that run
//        actually used, so a 0.85 run on a 0.9-threshold dataset rendered "passed"
//        while the publish gate refused it.
//
//   A: a request pinning a version whose eval scored 0.85 against a 0.9 threshold
//      renders the "0.85 / needs 0.90" label and a NON-green badge, and carries no
//      stale provenance chip.
//   B: save -> RELOAD -> the same verdict and label survive, read back from the
//      backend (DoD #2).
//
//   Fixtures are created IN-SPEC through the real REST API and cleaned up. Nothing
//   is scavenged from existing rows: a past spec's verdict tracked leftover state
//   because it grabbed "the first matching row". No page.route — a stubbed spec is
//   still a fake, and it was a route-stubbed spec that missed the mixed-content bug.
// ---------------------------------------------------------------------------

const TS = Date.now();
const ADMIN = {
  "X-User-Sub": "047fad5f-f38c-430a-bfba-6e4d9009314b",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

const AGENT_NAME = `e2e-evq-agent-${TS}`;
const DATASET_NAME = `e2e-evq-ds-${TS}`;

// The whole point: this run scores BELOW its own threshold. Under the old
// hardcoded 0.7 the UI would render it green/"passed"; under the run's real 0.9
// it must not.
const SCORE = 0.85;
const THRESHOLD = 0.9;

test.describe("publish queue — the verdict uses the run's own threshold", () => {
  let api: APIRequestContext;
  let ready = false;
  let versionId = "";

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });

    const fail = async (label: string, r: { status(): number; text(): Promise<string> }) => {
      // eslint-disable-next-line no-console
      console.log(`fixture ${label} failed: ${r.status()} ${(await r.text()).slice(0, 300)}`);
      return false;
    };

    const agentRes = await api.post("/api/v1/agents/", {
      data: {
        name: AGENT_NAME,
        team: "default",
        agent_type: "declarative",
        description: "eval-verdict publish-queue e2e fixture",
      },
    });
    if (!agentRes.ok()) { ready = await fail("agent", agentRes); return; }

    const verRes = await api.post(`/api/v1/agents/${AGENT_NAME}/versions`, {
      data: { version_tag: "v1", image_tag: "stub:v1", tools: [] },
    });
    if (!verRes.ok()) { ready = await fail("version", verRes); return; }
    versionId = (await verRes.json()).id;

    const dsRes = await api.post("/api/v1/playground/datasets", {
      data: {
        name: DATASET_NAME,
        mode: "reactive",
        items: [{ kind: "reactive", input_message: "ping", expected_output: "pong" }],
      },
    });
    if (!dsRes.ok()) { ready = await fail("dataset", dsRes); return; }
    const datasetId = (await dsRes.json()).id;

    // An eval on THIS version, with an explicit threshold the score does not clear.
    const runRes = await api.post("/api/v1/playground/eval-runs", {
      data: {
        dataset_id: datasetId,
        agent_name: AGENT_NAME,
        agent_version_id: versionId,
        pass_threshold: THRESHOLD,
      },
    });
    if (!runRes.ok()) { ready = await fail("eval run", runRes); return; }
    const runId = (await runRes.json()).id;

    // POST /playground/eval-runs LAUNCHES A REAL eval-runner Job. That Job will
    // PATCH the run with its own score when it finishes — overwriting anything set
    // before then. An earlier version of this fixture patched 0.85 immediately and
    // was FLAKY: the first test won the race and saw 0.85, the second ran ~20s
    // later and saw the Job's real 0.00. So: let the run SETTLE first, then pin the
    // score. Once terminal, nothing writes to it again.
    const settle = async () => {
      for (let i = 0; i < 40; i++) {
        const r = await api.get(`/api/v1/playground/eval-runs/${runId}`);
        if (r.ok()) {
          const s = (await r.json()).status;
          if (s === "completed" || s === "failed") return true;
        }
        await new Promise((res) => setTimeout(res, 3000));
      }
      return false;
    };
    if (!(await settle())) {
      // eslint-disable-next-line no-console
      console.log("eval run never reached a terminal state within 120s");
      ready = false;
      return;
    }

    const patchRes = await api.patch(`/api/v1/playground/eval-runs/${runId}`, {
      data: {
        status: "completed",
        overall_score: SCORE,
        total_items: 1,
        passed_count: 0,
        failed_count: 1,
      },
    });
    if (!patchRes.ok()) { ready = await fail("pin score", patchRes); return; }

    // Publishing is gated on eval_passed; this run deliberately did not pass, so
    // attest it the way an operator would, then publish — producing a request that
    // pins a version WITH an eval that did not clear its own bar.
    const markRes = await api.patch(
      `/api/v1/agents/${AGENT_NAME}/versions/${versionId}`,
      { data: { eval_passed: true } },
    );
    if (!markRes.ok()) { ready = await fail("mark eval_passed", markRes); return; }

    const pubRes = await api.post(`/api/v1/agents/${AGENT_NAME}/publish`, {
      data: { version_id: versionId, dependency_declaration: {} },
    });
    if (!pubRes.ok()) { ready = await fail("publish", pubRes); return; }

    ready = true;
  });

  test.afterAll(async () => {
    await api.delete(`/api/v1/agents/${AGENT_NAME}`).catch(() => {});
    await api.dispose();
  });

  const rowFor = (page: import("@playwright/test").Page) =>
    page.locator("tr").filter({ has: page.getByText(AGENT_NAME, { exact: true }) });

  test("A: renders the verdict against the run's OWN threshold, not a literal", async ({ page }) => {
    test.skip(!ready, "could not build the publish-request fixture (env gap)");
    test.setTimeout(90_000);

    const queueResp = page.waitForResponse(
      (r) => r.url().includes("/admin/publish-requests") && r.request().method() === "GET",
      { timeout: 30_000 },
    );
    await page.goto("/admin/publish-requests");
    await queueResp;

    const row = rowFor(page);
    await expect(row).toHaveCount(1, { timeout: 20_000 });

    // The reason a human can see WHY a good-looking score will not publish.
    await expect(row.getByTestId("eval-threshold-label")).toHaveText("0.85 / needs 0.90");

    // Under the old hardcoded `>= 0.7`, 0.85 rendered GREEN. It must not now.
    const badge = row.getByRole("button", { name: /85%/ });
    await expect(badge).toBeVisible();
    await expect(badge).not.toHaveClass(/green/);

    // The eval belongs to the pinned version, so there is nothing to warn about.
    await expect(row.getByTestId("eval-provenance-warning")).toHaveCount(0);
  });

  test("B: reload → the verdict and threshold survived (DoD #2 round-trip)", async ({ page }) => {
    test.skip(!ready, "could not build the publish-request fixture (env gap)");
    test.setTimeout(90_000);

    await page.goto("/admin/publish-requests");
    await page.waitForLoadState("networkidle");
    await expect(rowFor(page).getByTestId("eval-threshold-label")).toHaveText(
      "0.85 / needs 0.90",
      { timeout: 20_000 },
    );

    // Reload — the round-trip guard. A verdict computed from a client-side literal
    // would survive this too, which is why the assertion is on the THRESHOLD text,
    // not merely on a colour: 0.90 can only have come from the backend.
    await page.reload();
    await page.waitForLoadState("networkidle");

    const row = rowFor(page);
    await expect(row).toHaveCount(1, { timeout: 20_000 });
    await expect(row.getByTestId("eval-threshold-label")).toHaveText("0.85 / needs 0.90");
    await expect(row.getByRole("button", { name: /85%/ })).not.toHaveClass(/green/);
  });
});

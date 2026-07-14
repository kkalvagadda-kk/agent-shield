import { test, expect, request as pwRequest, type APIRequestContext } from "@playwright/test";

// ---------------------------------------------------------------------------
// durable-stream.spec.ts
//
// REAL user-path guard for the durable playground step-stream (StepTracker SSE /
// EventSource). This is the ONLY test that can catch the "Connection lost" class:
// it drives the actual browser through login → gateway → JWT → 2-replica →
// EventSource. A localhost httpx / kubectl-exec check hits ONE replica directly and
// CANNOT reproduce it (the per-replica in-memory _STEP_EVENTS buffer only broke
// under the real LB path). See memory feedback_test_like_a_user.
//
// SELF-PROVISIONING (no-fakes, like the bash suites): creates + deploys its own
// durable agent up front and tears it down after, so it runs meaningfully on EVERY
// gate run instead of skipping when no durable agent happens to be deployed.
// ---------------------------------------------------------------------------

// Interactive platform-admin sub (the identity global-setup logs in as) so the
// self-provisioned agent is visible in the browser's deployment dropdown.
const ADMIN = {
  "X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6",
  "X-User-Team": "platform",
};
const BASE_URL =
  process.env.PLAYWRIGHT_BASE_URL || "https://agentshield.127.0.0.1.nip.io:8443";
const AGENT = `dstream-${Date.now().toString().slice(-7)}`;

test.describe("durable playground step-stream (real SSE)", () => {
  let api: APIRequestContext;
  let provisioned = false;

  test.beforeAll(async () => {
    test.setTimeout(180_000); // deploying a real pod takes a bit
    api = await pwRequest.newContext({
      baseURL: BASE_URL,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });
    const prov = await api.get("/api/v1/llm-providers/?team=platform");
    const items = (await prov.json()).items;
    if (!items?.length) return; // no provider → beforeAll leaves provisioned=false → test skips loudly
    const pid = items[0].id;
    await api.post("/api/v1/agents/", {
      data: {
        name: AGENT,
        team: "platform",
        agent_type: "declarative",
        execution_shape: "durable",
        agent_class: "daemon",
        metadata: {
          instructions: "You answer factual questions. Reply with ONLY the answer — no preamble.",
          llm_provider_id: pid,
          tools: [],
        },
      },
    });
    await api.post(`/api/v1/agents/${AGENT}/deploy`, { data: { environment: "sandbox" } });
    for (let i = 0; i < 45; i++) {
      const d = await api.get(`/api/v1/agents/${AGENT}/deployments`);
      const deps = await d.json();
      if (Array.isArray(deps) && deps.some((x) => x.status === "running" && x.environment === "sandbox")) {
        provisioned = true;
        break;
      }
      await new Promise((r) => setTimeout(r, 3000));
    }
  });

  test.afterAll(async () => {
    if (api) {
      await api.delete(`/api/v1/agents/${AGENT}`).catch(() => {});
      await api.dispose();
    }
  });

  test("Launch Run streams steps with no 'Connection lost'", async ({ page }) => {
    if (!provisioned) {
      test.skip(true, `could not provision a running durable agent '${AGENT}' — env limit`);
      return;
    }

    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    // The Eval Runs page selects by DEPLOYMENT ("SELECT DEPLOYMENT" → "-- pick a
    // deployment --"). Options load async (React Query) — wait, then pick OUR agent.
    const depSelect = page.locator("select").filter({ hasText: /pick a deployment/i });
    await expect(depSelect).toBeVisible();
    await expect
      .poll(async () => depSelect.locator("option").count(), { timeout: 20000 })
      .toBeGreaterThan(1);
    const options = await depSelect.locator("option").allTextContents();
    const pick = options.find((o) => o.includes(AGENT));
    expect(pick, `self-provisioned agent ${AGENT} should appear in the deployment dropdown`).toBeTruthy();
    await depSelect.selectOption({ label: pick! });
    await page.waitForLoadState("networkidle");

    // Durable agent → RunLauncher (reactive uses the chat pane).
    const payload = page.locator('textarea[placeholder*="message"]');
    await expect(payload).toBeVisible({ timeout: 10000 });
    await payload.fill('{"message": "What is the capital of Texas?"}');

    // Real POST /playground/runs (no stub); the browser then opens the SSE stream.
    const runResp = page.waitForResponse(
      (r) => r.url().includes("/playground/runs") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: /Launch Run/i }).click();
    const posted = await runResp;
    expect(posted.status(), "POST /playground/runs should succeed").toBeLessThan(300);

    // StepTracker mounts and a real step streams in via EventSource.
    await expect(page.getByRole("heading", { name: /^Steps$/i })).toBeVisible({ timeout: 15000 });
    await expect
      .poll(
        async () => {
          if ((await page.getByText(/Connection lost/i).count()) > 0) return "connection-lost";
          const step = await page.getByText(/running|completed|awaiting/i).count();
          return step > 0 ? "streaming" : "waiting";
        },
        { timeout: 45000, intervals: [1000] },
      )
      .toBe("streaming");
    await expect(page.getByText(/Connection lost/i)).toHaveCount(0);
  });
});

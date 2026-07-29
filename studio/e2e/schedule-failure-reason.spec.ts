import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// schedule-failure-reason.spec.ts
//
//   THE BUG, IN A BROWSER. A scheduled run failed hourly and the deployment
//   overview rendered a red "Failing" badge directly above "Last Run: No runs
//   yet" — no reason anywhere on screen. The reason existed the whole time, in
//   `agent_runs.error_message`; nothing rendered it, and the two cards asked
//   different questions:
//
//     badge  ← GET /agents/{name}/health          (every run for the agent)
//     list   ← GET /deployments/{id}/runs         (runs carrying that deployment's FK)
//
//   Every trigger-driven run has BOTH deployment FK columns NULL, so the list was
//   permanently empty while the badge went red. 1,328 runs, none of them visible.
//
//   WHY THIS SPEC AND NOT ONLY VITEST: the Vitest suite proves the component
//   renders a reason WHEN HANDED ONE — it mocks `registryApi` wholesale, so it
//   cannot catch the component asking the wrong endpoint, the endpoint not
//   existing, or the field being dropped in serialization. That whole seam is
//   exactly where this bug lived. Only a real browser against the real API can
//   fail on it. (CLAUDE.md: "test the layer that can actually fail".)
//
//   FIXTURE STRATEGY: the failure is seeded through the REAL door. We create an
//   agent, deploy it to SANDBOX ONLY, arm a schedule, and fire
//   /internal/runs/start — the exact production sequence that produced the
//   original defect. No hand-written run row: a fabricated fixture would prove
//   the renderer works and nothing about the path that broke.
// ---------------------------------------------------------------------------

const SFX = Date.now().toString(36);
const AGENT = `cip-sched-fail-${SFX}`;

// Read off the page, not the DB — this is the operator's actual experience.
const REASON_RE = /no running production deployment/i;
const DNS_RE = /name or service not known|errno -2/i;

test.describe("scheduled overview explains a failed run", () => {
  test("a sandbox-only schedule fire renders its reason, not a bare Failing badge", async ({
    page,
    request,
  }) => {
    // ── Seed through the real API, in the browser's authenticated context ──────
    // `request` inherits storageState from global-setup, so these calls carry the
    // same Keycloak session the UI uses.
    const providers = await (
      await request.get(`/api/v1/llm-providers/?team=platform`)
    ).json();
    const providerId = providers.items?.[0]?.id;
    test.skip(!providerId, "no LLM provider seeded in this environment");

    const created = await request.post(`/api/v1/agents/`, {
      data: {
        name: AGENT,
        team: "platform",
        agent_type: "declarative",
        execution_shape: "durable",
        agent_class: "daemon",
        metadata: {
          instructions: "Autonomous check agent. Reply READY.",
          llm_provider_id: providerId,
          tools: [],
        },
      },
    });
    expect(created.ok(), `create agent: ${created.status()}`).toBeTruthy();

    try {
      // SANDBOX ONLY — never deployed to production. This is the whole fixture.
      await request.post(`/api/v1/agents/${AGENT}/deploy`, {
        data: { environment: "sandbox" },
      });

      const trig = await request.post(`/api/v1/agents/${AGENT}/triggers`, {
        data: {
          trigger_type: "schedule",
          cron_expression: "0 0 * * *",
          input_payload: { message: "scheduled check" },
          alert_on_failure: true,
        },
      });
      expect(trig.ok(), `create trigger: ${trig.status()}`).toBeTruthy();
      const triggerId = (await trig.json()).id;

      // Fire the REAL door the scheduler hits on a cron tick.
      const fired = await request.post(`/api/v1/internal/runs/start`, {
        data: {
          agent_name: AGENT,
          trigger_type: "schedule",
          trigger_id: triggerId,
          run_by: "serviceaccount:scheduler",
        },
      });
      // Refusal is RECORDED, not raised — a 409 would have been a log line nobody
      // reads. The run row is what makes the failure visible downstream.
      expect(fired.ok(), `internal run start: ${fired.status()}`).toBeTruthy();

      // ── Now the part only a browser can prove ────────────────────────────────
      await page.goto(`/agents/${AGENT}`);
      await page.waitForLoadState("networkidle");

      // Open the sandbox deployment; the overview router resolves to the scheduled
      // surface because the agent has a schedule trigger.
      const depLink = page.locator("main a", { hasText: `${AGENT}-` }).first();
      await expect(depLink).toBeVisible({ timeout: 20_000 });

      // Assert the surface actually calls the trigger-scoped producer. If it ever
      // reverts to the deployment-scoped read this waitForResponse times out —
      // which is the regression, caught at the wiring rather than at the pixels.
      const runsResp = page.waitForResponse(
        (r) =>
          /\/api\/v1\/agents\/[^/]+\/triggers\/[^/]+\/runs/.test(r.url()) &&
          r.request().method() === "GET",
        { timeout: 25_000 }
      );
      await depLink.click();
      await page.waitForURL("**/d/**", { timeout: 15_000 });
      expect((await runsResp).ok()).toBeTruthy();

      // The reason is ON SCREEN. This is the assertion the whole change exists for.
      const reason = page.getByTestId("last-run-error");
      await expect(reason).toBeVisible({ timeout: 20_000 });
      await expect(reason).toHaveText(REASON_RE);

      // …and it names the cause, not the symptom. A DNS error here means the
      // dispatch resolver was bypassed and a URL got rebuilt at the point of use.
      await expect(reason).not.toHaveText(DNS_RE);

      // The original contradiction must be gone: a failing schedule cannot also
      // claim it has no runs.
      await expect(page.getByText(/no runs yet/i)).toHaveCount(0);

      // Alert honesty (D): alerts are on with no address, so the card must say the
      // notification goes nowhere rather than showing a reassuring "On".
      await expect(page.getByTestId("alert-config-incomplete")).toBeVisible();
    } finally {
      // Best-effort teardown — a cleanup hiccup must not mask the verdict.
      await request.delete(`/api/v1/agents/${AGENT}`).catch(() => undefined);
    }
  });
});

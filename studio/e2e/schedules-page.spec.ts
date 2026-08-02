import { test, expect } from "@playwright/test";
import { captureAuthHeaders } from "./lib/apiAuth";

// ---------------------------------------------------------------------------
// schedules-page.spec.ts — the cross-artifact operations page (R5)
//
// WHY THIS LAYER, specifically.
//   The Vitest suite mocks `registryApi` wholesale, so it proves the component behaves
//   correctly WHEN HANDED data and WHEN ITS CALLS SUCCEED. It cannot see what the
//   endpoint emits, and it cannot see a write that the server accepts and ignores.
//   Both of those have already shipped here:
//
//     * `armed_at` was synthesised server-side from `created_at` — non-null on every
//       row — so deleted agents the lifecycle gate had just disarmed rendered an
//       "Armed" pill beside "this schedule is disabled". The endpoint suite never read
//       the field; the Vitest fixtures already had it right. Only the real page showed
//       it, and only by looking.
//     * The Disarm button PATCHed `{ armed: false }`. `AgentTriggerUpdate` declares no
//       such field, so FastAPI dropped it, the handler's `exclude_none` loop saw an
//       empty body, and the write answered 200 having changed nothing — while the toast
//       said "Disarmed". The Vitest test asserted `disarmTrigger` was CALLED, which it
//       was. A mock cannot fail a call the server silently no-ops.
//
//   Hence the shape below: the endpoint's own JSON drives the real render, and the one
//   write on this page is proven by reload, not by the toast.
//
// FIXTURE STRATEGY: seeded through the real API, in the two states that matter — one
//   that CAN fire and one that cannot. A single-state fixture passes just as well if
//   the page hard-codes either verdict, which is the mistake `will_fire` exists to
//   prevent.
// ---------------------------------------------------------------------------

const SFX = Date.now().toString(36);
const SBX = `sp-sbx-${SFX}`;   // sandbox only  -> armed, but cannot dispatch
const DEAD = `sp-dead-${SFX}`; // deleted       -> disarmed by the lifecycle gate

const SCHEDULES_RE = /\/api\/v1\/schedules(\?|$)/;

test.describe("schedules page", () => {
  test("reports per row whether a schedule will fire, and disarming survives reload", async ({
    page,
  }) => {
    // Cookies alone are not enough — every route here is `require_user`.
    const H = await captureAuthHeaders(page);
    const api = page.request;
    const providers = await (
      await api.get(`/api/v1/llm-providers/?team=platform`, { headers: H })
    ).json();
    const providerId = (providers.items ?? providers)?.[0]?.id;
    test.skip(!providerId, "no LLM provider seeded in this environment");

    const mkAgent = (name: string) =>
      api.post(`/api/v1/agents/`, {
        headers: H,
        data: {
          name,
          team: "platform",
          agent_type: "declarative",
          execution_shape: "durable",
          agent_class: "daemon",
          metadata: {
            instructions: "Autonomous check agent.",
            llm_provider_id: providerId,
            tools: [],
          },
        },
      });
    const arm = (name: string) =>
      api.post(`/api/v1/agents/${name}/triggers`, {
        headers: H,
        data: { trigger_type: "schedule", cron_expression: "0 0 * * *", alert_on_failure: false },
      });

    expect((await mkAgent(SBX)).ok(), `create ${SBX}`).toBeTruthy();
    expect((await mkAgent(DEAD)).ok(), `create ${DEAD}`).toBeTruthy();
    await api.post(`/api/v1/agents/${SBX}/deploy`, { headers: H, data: { environment: "sandbox" } });
    const tSbx: string = (await (await arm(SBX)).json()).id;
    const tDead: string = (await (await arm(DEAD)).json()).id;
    // Deleting builds the other half of the fixture: the write-side lifecycle gate
    // disarms this trigger, so the page must render it disarmed WITH its reason.
    expect((await api.delete(`/api/v1/agents/${DEAD}`, { headers: H })).ok()).toBeTruthy();

    try {
      const listed = page.waitForResponse(
        (r) => SCHEDULES_RE.test(r.url()) && r.request().method() === "GET",
        { timeout: 30_000 },
      );
      await page.goto("/schedules");
      expect((await listed).ok(), "GET /api/v1/schedules").toBeTruthy();

      // Un-gated nav. This route was DEMO-only until routers/schedules.py existed, and
      // a nav item pointing at an unregistered route is a dead link.
      await expect(page.getByRole("link", { name: /^Schedules/ })).toBeVisible();

      const sbxRow = page.getByTestId(`schedules-row-${tSbx}`);
      const deadRow = page.getByTestId(`schedules-row-${tDead}`);
      await expect(sbxRow).toBeVisible({ timeout: 20_000 });
      void tDead;

      // ── Armed, but blocked, and the reason names the CAUSE ───────────────────
      await expect(sbxRow.getByTestId("schedule-armed-badge")).toHaveText("Armed");
      await expect(sbxRow.getByTestId("schedule-will-not-fire")).toContainText(
        /production deployment|publish/i,
      );
      // A transport symptom here ("Name or service not known") would mean `will_fire`
      // stopped consulting `resolve_dispatch_target` and started reporting a dead
      // dispatch after the fact — the exact regression this page was built to end.
      await expect(sbxRow).not.toContainText(/name or service not known|errno -2/i);

      // ── Deleted agent: its SCHEDULE is gone from the page ────────────────────
      // CONTRACT CHANGE (2026-08-02). This used to assert the row was still LISTED
      // and Disarmed — the zombie-visibility guarantee. Agent delete now REMOVES
      // schedule triggers outright (trigger_lifecycle.delete_schedule_triggers +
      // migration 0078), because a disarmed schedule on a deleted agent is inert
      // (T-S95-004) and was two thirds of this page's rows.
      //
      // The guarantee is narrowed, not dropped: a kept-but-disarmed trigger on a
      // dead artifact must still be listed with a reason. Webhooks are the kept
      // case (deleting one cascades away its webhook_clients), and suite-96's
      // T-S96-003/006 asserts it on the same agent. Asserted there rather than
      // here because this page is scoped to trigger_type=schedule.
      await expect(deadRow).toHaveCount(0);

      // ── The one write on this page, proven by reload ─────────────────────────
      // REGRESSION (Disarm -> `{armed:false}` -> 200, no-op): the toast and the
      // optimistic refetch both looked correct. Only re-reading from the backend
      // distinguishes a write that landed from one the server dropped.
      const patched = page.waitForResponse(
        (r) => r.url().includes(`/triggers/${tSbx}`) && r.request().method() === "PATCH",
      );
      await sbxRow.getByTestId("schedule-arm-toggle").click();
      expect((await patched).ok(), "PATCH trigger").toBeTruthy();
      await expect(sbxRow.getByTestId("schedule-armed-badge")).toHaveText("Disarmed");

      const reListed = page.waitForResponse(
        (r) => SCHEDULES_RE.test(r.url()) && r.request().method() === "GET",
        { timeout: 30_000 },
      );
      await page.reload();
      await reListed;
      await expect(page.getByTestId(`schedules-row-${tSbx}`).getByTestId("schedule-armed-badge"))
        .toHaveText("Disarmed", { timeout: 20_000 });

      // Re-arming is the same control in the other direction — one field, one writer.
      const rearmed = page.waitForResponse(
        (r) => r.url().includes(`/triggers/${tSbx}`) && r.request().method() === "PATCH",
      );
      await page.getByTestId(`schedules-row-${tSbx}`).getByTestId("schedule-arm-toggle").click();
      expect((await rearmed).ok()).toBeTruthy();
      await expect(
        page.getByTestId(`schedules-row-${tSbx}`).getByTestId("schedule-armed-badge"),
      ).toHaveText("Armed");
      // Re-enable clears the disarm record, so no stale "disarmed by..." line survives
      // beside a live schedule. A stale explanation is read as a current one.
      await expect(
        page.getByTestId(`schedules-row-${tSbx}`).getByTestId("schedule-disarm-reason"),
      ).toHaveCount(0);

      // ── The banner counts only what is switched ON but blocked ───────────────
      // Counting disarmed rows would make the page nag about schedules nobody expects
      // to run, and an alarm that cries wolf stops being read.
      await expect(page.getByTestId("schedules-attention-banner")).toContainText(/will not fire/i);
    } finally {
      await api.delete(`/api/v1/agents/${SBX}`, { headers: H }).catch(() => undefined);
      await api.delete(`/api/v1/agents/${DEAD}`, { headers: H }).catch(() => undefined);
    }
  });
});

// ── R5 second pass: run history + edit in place ──────────────────────────────
// Both are UX-facing writes/reads the Vitest suite can only see through mocks. The
// sparkline in particular is derived server-side (`recent_runs`), so a mock proves
// the component renders an array — not that the endpoint sends one.
test.describe("schedules page — history and editing", () => {
  test("shows a run-history strip, and an edit survives a reload", async ({ page }) => {
    const H = await captureAuthHeaders(page);
    const api = page.request;
    const providers = await (
      await api.get(`/api/v1/llm-providers/?team=platform`, { headers: H })
    ).json();
    const providerId = (providers.items ?? providers)?.[0]?.id;
    test.skip(!providerId, "no LLM provider seeded in this environment");

    const NAME = `sp-edit-${Date.now().toString(36)}`;
    expect(
      (await api.post(`/api/v1/agents/`, {
        headers: H,
        data: {
          name: NAME, team: "platform", agent_type: "declarative",
          execution_shape: "durable", agent_class: "daemon",
          metadata: { instructions: "Edit probe.", llm_provider_id: providerId, tools: [] },
        },
      })).ok(),
    ).toBeTruthy();
    const trig = await (
      await api.post(`/api/v1/agents/${NAME}/triggers`, {
        headers: H,
        data: { trigger_type: "schedule", cron_expression: "0 9 * * 1", timezone: "UTC", alert_on_failure: false },
      })
    ).json();

    try {
      const listed = page.waitForResponse(
        (r) => SCHEDULES_RE.test(r.url()) && r.request().method() === "GET",
        { timeout: 30_000 },
      );
      await page.goto("/schedules");
      await listed;

      const row = page.getByTestId(`schedules-row-${trig.id}`);
      await expect(row).toBeVisible({ timeout: 20_000 });

      // ── History ────────────────────────────────────────────────────────────
      // A brand-new schedule has never run. It must say so, NOT render ten grey
      // bars — a newly armed schedule looking like ten failures is the specific
      // misread this component was written to avoid.
      await expect(row.getByTestId("run-sparkline-empty")).toBeVisible();
      await expect(row.getByTestId("run-sparkline")).toHaveCount(0);

      // ── Edit, then RELOAD ──────────────────────────────────────────────────
      // The assertion is the reload. An in-place edit that only updates the React
      // Query cache looks identical until the page is re-fetched.
      await row.getByTestId("schedule-edit-btn").click();
      const modal = page.getByTestId("edit-schedule-modal");
      await expect(modal).toBeVisible();

      const cron = modal.getByTestId("edit-schedule-cron");
      await expect(cron).toHaveValue("0 9 * * 1");
      await cron.fill("0 9 *");
      // Five fields or nothing — save must be blocked and say what it counted.
      await expect(modal.getByTestId("edit-schedule-save")).toBeDisabled();
      await expect(modal).toContainText(/3 fields/i);

      await cron.fill("30 6 * * *");
      const patched = page.waitForResponse(
        (r) => r.url().includes(`/triggers/${trig.id}`) && r.request().method() === "PATCH",
      );
      await modal.getByTestId("edit-schedule-save").click();
      expect((await patched).ok(), "PATCH trigger").toBeTruthy();
      await expect(page.getByTestId("edit-schedule-modal")).toHaveCount(0);

      const reListed = page.waitForResponse(
        (r) => SCHEDULES_RE.test(r.url()) && r.request().method() === "GET",
        { timeout: 30_000 },
      );
      await page.reload();
      await reListed;
      await expect(page.getByTestId(`schedules-row-${trig.id}`)).toContainText("30 6 * * *", {
        timeout: 20_000,
      });
    } finally {
      await api.delete(`/api/v1/agents/${NAME}`, { headers: H }).catch(() => undefined);
    }
  });
});

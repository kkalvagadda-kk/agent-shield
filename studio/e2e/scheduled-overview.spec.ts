import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// scheduled-overview.spec.ts  (WS-3 Phase 6, T010)
//   Proves the SCHEDULED OPERATE SURFACE in a real browser, against the live
//   deployed Studio (real Keycloak login, real gateway, NO route stubs):
//
//     agent detail → open a sandbox deployment → Deployment Overview resolves to
//     <OverviewScheduled> (the agent has a schedule trigger) → the next-fire /
//     schedule-health / failure-alerts / last-run cards render, driven by the
//     REAL GET /agents/{name}/health producer (asserted via waitForResponse) →
//     then in Settings: set the trigger's failure-alert email, wait on the real
//     PATCH /agents/{name}/triggers/{id}, RELOAD from the backend, and confirm
//     the value survived the round-trip (save → reload → assert, DoD #2).
//
//   Why we target a SEEDED already-scheduled + already-deployed agent instead of
//   creating one in-browser: reaching <OverviewScheduled> requires (a) an agent
//   with a schedule trigger AND (b) an existing deployment to open. Minting both
//   in-browser means the in-app Deploy modal + deploy-controller reconcile — extra
//   moving parts that make the setup brittle and are not what this surface proves.
//   `refund-processor` is seeded with exactly one enabled schedule trigger (cron
//   `0 9 * * 1`), no webhook trigger (so the overview resolves to Scheduled, not
//   Event-Driven), and sandbox deployments — the minimal real state this surface
//   needs. It is owned by the interactive platform-admin (the e2e login user), so
//   the deny-by-default registry serves it to us.
//
//   Boundary (honest): the health-endpoint SHAPE (mode/next_fire_at/health/
//   last_run_status/missed_fires) is proven end-to-end by the bash suites (CP3b),
//   and the card-rendering logic across every health payload by Vitest
//   (OverviewScheduled.test.tsx). What ONLY this browser spec can prove — and the
//   API-only bash suites cannot — is the live wiring: that the deployment-overview
//   router actually mounts <OverviewScheduled> for a scheduled agent, that the
//   component actually fires the real GET /health, that the cards paint, and that
//   the Settings alert-email edit persists through a real PATCH + reload. We assert
//   UI wiring + persistence, not a scheduled RUN firing (few agent pods deployed —
//   the same boundary every other UI spec accepts).
// ---------------------------------------------------------------------------

// Seeded scheduled agent (see header). One enabled schedule trigger, no webhook.
const AGENT = "refund-processor";
const ALERT_EMAIL = `oncall-${Date.now()}@example.com`;

test("scheduled overview renders next-fire/health/last-run + alert config persists on reload", async ({
  page,
}) => {
  // 1. Agent detail → the Deployments tab (default) lists sandbox deployments.
  await page.goto(`/agents/${AGENT}`);
  await page.waitForLoadState("networkidle");

  // Any sandbox deployment resolves to the same scheduled overview (the router
  // keys off the agent's triggers, not the deployment's run status), so open the
  // first one listed.
  const depLink = page.locator("main a", { hasText: `${AGENT}-` }).first();
  await expect(depLink).toBeVisible({ timeout: 15_000 });

  // The scheduled overview fetches the mode-aware health producer on mount.
  const healthResp = page.waitForResponse(
    (r) =>
      /\/api\/v1\/agents\/[^/]+\/health/.test(r.url()) &&
      r.request().method() === "GET",
    { timeout: 20_000 }
  );
  await depLink.click();
  await page.waitForURL("**/d/**", { timeout: 10_000 });
  const hr = await healthResp; // the operate surface actually called the health producer
  expect(hr.ok()).toBeTruthy();

  // 2. <OverviewScheduled> cards render: next-fire, failure-alert summary, last-run.
  await expect(page.getByText("Next Fire")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Failure Alerts")).toBeVisible();
  await expect(page.getByText("Last Run")).toBeVisible();

  // 3. save → reload → assert survived. Set the trigger's failure-alert email in
  //    Settings, wait on the real PATCH, reload from the backend, confirm it stuck.
  await page.goto(`/agents/${AGENT}`);
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: /^settings$/i }).click();

  // TriggerRow's failure-alert email input (placeholder alerts@example.com). The
  // closed NewScheduleForm (placeholder oncall@example.com) is not in the DOM, so
  // this placeholder resolves uniquely to the seeded trigger's row.
  const emailInput = page.getByPlaceholder("alerts@example.com");
  await expect(emailInput).toBeVisible({ timeout: 10_000 });
  await emailInput.fill(ALERT_EMAIL);

  const patchDone = page.waitForResponse(
    (r) =>
      /\/api\/v1\/agents\/[^/]+\/triggers\/[^/]+$/.test(r.url()) &&
      r.request().method() === "PATCH",
    { timeout: 20_000 }
  );
  await page.getByRole("button", { name: /^Save$/ }).click();
  const patchResp = await patchDone;
  expect(patchResp.ok()).toBeTruthy();

  // Reload from the backend and confirm the alert email survived the round-trip.
  await page.reload();
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: /^settings$/i }).click();
  const reloadedEmail = page.getByPlaceholder("alerts@example.com");
  await expect(reloadedEmail).toHaveValue(ALERT_EMAIL, { timeout: 10_000 });
});

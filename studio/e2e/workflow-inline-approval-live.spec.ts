import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// workflow-inline-approval-live.spec.ts — REAL browser run, no route stubs.
//
// Drives the actual flow-conditional workflow from the builder Run panel: the
// router LLM classifies "refund" → routes to wf-payout → wf-payout calls its
// high-risk tool → PARKS → the inline ApprovalCard must render → Approve fires
// the decide (PATCH /approvals/{id}) and the card clears.
//
// This is deliberately un-stubbed: the earlier route-stubbed test could not catch
// the mixed-content redirect that silently killed the /approvals fetch in a real
// HTTPS browser (the card never rendered). A real run against the real backend is
// the only thing that exercises the edge → nginx → registry-api redirect path.
// Depends on flow-conditional (id below) + wf-payout being deployed. It drives a
// real LLM, so allow generous timeouts.
// ---------------------------------------------------------------------------
const WID = "6fc9ea22-b651-40df-8199-f3ad2bf10bd9"; // flow-conditional (2-way fork)

test("sandbox workflow parks and is approved INLINE in the run panel (real run)", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto(`/workflows/${WID}/builder`);
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: /Run Workflow/i }).click();
  await page.getByPlaceholder(/message to pass/i).fill("I want a refund of $50 for order #123");
  await page.getByRole("button", { name: /Start Run/i }).click();

  // The run reaches wf-payout and parks (real router + payout LLM dispatch).
  await expect(page.getByText(/awaiting_approval/i).first()).toBeVisible({ timeout: 90_000 });

  // The inline card + Approve button must render (the mixed-content bug hid these).
  const card = page.getByTestId("workflow-inline-approval");
  await expect(card).toBeVisible({ timeout: 20_000 });
  await expect(card.getByText("refund_action")).toBeVisible();

  // No mixed-content / blocked-request errors reached the console.
  expect(errors.filter((e) => /Mixed Content/i.test(e)), "no mixed-content block").toHaveLength(0);

  // Approve fires the console decide (PATCH /approvals/{id}) and the card clears.
  const decided = page.waitForResponse(
    (r) => r.request().method() === "PATCH" && /\/api\/v1\/approvals\//.test(r.url()),
    { timeout: 20_000 },
  );
  await card.getByRole("button", { name: /^Approve$/i }).click();
  const resp = await decided;
  expect([200, 204]).toContain(resp.status());
  await expect(card).toBeHidden({ timeout: 20_000 });
});

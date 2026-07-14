import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// approvals-inbox.spec.ts
//   Proves the REAL browser journey for the Global Approvals Inbox (WS-1 T7):
//
//     1. Navigate to /approvals.
//     2. The inbox renders the shared <ApprovalCard> (data-testid="approval-card")
//        for a pending item — tool name + risk + args all in one place (M1).
//     3. Click Approve → a PATCH /api/v1/approvals/{id} decide call fires with
//        decision=approved (the UI wiring the API-only bash suites can't see).
//     4. After the decision, the list refetch no longer returns that item →
//        the card is gone (save → reload → assert the decision took effect).
//
//   The inbox depends on real parked durable runs to be populated, which need a
//   deployed durable agent pod at an OPA gate — the same "few agent pods" fixture
//   boundary the other UI specs accept. So the pending item + the decide response
//   are stubbed via route interception; this asserts the render + decide wiring +
//   post-decision state deterministically, without a live pod. The live-pod leg
//   (a genuinely parked workflow member) is in the gap ledger / manual UI plan.
// ---------------------------------------------------------------------------

const APPROVAL_ID = "11111111-1111-1111-1111-111111111111";

const PENDING_ITEM = {
  id: APPROVAL_ID,
  agent_name: "billing-agent",
  team: "platform",
  step_name: "tool:send_email",
  tool_name: "send_email",
  risk_level: "high",
  tool_args: { to: "customer@example.com", subject: "Invoice" },
  thread_context_snippet: null,
  sla_remaining_seconds: 720,
  created_at: new Date().toISOString(),
  context: "production",
  version: 1,
};

async function stubApprovals(page: Page, decided: { value: boolean }) {
  // GET list — returns the pending item until it's decided, then empty.
  await page.route("**/api/v1/approvals?**", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: decided.value ? [] : [PENDING_ITEM] }),
    });
  });
  // PATCH decide — records the decision and flips the list to empty.
  await page.route(`**/api/v1/approvals/${APPROVAL_ID}`, async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    decided.value = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

test("approvals inbox: pending item renders <ApprovalCard>, approve fires decide + clears", async ({
  page,
}) => {
  const decided = { value: false };
  await stubApprovals(page, decided);

  await page.goto("/approvals");
  await page.waitForLoadState("networkidle");

  // Page chrome loaded.
  await expect(page.getByRole("heading", { name: /approvals inbox/i })).toBeVisible();

  // The shared ApprovalCard renders the pending item (M1 — one body, all fields).
  const card = page.getByTestId("approval-card").first();
  await expect(card).toBeVisible();
  await expect(card).toContainText("send_email");
  await expect(card).toContainText("billing-agent");
  await expect(card).toContainText(/high/i);

  // Approve → PATCH decide with decision=approved.
  const decideResp = page.waitForResponse(
    (r) =>
      r.url().includes(`/api/v1/approvals/${APPROVAL_ID}`) &&
      r.request().method() === "PATCH",
    { timeout: 15_000 }
  );
  await card.getByRole("button", { name: /approve/i }).click();
  const resp = await decideResp;
  const body = resp.request().postDataJSON() as { decision?: string };
  expect(body.decision).toBe("approved");

  // Save → reload → assert: the refetch now returns empty → card is gone.
  await expect(page.getByTestId("approval-card")).toHaveCount(0, { timeout: 15_000 });
  await expect(page.getByText(/no pending approvals/i)).toBeVisible();
});

import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// approvals-inbox.spec.ts
//   Proves the REAL browser journeys for the Global Approvals Inbox:
//     * WS-1 T7  — the shared <ApprovalCard> renders a pending item; Approve fires a
//                  real PATCH /api/v1/approvals/{id} decide; the card clears on refetch.
//     * WS-2 T019 — a DAEMON approval card renders the derived principal_display
//                  ("service:X on behalf of Y") and the inbox reviewer-role <select>
//                  filter narrows the list to that scope (the T012/T013 UI wiring).
//
//   Harness boundary (documented — same as the other UI specs): the inbox is
//   populated by real PARKED durable runs, which need a deployed agent pod at an OPA
//   gate PLUS a service-identity trigger run. A daemon trigger-run's parked approval is
//   only mintable through the cluster-internal /internal/runs/start path (no public
//   ingress) + a minutes-long pod park — NOT seedable from the browser. So the LIST is
//   served from a data fixture via route fulfill; the assertions below still prove what
//   the API-only bash suites CANNOT: that the card renders principal_display, that the
//   reviewer-role filter narrows, and that Approve fires the real decide network call.
//   The daemon principal_display / reviewer_scope / 403 / resume behaviour is proven
//   END-TO-END on REAL rows (no fakes) by scripts/e2e/suite-70-daemon-identity.sh.
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

// The list endpoint is GET /api/v1/approvals/?status=pending&… (trailing slash before
// the query — see registryApi.listPendingApprovals). Match it with a regex so the query
// string and the trailing slash can't slip past a glob.
const LIST_RE = /\/api\/v1\/approvals\/(\?.*)?$/;
const ITEM_RE = (id: string) => new RegExp(`/api/v1/approvals/${id}$`);

async function stubApprovals(page: Page, decided: { value: boolean }) {
  // GET list — returns the pending item until it's decided, then empty.
  await page.route(LIST_RE, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: decided.value ? [] : [PENDING_ITEM] }),
    });
  });
  // PATCH decide — records the decision and flips the list to empty.
  await page.route(ITEM_RE(APPROVAL_ID), async (route) => {
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

// ---------------------------------------------------------------------------
// WS-2 T019 — daemon approval: principal_display card + reviewer-role filter.
// ---------------------------------------------------------------------------
const DAEMON_APPROVAL_ID = "22222222-2222-2222-2222-222222222222";
const USER_APPROVAL_ID = "33333333-3333-3333-3333-333333333333";

// A DAEMON (service-identity) trigger-run's parked approval: it carries the derived
// principal_display + reviewer_scope the backend (approvals._derive_reviewer_audit)
// computes for a daemon run. This is the exact shape suite-70 asserts on a REAL row.
const DAEMON_ITEM = {
  id: DAEMON_APPROVAL_ID,
  agent_name: "s70-agent",
  team: "platform",
  step_name: "tool:refund_action",
  tool_name: "refund_action",
  risk_level: "high",
  tool_args: { order_id: "A1", amount: 50 },
  thread_context_snippet: null,
  sla_remaining_seconds: 900,
  created_at: new Date().toISOString(),
  context: "production",
  version: 1,
  reviewer_scope: "agent:reviewer",
  principal_display: "service:s70-agent on behalf of platform-admin",
};

// A second, interactive/user-delegated approval routed to a DIFFERENT scope, so the
// reviewer-role filter has something to narrow AWAY from.
const USER_ITEM = {
  id: USER_APPROVAL_ID,
  agent_name: "support-bot",
  team: "platform",
  step_name: "tool:issue_credit",
  tool_name: "issue_credit",
  risk_level: "high",
  tool_args: { amount: 10 },
  thread_context_snippet: null,
  sla_remaining_seconds: 600,
  created_at: new Date().toISOString(),
  context: "production",
  version: 1,
  reviewer_scope: "finance:reviewer",
  principal_display: null,
};

test("approvals inbox (WS-2): daemon card shows principal_display + reviewer-role filter narrows", async ({
  page,
}) => {
  // Serve BOTH items on every list fetch (the reviewer-role filter is client-side —
  // registryApi.listPendingApprovals filters returned rows by reviewer_scope — so the
  // GET body is stable and the UI does the narrowing).
  await page.route(LIST_RE, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [DAEMON_ITEM, USER_ITEM] }),
    });
  });
  // Absorb the decide PATCH so it doesn't hit the (nonexistent) fixture id on the backend.
  await page.route(ITEM_RE(DAEMON_APPROVAL_ID), async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });

  await page.goto("/approvals");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", { name: /approvals inbox/i })).toBeVisible();

  // Both cards present initially.
  await expect(page.getByTestId("approval-card")).toHaveCount(2, { timeout: 15_000 });

  // The DAEMON card renders the derived principal_display ("service:X on behalf of Y").
  const daemonPrincipal = page.getByTestId("approval-card-principal").filter({
    hasText: "service:s70-agent on behalf of platform-admin",
  });
  await expect(daemonPrincipal).toBeVisible();

  // The reviewer-role <select> filter exists and offers the daemon scope option.
  const scopeFilter = page.getByRole("combobox", { name: /filter by reviewer role/i });
  await expect(scopeFilter).toBeVisible();
  await expect(scopeFilter.locator("option", { hasText: "agent:reviewer" })).toHaveCount(1);

  // Narrow to "agent:reviewer" → only the daemon approval survives.
  await scopeFilter.selectOption("agent:reviewer");
  await expect(page.getByTestId("approval-card")).toHaveCount(1, { timeout: 15_000 });
  const remaining = page.getByTestId("approval-card").first();
  await expect(remaining).toContainText("refund_action");
  await expect(remaining).toContainText("service:s70-agent on behalf of platform-admin");
  // The other-scope approval is filtered away.
  await expect(page.getByText("issue_credit")).toHaveCount(0);

  // Approve on the daemon card fires the real decide PATCH (wiring the bash suites can't see).
  const decideResp = page.waitForResponse(
    (r) =>
      r.url().includes(`/api/v1/approvals/${DAEMON_APPROVAL_ID}`) &&
      r.request().method() === "PATCH",
    { timeout: 15_000 }
  );
  await remaining.getByRole("button", { name: /approve/i }).click();
  const resp = await decideResp;
  const body = resp.request().postDataJSON() as { decision?: string };
  expect(body.decision).toBe("approved");
});

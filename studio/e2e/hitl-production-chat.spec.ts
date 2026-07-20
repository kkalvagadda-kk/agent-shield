import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// hitl-production-chat.spec.ts
//   THE coverage hole this closes: the PRODUCTION reactive-chat HITL sub-flow —
//   the hitl-waiting-banner + the poll of GET /chat/{run_id}/approval-status +
//   a reviewer deciding in the console → the chat AUTO-RESUMES. No test at any
//   layer drove this journey:
//     - hitl-deployment-chat.spec.ts tests the SANDBOX self-approve panel and
//       asserts the waiting-banner is ABSENT (the opposite case).
//     - approvals-inbox.spec.ts MOCKS the network (never a real chat/poll).
//     - the bash suites kubectl-exec the API and can't drive the browser poll loop.
//
//   The bug it catches (docs/bugs/reactive-hitl-approval-status-session-run-mismatch.md):
//   chat_approval_status keyed the lookup by run_id, but the pod creates the approval
//   under thread_id = session_id, and since POC-0 session_id != run_id — so the poll
//   returned {"status":"none"} forever, the banner never cleared, and the chat hung
//   after a reviewer approved. RED before the fix (banner never hides); GREEN after.
//
//   Fixture: hitl-agent with a RUNNING PRODUCTION deployment (web_search risk=high,
//   Ollama). Skips — same "few agent pods" boundary the other UI specs accept — if
//   no running production deployment / auth token / parking is available.
// ---------------------------------------------------------------------------

const AGENT = process.env.HITL_E2E_AGENT || "hitl-agent";
// Explicit "search the web" nudge so the small local Ollama model reliably calls
// web_search (risk=high → OPA require_approval → parks) instead of answering from memory.
const MESSAGE = "Search the web for the current weather in Austin, Texas right now.";

test("production chat: high-risk tool → waiting banner → reviewer approves in console → chat auto-resumes", async ({
  page,
}) => {
  test.setTimeout(240_000);

  // Capture a real Bearer token from one of the app's own authenticated calls
  // (same pattern as eval-v2-durable.spec.ts) so the spec can query + decide
  // approvals AS A REVIEWER via page.request — the console's exact PATCH.
  let authHeader: string | undefined;
  page.on("request", (req) => {
    const h = req.headers()["authorization"];
    if (h && h.startsWith("Bearer ") && req.url().includes("/api/v1/")) authHeader = h;
  });

  // --- Discover a RUNNING PRODUCTION deployment of the agent -----------------
  // Production deployments live in a SEPARATE table from sandbox ones, so try both
  // surfaces: the per-agent list (sandbox) AND the global production endpoint. Skip
  // gracefully (same "few agent pods" boundary the other UI specs accept) if none is
  // reachable — the deterministic guard for this bug is bash suite-45 T-S45-013.
  await page.goto("/deployments");
  await page.waitForLoadState("networkidle");
  test.skip(!authHeader, "no auth token captured from the app");
  const collect = async (url: string) => {
    const res = await page.request.get(url, { headers: { Authorization: authHeader! } });
    if (!res.ok()) return [];
    const b = await res.json();
    return Array.isArray(b) ? b : b.items ?? [];
  };
  const candidates = [
    ...(await collect(`/api/v1/agents/${AGENT}/deployments`)),
    ...(await collect(`/api/v1/deployments/?environment=production`)),
  ];
  const prod = candidates.find(
    (d: { environment?: string; status?: string; agent_name?: string }) =>
      d.environment === "production" &&
      d.status === "running" &&
      (d.agent_name == null || d.agent_name === AGENT)
  );
  test.skip(!prod, `${AGENT} has no running PRODUCTION deployment surfaced via the deployments API`);

  // Snapshot pending production approvals BEFORE, so we can identify THIS run's
  // new one (the cluster may hold unrelated pending rows from other e2e runs).
  const pendingBefore = new Set<string>();
  {
    const r = await page.request.get(`/api/v1/approvals/?status=pending&context=production`, {
      headers: { Authorization: authHeader! },
    });
    if (r.ok()) for (const a of (await r.json()).items ?? []) pendingBefore.add(a.id);
  }

  // --- Open the PRODUCTION chat + send the high-risk message -----------------
  await page.goto(`/agents/${AGENT}/d/${prod.id}/chat`);
  await page.waitForLoadState("networkidle");
  const input = page.getByPlaceholder(/message/i);
  const inputReady = await input.isVisible().catch(() => false);
  test.skip(!inputReady, "chat input did not render");

  const startResp = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      /\/agents\/[^/]+\/(deployments\/[^/]+\/)?chat$/.test(new URL(r.url()).pathname),
    { timeout: 20_000 }
  );
  await input.fill(MESSAGE);
  await page.getByRole("button").last().click();
  const started = await startResp;
  expect(started.status()).toBe(200);

  // --- PRODUCTION path: the WAITING BANNER appears (NOT the self-approve panel) --
  const banner = page.getByTestId("hitl-waiting-banner");
  await expect(banner).toBeVisible({ timeout: 45_000 });
  // This is the production model — there is no inline sandbox self-approve panel.
  await expect(page.getByTestId("sandbox-approval-panel")).toHaveCount(0);
  await expect(page.getByTestId("hitl-tool-name")).toContainText("web_search");

  // --- Find THIS run's new pending approval + decide it as a reviewer --------
  let approvalId: string | undefined;
  await expect
    .poll(
      async () => {
        const r = await page.request.get(
          `/api/v1/approvals/?status=pending&context=production`,
          { headers: { Authorization: authHeader! } }
        );
        if (!r.ok()) return false;
        const items = (await r.json()).items ?? [];
        const fresh = items.find(
          (a: { id: string; agent_name?: string; tool_name?: string }) =>
            a.agent_name === AGENT && a.tool_name === "web_search" && !pendingBefore.has(a.id)
        );
        if (fresh) approvalId = fresh.id;
        return !!fresh;
      },
      { timeout: 30_000 }
    )
    .toBe(true);

  // Deciding via PATCH /approvals/{id} is exactly what the console's Approve button
  // does (authority-gated + optimistic lock). Fetch the current version first.
  const one = await page.request.get(`/api/v1/approvals/${approvalId}`, {
    headers: { Authorization: authHeader! },
  });
  expect(one.ok()).toBeTruthy();
  const version = (await one.json()).version;
  const decide = await page.request.patch(`/api/v1/approvals/${approvalId}`, {
    headers: { Authorization: authHeader! },
    data: { decision: "approved", version, reviewer_id: "hitl-production-chat-spec" },
  });
  expect(decide.ok()).toBeTruthy();

  // --- THE ASSERTION: the chat's poll must detect the decision + AUTO-RESUME --
  // RED before the fix: the poll keyed by run_id never matched the session-keyed
  // approval → status="none" forever → the banner NEVER clears (chat hangs).
  // GREEN after the fix: poll sees "approved" → connectResumeStream → banner hides.
  await expect(banner).toBeHidden({ timeout: 60_000 });
  // And the resumed run completes against the pod (grounded weather answer, no error).
  await expect(
    page.getByText(/°|degrees|humidity|\bmph\b|temperature|wind|forecast|weather/i)
  ).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/Agent pod is unreachable/i)).toHaveCount(0);
  await expect(page.getByText(/\[Error:/)).toHaveCount(0);
});

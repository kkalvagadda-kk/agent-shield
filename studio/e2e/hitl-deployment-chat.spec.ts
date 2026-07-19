import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// hitl-deployment-chat.spec.ts
//   Proves the REAL browser journey for HITL on a deployed agent's chat —
//   the surface the API-only suites (kubectl exec) structurally cannot test,
//   and (crucially) drives it THROUGH the post-approval resume, which is where
//   a real regression hid: the resume proxies to the agent pod, and if that pod
//   is unreachable the chat shows "[Error: Agent pod is unreachable.]" AFTER the
//   approval panel already cleared. The old version of this spec stopped at
//   "panel hides" (which only proves the /decide call returned 200) and would
//   have stayed green through that bug. This version asserts the run actually
//   COMPLETES against the pod.
//
//     1. Open the deployed agent's chat (the "Chat" button → /agents/{name}/chat).
//     2. Send a message that triggers a high-risk tool (web_search).
//     3. The right-side self-approve PANEL appears and names web_search (HIGH),
//        with the LLM reasoning and the requester (audit trail).
//     4. Approve in the panel → /decide fires (200) → panel clears (auto-resume).
//     5. NEW — the resume-stream must reach the pod, run web_search, and the
//        model must answer: assert a grounded reply arrives AND that NO error
//        bubble ("[Error: …]" / "Agent pod is unreachable") is shown.
//     6. Sandbox approvals must NOT leak into the PRODUCTION HITL console.
//
//   Fixture: `hitl-agent` (reactive, web_search risk=high, Ollama) with a running
//   sandbox deployment — the canonical HITL fixture (serper-agent-4 was retired).
//   Override with HITL_E2E_AGENT. If no running deployment/chat is available the
//   test skips (same "few agent pods" boundary the other UI specs accept).
// ---------------------------------------------------------------------------

const AGENT = process.env.HITL_E2E_AGENT || "hitl-agent";
// Explicit "search the web" nudge so the small local Ollama model reliably calls
// web_search (and therefore parks for approval) instead of answering from memory.
const MESSAGE = "Search the web for the current weather in Austin, Texas right now.";

async function openAgentChat(page: Page): Promise<boolean> {
  // The exact surface a user lands on from the deployment's "Chat" button.
  await page.goto(`/agents/${AGENT}/chat`);
  await page.waitForLoadState("networkidle");
  // The chat input renders once the agent record resolves. If it never does
  // (fixture missing / not visible to this user), skip rather than fail.
  const input = page.getByPlaceholder(/message/i);
  try {
    await input.waitFor({ state: "visible", timeout: 15_000 });
    return true;
  } catch {
    return false;
  }
}

test("deployed-agent chat: high-risk tool → self-approve panel → resume COMPLETES against the pod", async ({
  page,
  context,
}) => {
  const opened = await openAgentChat(page);
  test.skip(!opened, `${AGENT} has no running deployment chat available`);

  // --- Send a message that triggers the high-risk web_search tool ----------
  // The plain /agents/{name}/chat route POSTs to /agents/{name}/chat (reactive);
  // exclude the resume-stream (a GET) and the /decide POST.
  const startResp = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      /\/agents\/[^/]+\/chat$/.test(new URL(r.url()).pathname),
    { timeout: 20_000 }
  );
  await page.getByPlaceholder(/message/i).fill(MESSAGE);
  await page.getByRole("button").last().click();
  const started = await startResp;
  expect(started.status()).toBe(200);

  // --- Sandbox → the right-side self-approve PANEL appears ------------------
  const panel = page.getByTestId("sandbox-approval-panel");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("sandbox-approval-row")).toContainText("web_search");
  // The developer self-approves here; there is no "open console" waiting banner.
  await expect(page.getByTestId("hitl-waiting-banner")).toHaveCount(0);
  // WHY — the LLM's reasoning is shown so the approver can decide informed.
  await expect(page.getByTestId("sandbox-approval-reasoning")).toBeVisible();
  await expect(page.getByTestId("sandbox-approval-reasoning")).not.toBeEmpty();
  // WHO — the requester is shown (self-approve, but named for the audit trail).
  await expect(panel).toContainText(/Requested by/i);
  const approve = panel.getByRole("button", { name: /^Approve$/ });
  await expect(approve).toBeVisible();

  // --- Approve → /decide fires (200) → panel clears (auto-resume) -----------
  const decideResp = page.waitForResponse(
    (r) =>
      r.url().includes("/api/v1/playground/approvals/") &&
      r.url().includes("/decide") &&
      r.request().method() === "POST",
    { timeout: 20_000 }
  );
  await approve.click();
  const decided = await decideResp;
  expect(decided.status()).toBe(200);
  // Panel clears on its own once the graph resumes. (Necessary but NOT sufficient
  // — the resume proxy to the pod happens AFTER this.)
  await expect(panel).toBeHidden({ timeout: 30_000 });

  // --- THE ASSERTION THAT CATCHES "Agent pod is unreachable" ---------------
  // After approval the resume-stream (/agents/{name}/chat/{run}/resume-stream)
  // proxies to the agent pod, which runs web_search and streams the answer back.
  // If the pod is unreachable the chat renders "[Error: Agent pod is unreachable.]"
  // instead. Assert a GROUNDED weather answer arrives (proves tool ran + model
  // answered), then assert no error bubble appears. Ollama is slow → allow 90s.
  await expect(
    page.getByText(/°|degrees|humidity|\bmph\b|temperature|wind|forecast/i)
  ).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/Agent pod is unreachable/i)).toHaveCount(0);
  await expect(page.getByText(/\[Error:/)).toHaveCount(0);

  // --- Sandbox approvals must NOT appear in the PRODUCTION HITL console -----
  const hitlConsole = await context.newPage();
  await hitlConsole.goto(`/hitl`);
  await hitlConsole.waitForLoadState("networkidle");
  // The production queue shows a "production approvals only" notice and never
  // this sandbox agent's row. (If empty-state or no agent row, both are fine.)
  await expect(
    hitlConsole.getByText(/Showing production approvals only/i)
  ).toBeVisible({ timeout: 15_000 });

  await hitlConsole.close();
});

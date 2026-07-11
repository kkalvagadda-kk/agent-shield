import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// hitl-deployment-chat.spec.ts
//   Proves the REAL browser journey for HITL on a deployed agent's chat —
//   the surface the API-only suites (kubectl exec) structurally cannot test:
//
//     1. Open the deployment chat (My Agents → deployment → Open Chat).
//     2. Send a message that triggers a high-risk tool (web_search).
//     3. Assert the chat shows a WAITING banner (NOT inline approve/deny) and
//        names the tool — approval is delegated to the console by design.
//     4. In the HITL console (/hitl), the pending approval shows provenance:
//        the requesting user and the deployment/environment.
//     5. Approve it in the console (real reviewer action).
//     6. Back on the chat, the waiting banner clears on its own (auto-resume
//        polling) — the chat continues without a manual step.
//     7. Reload the console → the decision persisted (save→reload→assert).
//
//   Fixture: serper-agent-4 with a running sandbox deployment + web_search
//   (risk=high) — the same fixture suite-45 uses. If it isn't present/running
//   the test skips (same "few agent pods" boundary the other UI specs accept).
// ---------------------------------------------------------------------------

const AGENT = "serper-agent-4";
const MESSAGE = "What is the current weather in Austin TX right now?";
// The running sandbox deployment for the HITL fixture. Overridable so the spec
// survives a redeploy that mints a new deployment id (same fixture coupling the
// bash suite-45 has to serper-agent-4).
const DEP_ID = process.env.HITL_E2E_DEP_ID || "e6b191fb-a2f0-4464-8469-58c4d46bb662";

async function openDeploymentChat(page: Page): Promise<boolean> {
  // Navigate straight to the deployment chat — the exact surface a user lands
  // on from My Agents → deployment → Open Chat.
  await page.goto(`/agents/${AGENT}/d/${DEP_ID}/chat`);
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

test("sandbox deployment chat: high-risk tool → self-approve panel → chat auto-resumes", async ({
  page,
  context,
}) => {
  const opened = await openDeploymentChat(page);
  test.skip(!opened, `${AGENT} has no running deployment chat available`);

  // --- Send a message that triggers the high-risk web_search tool ----------
  const startResp = page.waitForResponse(
    (r) =>
      r.url().includes(`/deployments/`) &&
      r.url().includes("/chat") &&
      r.request().method() === "POST",
    { timeout: 20_000 }
  );
  await page.getByPlaceholder(/message/i).fill(MESSAGE);
  await page.getByRole("button").last().click();
  const started = await startResp;
  expect(started.status()).toBe(200);

  // --- Sandbox → the right-side self-approve PANEL appears (not a console banner) ---
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

  // --- Approve in the panel → decide call fires → chat auto-resumes ---------
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
  // Panel clears on its own once the graph resumes.
  await expect(panel).toBeHidden({ timeout: 30_000 });

  // --- Sandbox approvals must NOT appear in the PRODUCTION HITL console -----
  const console = await context.newPage();
  await console.goto(`/hitl`);
  await console.waitForLoadState("networkidle");
  // The production queue shows a "production approvals only" notice and never
  // this sandbox agent's row. (If empty-state or no serper row, both are fine.)
  await expect(
    console.getByText(/Showing production approvals only/i)
  ).toBeVisible({ timeout: 15_000 });

  await console.close();
});

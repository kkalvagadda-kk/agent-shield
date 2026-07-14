import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// durable-stream.spec.ts
//
// REAL user-path verification of the durable playground step-stream (StepTracker
// SSE / EventSource). This is the ONLY test that can catch the "Connection lost"
// class of bug: it drives the actual browser through the gateway → nginx → JWT →
// 2-replica → EventSource path. A localhost `httpx` / kubectl-exec check hits ONE
// replica directly and CANNOT reproduce it (see memory feedback_test_like_a_user;
// the per-replica in-memory _STEP_EVENTS buffer only broke under the real LB path).
//
// Drives: /playground → select a durable agent → fill payload → Launch Run →
// assert the StepTracker mounts, a real step streams in, and "Connection lost"
// NEVER appears. No route stubbing — real POST /playground/runs + real SSE.
// ---------------------------------------------------------------------------

test.describe("durable playground step-stream (real SSE)", () => {
  test("Launch Run streams steps with no 'Connection lost'", async ({ page }) => {
    await page.goto("/playground");
    await page.waitForLoadState("networkidle");

    // The Eval Runs page selects by DEPLOYMENT ("SELECT DEPLOYMENT" → "-- pick a
    // deployment --"). Prefer the standing durable demo agent; else any running one.
    const depSelect = page.locator("select").filter({ hasText: /pick a deployment/i });
    await expect(depSelect).toBeVisible();
    // Deployment options load async (React Query). Wait for them so we don't race an
    // empty dropdown and skip spuriously.
    await expect
      .poll(async () => depSelect.locator("option").count(), { timeout: 15000 })
      .toBeGreaterThan(1);
    const options = await depSelect.locator("option").allTextContents();
    const pick =
      options.find((o) => /trigger-demo-a/.test(o)) ??
      options.find((o) => o.trim() && !/pick a deployment/i.test(o));
    if (!pick) {
      test.skip(true, "no running deployment available in this environment");
      return;
    }
    await depSelect.selectOption({ label: pick });
    await page.waitForLoadState("networkidle");

    // The RunLauncher only renders for a DURABLE agent (reactive uses the chat pane).
    const payload = page.locator('textarea[placeholder*="message"]');
    await expect(payload).toBeVisible({ timeout: 10000 });
    await payload.fill('{"message": "What is the capital of Texas?"}');

    // Real POST /playground/runs (no stub) — then the browser opens the SSE stream.
    const runResp = page.waitForResponse(
      (r) => r.url().includes("/playground/runs") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: /Launch Run/i }).click();
    const posted = await runResp;
    expect(posted.status(), "POST /playground/runs should succeed").toBeLessThan(300);

    // StepTracker mounts (Steps heading) and a real step streams in via EventSource.
    await expect(page.getByRole("heading", { name: /^Steps$/i })).toBeVisible({ timeout: 15000 });

    // The exact failure the user saw must NEVER render. Give the durable run time
    // to dispatch + stream, then assert a step appeared AND no "Connection lost".
    await expect
      .poll(
        async () => {
          const lost = await page.getByText(/Connection lost/i).count();
          if (lost > 0) return "connection-lost";
          // a rendered step shows a status word (running/completed/awaiting)
          const stepish = await page.getByText(/running|completed|awaiting/i).count();
          return stepish > 0 ? "streaming" : "waiting";
        },
        { timeout: 45000, intervals: [1000] },
      )
      .toBe("streaming");

    // Final belt-and-suspenders: the failure text is absent.
    await expect(page.getByText(/Connection lost/i)).toHaveCount(0);
  });
});

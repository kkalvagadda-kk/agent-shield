import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// workflow-cost.spec.ts
//
// Gap: the Observability → Traces table showed Cost "—" for every workflow run.
// A workflow parent makes no LLM calls of its own, so its own Langfuse trace has
// no cost; the fix (registry-api 0.2.176) rolls its members' costs up onto the
// parent in the cost-backfill sweep. This proves it the way the user saw the bug:
// load the real Traces page through the gateway and assert a trigger-demo-flow
// (workflow) row now renders a $ cost, not "—".
//
// Score stays "—" for these rows BY DESIGN (score is a judge/eval result and these
// are trigger/scheduled runs, not eval runs) — so this spec asserts cost only.
// ---------------------------------------------------------------------------

test.describe("workflow run cost shows in Traces", () => {
  test("a trigger-demo-flow row renders a $ cost (not —)", async ({ page }) => {
    await page.goto("/observability/traces");
    await page.waitForLoadState("networkidle");

    // Rows are grid divs; the header grid has no agent name, so filtering by
    // "trigger-demo-flow" yields only data rows for the workflow.
    const rows = page.locator("div.grid").filter({ hasText: "trigger-demo-flow" });
    await expect
      .poll(
        async () => {
          const n = await rows.count();
          for (let i = 0; i < n; i++) {
            if (/\$\d/.test(await rows.nth(i).innerText())) return true;
          }
          return false;
        },
        { timeout: 20000, intervals: [1000] },
      )
      .toBe(true);
  });
});

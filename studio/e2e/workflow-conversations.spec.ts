import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// workflow-conversations.spec.ts
//
//   Proves the workflow deployment Conversations tab is no longer empty. A
//   workflow's transcript is authored by its MEMBERS (member agent_name, NULL
//   user_id), so the tab used to query GET /agents/{workflow_name}/... and match
//   nothing. It now resolves the list through the workflow's parent runs:
//   GET /workflows/{id}/conversations.
//
//   The journey (real browser, network + routing assertions):
//     1. Resolve a workflow deployment via the API.
//     2. Open the deployment overview → Conversations tab.
//     3. Assert the tab fires GET /workflows/{id}/conversations (the FIX — NOT
//        the per-agent endpoint).
//     4. If the caller has conversations, assert a row renders and clicking it
//        routes to /workflows/{id}/d/{depId}/chat?session=<thread> (resume seed).
//
//   The conversation ROWS depend on the browser user having run this workflow
//   (owner-scoped). When there are none the row/routing assertions annotate-skip
//   (same warm-fixture boundary the other specs accept); the network-call
//   assertion — the actual regression guard — always runs.
// ---------------------------------------------------------------------------

const ADMIN = {
  "X-User-Sub": "047fad5f-f38c-430a-bfba-6e4d9009314b",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

test.describe("workflow deployment Conversations tab", () => {
  let api: APIRequestContext;
  let workflowId = "";
  let depId = "";

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });
    // Find any workflow deployment to open. Prefer one whose workflow already has
    // conversations for this user, so the row + routing assertions exercise too.
    const depResp = await api.get("/api/v1/deployments/workflows");
    const deps = depResp.ok() ? await depResp.json() : [];
    const list: Array<{ id: string; workflow_id: string }> = Array.isArray(deps) ? deps : deps.items ?? [];
    for (const d of list) {
      const c = await api.get(`/api/v1/workflows/${d.workflow_id}/conversations`);
      if (c.ok() && (await c.json()).length > 0) {
        workflowId = d.workflow_id;
        depId = d.id;
        break;
      }
    }
    // Fallback: any deployment at all (network-call assertion still meaningful).
    if (!workflowId && list.length) {
      workflowId = list[0].workflow_id;
      depId = list[0].id;
    }
  });

  test.afterAll(async () => {
    if (api) await api.dispose().catch(() => {});
  });

  test("Conversations tab lists via GET /workflows/{id}/conversations", async ({ page }) => {
    test.skip(!workflowId || !depId, "no workflow deployment available (env gap)");
    test.setTimeout(60_000);

    const convResp = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/workflows/${workflowId}/conversations`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );

    await page.goto(`/workflows/${workflowId}/d/${depId}`);
    await page.getByRole("button", { name: "conversations" }).click();

    // THE regression guard: the tab hits the WORKFLOW endpoint, not the per-agent one.
    const resp = await convResp;
    expect(resp.status()).toBe(200);
    const rows = (await resp.json()) as Array<{ thread_id: string; title: string | null }>;

    if (rows.length === 0) {
      test.info().annotations.push({
        type: "skip-detail",
        description: "workflow has no conversations for this user — row/routing not exercised",
      });
      return;
    }

    // A row renders, and clicking it routes to the workflow chat seeded with the
    // thread (WorkflowChatPage reads ?session).
    const firstThread = rows[0].thread_id;
    const row = page.locator("button", { hasText: rows[0].title ?? "" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.click();
    await expect(page).toHaveURL(
      new RegExp(`/workflows/${workflowId}/d/${depId}/chat\\?session=${firstThread}`)
    );
  });
});

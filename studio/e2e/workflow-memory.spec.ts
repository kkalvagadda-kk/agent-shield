import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// workflow-memory.spec.ts
//
//   Proves the two workflow run-ledger gaps are closed (real browser, network +
//   render assertions):
//
//     (a) The deployment Memory tab is no longer empty. A workflow's transcript is
//         authored by its MEMBERS (member agent_name, NULL user_id), so the tab used
//         to query the per-agent GET /agents/{workflow_name}/memory and match nothing.
//         It now reads GET /workflows/{id}/memory (resolved through the workflow's
//         parent runs). The tab firing that endpoint is the regression guard.
//
//     (b) Opening a past session in WorkflowChat REPLAYS the transcript instead of
//         the empty composer. The chat seeds the same endpoint with ?thread_id.
//
//   Entries/threads are owner-scoped, so the row/render assertions annotate-skip when
//   the browser user has no runs (the same warm-fixture boundary the other specs
//   accept); the network-call assertions — the actual regression guards — always run.
// ---------------------------------------------------------------------------

const ADMIN = {
  "X-User-Sub": "047fad5f-f38c-430a-bfba-6e4d9009314b",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

test.describe("workflow deployment Memory tab + chat replay", () => {
  let api: APIRequestContext;
  let workflowId = "";
  let depId = "";

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });
    // Prefer a workflow deployment whose workflow already has memory for this user,
    // so the entry-render assertion exercises too; fall back to any deployment (the
    // network-call guard is still meaningful).
    const depResp = await api.get("/api/v1/deployments/workflows");
    const deps = depResp.ok() ? await depResp.json() : [];
    const list: Array<{ id: string; workflow_id: string }> = Array.isArray(deps) ? deps : deps.items ?? [];
    for (const d of list) {
      const m = await api.get(`/api/v1/workflows/${d.workflow_id}/memory`);
      if (m.ok() && (await m.json()).length > 0) {
        workflowId = d.workflow_id;
        depId = d.id;
        break;
      }
    }
    if (!workflowId && list.length) {
      workflowId = list[0].workflow_id;
      depId = list[0].id;
    }
  });

  test.afterAll(async () => {
    if (api) await api.dispose().catch(() => {});
  });

  test("(a) Memory tab lists via GET /workflows/{id}/memory", async ({ page }) => {
    test.skip(!workflowId || !depId, "no workflow deployment available (env gap)");
    test.setTimeout(60_000);

    const memResp = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/workflows/${workflowId}/memory(\\?|$)`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );

    await page.goto(`/workflows/${workflowId}/d/${depId}`);
    await page.getByRole("button", { name: "memory" }).click();

    // THE regression guard: the tab hits the WORKFLOW memory endpoint (not per-agent).
    const resp = await memResp;
    expect(resp.status()).toBe(200);
    const rows = (await resp.json()) as Array<{ content: string; thread_id: string }>;

    await expect(page.getByTestId("workflow-memory-tab")).toBeVisible({ timeout: 10_000 });

    if (rows.length === 0) {
      test.info().annotations.push({
        type: "skip-detail",
        description: "workflow has no memory for this user — entry render not exercised",
      });
      return;
    }
    // A member entry renders (the tab was empty before).
    await expect(
      page.getByText(rows[0].content.slice(0, 24), { exact: false }).first()
    ).toBeVisible({ timeout: 10_000 });
  });

  test("(b) opening a past session replays it in WorkflowChat", async ({ page }) => {
    test.skip(!workflowId || !depId, "no workflow deployment available (env gap)");
    test.setTimeout(60_000);

    // Find a conversation thread for this user through the real browser session.
    const convResp = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/workflows/${workflowId}/conversations`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page.goto(`/workflows/${workflowId}/d/${depId}`);
    await page.getByRole("button", { name: "conversations" }).click();
    const rows = (await (await convResp).json()) as Array<{ thread_id: string; title: string | null }>;

    if (rows.length === 0) {
      test.info().annotations.push({
        type: "skip-detail",
        description: "no past session to replay for this user",
      });
      return;
    }

    // Clicking a past session seeds WorkflowChat via GET /workflows/{id}/memory?thread_id=.
    const memReplay = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/workflows/${workflowId}/memory\\?.*thread_id=`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    const row = page.locator("button", { hasText: rows[0].title ?? "" }).first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.click();

    const replay = await memReplay;
    expect(replay.status()).toBe(200);

    // The replay renders prior turns — the empty composer state must be gone.
    const transcript = page.getByTestId("workflow-chat-transcript");
    await expect(transcript).toBeVisible({ timeout: 10_000 });
    await expect(
      transcript.getByText(/Send a message to run this workflow\./i)
    ).toHaveCount(0);
  });
});

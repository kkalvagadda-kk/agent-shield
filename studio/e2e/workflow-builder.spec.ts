import { test, expect, type APIRequestContext } from "@playwright/test";

// ---------------------------------------------------------------------------
// workflow-builder.spec.ts
//   Proves the unified composite Workflow builder renders and its Add-Agent
//   modal offers BOTH "Existing Agent" and "Create New Agent" — the core of the
//   builder-unification work. Asserts real UI wiring in a real browser (the
//   class of gap that slipped through before).
//
//   The "edges persist across reload" test (G-8) seeds a workflow+edge through
//   the Studio nginx /api proxy (X-User-Sub identity, same as the bash suites),
//   then loads the builder and asserts the edge survives a page reload — the
//   real browser round-trip guarding the wipe-on-load regression.
// ---------------------------------------------------------------------------

// Seed helper: create agents + a workflow with one conditional edge via the
// proxied API. Returns the workflow id + created agent ids for cleanup.
const SYS = { "X-User-Sub": "system" };
async function seedWorkflowWithEdge(request: APIRequestContext, suffix: string) {
  const team = "platform";
  const names = [`wfb-a-${suffix}`, `wfb-b-${suffix}`];
  const agentIds: string[] = [];
  for (const name of names) {
    const r = await request.post("/api/v1/agents/", {
      headers: SYS,
      data: { name, team, agent_type: "declarative", execution_shape: "reactive" },
    });
    if (!r.ok()) throw new Error(`seed agent ${name}: ${r.status()} ${await r.text()}`);
    agentIds.push((await r.json()).id);
  }
  const wr = await request.post("/api/v1/workflows", {
    headers: SYS,
    data: { name: `wfb-wf-${suffix}`, team, orchestration: "conditional" },
  });
  if (!wr.ok()) throw new Error(`seed workflow: ${wr.status()} ${await wr.text()}`);
  const wid = (await wr.json()).id;
  await request.post(`/api/v1/workflows/${wid}/members`, { headers: SYS, data: { agent_id: agentIds[0], position: 1 } });
  await request.post(`/api/v1/workflows/${wid}/members`, { headers: SYS, data: { agent_id: agentIds[1], position: 2 } });
  await request.post(`/api/v1/workflows/${wid}/edges`, {
    headers: SYS,
    data: { source_agent_id: agentIds[0], target_agent_id: agentIds[1], condition: "approved", position: 1 },
  });
  return { wid, agentIds };
}

// Seed a conditional FORK: one router with two outgoing edges (router→a, router→b).
// Used to prove the builder lays a fork out as a fan-out (targets on distinct rows),
// not a single row that reads as a linear chain.
async function seedForkWorkflow(request: APIRequestContext, suffix: string) {
  const team = "platform";
  const names = [`wff-router-${suffix}`, `wff-a-${suffix}`, `wff-b-${suffix}`];
  const agentIds: string[] = [];
  for (const name of names) {
    const r = await request.post("/api/v1/agents/", {
      headers: SYS,
      data: { name, team, agent_type: "declarative", execution_shape: "durable" },
    });
    if (!r.ok()) throw new Error(`seed agent ${name}: ${r.status()} ${await r.text()}`);
    agentIds.push((await r.json()).id);
  }
  const wr = await request.post("/api/v1/workflows", {
    headers: SYS,
    data: { name: `wff-wf-${suffix}`, team, orchestration: "conditional", execution_shape: "durable" },
  });
  if (!wr.ok()) throw new Error(`seed workflow: ${wr.status()} ${await wr.text()}`);
  const wid = (await wr.json()).id;
  for (let i = 0; i < agentIds.length; i++) {
    await request.post(`/api/v1/workflows/${wid}/members`, { headers: SYS, data: { agent_id: agentIds[i], position: i + 1 } });
  }
  // Fork: router -> a [refund], router -> b [default]
  await request.post(`/api/v1/workflows/${wid}/edges`, { headers: SYS, data: { source_agent_id: agentIds[0], target_agent_id: agentIds[1], condition: "refund", position: 1 } });
  await request.post(`/api/v1/workflows/${wid}/edges`, { headers: SYS, data: { source_agent_id: agentIds[0], target_agent_id: agentIds[2], condition: null, position: 2 } });
  return { wid, agentIds, names };
}

test.describe("workflow builder", () => {
  test("new-workflow canvas renders with toolbar actions", async ({ page }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("button", { name: /Add Agent/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Save$/i })).toBeVisible();
    await expect(page.getByText(/Add agents to build your workflow/i)).toBeVisible();
  });

  test("Add Agent modal offers Existing and Create New tabs", async ({ page }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /Add Agent/i }).click();

    // Both tabs present (the fix for "builder forces creating new agents").
    await expect(page.getByRole("button", { name: /Existing Agent/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Create New Agent/i })).toBeVisible();

    // Switching to the create tab reveals the inline-create form.
    await page.getByRole("button", { name: /Create New Agent/i }).click();
    await expect(page.getByPlaceholder("my-agent")).toBeVisible();
    await expect(page.getByRole("button", { name: /Create & Add/i })).toBeVisible();
  });

  test("existing composite workflows list renders", async ({ page }) => {
    await page.goto("/workflows");
    await page.waitForLoadState("networkidle");
    // Either a table of workflows or an empty-state — both prove the page loaded.
    const heading = page.getByRole("heading", { name: /Workflows/i }).first();
    await expect(heading).toBeVisible();
  });

  // G-8: real browser save→reload→assert. Seeds a workflow with one conditional
  // edge via the API, loads the builder, and asserts the edge (+ its condition
  // label) is rendered after the page fully loads — guarding the wipe-on-load
  // regression where store.setEdges([]) discarded persisted edges.
  test("persisted edges survive a builder reload", async ({ page, request }) => {
    const suffix = String(Date.now());
    const { wid, agentIds } = await seedWorkflowWithEdge(request, suffix);
    try {
      await page.goto(`/workflows/${wid}/builder`);
      await page.waitForLoadState("networkidle");
      // Two member nodes render...
      await expect(page.locator(".react-flow__node")).toHaveCount(2);
      // ...and the persisted edge (with its condition label) is present — NOT wiped.
      await expect(page.locator(".react-flow__edge")).toHaveCount(1);
      await expect(page.getByText("approved").first()).toBeVisible();
    } finally {
      // Cleanup: archive workflow + delete seeded agents.
      await request.delete(`/api/v1/workflows/${wid}`, { headers: SYS }).catch(() => {});
      for (const id of agentIds) {
        const name = `wfb-${id === agentIds[0] ? "a" : "b"}-${suffix}`;
        await request.delete(`/api/v1/agents/${name}`, { headers: SYS }).catch(() => {});
      }
    }
  });

  // G-9: execution-shape toggle in Create New tab shows Reactive + Durable buttons
  // and the helper text clarifying that workflow members can't self-fire.
  test("execution shape toggle buttons present in Create New tab", async ({ page }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    // Open the Add Agent modal and switch to the Create New tab.
    await page.getByRole("button", { name: /Add Agent/i }).click();
    await page.getByRole("button", { name: /Create New Agent/i }).click();

    // Both shape toggle buttons are visible.
    await expect(page.getByRole("button", { name: /reactive/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /durable/i })).toBeVisible();

    // Helper text confirms that workflow members can't be self-triggering.
    await expect(
      page.getByText(/Members can't be scheduled or event-driven/i)
    ).toBeVisible();
  });

  // WS-0/D1: the Save modal exposes an Authority (class) selector; a daemon workflow
  // create → POST carries agent_class=daemon and it persists (backend reload). Drives
  // the real journey (inline-create a member → Save modal → pick Daemon → save).
  test("save modal: create daemon workflow → POST carries agent_class → persisted", async ({ page, request }) => {
    const suffix = String(Date.now());
    const wfName = `wfb-daemon-${suffix}`;
    const memberName = `wfb-mem-${suffix}`;
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    // Add one member via inline create (self-contained; also sets the workflow team).
    await page.getByRole("button", { name: /Add Agent/i }).click();
    await page.getByRole("button", { name: /Create New Agent/i }).click();
    await page.getByPlaceholder("my-agent").fill(memberName);
    const memberCreated = page.waitForResponse(
      (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/agents/"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create & Add/i }).click();
    await memberCreated;

    // Open the Save modal, choose Daemon authority, name it, save.
    await page.getByRole("button", { name: /^Save$/i }).click();
    await page.getByPlaceholder("my-workflow").fill(wfName);
    await page.getByLabel(/Authority/i).selectOption("daemon");
    const wfCreated = page.waitForResponse(
      (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/workflows"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Save Workflow/i }).click();
    const resp = await wfCreated;
    expect(resp.status()).toBe(201);
    const body = await resp.json();
    expect(body.agent_class).toBe("daemon");
    const wid = body.id;

    try {
      // Reload from the backend: the persisted workflow still carries daemon.
      const check = await request.get(`/api/v1/workflows/${wid}`, { headers: SYS });
      expect(check.ok()).toBeTruthy();
      expect((await check.json()).agent_class).toBe("daemon");
    } finally {
      await request.delete(`/api/v1/workflows/${wid}`, { headers: SYS }).catch(() => {});
      await request.delete(`/api/v1/agents/${memberName}`, { headers: SYS }).catch(() => {});
    }
  });

  // WS-6 operate parity: a sandbox workflow's HITL is decided INLINE in the run
  // panel (no trip to the Catalog → Approvals console, which is production-only).
  // A parked run + its sandbox approval can't be produced deterministically
  // without a live agent pod, so we stub the trigger/tree/approvals endpoints
  // (page.route) and prove the REAL browser wiring: the inline ApprovalCard
  // renders for the parked member and clicking Approve fires PATCH /approvals
  // (the console decide that triggers _resume_and_advance). This is the layer
  // that can actually break — React render + click → network — which the bash
  // API suites can't see. The resume/advance itself is covered by suite-55/56.
  test("sandbox workflow HITL is approved inline in the run panel", async ({ page, request }) => {
    const suffix = `inline-${Date.now()}`;
    const wr = await request.post("/api/v1/workflows", {
      headers: SYS,
      data: { name: `wfb-inline-${suffix}`, team: "platform", orchestration: "sequential" },
    });
    if (!wr.ok()) throw new Error(`seed workflow: ${wr.status()} ${await wr.text()}`);
    const wid = (await wr.json()).id;

    const runId = "11111111-1111-1111-1111-111111111111";
    const childThread = `th-${suffix}`;
    const approvalId = "22222222-2222-2222-2222-222222222222";

    const parkedTree = {
      parent: {
        id: runId, agent_name: `wfb-inline-${suffix}`, status: "awaiting_approval",
        context: "sandbox", thread_id: `p-${suffix}`, started_at: new Date().toISOString(),
        completed_at: null, output: null, error_message: null, latency_ms: null,
        langfuse_trace_id: null, trigger_type: null, run_by: null, team: "platform",
        input: null, cost_usd: null,
      },
      children: [{
        id: "33333333-3333-3333-3333-333333333333", agent_name: "wf-payout",
        status: "awaiting_approval", context: "sandbox", thread_id: childThread,
        started_at: new Date().toISOString(), completed_at: null, output: null,
        error_message: null, latency_ms: null, langfuse_trace_id: null,
        trigger_type: null, run_by: null, team: "platform", input: null, cost_usd: null,
      }],
    };
    const approval = {
      id: approvalId, agent_name: "wf-payout", team: "platform", step_name: "payout",
      tool_name: "refund_action", risk_level: "high", tool_args: { amount: 50 },
      thread_context_snippet: null, sla_remaining_seconds: 600,
      created_at: new Date().toISOString(), context: "sandbox", version: 1,
      thread_id: childThread,
    };

    // Stub the run trigger, the run-tree poll, and the self-service approval list.
    await page.route(`**/api/v1/workflows/${wid}/runs`, (route) =>
      route.request().method() === "POST"
        ? route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ run_id: runId }) })
        : route.fallback());
    await page.route(`**/api/v1/workflows/${wid}/runs/${runId}/tree`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(parkedTree) }));
    // Pathname predicate (not a `?`-glob, whose semantics vary): the list is
    // GET /api/v1/approvals (query carries context); the decide below is
    // PATCH /api/v1/approvals/{id} — distinct pathnames, so no overlap.
    await page.route((u) => u.pathname === "/api/v1/approvals", (route) => {
      const ctx = new URL(route.request().url()).searchParams.get("context");
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ items: ctx === "sandbox" ? [approval] : [], total: ctx === "sandbox" ? 1 : 0 }) });
    });
    // Stub the console decide (PATCH /approvals/{id}) — the stubbed approval has
    // no DB row, so a real call would 404. The backend resume/advance path is
    // covered by the bash suites; here we only prove the click → PATCH wiring.
    await page.route(`**/api/v1/approvals/${approvalId}`, (route) =>
      route.request().method() === "PATCH"
        ? route.fulfill({ status: 200, contentType: "application/json",
            body: JSON.stringify({ id: approvalId, status: "approved", version: 2 }) })
        : route.fallback());

    try {
      await page.goto(`/workflows/${wid}/builder`);
      await page.waitForLoadState("networkidle");

      // Open the run panel and start a run.
      await page.getByRole("button", { name: /Run Workflow/i }).click();
      await page.getByPlaceholder(/message to pass/i).fill("pay out 50");
      await page.getByRole("button", { name: /Start Run/i }).click();

      // The inline approval card appears for the parked member (poll is 3s).
      const card = page.getByTestId("workflow-inline-approval");
      await expect(card).toBeVisible({ timeout: 15_000 });
      await expect(card.getByText("refund_action")).toBeVisible();

      // Clicking Approve fires the console decide (PATCH /approvals/{id}).
      const decided = page.waitForResponse(
        (r) => r.request().method() === "PATCH" && r.url().includes(`/api/v1/approvals/${approvalId}`),
        { timeout: 15_000 });
      await card.getByRole("button", { name: /^Approve$/i }).click();
      const resp = await decided;
      expect([200, 204].includes(resp.status())).toBeTruthy();
    } finally {
      await request.delete(`/api/v1/workflows/${wid}`, { headers: SYS }).catch(() => {});
    }
  });

  // A conditional FORK must render as a fan-out — the two edges leaving the router
  // go to targets on DISTINCT rows (was the bug: every node placed at the same y,
  // so a fork collapsed into a misleading linear chain). Layered layout keys node
  // position on the edge graph (computeWorkflowLayout).
  test("conditional fork renders as a fan-out (targets on distinct rows)", async ({ page, request }) => {
    const suffix = String(Date.now());
    const { wid, names } = await seedForkWorkflow(request, suffix);
    try {
      await page.goto(`/workflows/${wid}/builder`);
      await page.waitForLoadState("networkidle");
      await expect(page.locator(".react-flow__node")).toHaveCount(3);

      const box = async (name: string) => {
        const b = await page.locator(".react-flow__node", { hasText: name }).first().boundingBox();
        if (!b) throw new Error(`no bounding box for ${name}`);
        return b;
      };
      const router = await box(names[0]);
      const a = await box(names[1]);
      const b = await box(names[2]);

      // Router sits left of both targets (it's the shallower column)...
      expect(router.x).toBeLessThan(a.x);
      expect(router.x).toBeLessThan(b.x);
      // ...and the two fork targets are on DIFFERENT rows (not collapsed onto one line).
      expect(Math.abs(a.y - b.y)).toBeGreaterThan(40);
    } finally {
      await request.delete(`/api/v1/workflows/${wid}`, { headers: SYS }).catch(() => {});
      for (const name of names) await request.delete(`/api/v1/agents/${name}`, { headers: SYS }).catch(() => {});
    }
  });

  // G-10: Triggers button appears on a saved workflow and opens the triggers panel.
  test("Triggers button opens workflow triggers panel", async ({ page, request }) => {
    const suffix = `trig-${Date.now()}`;
    const wr = await request.post("/api/v1/workflows", {
      headers: SYS,
      data: { name: `wfb-triggers-${suffix}`, team: "platform", orchestration: "sequential" },
    });
    if (!wr.ok()) throw new Error(`seed workflow: ${wr.status()} ${await wr.text()}`);
    const wid = (await wr.json()).id;
    try {
      await page.goto(`/workflows/${wid}/builder`);
      await page.waitForLoadState("networkidle");

      // Triggers button is visible only for saved (non-new) workflows.
      await expect(page.getByRole("button", { name: /Triggers/i })).toBeVisible();
      await page.getByRole("button", { name: /Triggers/i }).click();

      // The panel opens with both Schedule and Webhook sections.
      await expect(page.getByText(/Schedules/i).first()).toBeVisible();
      await expect(page.getByText(/Webhooks/i).first()).toBeVisible();
    } finally {
      await request.delete(`/api/v1/workflows/${wid}`, { headers: SYS }).catch(() => {});
    }
  });
});

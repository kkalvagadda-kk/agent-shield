import {
  test,
  expect,
  request as pwRequest,
  type Browser,
  type APIRequestContext,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// context-attribution.spec.ts  (context-storage POC-2)
//   Proves the two REAL browser journeys the API-only bash suites (kubectl exec)
//   structurally cannot test — attribution is a rendering concern, and the
//   toggle is a persistence round-trip through the builder UI:
//
//   (a) Attributed workflow bubbles — running a MULTI-AGENT workflow in the
//       Catalog production chat renders one labeled AttributedBubble PER MEMBER
//       (from the run tree's children[]), not a single collapsed final-output
//       blob. We assert ≥2 attributed member bubbles, each showing its member
//       (agent) name, tied to the distinct agent_name values the run tree poll
//       actually returned.
//
//       This test is SELF-CONTAINED: instead of scanning the catalog and hoping
//       to land on a clean multi-member workflow (the old version hit
//       trigger-demo-flow, which renders a single fallback bubble), beforeAll
//       builds its OWN guaranteed 2-member workflow via the REST API — create +
//       deploy two reactive memory-enabled agents, compose them into a sequential
//       workflow, snapshot a passing version, and publish it to the catalog — then
//       drives the Catalog chat on the resulting artifact. Mirrors the exact bodies
//       suite-75 Section A uses (scripts/e2e/suite-75-context-storage.sh) plus the
//       version → publish → admin-approve promotion path
//       (services/registry-api/routers/composite_workflows.py::publish_workflow +
//       routers/admin.py::approve_publish_request).
//
//   (b) Toggle save → reload → assert — the WorkflowBuilder first-save modal
//       exposes "Share context between agents" (composite `memory_enabled`).
//       We set it to a NON-default value (uncheck → false), save (POST
//       /workflows), reload the builder route, and assert the value PERSISTED by
//       reading the GET /workflows/{id} the builder issues on mount (which is
//       also what seeds `setSaveMemoryEnabled`). The toggle only lives in the
//       first-save modal — a saved workflow's Save resaves without a modal — so
//       the persisted-value proof is the backend read on reload, per DoD #2.
//
//   Boundary (same as workflows.spec / hitl-deployment-chat.spec): we assert UI
//   WIRING + PERSISTENCE + the network calls, NOT agent-execution completion.
//   Few agent pods are warm, so a full multi-member run may not finish; test (a)
//   SKIPS gracefully (only) when its own workflow never produces a terminal run
//   tree with ≥2 completed children.
// ---------------------------------------------------------------------------

const TS = Date.now();
const TOGGLE_AGENT = `e2e-ctx-agent-${TS}`;
const TOGGLE_WORKFLOW = `e2e-ctx-wf-${TS}`;

// Header-auth identity for the REST fixture setup (same admin the browser logs in
// as — platform-admin's real Keycloak sub — so created_by matches and the browser
// can trigger the run it navigates to). Mirrors webhook-public-url.spec's ADMIN.
const ADMIN = {
  "X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6",
  "X-User-Team": "platform",
};
// The REST API is reachable at the same origin the browser uses (Studio's nginx
// proxies /api/v1 → registry-api); baseURL comes from PLAYWRIGHT_BASE_URL.
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";
const INSTR =
  "You are a helpful assistant with memory. Reply in one short sentence.";

// ---------------------------------------------------------------------------
// Shared helpers (mirror workflows.spec.ts)
// ---------------------------------------------------------------------------
async function createAgentViaUI(browser: Browser, agentName: string): Promise<void> {
  const ctx = await browser.newContext({ storageState: "e2e/.auth/state.json" });
  const page = await ctx.newPage();
  try {
    await page.goto("/agents/new");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /No-code/i }).click();
    await page.waitForLoadState("domcontentloaded");

    await page.getByPlaceholder("my-agent").fill(agentName);

    const done = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs"),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Create Agent/i }).click();
    await done;
    await page.waitForURL(`**/agents/${agentName}`, { timeout: 15_000 });
  } finally {
    await ctx.close();
  }
}

async function deleteAgentViaUI(browser: Browser, agentName: string): Promise<void> {
  const ctx = await browser.newContext({ storageState: "e2e/.auth/state.json" });
  const page = await ctx.newPage();
  try {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const row = page.locator("tr", { hasText: agentName });
    if ((await row.count()) === 0) return;
    page.once("dialog", (d) => d.accept());
    await row.getByRole("button", { name: /Delete/i }).click();
    await page.waitForLoadState("networkidle");
  } finally {
    await ctx.close();
  }
}

// ---------------------------------------------------------------------------
// (a) Attributed workflow bubbles in the Catalog production chat
//
// Self-contained: beforeAll builds a KNOWN 2-member workflow via the REST API and
// publishes it to the catalog, so the test drives a clean multi-member run instead
// of scanning the catalog and landing on a single-bubble fallback.
// ---------------------------------------------------------------------------
test.describe("catalog workflow attribution", () => {
  let api: APIRequestContext;
  // Populated by beforeAll only when the whole fixture came up; empty string ⇒
  // the test skips for a genuine environment gap (no LLM provider for the team).
  let artifactId = "";
  let workflowId = "";
  let skipReason = "";
  const agentA = `e2e-ctx-mem-a-${TS}`;
  const agentB = `e2e-ctx-mem-b-${TS}`;
  const wfName = `e2e-ctx-catwf-${TS}`;
  const createdAgents: string[] = [];

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });

    // An LLM provider is required to build a runnable agent. Its genuine absence
    // in a stripped env is the ONE legitimate setup skip (mirrors suite-75).
    const prov = await api.get("/api/v1/llm-providers/", { params: { team: "platform" } });
    expect(prov.ok(), `list llm-providers: ${prov.status()}`).toBeTruthy();
    const provJson = await prov.json();
    const provItems = Array.isArray(provJson) ? provJson : provJson.items ?? [];
    const pid = provItems[0]?.id;
    if (!pid) {
      skipReason = "no LLM provider for team platform";
      return;
    }

    // 1. two distinct reactive, memory-enabled agents (suite-75 Section A body).
    for (const name of [agentA, agentB]) {
      const c = await api.post("/api/v1/agents/", {
        data: {
          name,
          team: "platform",
          agent_type: "declarative",
          execution_shape: "reactive",
          memory_enabled: true,
          metadata: { instructions: INSTR, llm_provider_id: pid, tools: [] },
        },
      });
      expect(c.ok(), `create agent ${name}: ${c.status()} ${await c.text()}`).toBeTruthy();
      createdAgents.push(name);
      // 2. deploy each to sandbox (request must be accepted; the pod actually
      //    reaching Ready is the capacity boundary handled in the test body).
      const d = await api.post(`/api/v1/agents/${name}/deploy`, {
        data: { environment: "sandbox" },
      });
      expect(d.ok(), `deploy agent ${name}: ${d.status()} ${await d.text()}`).toBeTruthy();
    }

    // 3. a composite workflow (sequential, share-context on).
    const wf = await api.post("/api/v1/workflows", {
      data: {
        name: wfName,
        team: "platform",
        orchestration: "sequential",
        execution_shape: "reactive",
        memory_enabled: true,
      },
    });
    expect(wf.ok(), `create workflow: ${wf.status()} ${await wf.text()}`).toBeTruthy();
    workflowId = (await wf.json()).id;

    // 4. add BOTH agents as ordered members.
    for (let i = 0; i < createdAgents.length; i++) {
      const g = await api.get(`/api/v1/agents/${createdAgents[i]}`);
      expect(g.ok(), `get agent ${createdAgents[i]}: ${g.status()}`).toBeTruthy();
      const agentId = (await g.json()).id;
      const m = await api.post(`/api/v1/workflows/${workflowId}/members`, {
        data: { agent_id: agentId, position: i + 1 },
      });
      expect(m.ok(), `add member ${createdAgents[i]}: ${m.status()} ${await m.text()}`).toBeTruthy();
    }

    // 5. snapshot a version (eval_passed opens the publish eval gate — this is a
    //    fixture, so we set it directly rather than running a real eval).
    const ver = await api.post(`/api/v1/workflows/${workflowId}/versions`, {
      data: { eval_passed: true, notes: "e2e attribution fixture" },
    });
    expect(ver.ok(), `create version: ${ver.status()} ${await ver.text()}`).toBeTruthy();
    const versionId = (await ver.json()).id;

    // 6. submit the publish request.
    const pub = await api.post(`/api/v1/workflows/${workflowId}/publish`, {
      data: { version_id: versionId },
    });
    expect(pub.ok(), `publish workflow: ${pub.status()} ${await pub.text()}`).toBeTruthy();
    const prId = (await pub.json()).publish_request_id;

    // 7. admin-approve → materializes the catalog PublishedArtifact (type=workflow,
    //    source_id=workflowId). Grant to team platform so it's catalog-visible.
    const appr = await api.post(`/api/v1/admin/publish-requests/${prId}/approve`, {
      data: { grantee_teams: ["platform"] },
    });
    expect(appr.ok(), `approve publish: ${appr.status()} ${await appr.text()}`).toBeTruthy();
    artifactId = (await appr.json()).artifact_id;
    expect(artifactId, "approve should return a catalog artifact_id").toBeTruthy();
  });

  test.afterAll(async () => {
    // Best-effort cleanup so the catalog/cluster don't accumulate fixtures.
    if (api) {
      if (workflowId) await api.delete(`/api/v1/workflows/${workflowId}`).catch(() => {});
      for (const name of createdAgents) {
        await api.delete(`/api/v1/agents/${name}`).catch(() => {});
      }
      await api.dispose().catch(() => {});
    }
  });

  test("catalog workflow chat renders ≥2 attributed member bubbles", async ({ page }) => {
    test.skip(!artifactId, skipReason || "workflow fixture did not come up");
    // The multi-member run poll can take ~90s; the default 60s test timeout is
    // too tight, so extend it (setup already ran in beforeAll).
    test.setTimeout(140_000);

    // Drive the real Catalog production chat on OUR published workflow artifact.
    await page.goto(`/catalog/${artifactId}/chat`);
    await page.waitForLoadState("networkidle");
    const input = page.getByRole("textbox");
    // Input enables once the artifact resolves (disabled while `!agentName`).
    await expect(input).toBeEnabled({ timeout: 15_000 });

    // Wait for a run-tree poll that carries ≥2 members and has reached a terminal
    // state — that's when CatalogChatPage renders the per-member attributed bubbles.
    const treeResp = page.waitForResponse(
      async (r) => {
        if (!/\/api\/v1\/workflows\/[^/]+\/runs\/[^/]+\/tree/.test(r.url())) return false;
        if (r.request().method() !== "GET") return false;
        try {
          const j = await r.json();
          const terminal = j?.parent?.status === "completed" || j?.parent?.status === "failed";
          return terminal && Array.isArray(j?.children) && j.children.length >= 2;
        } catch {
          return false;
        }
      },
      { timeout: 90_000 }
    );

    // Sending a message on a workflow artifact triggers POST /workflows/{id}/runs.
    const runResp = page.waitForResponse(
      (r) =>
        /\/api\/v1\/workflows\/[^/]+\/runs$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 }
    );

    await input.fill("Run the workflow end to end.");
    await page.locator('button[type="submit"]').click();

    // The run must actually kick off; if the trigger itself fails, that's a real bug.
    const started = await runResp;
    expect([200, 201, 202]).toContain(started.status());

    // A completing multi-member run needs warm pods; when the two agent pods never
    // warm up, the tree poll never reaches ≥2 terminal children → capacity skip
    // (same "few warm pods" boundary suite-75 accepts). A run that COMPLETES but
    // renders missing bubbles is NOT skipped — it falls through and FAILS below.
    let children: Array<{ agent_name?: string }> = [];
    try {
      const resp = await treeResp;
      const tree = await resp.json();
      children = tree.children ?? [];
    } catch {
      test.skip(true, "no completing multi-member run — few warm pods");
      return;
    }

    const names = Array.from(
      new Set(children.map((c) => c.agent_name).filter((n): n is string => !!n))
    );
    expect(names.length).toBeGreaterThanOrEqual(2);

    // Each member name is rendered as an AttributedBubble label (colored dot + name)
    // in the messages area — the run no longer collapses into one final-output blob.
    const messages = page.locator("div.overflow-y-auto");
    for (const name of names) {
      await expect(messages.getByText(name, { exact: true }).first()).toBeVisible();
    }
    // The attribution color dots (span.w-2.h-2.rounded-full precede each label) —
    // at least one per member bubble.
    await expect(
      messages.locator("span.w-2.h-2.rounded-full").nth(1)
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// (b) "Share context between agents" toggle — save → reload → assert persisted
// ---------------------------------------------------------------------------
test.describe("share-context toggle persistence", () => {
  test.beforeAll(async ({ browser }) => {
    await createAgentViaUI(browser, TOGGLE_AGENT);
  });

  test.afterAll(async ({ browser }) => {
    await deleteAgentViaUI(browser, TOGGLE_AGENT);
  });

  test("toggle saves and survives a builder reload", async ({ page }) => {
    await page.goto("/workflows/new");
    await page.waitForLoadState("networkidle");

    // Add our agent so Save is allowed (workflow needs ≥1 member).
    await page.getByRole("button", { name: /Add Existing Agent/i }).click();
    await expect(page.getByText(TOGGLE_AGENT)).toBeVisible({ timeout: 10_000 });
    await page
      .locator(`xpath=//p[normalize-space()="${TOGGLE_AGENT}"]/../../../button`)
      .click();
    await page.getByRole("button", { name: /Done/i }).click();

    // Open the first-save modal — the only surface that renders the toggle.
    await page.getByRole("button", { name: /^Save$/i }).click();
    await expect(page.getByRole("heading", { name: /Save Workflow/i })).toBeVisible();

    await page.locator("input#wfb-name").fill(TOGGLE_WORKFLOW);

    // The toggle defaults to checked (share context ON). Flip it OFF so the
    // persisted value is a NON-default → the reload assertion is meaningful.
    const toggle = page
      .locator("label", { hasText: /Share context between agents/i })
      .locator('input[type="checkbox"]');
    await expect(toggle).toBeChecked();
    await toggle.uncheck();
    await expect(toggle).not.toBeChecked();

    // Save → POST /api/v1/workflows carries memory_enabled=false.
    const wfPost = page.waitForResponse(
      (r) => /\/api\/v1\/workflows$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Save Workflow/i }).click();
    const created = await wfPost;
    expect(created.status()).toBe(201);
    // The request we sent must carry the flag we set (wiring proof).
    expect(created.request().postDataJSON()?.memory_enabled).toBe(false);

    // App routes to /workflows/{id}/builder after save.
    await page.waitForURL(/\/workflows\/.+\/builder/, { timeout: 15_000 });
    const builderUrl = page.url();
    const wfId = builderUrl.match(/\/workflows\/([^/]+)\/builder/)?.[1];
    expect(wfId).toBeTruthy();

    // RELOAD the builder route and assert the persisted value survived by reading
    // the GET /workflows/{id} the page issues on mount (which also seeds the
    // toggle state). This is the save→reload→assert backend round-trip (DoD #2).
    const wfGet = page.waitForResponse(
      (r) =>
        new RegExp(`/api/v1/workflows/${wfId}$`).test(r.url()) &&
        r.request().method() === "GET",
      { timeout: 20_000 }
    );
    await page.goto(builderUrl);
    const reloaded = await wfGet;
    expect(reloaded.status()).toBe(200);
    const body = await reloaded.json();
    expect(body.memory_enabled).toBe(false);
  });
});

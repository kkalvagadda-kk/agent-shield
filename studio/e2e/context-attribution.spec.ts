import { test, expect, type Browser, type Page } from "@playwright/test";

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
//   SKIPS gracefully when no reachable/completing multi-agent workflow exists.
// ---------------------------------------------------------------------------

const TS = Date.now();
const TOGGLE_AGENT = `e2e-ctx-agent-${TS}`;
const TOGGLE_WORKFLOW = `e2e-ctx-wf-${TS}`;

// Optional override: point test (a) straight at a known published multi-agent
// workflow artifact (mirrors hitl-deployment-chat.spec's DEP_ID override so the
// spec survives an env where catalog discovery finds nothing).
const WF_ARTIFACT = process.env.CTX_E2E_WORKFLOW_ARTIFACT || "";

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

// Reach a multi-agent workflow's Catalog production chat. Returns the chat page
// (already navigated) or null if none is discoverable in this env → the caller
// skips. Discovery: env override first, else scan the catalog for a card whose
// type badge is "workflow" and open its /chat surface.
async function openWorkflowChat(page: Page): Promise<boolean> {
  let artifactId = WF_ARTIFACT;
  if (!artifactId) {
    await page.goto("/catalog");
    await page.waitForLoadState("networkidle");
    // Catalog cards are <Link to="/catalog/{id}"> containing a <span class="badge">
    // with the artifact type. Pick the first whose badge reads "workflow".
    const wfCard = page
      .locator('a[href^="/catalog/"]', {
        has: page.locator("span.badge", { hasText: /^workflow$/i }),
      })
      .first();
    if ((await wfCard.count()) === 0) return false;
    const href = await wfCard.getAttribute("href");
    if (!href) return false;
    artifactId = href.split("/catalog/")[1];
  }

  await page.goto(`/catalog/${artifactId}/chat`);
  await page.waitForLoadState("networkidle");
  // The composite input enables once the artifact resolves (input is disabled
  // while `!agentName`). If it never enables, the artifact isn't reachable/visible.
  const input = page.getByRole("textbox");
  try {
    await expect(input).toBeEnabled({ timeout: 15_000 });
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// (a) Attributed workflow bubbles in the Catalog production chat
// ---------------------------------------------------------------------------
test("catalog workflow chat renders ≥2 attributed member bubbles", async ({ page }) => {
  const reached = await openWorkflowChat(page);
  test.skip(!reached, "no reachable multi-agent workflow chat in this env");

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
    { timeout: 50_000 }
  );

  // Sending a message on a workflow artifact triggers POST /workflows/{id}/runs.
  const runResp = page.waitForResponse(
    (r) =>
      /\/api\/v1\/workflows\/[^/]+\/runs$/.test(r.url()) && r.request().method() === "POST",
    { timeout: 20_000 }
  );

  await page.getByRole("textbox").fill("Run the workflow end to end.");
  await page.locator('button[type="submit"]').click();

  // The run must actually kick off; if the trigger itself fails, that's a real bug.
  const started = await runResp;
  expect([200, 201, 202]).toContain(started.status());

  // A completing multi-member run needs warm pods; when none are, the tree poll
  // never reaches ≥2 terminal children → skip (same "few pods" boundary).
  let children: Array<{ agent_name?: string }> = [];
  try {
    const resp = await treeResp;
    const tree = await resp.json();
    children = tree.children ?? [];
  } catch {
    test.skip(true, "no completing multi-member workflow run available (few warm pods)");
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

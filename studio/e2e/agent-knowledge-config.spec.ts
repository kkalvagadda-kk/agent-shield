import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// agent-knowledge-config.spec.ts
//
//   Proves the AGENT-SIDE Knowledge Base configuration journey — "Knowledge
//   Search is a special config, not a hand-picked tool":
//
//     A. Create Agent (/agents/new → No-code):
//        - the `knowledge_search` tool is HIDDEN from the Tools picker;
//        - a dedicated Knowledge Bases picker lists the team's KBs;
//        - checking a KB + Create binds it (POST /agents/ then
//          PUT /knowledge-bases/{kb}/agents/{id}), which auto-attaches
//          knowledge_search server-side.
//        - reload the new agent's Settings → the KB checkbox is PRE-SELECTED
//          (read back via GET /knowledge-bases/agent-bindings/{id}) and
//          knowledge_search is still hidden from Tools. (DoD #2 round-trip.)
//
//     B. Agent Settings reconcile-on-save:
//        - uncheck the KB + Save → unbind (DELETE) + updateAgent (PUT) fire →
//          reload → the checkbox stays UNCHECKED (unbind persisted);
//        - re-check + Save → reload → the checkbox is CHECKED again (bind
//          persisted). This is the distinct SettingsContent reconcile path.
//
//   The KB fixture is created via the REST API (header-auth as platform-admin,
//   mirroring knowledge.spec.ts); the whole journey is then driven through the
//   real React UI with network-call + persistence assertions. No agent LLM run
//   is required — this is a CONFIG-wiring journey (the grounded-answer path is
//   covered by knowledge.spec.ts + bash suite-77/suite-80).
// ---------------------------------------------------------------------------

const TS = Date.now();
// The CURRENT platform-admin Keycloak sub (from GET /me on the live realm). Must
// match the browser JWT's sub so REST-created fixtures land in the same identity
// the browser session sees. (The realm was re-seeded; the old 75c7c8b3… sub now
// 401s — see the stale-fixture-sub gap in the manual test plan.)
const ADMIN = {
  "X-User-Sub": "047fad5f-f38c-430a-bfba-6e4d9009314b",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

const KB_NAME = `e2e-akc-kb-${TS}`;
const AGENT_NAME = `e2e-akc-agent-${TS}`;

const KNOWLEDGE_SEARCH_TOOL = {
  name: "knowledge_search",
  display_name: "Knowledge Search",
  description:
    "Search the team's knowledge base for passages relevant to a question. " +
    "Returns the most relevant document chunks with their source.",
  type: "http",
  risk_level: "low",
  owner_team: "platform",
  side_effecting: false,
  http_method: "POST",
  http_url:
    "http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/api/v1/internal/knowledge/search",
  http_headers: {
    "Content-Type": "application/json",
    "X-Agent-Team": "{{AGENTSHIELD_AGENT_TEAM}}",
    "X-Agent-Name": "{{AGENT_NAME}}",
  },
  http_body_template: '{"query": "{{query}}", "k": 5}',
  input_schema: {
    type: "object",
    properties: { query: { type: "string", description: "The search phrase." } },
    required: ["query"],
  },
};

async function openNoCode(page: Page) {
  await page.goto("/agents/new");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: /No-code/i }).click();
}

// The KB checkbox inside the dedicated Knowledge Bases picker, scoped by name.
function kbCheckbox(page: Page) {
  return page
    .getByTestId("kb-picker")
    .locator("label", { hasText: KB_NAME })
    .getByRole("checkbox");
}

test.describe("agent-side Knowledge Base config (special config, not a tool)", () => {
  let api: APIRequestContext;
  let kbId = "";
  let kbReady = false;

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });
    // knowledge_search must exist so binding a KB can attach it (idempotent).
    await api.post("/api/v1/tools/", { data: KNOWLEDGE_SEARCH_TOOL }).catch(() => {});
    // A KB for the picker to list (no source needed — the picker only lists it).
    const r = await api.post("/api/v1/knowledge-bases", {
      data: { name: KB_NAME, description: "agent-knowledge-config e2e" },
    });
    kbReady = r.ok();
    if (kbReady) kbId = (await r.json()).id;
  });

  test.afterAll(async () => {
    if (api) {
      await api.delete(`/api/v1/agents/${AGENT_NAME}`).catch(() => {});
      if (kbId) await api.delete(`/api/v1/knowledge-bases/${kbId}`).catch(() => {});
      await api.dispose().catch(() => {});
    }
  });

  test("A: create with a KB → knowledge_search hidden, binding persists", async ({ page }) => {
    test.skip(!kbReady, "could not create the fixture KB (env gap)");
    test.setTimeout(60_000);

    await openNoCode(page);

    // The Knowledge Bases picker lists our KB; the Tools picker HIDES knowledge_search.
    await expect(page.getByTestId("kb-picker")).toContainText(KB_NAME, { timeout: 15_000 });
    await expect(page.getByTestId("tools-picker")).toBeVisible();
    await expect(page.getByTestId("tools-picker")).not.toContainText("Knowledge Search");

    // Fill name, select the KB, create.
    await page.getByPlaceholder("my-agent").fill(AGENT_NAME);
    await kbCheckbox(page).check();
    await expect(kbCheckbox(page)).toBeChecked();

    const createResp = page.waitForResponse(
      (r) => r.request().method() === "POST" && new URL(r.url()).pathname.endsWith("/agents/"),
      { timeout: 20_000 }
    );
    const bindResp = page.waitForResponse(
      (r) =>
        r.request().method() === "PUT" &&
        new RegExp(`/api/v1/knowledge-bases/${kbId}/agents/`).test(r.url()),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /^Create Agent$/i }).click();
    expect((await createResp).status()).toBe(201);
    expect((await bindResp).status()).toBe(200);

    // save → reload → assert: the new agent's Settings pre-selects the bound KB
    // (GET /knowledge-bases/agent-bindings/{id}) and still hides knowledge_search.
    await page.goto(`/agents/${AGENT_NAME}`);
    await page.getByRole("button", { name: "settings" }).click();
    await expect(kbCheckbox(page)).toBeChecked({ timeout: 15_000 });
    await expect(page.getByTestId("tools-picker")).not.toContainText("knowledge_search");
  });

  test("B: Settings reconcile — unbind then rebind survive save→reload", async ({ page }) => {
    test.skip(!kbReady, "could not create the fixture KB (env gap)");
    test.setTimeout(60_000);

    const openSettings = async () => {
      await page.goto(`/agents/${AGENT_NAME}`);
      await page.getByRole("button", { name: "settings" }).click();
      await expect(page.getByTestId("kb-picker")).toContainText(KB_NAME, { timeout: 15_000 });
    };

    // Starts bound (from test A) → uncheck → Save (updateAgent PUT fires).
    await openSettings();
    await expect(kbCheckbox(page)).toBeChecked();
    await kbCheckbox(page).uncheck();
    let saveResp = page.waitForResponse(
      (r) => r.request().method() === "PUT" && new RegExp(`/api/v1/agents/${AGENT_NAME}$`).test(r.url()),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Save Changes/i }).click();
    expect((await saveResp).status()).toBe(200);

    // reload → the unbind persisted (checkbox stays unchecked, read from backend).
    await openSettings();
    await expect(kbCheckbox(page)).not.toBeChecked();

    // Re-check → Save → reload → the bind persisted (checkbox checked again).
    await kbCheckbox(page).check();
    saveResp = page.waitForResponse(
      (r) => r.request().method() === "PUT" && new RegExp(`/api/v1/agents/${AGENT_NAME}$`).test(r.url()),
      { timeout: 20_000 }
    );
    await page.getByRole("button", { name: /Save Changes/i }).click();
    expect((await saveResp).status()).toBe(200);

    await openSettings();
    await expect(kbCheckbox(page)).toBeChecked();
  });

  // C: the inline "Edit Agent" modal on the Agents list — the 3rd agent-editing
  // surface Task 13 originally missed (knowledge_search leaked in as a tool with no
  // KB picker). Proves the class-fix (shared ToolsPicker/KnowledgeBasePicker) reaches
  // it too. The agent starts bound (re-bound at the end of test B).
  test("C: Edit Agent modal hides knowledge_search and shows the KB picker", async ({ page }) => {
    test.skip(!kbReady, "could not create the fixture KB (env gap)");
    test.setTimeout(60_000);

    await page.goto("/agents");
    await page.waitForLoadState("networkidle");
    await page.getByPlaceholder(/search agents/i).fill(AGENT_NAME);

    // Scope to <tbody> (the <thead> header row also matches hasText otherwise) and
    // wait for the search filter to settle to exactly our agent's row.
    const row = page.locator("tbody tr", { hasText: AGENT_NAME });
    await expect(row).toHaveCount(1, { timeout: 15_000 });
    await row.getByRole("button", { name: /^Edit$/ }).click();

    // The modal opened on our agent.
    await expect(
      page.getByRole("heading", { name: new RegExp(`Edit Agent — ${AGENT_NAME}`) })
    ).toBeVisible({ timeout: 15_000 });

    // Tools list must NOT list knowledge_search; the KB picker must be present + pre-selected.
    await expect(page.getByTestId("tools-picker")).toBeVisible();
    await expect(page.getByTestId("tools-picker")).not.toContainText("Knowledge Search");
    await expect(page.getByTestId("tools-picker")).not.toContainText("knowledge_search");
    await expect(page.getByTestId("kb-picker")).toContainText(KB_NAME, { timeout: 15_000 });
    await expect(
      page.getByTestId("kb-picker").locator("label", { hasText: KB_NAME }).getByRole("checkbox")
    ).toBeChecked();
  });
});

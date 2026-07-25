import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// tools-picker-drawer.spec.ts
//
//   Proves the TOOLS side of the browse-and-select picker journey. Tool selection
//   had no browser coverage at all before this: agent-knowledge-config.spec.ts
//   only ever asserted that `knowledge_search` was ABSENT from the Tools picker,
//   never that picking a real tool works or persists.
//
//     A. Create Agent (/agents/new → No-code):
//        - the builder shows no catalog inline, only an "Add from catalog" button;
//        - the drawer lists tools as tiles carrying risk + type;
//        - the drawer exposes NO edit/delete/create affordance (tools are shared
//          team resources — a destructive control here would let a mis-click
//          during agent assembly delete a tool other agents depend on);
//        - search narrows the tiles;
//        - selecting a tile + Done surfaces it as a chip on the builder;
//        - Create persists the tool into the agent's metadata.tools.
//
//     B. Save → reload → assert (DoD #2): reopen the created agent's Settings and
//        confirm the tool is still selected, read back from the backend — both as
//        a chip on the surface and as a checked box inside the drawer.
//
//   The tool fixture is created via the REST API (header-auth as platform-admin,
//   mirroring agent-knowledge-config.spec.ts); the journey is then driven through
//   the real React UI. No agent LLM run is required — this is config wiring.
// ---------------------------------------------------------------------------

const TS = Date.now();
const ADMIN = {
  "X-User-Sub": "047fad5f-f38c-430a-bfba-6e4d9009314b",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

const TOOL_NAME = `e2e_tpd_tool_${TS}`;
const TOOL_LABEL = `E2E Picker Tool ${TS}`;
const AGENT_NAME = `e2e-tpd-agent-${TS}`;

const FIXTURE_TOOL = {
  name: TOOL_NAME,
  display_name: TOOL_LABEL,
  // Multi-line on purpose: the tile clamps to 2 lines, and the description field
  // itself is now a textarea.
  description:
    "Looks up a widget by id for the picker-drawer e2e.\n" +
    "Args: widget_id (str).\n" +
    "Read-only; never mutates inventory.",
  type: "http",
  risk_level: "high",
  owner_team: "platform",
  side_effecting: false,
  http_method: "GET",
  http_url: "https://example.invalid/widgets/{{widget_id}}",
  input_schema: {
    type: "object",
    properties: { widget_id: { type: "string", description: "Widget id." } },
    required: ["widget_id"],
  },
};

async function openNoCode(page: Page) {
  await page.goto("/agents/new");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: /No-code/i }).click();
}

const toolsPicker = (page: Page) => page.getByTestId("tools-picker");
const toolsDrawer = (page: Page) => page.getByTestId("tools-picker-drawer");

async function openToolsDrawer(page: Page) {
  if (!(await toolsDrawer(page).isVisible().catch(() => false))) {
    await toolsPicker(page).getByRole("button", { name: /add from catalog/i }).click();
  }
  await expect(toolsDrawer(page)).toBeVisible({ timeout: 15_000 });
}

async function closeToolsDrawer(page: Page) {
  await toolsDrawer(page).getByRole("button", { name: /^Done$/ }).click();
  await expect(toolsDrawer(page)).toBeHidden();
}

function toolCheckbox(page: Page) {
  return toolsDrawer(page).locator("label", { hasText: TOOL_LABEL }).getByRole("checkbox");
}

test.describe("tools picker — browse-and-select tile drawer", () => {
  let api: APIRequestContext;
  let toolReady = false;

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });
    const r = await api.post("/api/v1/tools/", { data: FIXTURE_TOOL });
    toolReady = r.ok();
    if (!toolReady) {
      // eslint-disable-next-line no-console
      console.log(`fixture tool create failed: ${r.status()} ${await r.text()}`);
    }
  });

  test.afterAll(async () => {
    await api.delete(`/api/v1/agents/${AGENT_NAME}`).catch(() => {});
    await api.delete(`/api/v1/tools/${TOOL_NAME}`).catch(() => {});
    await api.dispose();
  });

  test("A: drawer browses tools as tiles and selection persists on create", async ({ page }) => {
    test.skip(!toolReady, "could not create the fixture tool (env gap)");
    test.setTimeout(90_000);

    await openNoCode(page);
    await expect(toolsPicker(page)).toBeVisible({ timeout: 15_000 });

    // The catalog is NOT inline — only the button is, and nothing is selected yet.
    await expect(toolsPicker(page)).toContainText(/no tools selected/i);
    await expect(toolsDrawer(page)).toBeHidden();
    await expect(toolsPicker(page)).not.toContainText(TOOL_LABEL);

    // Open the drawer → the tile is there, carrying its risk + type chips.
    await openToolsDrawer(page);
    const tile = toolsDrawer(page).locator("label", { hasText: TOOL_LABEL });
    await expect(tile).toBeVisible({ timeout: 15_000 });
    await expect(tile).toContainText("high");
    await expect(tile).toContainText("http");

    // No destructive or authoring controls in a picker.
    await expect(toolsDrawer(page).getByRole("button", { name: /^edit$/i })).toHaveCount(0);
    await expect(toolsDrawer(page).getByRole("button", { name: /^delete$/i })).toHaveCount(0);
    await expect(
      toolsDrawer(page).getByRole("button", { name: /new tool|create tool/i }),
    ).toHaveCount(0);

    // Search narrows to our tool, then clears back.
    await toolsDrawer(page)
      .getByPlaceholder(/search by name or description/i)
      .fill("widget by id for the picker-drawer");
    await expect(tile).toBeVisible();
    await toolsDrawer(page).getByPlaceholder(/search by name or description/i).fill("zzz-no-match");
    await expect(toolsDrawer(page)).toContainText(/tools are managed under tools/i);
    await toolsDrawer(page).getByPlaceholder(/search by name or description/i).fill("");

    // Select it → Done → it surfaces as a chip on the builder.
    await toolCheckbox(page).check();
    await expect(toolCheckbox(page)).toBeChecked();
    await closeToolsDrawer(page);
    await expect(toolsPicker(page)).toContainText(TOOL_LABEL);

    // Create the agent and assert the tool reached the request body.
    await page.getByPlaceholder("my-agent").fill(AGENT_NAME);
    const created = page.waitForResponse(
      (r) => r.request().method() === "POST" && /\/api\/v1\/agents\/?$/.test(r.url()),
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: /^Create Agent$/i }).click();
    const resp = await created;
    expect(resp.status(), await resp.text()).toBeLessThan(300);
    expect(JSON.parse(resp.request().postData() ?? "{}").metadata.tools).toContain(TOOL_NAME);
  });

  test("B: save → reload → the selected tool survived", async ({ page }) => {
    test.skip(!toolReady, "could not create the fixture tool (env gap)");
    test.setTimeout(90_000);

    // Read the agent back from the backend, not from any in-memory store.
    await page.goto(`/agents/${AGENT_NAME}`);
    await page.getByRole("button", { name: "settings" }).click();
    await expect(toolsPicker(page)).toBeVisible({ timeout: 15_000 });

    // The chip alone is the round-trip proof; the drawer confirms the box state.
    await expect(toolsPicker(page)).toContainText(TOOL_LABEL, { timeout: 15_000 });
    await openToolsDrawer(page);
    await expect(toolCheckbox(page)).toBeChecked({ timeout: 15_000 });
    await closeToolsDrawer(page);

    // Deselect via the chip's remove control → Save → reload → stayed removed.
    await toolsPicker(page).getByRole("button", { name: new RegExp(`Remove ${TOOL_LABEL}`) }).click();
    await expect(toolsPicker(page)).not.toContainText(TOOL_LABEL);
    const saved = page.waitForResponse(
      (r) => r.request().method() === "PUT" && new RegExp(`/api/v1/agents/${AGENT_NAME}$`).test(r.url()),
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: /Save Changes/i }).click();
    expect((await saved).status()).toBe(200);

    await page.goto(`/agents/${AGENT_NAME}`);
    await page.getByRole("button", { name: "settings" }).click();
    await expect(toolsPicker(page)).toBeVisible({ timeout: 15_000 });
    await expect(toolsPicker(page)).toContainText(/no tools selected/i, { timeout: 15_000 });
  });
});

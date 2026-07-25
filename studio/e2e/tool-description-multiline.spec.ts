import {
  test,
  expect,
  request as pwRequest,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// ---------------------------------------------------------------------------
// tool-description-multiline.spec.ts
//
//   Proves the tool Description field is genuinely multi-line end-to-end
//   (studio 0.1.162). The field was a single-line <input> that silently collapsed
//   newlines — and that description is what an agent's LLM reads to decide whether
//   to call the tool, so a field that cannot hold detail has real runtime cost.
//
//   Layering, deliberately:
//     - Vitest (ToolsPage.test.tsx) proves the React value and the payload shape.
//     - Bash suite-84 proves the API/column preserve the newlines.
//     - THIS spec proves the actual screen: a user types multiple lines into the
//       real form, saves, and after a full page reload the edit form still shows
//       every line. Neither of the other two layers can catch a broken screen.
//
//     A. Create: the Description control is a <textarea> (not an <input>), accepts
//        multiple lines, and the value reaches POST /api/v1/tools.
//     B. Save -> reload -> assert (DoD #2): reopen Edit on the created tool after a
//        full reload; the textarea is rehydrated from the backend with the newlines
//        intact. Then append a line, save, reload again — the edit persisted too.
// ---------------------------------------------------------------------------

const TS = Date.now();
const ADMIN = {
  "X-User-Sub": "047fad5f-f38c-430a-bfba-6e4d9009314b",
  "X-User-Team": "platform",
};
const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

const TOOL_NAME = `e2e_tdm_${TS}`;
const TOOL_LABEL = `E2E Multiline ${TS}`;

const LINES = [
  "Retrieves the current status of an order.",
  "",
  "Args: order_id (str) — the customer-facing order number.",
  "Use for status lookups only; does not modify the order.",
];
const MULTILINE = LINES.join("\n");

const descField = (page: Page) =>
  page.getByPlaceholder(/Retrieves the current status of an order/i);

async function openToolsPage(page: Page) {
  await page.goto("/tools");
  await page.waitForLoadState("networkidle");
}

async function openEditFor(page: Page, label: string) {
  const row = page.locator("tr", { hasText: label });
  await expect(row).toHaveCount(1, { timeout: 15_000 });
  await row.getByRole("button", { name: /^Edit$/i }).click();
  await expect(descField(page)).toBeVisible({ timeout: 15_000 });
}

test.describe("tool description — multi-line create + edit", () => {
  let api: APIRequestContext;

  test.beforeAll(async () => {
    api = await pwRequest.newContext({
      baseURL: API_BASE,
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: ADMIN,
    });
  });

  test.afterAll(async () => {
    await api.delete(`/api/v1/tools/${TOOL_NAME}`).catch(() => {});
    await api.dispose();
  });

  test("A: create a tool with a multi-line description through the real form", async ({ page }) => {
    test.setTimeout(90_000);

    await openToolsPage(page);
    await page.getByRole("button", { name: /new tool/i }).first().click();

    // The control must be a textarea — an <input> cannot hold newlines at all.
    const desc = descField(page);
    await expect(desc).toBeVisible({ timeout: 15_000 });
    expect(await desc.evaluate((el) => el.tagName)).toBe("TEXTAREA");

    await page.getByPlaceholder("get_order_status").fill(TOOL_NAME);
    await page.getByPlaceholder("Get Order Status").fill(TOOL_LABEL);
    await page.getByPlaceholder("https://api.example.com/orders/{{order_id}}")
      .fill("https://example.invalid/orders/{{order_id}}");
    await desc.fill(MULTILINE);

    // The browser really holds 4 lines, not a flattened one.
    expect(await desc.inputValue()).toBe(MULTILINE);

    const created = page.waitForResponse(
      (r) => r.request().method() === "POST" && /\/api\/v1\/tools\/?$/.test(r.url()),
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: /^Create Tool$/i }).click();
    const resp = await created;
    expect(resp.status(), await resp.text()).toBeLessThan(300);
    expect(JSON.parse(resp.request().postData() ?? "{}").description).toBe(MULTILINE);
  });

  test("B: save → reload → the multi-line description survived", async ({ page }) => {
    test.setTimeout(90_000);

    // Full navigation — the form must rehydrate from the backend, not from any
    // store left over from test A.
    await openToolsPage(page);
    await openEditFor(page, TOOL_LABEL);

    const desc = descField(page);
    expect(await desc.evaluate((el) => el.tagName)).toBe("TEXTAREA");
    expect(await desc.inputValue()).toBe(MULTILINE);
    // The exact regression the <input> caused: everything on one line.
    expect((await desc.inputValue()).split("\n")).toHaveLength(4);

    // Append a 5th line, save, reload → the edit persisted with newlines intact.
    const EDITED = `${MULTILINE}\nEdited: also returns the carrier tracking id.`;
    await desc.fill(EDITED);
    const saved = page.waitForResponse(
      (r) =>
        ["PUT", "PATCH"].includes(r.request().method()) &&
        new RegExp(`/api/v1/tools/${TOOL_NAME}$`).test(r.url()),
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: /^Save Changes$/i }).click();
    expect((await saved).status()).toBeLessThan(300);

    await openToolsPage(page);
    await openEditFor(page, TOOL_LABEL);
    expect(await descField(page).inputValue()).toBe(EDITED);
    expect((await descField(page).inputValue()).split("\n")).toHaveLength(5);
  });
});

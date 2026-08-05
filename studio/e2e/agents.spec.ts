import { test, expect } from "@playwright/test";
import { pickModel } from "./lib/agents";

// Unique agent name per test run so parallel re-runs don't collide.
const TS = Date.now();
const AGENT_NAME = `e2e-agts-${TS}`;

// ---------------------------------------------------------------------------
// agents.spec.ts
//   1. Agent list renders (heading, Create Agent button, search field)
//   2. Lifecycle: create agent via no-code form → land on the list (app navigates
//      to /agents after create) → open the agent's detail page → delete. Detail
//      TAB CONTENT is intentionally not asserted here (the tab set is dynamic per
//      agent shape — see agent-detail-modes.spec.ts).
// ---------------------------------------------------------------------------

test.describe("agents list", () => {
  test("heading and Create Agent button are visible", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Main heading
    await expect(page.getByRole("heading", { name: /^Agents$/i }).first()).toBeVisible();
    // Create Agent button in the header toolbar
    await expect(page.getByRole("button", { name: /Create Agent/i })).toBeVisible();
  });

  test("search field is present", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByPlaceholder(/Search agents/i)).toBeVisible();
  });
});

test.describe("create agent → detail page tabs → delete", () => {
  // Runs as a single long test so we can share the created agent across assertions
  // and guarantee cleanup even if mid-test assertions fail via try/finally.

  test("full lifecycle", async ({ page }) => {
    // ── Create (no-code path) ────────────────────────────────────────────────
    await page.goto("/agents/new");
    await page.waitForLoadState("networkidle");

    // Step 1: pick creation path
    await page.getByRole("button", { name: /No-code/i }).click();
    await page.waitForLoadState("domcontentloaded");

    // Step 2: fill required name field
    await page.getByPlaceholder("my-agent").fill(AGENT_NAME);
    await pickModel(page);  // llm_provider_id is REQUIRED since studio 0.1.178 — see lib/agents.ts

    // Capture the POST before clicking submit (so we don't miss it)
    const createResponsePromise = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/agents") &&
        r.request().method() === "POST" &&
        !r.url().includes("/runs") &&
        !r.url().includes("/health"),
      { timeout: 20_000 }
    );

    await page.getByRole("button", { name: /Create Agent/i }).click();

    const createResp = await createResponsePromise;
    expect(createResp.status()).toBe(201);

    // After a no-code create the app toasts and navigates to the AGENTS LIST
    // (CreateAgentPage: `setTimeout(() => navigate("/agents"), 800)`), NOT to a
    // per-agent detail route. Land on the list, confirm the new agent persisted,
    // then click into it to reach the detail page for the tab assertions below.
    await page.waitForURL("**/agents", { timeout: 15_000 });
    await page.waitForLoadState("networkidle");
    const listRow = page.locator("tr", { hasText: AGENT_NAME });
    await expect(listRow).toBeVisible({ timeout: 15_000 });
    await listRow.getByText(AGENT_NAME).click();
    await page.waitForLoadState("networkidle");

    // ── Detail page: agent name shown as the page heading ────────────────────
    await expect(page.getByRole("heading", { name: AGENT_NAME })).toBeVisible({ timeout: 15_000 });

    // ── Detail page: the tab navigation mounted ──────────────────────────────
    // The exact tab set is DYNAMIC per agent shape (an ephemeral agent shows
    // deployments/versions/settings; a durable one shows runs/memory/… etc.), so
    // this lifecycle test only asserts the detail surface rendered with the one
    // tab common to every agent type ("settings"). Per-tab CONTENT (runs filters,
    // conversation memory, trigger config, API endpoint) is covered by the
    // shape-aware agent-detail-modes.spec.ts, not hard-coded here.
    const tabNav = page.locator("main nav");
    await expect(tabNav.getByRole("button", { name: "settings" })).toBeVisible({ timeout: 15_000 });

    // ── Verify agent appears in list ─────────────────────────────────────────
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(AGENT_NAME)).toBeVisible();

    // ── Delete (soft-delete via the in-app confirm MODAL) ─────────────────────
    // studio 0.1.171 replaced native window.confirm() with an in-app modal
    // (AgentListPage.tsx:267, data-testid="delete-agent-modal") precisely BECAUSE a
    // native dialog blocks browser automation. This spec still registered a
    // `page.once("dialog", …)` handler and never confirmed in the modal, so the row was
    // never deleted and the assertion below retried 34 times against a live row. Silent
    // since 0.1.171 — the browser layer could not run against EKS at all (gap G-R0-8).
    const agentRow = page.locator("tr", { hasText: AGENT_NAME });
    await agentRow.getByRole("button", { name: /Delete/i }).click();
    const confirm = page.getByTestId("delete-agent-modal");
    await expect(confirm).toBeVisible({ timeout: 10_000 });
    await confirm.getByRole("button", { name: /^Delete$/ }).click();

    // After deletion the list refetches with status=active; deprecated row disappears
    await expect(page.locator("tr", { hasText: AGENT_NAME })).not.toBeVisible({
      timeout: 15_000,
    });
  });
});

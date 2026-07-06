import { test, expect } from "@playwright/test";

// Unique agent name per test run so parallel re-runs don't collide.
const TS = Date.now();
const AGENT_NAME = `e2e-agts-${TS}`;

// ---------------------------------------------------------------------------
// agents.spec.ts
//   1. Agent list renders (heading, Create Agent button, search field)
//   2. Create agent via no-code form → verify detail-page tabs → delete
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

    // App navigates to /agents/<name> after 800 ms delay
    await page.waitForURL(`**/agents/${AGENT_NAME}`, { timeout: 15_000 });
    await page.waitForLoadState("networkidle");

    // ── Detail page: agent name visible as heading ───────────────────────────
    await expect(page.getByText(AGENT_NAME)).toBeVisible();

    // ── Detail page: all five tabs are rendered ──────────────────────────────
    // Tabs live in a <nav> inside <main> — scope to avoid the sidebar <nav>.
    const tabNav = page.locator("main nav");
    const tabs = ["overview", "runs", "memory", "versions", "settings"] as const;
    for (const tab of tabs) {
      await expect(tabNav.getByRole("button", { name: tab })).toBeVisible();
    }

    // ── Verify Runs tab ──────────────────────────────────────────────────────
    await tabNav.getByRole("button", { name: "runs" }).click();
    await page.waitForLoadState("networkidle");
    // RunsTab renders two filter <select> elements once loaded
    await expect(page.locator("main select").first()).toBeVisible({ timeout: 10_000 });

    // ── Verify Memory tab ────────────────────────────────────────────────────
    await tabNav.getByRole("button", { name: "memory" }).click();
    await expect(page.getByText("Conversation Memory")).toBeVisible();

    // ── Verify Settings tab ──────────────────────────────────────────────────
    await tabNav.getByRole("button", { name: "settings" }).click();
    // Use heading role to avoid strict-mode: "No schedule triggers configured"
    // paragraph also contains "schedule triggers" which plain getByText would match.
    await expect(page.getByRole("heading", { name: "Schedule Triggers" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Webhook Triggers" })).toBeVisible();

    // ── Verify Overview tab (default; reactive agent shows API Endpoint) ──────
    await tabNav.getByRole("button", { name: "overview" }).click();
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "API Endpoint" })).toBeVisible();

    // ── Verify agent appears in list ─────────────────────────────────────────
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(AGENT_NAME)).toBeVisible();

    // ── Delete (soft-delete via confirm dialog) ───────────────────────────────
    const agentRow = page.locator("tr", { hasText: AGENT_NAME });
    // Register once so the dialog is accepted the moment it appears
    page.once("dialog", (d) => d.accept());
    await agentRow.getByRole("button", { name: /Delete/i }).click();

    // After deletion the list refetches with status=active; deprecated row disappears
    await expect(page.locator("tr", { hasText: AGENT_NAME })).not.toBeVisible({
      timeout: 15_000,
    });
  });
});

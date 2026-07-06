import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// create-agent-wizard.spec.ts
//   Proves the create-agent wizard exposes the 4-way type picker and that the
//   trigger fields adapt to the chosen type (Scheduled → cron, Event-driven →
//   filter). Real-browser wiring check.
// ---------------------------------------------------------------------------

async function openNoCode(page: import("@playwright/test").Page) {
  await page.goto("/agents/new");
  await page.waitForLoadState("networkidle");
  await page.getByRole("button", { name: /No-code/i }).click();
}

test.describe("create-agent wizard", () => {
  test("shows the four agent-type cards", async ({ page }) => {
    await openNoCode(page);
    await expect(page.getByRole("button", { name: /Reactive/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Durable/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Scheduled/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /Event-Driven/i })).toBeVisible();
  });

  test("Scheduled reveals cron/timezone fields", async ({ page }) => {
    await openNoCode(page);
    await expect(page.getByPlaceholder("0 9 * * 1")).toHaveCount(0);
    await page.getByRole("button", { name: /Scheduled/i }).click();
    await expect(page.getByPlaceholder("0 9 * * 1")).toBeVisible();
  });

  test("Event-Driven reveals filter-condition fields", async ({ page }) => {
    await openNoCode(page);
    await page.getByRole("button", { name: /Event-Driven/i }).click();
    await expect(page.getByText(/Filter conditions/i)).toBeVisible();
    await expect(page.getByPlaceholder("event_type")).toBeVisible();
  });

  test("Reactive shows no trigger fields", async ({ page }) => {
    await openNoCode(page);
    await page.getByRole("button", { name: /Reactive/i }).click();
    await expect(page.getByPlaceholder("0 9 * * 1")).toHaveCount(0);
    await expect(page.getByText(/Filter conditions/i)).toHaveCount(0);
  });

  test("Scheduled reveals a JSON input-payload field", async ({ page }) => {
    await openNoCode(page);
    await page.getByRole("button", { name: /Scheduled/i }).click();
    await expect(page.getByText(/Input payload — JSON job spec/i)).toBeVisible();
  });

  // The per-type instructions-template swap (textarea .value) is verified in the
  // Vitest suite (CreateAgentPage.test.tsx) — controlled-textarea value isn't
  // reliably assertable via Playwright's text matchers.
});

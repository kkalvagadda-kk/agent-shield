import { test, expect } from "@playwright/test";

// Proves the harness: authenticated session loads the Studio SPA and the
// primary nav renders (i.e. Keycloak login + token exchange + app boot worked).
test.describe("smoke", () => {
  test("authenticated app loads with nav", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Should NOT be sitting on the Keycloak login form.
    await expect(page.locator("#username")).toHaveCount(0);
    // Sidebar nav for the core sections is present.
    const body = page.locator("body");
    await expect(body).toContainText(/Agents/i);
  });

  test("agents list route renders", async ({ page }) => {
    await page.goto("/"); // AgentListPage is the index route
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("heading", { name: "Agents" }).first()).toBeVisible();
  });
});

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// cost-console.spec.ts
//   Proves the cost-tracking user journey:
//     sidebar "Cost" entry → /observability/costs → GET /observability/costs
//     fires → the console renders (headline totals + breakdown panels) →
//     env toggle re-queries the sandbox scope → dashboard "Cost console" link
//     round-trips back to the page.
//
//   Read-only view (no write surface), so no save→reload assertion — we assert
//   the network call fires and the UI wires to it, per the same boundary the
//   other observability-less specs accept (agent runs may be sparse).
// ---------------------------------------------------------------------------

test.use({ storageState: "e2e/.auth/state.json" });

test("Cost console: sidebar → page → network → render → env toggle", async ({ page }) => {
  // Journey starts at the sidebar entry, not a deep link.
  await page.goto("/");
  await page.waitForLoadState("networkidle");

  const costsCall = page.waitForResponse(
    (r) => r.url().includes("/observability/costs") && r.request().method() === "GET",
    { timeout: 20_000 }
  );
  await page.getByRole("link", { name: "Cost", exact: true }).click();
  const prodResp = await costsCall;
  expect(prodResp.url()).toContain("environment=production");

  // Page rendered with its headline + a breakdown panel.
  await expect(page.getByRole("heading", { name: /Cost Console/i })).toBeVisible();
  await expect(page.getByText("Total Spend")).toBeVisible();
  await expect(page.getByRole("heading", { name: /Spend by Model/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Spend by Agent/i })).toBeVisible();

  // Env toggle re-queries the sandbox scope (sandbox spend never mixes with prod).
  const sandboxCall = page.waitForResponse(
    (r) =>
      r.url().includes("/observability/costs") &&
      r.url().includes("environment=sandbox"),
    { timeout: 20_000 }
  );
  await page.getByRole("button", { name: "sandbox", exact: true }).click();
  await sandboxCall;
});

test("Dashboard LLM Cost panel links to the Cost console", async ({ page }) => {
  const dashCall = page.waitForResponse(
    (r) => r.url().includes("/observability/dashboard") && r.request().method() === "GET",
    { timeout: 20_000 }
  );
  await page.goto("/observability/dashboard/production");
  await dashCall;

  await expect(page.getByRole("heading", { name: /LLM Cost/i })).toBeVisible();
  await page.getByRole("link", { name: /Cost console/i }).click();
  await page.waitForURL("**/observability/costs", { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: /Cost Console/i })).toBeVisible();
});

import { test, expect, type Page } from "@playwright/test";

// ─────────────────────────────────────────────────────────────────────────────
// REGRESSION — the whole app must not unmount because ONE sidebar query returned
// a shape its consumer did not expect.
//
// The defect (studio 0.1.181, fixed 0.1.182):
//   Sidebar.tsx called `fetch("/api/v1/admin/teams-summary").then(r => r.json())`
//   — raw fetch, no Authorization header, and no `r.ok` check. That worked only
//   because the endpoint was UNAUTHENTICATED. registry-api 0.2.262 closed that
//   hole (anonymous `POST /api/v1/admin/users {"role":"platform-admin"}` returned
//   201), so the call began returning 401 `{"detail":"Authentication required"}`.
//   `r.json()` parsed that OBJECT and React Query stored it as SUCCESS data, then
//       const myTeam = (sidebarTeams ?? []).find(...)
//   threw `TypeError: (a ?? []).find is not a function` INSIDE a useMemo. Uncaught
//   → React unmounted the tree → blank page on EVERY route, not just /agents.
//
// Why this spec and not a Vitest: the crash needed the real router + real
// QueryClient + a real non-array HTTP response to reproduce. A mocked-module
// component test hands the component the shape the test author imagined.
//
// The `pageerror` listener IS the assertion. Asserting only "heading is visible"
// would let a future regression that renders but logs an uncaught TypeError pass.
// Against 0.1.181 this listener captures the TypeError above.
//
// Note this guards the CLASS, not the instance: the `?? []` guard covers only
// null/undefined, so a 500, an HTML error page from the gateway, or any future
// envelope change would have blanked the app identically. Both cases below assert
// the shell survives, so the fix is the array coercion — not "the endpoint is 200
// again". Do NOT rewrite these to depend on the endpoint's live auth state.
// ─────────────────────────────────────────────────────────────────────────────

const TEAMS_SUMMARY = "**/api/v1/admin/teams-summary";

/** Collect uncaught page exceptions; a non-empty list fails the test. */
function trackPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

test.describe("app shell resilience — /admin/teams-summary", () => {
  test("401 from teams-summary does not blank the app", async ({ page }) => {
    const errors = trackPageErrors(page);

    await page.route(TEAMS_SUMMARY, (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Authentication required" }),
      }),
    );

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // The app is mounted: index route heading rendered, and we are not on Keycloak.
    await expect(page.locator("#username")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Agents" }).first()).toBeVisible();
    // Sidebar itself still rendered — it is the component that owns the failing query.
    await expect(page.locator("nav, aside").first()).toBeVisible();

    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("non-array 200 body from teams-summary does not blank the app", async ({ page }) => {
    const errors = trackPageErrors(page);

    // A 200 whose body is an object, not a list — the same shape surprise arriving
    // through the success path instead of the error path.
    await page.route(TEAMS_SUMMARY, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ teams: [] }),
      }),
    );

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "Agents" }).first()).toBeVisible();
    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("My Agents renders when teams-summary fails", async ({ page }) => {
    // MyAgentsPage read the SAME endpoint with the same raw fetch. It had
    // `if (!r.ok) return []` so it degraded QUIETLY instead of crashing — the
    // "Shared With Me" panel rendered empty as though the user had no shared
    // agents. Quiet is still wrong; this asserts the page survives rather than
    // asserting the (correctly empty) panel contents.
    const errors = trackPageErrors(page);

    await page.route(TEAMS_SUMMARY, (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Authentication required" }),
      }),
    );

    await page.goto("/my-agents");
    await page.waitForLoadState("networkidle");

    await expect(page.locator("#username")).toHaveCount(0);
    await expect(page.locator("body")).toContainText(/agent/i);

    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toHaveLength(0);
  });
});

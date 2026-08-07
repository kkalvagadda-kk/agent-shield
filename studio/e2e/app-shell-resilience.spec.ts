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
//
// RETARGETED 2026-08-06 (RBAC R2, studio 0.1.184). READ THIS BEFORE EDITING THE URL.
// These cases stubbed `/api/v1/admin/teams-summary`. R2 moved the sidebar and My
// Agents onto the self-scoped `/api/v1/me/team` and made the census admin-only — so
// the stub stopped intercepting anything the shell calls, and all three cases went on
// passing while asserting NOTHING. They were green in the R2 regression sweep for
// exactly that reason. A route stub is only a guard while the app still requests the
// route it names; when the producer moves, the stub silently becomes decoration.
// That is the same failure shape as the bug this file exists for — code that looks
// like it is doing something and is not. So this is no longer left to a comment:
// every case asserts its stub actually FIRED (`expectStubWasUsed`). If the sidebar's
// team query moves again, these fail on the next run instead of going quietly green.
// ─────────────────────────────────────────────────────────────────────────────

const SIDEBAR_TEAM = "**/api/v1/me/team";

/** Collect uncaught page exceptions; a non-empty list fails the test. */
function trackPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

/**
 * Install the stub AND count how often it fired.
 *
 * Every case asserts the count is non-zero. That is not belt-and-braces: when R2 moved
 * the sidebar off `/admin/teams-summary`, the old stub stopped matching anything the app
 * requests and all three cases kept passing while asserting nothing at all. A comment
 * saying "move this constant if the query moves" would not have caught it — nobody reads
 * a comment when the suite is green. This does, mechanically, on the next run.
 */
function stubTeamQuery(page: Page, fulfill: { status: number; body: unknown }) {
  const hits = { n: 0 };
  page.route(SIDEBAR_TEAM, (route) => {
    hits.n += 1;
    return route.fulfill({
      status: fulfill.status,
      contentType: "application/json",
      body: JSON.stringify(fulfill.body),
    });
  });
  return hits;
}

function expectStubWasUsed(hits: { n: number }) {
  expect(
    hits.n,
    `the ${SIDEBAR_TEAM} stub never fired — the app shell no longer requests it, so this ` +
      `case is asserting nothing. Point SIDEBAR_TEAM at whatever the sidebar's team query ` +
      `now calls (see Sidebar.tsx).`,
  ).toBeGreaterThan(0);
}

test.describe("app shell resilience — the sidebar team query", () => {
  test("401 from the team query does not blank the app", async ({ page }) => {
    const errors = trackPageErrors(page);

    const hits = stubTeamQuery(page, { status: 401, body: { detail: "Authentication required" } });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // The app is mounted: index route heading rendered, and we are not on Keycloak.
    await expect(page.locator("#username")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Agents" }).first()).toBeVisible();
    // Sidebar itself still rendered — it is the component that owns the failing query.
    await expect(page.locator("nav, aside").first()).toBeVisible();

    expectStubWasUsed(hits);
    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("non-array 200 body from the team query does not blank the app", async ({ page }) => {
    const errors = trackPageErrors(page);

    // A 200 whose `grants` is an object rather than a list — the same shape surprise
    // arriving through the success path instead of the error path. `grants` is what
    // the consumer iterates now, so this is the exact analogue of the original body
    // (which made the whole payload a non-array); pointing this at the old shape after
    // the retarget would stub a field nobody reads.
    const hits = stubTeamQuery(page, {
      status: 200,
      body: { team: "platform", namespace: null, grants: { nope: 1 } },
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "Agents" }).first()).toBeVisible();
    expectStubWasUsed(hits);
    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toHaveLength(0);
  });

  test("My Agents renders when the team query fails", async ({ page }) => {
    // MyAgentsPage read the SAME endpoint with the same raw fetch. It had
    // `if (!r.ok) return []` so it degraded QUIETLY instead of crashing — the
    // "Shared With Me" panel rendered empty as though the user had no shared
    // agents. Quiet is still wrong; this asserts the page survives rather than
    // asserting the (correctly empty) panel contents.
    const errors = trackPageErrors(page);

    const hits = stubTeamQuery(page, { status: 401, body: { detail: "Authentication required" } });

    await page.goto("/my-agents");
    await page.waitForLoadState("networkidle");

    await expect(page.locator("#username")).toHaveCount(0);
    await expect(page.locator("body")).toContainText(/agent/i);

    expectStubWasUsed(hits);
    expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toHaveLength(0);
  });
});

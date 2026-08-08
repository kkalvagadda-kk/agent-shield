import { test, expect, type Page, request as pwRequest } from "@playwright/test";
import { assertRoleSession, stateFor } from "./lib/roles";

// ---------------------------------------------------------------------------
// rbac-role-journeys.spec.ts — RBAC R2, the layer that can actually fail.
//
// R2 is the first phase where somebody gets a 403 they did not get before:
// `/api/v1/admin/*` and `/api/v1/admin/users*` now require the platform-admin
// global role, and `POST /agents/` requires contributor+. suite-98 proves the
// API returns those 403s. It cannot prove the PRODUCT still works — and the last
// time an authorization change shipped, the API was correct and the app rendered
// a blank page on every route for every user
// (docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md).
//
// That bug is the reason this file exists. It was not caught because:
//   - the bash suites `kubectl exec` into the pod and never open a browser, and
//   - every Playwright spec ran as platform-admin, so no spec ever experienced a
//     403 at all.
// A role gate whose only witnesses are already privileged is a guard that cannot
// fail. global-setup now provisions `e2e-contributor` and `e2e-consumer` through
// the real POST /api/v1/admin/users and saves a session for each; these specs
// drive the deployed Studio AS those users.
//
// WHAT EACH CASE IS FOR
//   T-RJ-001/002  a non-admin's app shell survives the new 403s — zero uncaught
//                 page errors. This is the blank-page class, asserted directly.
//   T-RJ-003/004  the Admin nav is absent for non-admins and present for admins
//                 (deny the wrong role WITHOUT denying the right one — an
//                 over-broad R2 that hides Admin from everyone passes half a
//                 suite otherwise).
//   T-RJ-005      a deep link to /admin/access does not render the admin page for
//                 a consumer, and STILL does not after a reload. The reload is
//                 the point: a guard that only holds on first render is a guard
//                 that fails on refresh, which is how users actually arrive.
//   T-RJ-006      "Shared With Me" still renders for a consumer. This is the
//                 regression test for R2's real hazard: /admin/teams-summary was
//                 read by the sidebar for EVERY role, and locking it without
//                 splitting out /api/v1/me/team would have silently emptied this
//                 section platform-wide.
//   T-RJ-007      the grant picker still populates for a contributor, proving
//                 /api/v1/users/directory replaced /admin/users where it had to.
//   T-RJ-008      the UI guard is not the enforcement. A consumer's own token,
//                 used directly, is still refused by the API — because hiding a
//                 nav item is a courtesy and a 403 is the control.
//
// NO route stubs anywhere. Every response comes from the deployed platform.
// ---------------------------------------------------------------------------

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://agentshield.127.0.0.1.nip.io:8443";
const PERSONA_PASS = process.env.E2E_PERSONA_PASS || "Persona2024!";

/**
 * Uncaught page errors are THE assertion, not a nicety.
 *
 * The blank page rendered nothing at all, but a subtler regression can render
 * fine and still throw — asserting only "the heading is visible" would let that
 * through. React unmounts the tree on an uncaught render throw, so this listener
 * is what stands between a 403 and a dead app.
 */
function trackPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

function expectNoPageErrors(errors: string[]) {
  expect(errors, `uncaught page errors: ${errors.join(" | ")}`).toHaveLength(0);
}

/** The sidebar's build marker — renders only once React has mounted the shell. */
const SHELL = '[data-testid="studio-build"]';

// ── consumer ────────────────────────────────────────────────────────────────

test.describe("consumer", () => {
  test.use({ storageState: stateFor("consumer") });
  test.beforeAll(() => assertRoleSession("consumer"));

  test("T-RJ-001 the app shell mounts and stays up under R2's 403s", async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto(`${BASE_URL}/agents`);
    await expect(page.locator(SHELL)).toBeVisible();
    // The route that was blank. A consumer now gets 403 from several admin
    // endpoints the shell used to read freely; none of them may take the app out.
    await expect(page.getByRole("navigation").first()).toBeVisible();
    expectNoPageErrors(errors);
  });

  test("T-RJ-003 sees NO Admin section in the sidebar", async ({ page }) => {
    await page.goto(`${BASE_URL}/agents`);
    await expect(page.locator(SHELL)).toBeVisible();
    // Scoped to the sidebar: "Admin" appears in page copy elsewhere, and a bare
    // getByText would pass for the wrong reason.
    await expect(page.locator("aside").getByText("Admin", { exact: true })).toHaveCount(0);
    // …while ordinary navigation is untouched. A consumer losing the whole nav
    // would also satisfy the assertion above.
    await expect(page.locator("aside").getByText("Catalog", { exact: true })).toBeVisible();
  });

  test("T-RJ-005 a deep link to /admin/access is refused, and stays refused after reload", async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto(`${BASE_URL}/admin/access`);
    await expect(page.locator(SHELL)).toBeVisible();
    // RequireRole redirects to "/" rather than rendering the page.
    await expect(page.getByRole("heading", { name: /access control/i })).toHaveCount(0);
    expect(new URL(page.url()).pathname).not.toBe("/admin/access");

    // save → reload → assert survived, applied to a GUARD rather than to a row:
    // the interesting failure is a gate that holds on first render and falls open
    // on refresh, when role resolution races the route.
    await page.reload();
    await expect(page.locator(SHELL)).toBeVisible();
    await expect(page.getByRole("heading", { name: /access control/i })).toHaveCount(0);
    expectNoPageErrors(errors);
  });

  test("T-RJ-006 'Shared With Me' still renders — /me/team replaced the admin census", async ({ page }) => {
    const errors = trackPageErrors(page);
    // The self-scoped endpoint must actually be called, and must not 403. Before
    // R2 this section read /admin/teams-summary, which is now admin-only.
    const meTeam = page.waitForResponse(
      (r) => r.url().includes("/api/v1/me/team") && r.request().method() === "GET",
      { timeout: 20_000 },
    );
    await page.goto(`${BASE_URL}/agents`);
    const res = await meTeam;
    expect(res.status(), "a consumer must be able to read their own team").toBe(200);

    await expect(page.locator("aside").getByText("Shared With Me", { exact: true })).toBeVisible();
    expectNoPageErrors(errors);
  });

  test("T-RJ-008 the API refuses a consumer's own token — the nav guard is not the control", async () => {
    // Hiding the Admin menu is a courtesy to the user. If the only thing stopping
    // a consumer from reading /admin/users were a hidden <a>, R2 would be theatre.
    const ctx = await pwRequest.newContext({ baseURL: BASE_URL, ignoreHTTPSErrors: true });
    const tokenRes = await ctx.post("/realms/agentshield/protocol/openid-connect/token", {
      form: {
        grant_type: "password",
        client_id: "agentshield-studio",
        username: "e2e-consumer",
        password: PERSONA_PASS,
      },
    });
    expect(tokenRes.ok(), `consumer token: ${tokenRes.status()}`).toBeTruthy();
    const token = (await tokenRes.json()).access_token;

    const res = await ctx.get("/api/v1/admin/users", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(
      res.status(),
      "a consumer must not be able to enumerate platform users (measured 200 on 0.2.261)",
    ).toBe(403);
    await ctx.dispose();
  });
});

// ── contributor ─────────────────────────────────────────────────────────────

// A fixed name, seeded idempotently: 409 means a previous run made it, which is a
// success, so repeated runs create exactly one row ever rather than one per run.
const PICKER_AGENT = "rj-picker-probe";

test.describe("contributor", () => {
  test.use({ storageState: stateFor("contributor") });
  test.beforeAll(() => assertRoleSession("contributor"));

  test.beforeAll(async () => {
    // Seeded as platform-admin over the API so the case does not depend on whatever
    // agents happen to exist on the cluster. The contributor only READS it here; the
    // claim under test is that the picker populates, not that the grant succeeds.
    const ctx = await pwRequest.newContext({ baseURL: BASE_URL, ignoreHTTPSErrors: true });
    const tok = await ctx.post("/realms/agentshield/protocol/openid-connect/token", {
      form: {
        grant_type: "password",
        client_id: "agentshield-studio",
        username: process.env.STUDIO_E2E_USER || "platform-admin",
        password: process.env.STUDIO_E2E_PASSWORD || "PlatformAdmin2024",
      },
    });
    expect(tok.ok(), `admin token: ${tok.status()}`).toBeTruthy();
    const res = await ctx.post("/api/v1/agents/", {
      headers: { Authorization: `Bearer ${(await tok.json()).access_token}` },
      data: { name: PICKER_AGENT, team: "platform", description: "T-RJ-007 picker probe" },
    });
    expect(
      [201, 409].includes(res.status()),
      `seed ${PICKER_AGENT}: ${res.status()} ${await res.text()}`,
    ).toBeTruthy();
    await ctx.dispose();
  });

  test("T-RJ-002 the app shell mounts and stays up under R2's 403s", async ({ page }) => {
    const errors = trackPageErrors(page);
    await page.goto(`${BASE_URL}/agents`);
    await expect(page.locator(SHELL)).toBeVisible();
    expectNoPageErrors(errors);
  });

  test("T-RJ-004 sees NO Admin section, but keeps the Build surfaces", async ({ page }) => {
    await page.goto(`${BASE_URL}/agents`);
    await expect(page.locator(SHELL)).toBeVisible();
    await expect(page.locator("aside").getByText("Admin", { exact: true })).toHaveCount(0);
    // A contributor creates things. If R2 had over-reached, this is what would go.
    await expect(page.locator("aside").getByText("Build", { exact: true })).toBeVisible();
  });

  test("T-RJ-007 the grant picker populates from /users/directory, not /admin/users", async ({ page }) => {
    const errors = trackPageErrors(page);
    // The model says an agent-admin may delegate on their own artifact (design §2),
    // so this picker has to work for a non-admin. It used to read /admin/users,
    // which R2 restricts — without the directory endpoint the form would open onto
    // an empty list: a capability broken silently by an authorization change.
    //
    // Navigates DIRECTLY to a seeded agent (see beforeAll) instead of clicking the
    // first row of /agents. The first draft did the latter and skipped when the list
    // was empty — a conditional skip is a guard that cannot fail, which is the exact
    // defect this whole spec file exists to correct. It also failed for a second,
    // duller reason: the skip fired while a `waitForResponse` was still pending, and
    // Playwright reported "Test ended" rather than the real cause.
    const directory = page.waitForResponse(
      (r) => r.url().includes("/api/v1/users/directory") && r.request().method() === "GET",
      { timeout: 20_000 },
    );

    await page.goto(`${BASE_URL}/agents/${PICKER_AGENT}`);
    await expect(page.locator(SHELL)).toBeVisible();
    await page.locator("main nav").getByRole("button", { name: "settings" }).click();
    // The header "Grant" button opens the form (it hides once open).
    await page.getByRole("main").getByRole("button", { name: /^grant$/i }).click();

    const res = await directory;
    expect(res.status(), "a contributor must be able to read the user directory").toBe(200);
    const picker = page.getByLabel(/user to grant/i);
    await expect(picker).toBeVisible();
    // >1 because option[0] is the "Select a user…" placeholder; an empty picker
    // is exactly the silent breakage this case exists to catch.
    await expect
      .poll(async () => picker.locator("option").count(), { timeout: 15_000 })
      .toBeGreaterThan(1);
    expectNoPageErrors(errors);
  });
});

// ── R3: artifact-scoped mutations, from the browser ─────────────────────────
//
// suite-98 proves the API returns these 403s. What only the browser can show is what
// the person SEES when it happens — R2's lesson was that a correct API and a broken
// screen look identical from the pod.

test.describe("contributor — R3 artifact scope", () => {
  test.use({ storageState: stateFor("contributor") });
  test.beforeAll(() => assertRoleSession("contributor"));

  test("T-RJ-010 opening someone else's agent does not blank the app", async ({ page }) => {
    // rj-picker-probe is seeded by the admin, so this contributor holds no grant on it.
    // Every management call the detail page makes will now 403. The page must degrade,
    // not unmount — this is the blank-page class re-asserted at the new 403 surface.
    const errors = trackPageErrors(page);
    await page.goto(`${BASE_URL}/agents/${PICKER_AGENT}`);
    await expect(page.locator(SHELL)).toBeVisible();
    await expect(page.getByRole("main")).toBeVisible();
    expectNoPageErrors(errors);
  });

  test("T-RJ-011 a non-owner's edit is REFUSED by the API, not just hidden by the UI", async () => {
    // The nav-guard-is-not-the-control case, at artifact scope. If the only thing
    // stopping a non-owner from editing were a disabled button, R3 would be theatre.
    const ctx = await pwRequest.newContext({ baseURL: BASE_URL, ignoreHTTPSErrors: true });
    const tokenRes = await ctx.post("/realms/agentshield/protocol/openid-connect/token", {
      form: {
        grant_type: "password",
        client_id: "agentshield-studio",
        username: "e2e-contributor",
        password: PERSONA_PASS,
      },
    });
    expect(tokenRes.ok(), `contributor token: ${tokenRes.status()}`).toBeTruthy();
    const token = (await tokenRes.json()).access_token;

    const res = await ctx.patch(`/api/v1/agents/${PICKER_AGENT}`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { description: "hijacked by a non-owner" },
    });
    expect(
      res.status(),
      "a contributor with no grant on this agent must not be able to edit it",
    ).toBe(403);
    await ctx.dispose();
  });
});

// ── Decisions 46 + 47: tool ownership and the private default, in the browser ──
//
// suite-98 T-S98-020..027 proves the API side. What only a browser can show is the
// screen a contributor actually gets: 0.2.267 started deriving `owner_team` from the
// caller and answering 403 when a non-admin asked for another team, while the form kept
// offering a free-text Team input to everyone. The API was correct and the form was
// lying — the exact split this file exists for.

test.describe("contributor — tool ownership (Decisions 46 + 47)", () => {
  test.use({ storageState: stateFor("contributor") });
  test.beforeAll(() => assertRoleSession("contributor"));

  const TOOL = `e2e_rj_owned_${Date.now()}`;

  test("T-RJ-012 the Team field is read-only for a non-admin, and the tool saves and survives a reload", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const errors = trackPageErrors(page);

    await page.goto(`${BASE_URL}/tools`);
    await expect(page.locator(SHELL)).toBeVisible();
    await page.getByRole("button", { name: /new tool/i }).first().click();

    // The field a contributor does not get to choose. Rendered — not hidden — because
    // the owning team decides who can see and use the tool.
    const team = page.getByTestId("tool-owner-team-readonly");
    await expect(team).toBeVisible({ timeout: 15_000 });
    await expect(team).toBeDisabled();
    await expect(team).toHaveValue("platform");

    await page.getByPlaceholder("get_order_status").fill(TOOL);
    await page
      .getByPlaceholder("https://api.example.com/orders/{{order_id}}")
      .fill("https://example.invalid/rj-owned");

    const created = page.waitForResponse(
      (r) => r.request().method() === "POST" && /\/api\/v1\/tools\/?$/.test(r.url()),
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: /^Create Tool$/i }).click();
    const resp = await created;

    // 201, not the 403 the free-text field used to produce — and the request carries NO
    // owner_team at all. "Sent my own team" and "sent nothing" both yield the right row,
    // but only the second one is the client declining to assert what it does not decide.
    expect(resp.status(), await resp.text()).toBe(201);
    expect(JSON.parse(resp.request().postData() ?? "{}")).not.toHaveProperty("owner_team");

    // Save -> RELOAD FROM THE BACKEND -> assert (DoD #2). A full navigation, so the row
    // comes from the API and not from any store left behind by the create.
    await page.goto(`${BASE_URL}/tools`);
    await expect(page.locator(SHELL)).toBeVisible();
    const row = page.locator("tr", { hasText: TOOL });
    await expect(row).toHaveCount(1, { timeout: 20_000 });
    // The team the SERVER derived, rendered in the row's Team cell.
    await expect(row).toContainText("platform");

    expectNoPageErrors(errors);
  });

  test("T-RJ-013 a teammate does NOT see the contributor's private draft", async () => {
    // The other half of Decision 47's default flip. A tool created after migration 0080 is
    // `private`, and visibility is CREATOR-scoped — the same rule agents and workflows have
    // always used. "Drafts are yours until you share."
    //
    // This case briefly asserted the OPPOSITE (that a teammate sees it) while visibility was
    // team-scoped. That was wrong: it conflated Decision 46's USE axis — owner_team, who may
    // CALL the tool — with Decision 47's VISIBILITY axis, who SEES it in a catalog.
    //
    // e2e-consumer is provisioned into team `platform`, same team as e2e-contributor, so a
    // 0 here is specifically "not my draft" and not "wrong team".
    const ctx = await pwRequest.newContext({ baseURL: BASE_URL, ignoreHTTPSErrors: true });
    const tokenRes = await ctx.post("/realms/agentshield/protocol/openid-connect/token", {
      form: {
        grant_type: "password",
        client_id: "agentshield-studio",
        username: "e2e-consumer",
        password: PERSONA_PASS,
      },
    });
    expect(tokenRes.ok(), `consumer token: ${tokenRes.status()}`).toBeTruthy();
    const token = (await tokenRes.json()).access_token;

    const res = await ctx.get(`/api/v1/tools/?name=${TOOL}&limit=5`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status()).toBe(200);
    const items = (await res.json()).items ?? [];
    expect(
      items.filter((i: { name: string }) => i.name === TOOL),
      "a teammate must NOT see another person's private draft — same as a draft agent",
    ).toHaveLength(0);
    await ctx.dispose();
  });
});

// ── platform-admin (the "did not deny the right role" half) ─────────────────

test.describe("platform-admin", () => {
  test.beforeAll(() => assertRoleSession("platform-admin"));

  test("T-RJ-009 still sees Admin and can open Access Control", async ({ page }) => {
    const errors = trackPageErrors(page);
    const users = page.waitForResponse(
      (r) => r.url().includes("/api/v1/admin/users") && r.request().method() === "GET",
      { timeout: 20_000 },
    );
    await page.goto(`${BASE_URL}/admin/access`);
    await expect(page.locator(SHELL)).toBeVisible();
    const res = await users;
    expect(res.status(), "R2 must deny the wrong role without denying the right one").toBe(200);
    await expect(page.locator("aside").getByText("Admin", { exact: true })).toBeVisible();
    expectNoPageErrors(errors);
  });
});

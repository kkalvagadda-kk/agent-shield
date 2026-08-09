import { test, expect, type APIRequestContext, request as pwRequest } from "@playwright/test";

// ---------------------------------------------------------------------------
// admin-access-roles.spec.ts  (rbac-design.md §2.1 / §8.4)
//
// Real-browser coverage of the role vocabulary on /admin/access after the global
// read-only role was renamed `viewer` -> `consumer`.
//
// Why this spec exists: the role dropdown is the ONLY surface that writes
// user_team_assignments.role from the UI, and it previously offered the legacy
// names (admin/operator/viewer). A rename that updates ROLE_HIERARCHY but leaves
// the dropdown writing legacy strings is invisible to Vitest (which never renders
// the real page against the real API) and invisible to the bash suites (which
// never open a browser). This drives the actual control.
//
// Covers:
//   1. The dropdown offers exactly the canonical names — no legacy spelling.
//   2. Assigning `consumer` through the UI persists: save -> full page reload ->
//      the chip still reads `consumer` (round-trip through the API + DB).
// ---------------------------------------------------------------------------

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "https://agentshield.127.0.0.1.nip.io:8443";

const CANONICAL_ROLES = ["platform-admin", "contributor", "consumer"];
const LEGACY_ROLES = ["admin", "operator", "viewer"];

async function bearer(): Promise<string> {
  const ctx = await pwRequest.newContext({ baseURL: BASE_URL, ignoreHTTPSErrors: true });
  const r = await ctx.post("/realms/agentshield/protocol/openid-connect/token", {
    form: {
      grant_type: "password",
      client_id: "agentshield-studio",
      username: process.env.STUDIO_E2E_USER || "platform-admin",
      password: process.env.STUDIO_E2E_PASSWORD || "PlatformAdmin2024",
    },
  });
  expect(r.ok(), `token: ${r.status()} ${await r.text()}`).toBeTruthy();
  const tok = (await r.json()).access_token as string;
  await ctx.dispose();
  return tok;
}

const STAMP = Date.now().toString().slice(-7);
const USERNAME = `roletest-${STAMP}`;

let api: APIRequestContext;
let kcId = "";

test.beforeAll(async () => {
  const token = await bearer();
  api = await pwRequest.newContext({
    baseURL: BASE_URL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
  });
  // Seed a throwaway user to retarget — never mutate the platform-admin running
  // the suite, or the rest of the run loses its privileges.
  const r = await api.post("/api/v1/admin/users", {
    data: {
      username: USERNAME,
      email: `${USERNAME}@example.com`,
      first_name: "Role",
      last_name: "Test",
      temp_password: "RoleTest2024!",
      team: "default",
      role: "contributor",
    },
  });
  expect(r.ok(), `seed user: ${r.status()} ${await r.text()}`).toBeTruthy();
  kcId = (await r.json()).kc_id;
});

test.afterAll(async () => {
  if (kcId) await api.delete(`/api/v1/admin/users/${kcId}`).catch(() => {});
  await api.dispose();
});

test("role dropdown offers only canonical role names (§8.4)", async ({ page }) => {
  await page.goto(`${BASE_URL}/admin/access`);
  await page.getByRole("row", { name: new RegExp(USERNAME) }).waitFor({ timeout: 20_000 });

  // Open the edit modal for the seeded user.
  await page.getByRole("row", { name: new RegExp(USERNAME) }).getByRole("button", { name: /edit/i }).click();

  const select = page.locator("select").filter({ has: page.locator(`option[value="consumer"]`) }).first();
  await expect(select).toBeVisible();

  const options = await select.locator("option").evaluateAll((els) =>
    els.map((e) => (e as HTMLOptionElement).value),
  );
  expect(options).toEqual(CANONICAL_ROLES);
  for (const legacy of LEGACY_ROLES) expect(options).not.toContain(legacy);
});

test("assigning consumer persists across a reload (save -> reload -> assert)", async ({ page }) => {
  await page.goto(`${BASE_URL}/admin/access`);
  const row = page.getByRole("row", { name: new RegExp(USERNAME) });
  await row.waitFor({ timeout: 20_000 });
  await row.getByRole("button", { name: /edit/i }).click();

  const select = page.locator("select").filter({ has: page.locator(`option[value="consumer"]`) }).first();
  await select.selectOption("consumer");

  // Assert the write actually left the browser, not just the store.
  const saved = page.waitForResponse(
    (r) => /\/api\/v1\/admin\/users\//.test(r.url()) && r.request().method() === "PATCH" && r.ok(),
    { timeout: 20_000 },
  );
  // EditUserModal's button reads "Save Changes" (AdminAccessPage.tsx:452), and has since
  // 3192ebe — BEFORE this test was written in 8baba26. The original locator here was
  // /^save$/i, anchored, so it could never match: this test has been RED since the day it
  // was authored and had never once passed. Nothing surfaced it because the browser layer
  // could not run against EKS at all (gap G-R0-8), so the mandatory save->reload->assert
  // guard on the RBAC admin write path was silently absent. Match the real button.
  await page.getByRole("button", { name: /^save changes$/i }).click();
  const patch = await saved;
  expect((await patch.json()).role).toBe("consumer");

  // Full reload — the chip must be rehydrated from the backend, not from memory.
  await page.reload();
  const reloaded = page.getByRole("row", { name: new RegExp(USERNAME) });
  await reloaded.waitFor({ timeout: 20_000 });
  await expect(reloaded).toContainText("consumer");
  await expect(reloaded).not.toContainText("viewer");

  // And confirm at the source of truth, not only in the DOM.
  const api_r = await api.get(`/api/v1/admin/users`);
  const users = (await api_r.json()) as { username: string; role: string | null }[];
  expect(users.find((u) => u.username === USERNAME)?.role).toBe("consumer");
});

test("bootstrap gives platform-admin a role row, so the Admin menu renders (R0 / Decision 40)", async ({
  page,
}) => {
  // The 2026-07-20 symptom was structural, not visual: the assignment row was pinned to
  // a `sub` captured at seed time, the realm was later recreated, Keycloak reissued the
  // admin under a NEW sub, /me answered role=null, `isAtLeast("platform-admin")` was
  // false (Sidebar.tsx:392) and the Admin section silently vanished. Nothing errored —
  // a menu just disappeared, which is why no suite caught it. Nothing in the install
  // wrote that row at all; scripts/seed-platform-admin-role.sh patched it after the
  // fact. It is now written by registry-api's lifespan bootstrap
  // (services/registry-api/bootstrap_admin.py), which looks the admin up by USERNAME on
  // every start so re-pinning falls out of the design.
  //
  // This asserts the WHOLE chain from the browser — real Keycloak login (global-setup)
  // -> GET /me -> the sidebar — because that is the only layer where the symptom was
  // visible. suite-97 T-S97-004 proves the same property from the other end (delete the
  // Keycloak admin, restart, require a re-pin); neither replaces the other.
  //
  // /me is fetched exactly once, in main.tsx, BEFORE the first render — so the waiter
  // must be armed before goto(). A 403 there is swallowed into `role = null`
  // (main.tsx:37-39), i.e. the failure is silent by design and only the sidebar shows it.
  const me = page.waitForResponse(
    (r) => r.url().includes("/api/v1/me") && r.request().method() === "GET",
    { timeout: 30_000 },
  );
  await page.goto(BASE_URL);
  const meResponse = await me;
  const body = await meResponse.json();
  expect(meResponse.status(), `/me: ${meResponse.status()} ${JSON.stringify(body)}`).toBe(200);
  expect(body.role, `/me role: ${JSON.stringify(body)}`).toBe("platform-admin");
  expect(body.team).toBe("platform");

  // `CollapsibleSection` (Sidebar.tsx:201-224) renders its label as a real <button>, so
  // the role selector is correct. The name regex is case-INSENSITIVE on purpose: the
  // label is uppercased by CSS (`uppercase` at Sidebar.tsx:216), and whether an
  // accessible name reflects `text-transform` is a browser/engine detail this assertion
  // must not depend on. The DOM text is "Admin"; the rendered text is "ADMIN"; both pass.
  await expect(page.getByRole("button", { name: /^admin$/i })).toBeVisible({ timeout: 20_000 });
});

test("create a user THROUGH THE UI → it persists across a reload (R0 / FR-8)", async ({
  page,
}) => {
  // R0 made POST /api/v1/admin/users ATOMIC: the Keycloak user, its realm role and the
  // user_team_assignments row now land together or not at all, with a compensating
  // kc_delete and a 502 on failure. suite-97 T-S97-007/008 prove that at the API. NOTHING
  // proved it through the screen — the other tests in this file seed their fixture with
  // api.post(), so the Create User modal itself had no journey at all, and CLAUDE.md DoD
  // rule 2's save→reload→assert existed for the EDIT surface but not the CREATE one.
  //
  // Emails use @example.com deliberately: UserCreate.email is EmailStr and email-validator
  // rejects .local as an RFC 6762 special-use TLD, so @agentshield.local answers 422. The
  // bootstrap can pin platform-admin@agentshield.local only because it calls
  // keycloak_client.create_user directly and never sees the model.
  const NAME = `uicreate-${Date.now()}`;
  let createdKcId = "";

  try {
    await page.goto(`${BASE_URL}/admin/access`);
    await page.getByRole("button", { name: /^Create User$/i }).click();

    // getByPlaceholder matches by SUBSTRING unless exact — "Smith" otherwise resolves to
    // three fields (jsmith / j.smith@company.com / Smith) and fails strict mode. Same
    // ambiguity class this triage has been clearing all day; it caught me too.
    await page.getByPlaceholder("jsmith", { exact: true }).fill(NAME);
    await page.getByPlaceholder("j.smith@company.com", { exact: true }).fill(`${NAME}@example.com`);
    await page.getByPlaceholder("Jane", { exact: true }).fill("UI");
    await page.getByPlaceholder("Smith", { exact: true }).fill("Created");
    await page.getByPlaceholder("••••••••", { exact: true }).fill("UiCreate2024!");

    // Team is required and its options come from the live cluster — pick a real one
    // rather than hardcoding, so this does not track one cluster's seed data.
    const teamSelect = page.locator("select").filter({ has: page.locator('option[value=""]') }).first();
    const teamValue = await teamSelect.locator("option").nth(1).getAttribute("value");
    expect(teamValue, "no team exists in this cluster to assign").toBeTruthy();
    await teamSelect.selectOption(teamValue!);

    const roleSelect = page.locator("select").filter({ has: page.locator('option[value="consumer"]') }).first();
    await roleSelect.selectOption("consumer");

    // Assert the write left the browser AND that the server answered 201 — not a toast,
    // not a closed modal. A 502 here is R0's compensating-rollback path surfacing.
    const created = page.waitForResponse(
      (r) =>
        /\/api\/v1\/admin\/users\/?$/.test(r.url()) && r.request().method() === "POST",
      { timeout: 20_000 },
    );
    await page.getByRole("button", { name: /^Create User$/i }).last().click();
    const resp = await created;
    expect(resp.status(), `create failed: ${await resp.text()}`).toBe(201);
    createdKcId = (await resp.json()).kc_id;

    // FULL RELOAD — the row must be rehydrated from the backend, not from the mutation
    // cache. This is the round-trip R0's atomicity claim actually rests on: if the
    // Keycloak user existed but the assignment row did not, the role cell would be empty.
    await page.reload();
    const row = page.getByRole("row", { name: new RegExp(NAME) });
    await row.waitFor({ timeout: 20_000 });
    await expect(row).toContainText("consumer");
  } finally {
    if (createdKcId) {
      await page.request
        .delete(`${BASE_URL}/api/v1/admin/users/${createdKcId}`)
        .catch(() => {});
    }
  }
});

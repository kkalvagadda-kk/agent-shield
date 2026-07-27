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
  await page.getByRole("button", { name: /^save$/i }).click();
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

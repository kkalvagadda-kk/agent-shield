import { chromium, request, type Browser, type FullConfig } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { stateFor, type GlobalRole } from "./lib/roles";

// ─────────────────────────────────────────────────────────────────────────────
// MULTI-ROLE AUTH. Authenticate once per global role and persist one
// storageState per role, so a spec can drive the product as somebody who is NOT
// a platform admin.
//
// WHY (RBAC R2, 2026-08-06). Until this change every one of the 47 Playwright
// specs ran as `platform-admin`, because global-setup logged in exactly one
// user. The bash layer has the same shape: suite-98's header records that of 61
// suites minting a token, every single one authenticates as platform-admin. So
// the moment R2 starts returning 403 to non-admins, the ENTIRE browser suite
// would stay green whether the role gates worked, were inverted, or 403'd
// everybody — every caller already is the role that passes every check. A guard
// that cannot fail is the defect this repo keeps rediscovering; see
// docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md, where a change
// that broke the app for every user passed the full suite because nothing drove
// the screen that broke.
//
// The personas are the SAME identities suite-98 uses (`e2e-consumer`,
// `e2e-contributor`, team `platform`, password from E2E_PERSONA_PASS), created
// through the REAL `POST /api/v1/admin/users` path rather than seeded by hand —
// a fixture that bypasses the creation path proves nothing about it.
//
// Provisioning FAILS LOUD. If a persona cannot be created or logged in, this
// throws instead of skipping: a role spec silently not running is
// indistinguishable from a role spec passing, and that is exactly how
// `docs/testing/manual-ui-e2e-test-plan.md` G-R0-9 stayed red for months.
// ─────────────────────────────────────────────────────────────────────────────

const AUTH_DIR = path.resolve("e2e", ".auth");

// The path per role comes from lib/roles.ts, which the specs also import. Defining it
// in both places would be two producers of one fact — the duplication that let a raw
// `fetch` copy of /admin/teams-summary sit unnoticed in the browser until it blanked
// the app. A rename here has to break the specs, not silently diverge from them.

interface Persona {
  role: GlobalRole;
  username: string;
  password: string;
  /** Global role to create the user with. Omitted for the pre-existing admin. */
  provisionAs?: string;
}

const PERSONA_PASS = process.env.E2E_PERSONA_PASS || "Persona2024!";

const PERSONAS: Persona[] = [
  {
    role: "platform-admin",
    username: process.env.STUDIO_E2E_USER || "platform-admin",
    password: process.env.STUDIO_E2E_PASSWORD || "PlatformAdmin2024",
  },
  { role: "contributor", username: "e2e-contributor", password: PERSONA_PASS, provisionAs: "contributor" },
  { role: "consumer", username: "e2e-consumer", password: PERSONA_PASS, provisionAs: "consumer" },
];

/**
 * Log a user in through the real Keycloak form and persist the session.
 *
 * A FRESH browser context per persona is mandatory, not tidiness: Keycloak keeps
 * an SSO cookie, so reusing a context would silently hand the second persona the
 * first one's session and every role assertion built on it would be inverted.
 */
async function loginAndSave(
  browser: Browser,
  baseURL: string,
  persona: Persona,
): Promise<void> {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  try {
    await page.goto(baseURL, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("#username", { timeout: 30_000 });
    await page.fill("#username", persona.username);
    await page.fill("#password", persona.password);
    await Promise.all([
      page.waitForURL((url) => url.href.startsWith(baseURL), { timeout: 30_000 }),
      page.click("#kc-login, button[type=submit], input[type=submit]"),
    ]);
    // Wait for a CONCRETE app signal, not `networkidle`. The sidebar polls
    // pending approvals every 30s (`refetchInterval`), so the network is never
    // idle for long and that wait could time out on a healthy app — which then
    // left a half-written state.json behind and failed every spec in the batch
    // with what looked like an auth regression. The build marker renders only
    // after keycloak-js has exchanged the token and React has mounted, which is
    // the thing actually being waited for.
    await page.waitForSelector('[data-testid="studio-build"]', { timeout: 30_000 });
    await context.storageState({ path: stateFor(persona.role) });
    // eslint-disable-next-line no-console
    console.log(`[global-setup] ${persona.role}: authenticated as ${persona.username}`);
  } finally {
    await context.close();
  }
}

/**
 * Direct-access-grant token for the admin, used to provision the personas.
 *
 * Against `${baseURL}/realms/...` — the gateway proxies /realms to Keycloak, which
 * is the same path artifact-grants.spec.ts already uses successfully. Reading
 * `keycloakUrl` out of /config.json would be more general and less reliable: that
 * value can be a cluster-internal URL the test host cannot resolve.
 */
async function adminToken(baseURL: string, persona: Persona): Promise<string> {
  const realm = process.env.E2E_KC_REALM || "agentshield";
  const clientId = process.env.E2E_KC_CLIENT || "agentshield-studio";
  const api = await request.newContext({ baseURL, ignoreHTTPSErrors: true });
  try {
    const res = await api.post(`/realms/${realm}/protocol/openid-connect/token`, {
      form: {
        grant_type: "password",
        client_id: clientId,
        username: persona.username,
        password: persona.password,
      },
    });
    if (!res.ok()) {
      throw new Error(`token grant for ${persona.username} -> ${res.status()} ${await res.text()}`);
    }
    return (await res.json()).access_token;
  } finally {
    await api.dispose();
  }
}

/**
 * Create the persona through the real admin API, or repair it if it drifted.
 *
 * Idempotent: 409 means it already exists, in which case the global role is
 * RE-PINNED. A persona whose role silently drifted inverts every assertion built
 * on it — the same reasoning as `e2e_ensure_persona` in scripts/e2e/lib/e2e-auth.sh,
 * which is where these identities come from.
 */
async function provision(baseURL: string, token: string, persona: Persona): Promise<void> {
  const api = await request.newContext({
    baseURL,
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  try {
    const create = await api.post("/api/v1/admin/users", {
      data: {
        username: persona.username,
        // @example.com, never @agentshield.local: UserCreate.email is an EmailStr
        // and email-validator rejects .local as an RFC 6762 special-use TLD (422).
        email: `${persona.username}@example.com`,
        first_name: "E2E",
        last_name: "Persona",
        temp_password: persona.password,
        team: "platform",
        role: persona.provisionAs,
      },
    });

    let kcId: string | undefined;
    if (create.status() === 201) {
      kcId = (await create.json()).kc_id;
    } else if (create.status() === 409) {
      const list = await api.get("/api/v1/admin/users");
      const match = (await list.json()).find((u: any) => u.username === persona.username);
      if (!match) throw new Error(`${persona.username} reported 409 but is not listed`);
      kcId = match.kc_id;
      if (match.role !== persona.provisionAs) {
        await api.patch(`/api/v1/admin/users/${kcId}`, {
          data: { role: persona.provisionAs, team: "platform" },
        });
      }
    } else {
      throw new Error(`create ${persona.username} -> ${create.status()} ${await create.text()}`);
    }

    // create_user sets requiredActions=["UPDATE_PASSWORD"]; Keycloak refuses to
    // authenticate such a user at all ("Account is not fully set up"), so the
    // browser login below would hang on a form it cannot satisfy.
    const reset = await api.post(`/api/v1/admin/users/${kcId}/reset-password`, {
      data: { new_password: persona.password, temporary: false },
    });
    if (!reset.ok()) {
      throw new Error(`reset-password ${persona.username} -> ${reset.status()}`);
    }
  } finally {
    await api.dispose();
  }
}

export default async function globalSetup(_config: FullConfig) {
  const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const resolverRules = process.env.PLAYWRIGHT_HOST_RESOLVER_RULES;
  const browser = await chromium.launch(
    resolverRules ? { args: [`--host-resolver-rules=${resolverRules}`] } : {},
  );

  try {
    const admin = PERSONAS[0];
    await loginAndSave(browser, baseURL, admin);
    // The default storageState in playwright.config points here. Every existing
    // spec keeps running as platform-admin with no change; only specs that opt in
    // via `test.use({ storageState })` see another role.
    fs.copyFileSync(stateFor(admin.role), path.join(AUTH_DIR, "state.json"));

    const token = await adminToken(baseURL, admin);
    for (const persona of PERSONAS.slice(1)) {
      await provision(baseURL, token, persona);
      await loginAndSave(browser, baseURL, persona);
    }
  } finally {
    await browser.close();
  }
}

// e2e/lib/api.ts — authenticated API contexts + fixtures seeded ahead of time.
//
// We seed slow-to-build fixtures (the deterministic tool + the eval dataset) via API and
// let the browser test consume them — the user-approved "create the data ahead of time"
// rule.
//
// THIS FILE USED TO SAY: "The registry-api has no global auth middleware in-cluster;
// identity comes from the X-User-Sub / X-User-Team headers (same pattern every existing
// spec uses)." That was true when it was written and is now false. R1 put `require_user`
// on ten routers, R2/R3 added role and artifact gates, and G-R3-6 (0.2.267) closed
// `POST /api/v1/tools/`. The first mutation to hit a closed router was
// `seedDeterministicTool`, which started failing with a 401 that surfaced as
// "lifecycle journey leg 1" rather than as an auth problem.
//
// So `ctx()` now mints a REAL Keycloak token by direct access grant, the same way
// global-setup.ts provisions its personas. The X-User-* headers stay: several handlers
// still read `X-User-Team` for team scoping, and dropping them would change fixture
// placement in ways unrelated to this fix. What changed is that identity is now
// ASSERTED with a signature instead of announced in a header.
//
// Why a token per identity and not one shared admin token: `userApi()` exists precisely
// so owner-scoped seeds carry the browser's own sub. Minting both keeps that property —
// a single admin token would silently make every "the user owns this" fixture wrong.
import { request as pwRequest, type APIRequestContext } from "@playwright/test";

export const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

// STALE — kept only because they are exported and the README references them. NEITHER of
// these subs has a `user_team_assignments` row on the current cluster: a realm recreation
// mints new subs, and nothing updated these literals. `ctx()` no longer reads them; it
// derives the sub from the token it mints. Do not add a new caller.
export const ADMIN_SUB = "047fad5f-f38c-430a-bfba-6e4d9009314b";
export const USER_SUB = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6";
export const TEAM = "platform";

// Keycloak usernames for the two subs above. Kept beside them so the pair cannot drift:
// a sub without its username is unusable now that a token is required.
const ADMIN_USER = process.env.STUDIO_E2E_USERNAME || "platform-admin";
const ADMIN_PASS = process.env.STUDIO_E2E_PASSWORD || "PlatformAdmin2024";
const USER_USER = process.env.STUDIO_E2E_USER_USERNAME || ADMIN_USER;
const USER_PASS = process.env.STUDIO_E2E_USER_PASSWORD || ADMIN_PASS;

const tokenCache = new Map<string, string>();

/** Direct access grant. Same realm/client/flow as global-setup.ts's adminToken(). */
async function tokenFor(username: string, password: string): Promise<string> {
  const cached = tokenCache.get(username);
  if (cached) return cached;
  const realm = process.env.E2E_KC_REALM || "agentshield";
  const clientId = process.env.E2E_KC_CLIENT || "agentshield-studio";
  const api = await pwRequest.newContext({ baseURL: API_BASE, ignoreHTTPSErrors: true });
  try {
    const res = await api.post(`/realms/${realm}/protocol/openid-connect/token`, {
      form: { grant_type: "password", client_id: clientId, username, password },
    });
    if (!res.ok()) {
      // Loud and specific. A silent fallback to header-only auth is what made the
      // original failure look like a broken journey instead of a missing credential.
      throw new Error(
        `e2e/lib/api.ts: token grant for ${username} -> ${res.status()} ${await res.text()}. ` +
        `Fixture seeding needs a real JWT since R1/G-R3-6; header identity is no longer accepted ` +
        `on mutating routes.`,
      );
    }
    const tok = (await res.json()).access_token as string;
    tokenCache.set(username, tok);
    return tok;
  } finally {
    await api.dispose();
  }
}

/** The `sub` claim out of a JWT. No verification — this is a test helper reading its own token. */
function subOf(token: string): string {
  const raw = token.split(".")[1] ?? "";
  const json = Buffer.from(raw.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
  return JSON.parse(json).sub as string;
}

async function ctx(team: string | undefined, username: string, password: string): Promise<APIRequestContext> {
  const token = await tokenFor(username, password);
  const extraHTTPHeaders: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    // Derived FROM THE TOKEN, never a constant. The two hardcoded subs this file used to
    // send (see ADMIN_SUB / USER_SUB below) have no `user_team_assignments` row on the
    // current cluster at all — they predate a realm recreation, which mints new subs for
    // every user. Under header-only identity that was invisible; the moment a real
    // credential arrived it would have meant the header and the signature naming two
    // different people, with handlers free to pick either.
    //
    // Deriving it here makes disagreement unrepresentable. `deployment-conversations` and
    // `conversations-sidebar` reached the same conclusion from the other direction and
    // call resolveSessionSub() at run time; this is the same rule applied where the token
    // is already in hand.
    "X-User-Sub": subOf(token),
  };
  if (team) extraHTTPHeaders["X-User-Team"] = team;
  return pwRequest.newContext({ baseURL: API_BASE, ignoreHTTPSErrors: true, extraHTTPHeaders });
}

/** Team-shared seed identity (tools/agents/datasets/versions). */
export const adminApi = () => ctx(TEAM, ADMIN_USER, ADMIN_PASS);
/** The browser's own identity — for owner-scoped seeds the UI must read back. */
export const userApi = () => ctx(TEAM, USER_USER, USER_PASS);

/** Unique, human-scannable name. Playwright specs may use Date.now(). */
export function uniqueName(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}`;
}

/**
 * Seed a DETERMINISTIC, high-risk HTTP tool the journey agent binds. It POSTs to the
 * in-cluster `/echo` endpoint (registry-api's httpbin replacement, suite-63) which reflects
 * the request — so the agent's tool output is predictable (stable assertions, no external
 * dependency). `risk_level:"high"` so the adversarial-pass gate actually bites at publish.
 * Returns the tool name (delete with `api.delete('/api/v1/tools/'+name)`).
 */
export async function seedDeterministicTool(api: APIRequestContext, name = uniqueName("journey-echo")): Promise<string> {
  const r = await api.post("/api/v1/tools/", {
    data: {
      name,
      // display_name == name so the picker tile's visible text is the unique name we
      // filter the tile by (the drawer renders the display_name).
      display_name: name,
      description: "Deterministic echo tool for the lifecycle journey suite — reflects its input.",
      type: "http",
      risk_level: "high",
      side_effecting: false,
      http_method: "POST",
      http_url: "http://agentshield-registry-api.agentshield-platform:8000/echo",
      http_headers: { "Content-Type": "application/json" },
      http_body_template: '{"q": "{{q}}"}',
      input_schema: { type: "object", properties: { q: { type: "string" } }, required: ["q"] },
    },
  });
  if (!r.ok()) throw new Error(`seedDeterministicTool ${r.status()}: ${await r.text()}`);
  return name;
}

/**
 * Seed a reactive eval dataset (validates BOTH an agent and a workflow). A reactive item
 * is `{kind:"reactive", input_message, expected_output}`. Returns the dataset id.
 */
export async function seedReactiveDataset(
  api: APIRequestContext,
  opts: { name?: string; items?: Array<{ input_message: string; expected_output: string }> } = {},
): Promise<string> {
  const name = opts.name ?? uniqueName("journey-dataset");
  const items = (opts.items ?? [{ input_message: "ping", expected_output: "pong" }]).map((i) => ({
    kind: "reactive",
    input_message: i.input_message,
    expected_output: i.expected_output,
  }));
  const r = await api.post("/api/v1/playground/datasets", { data: { name, mode: "reactive", items } });
  if (!r.ok()) throw new Error(`seedReactiveDataset ${r.status()}: ${await r.text()}`);
  return (await r.json()).id as string;
}

/**
 * Seed a conversation transcript for an agent so an owner-scoped History read has data even
 * without a warm pod. `sub` MUST be the browser's USER_SUB for the UI to see it.
 */
export async function seedConversation(
  api: APIRequestContext,
  agentName: string,
  opts: { threadId: string; sub?: string; deploymentId?: string; messages: Array<{ role: string; content: string }> },
): Promise<void> {
  const r = await api.post(`/api/v1/agents/${agentName}/memory`, {
    data: {
      thread_id: opts.threadId,
      session_id: opts.threadId,
      user_id: opts.sub ?? USER_SUB,
      deployment_id: opts.deploymentId,
      messages: opts.messages,
    },
  });
  if (!r.ok()) throw new Error(`seedConversation ${r.status()}: ${await r.text()}`);
}

/**
 * Auth headers for specs that build their own APIRequestContext.
 *
 * Several specs predate `ctx()` and hand-roll `extraHTTPHeaders: { "X-User-Sub": ... }`.
 * That stopped being identity when R1 put `require_user` on ten routers and G-R3-6 closed
 * the tool routes — those calls now 401, and the failure surfaces as whatever UI step
 * depended on the fixture rather than as an auth problem.
 *
 * Rather than teach each spec to mint its own token (ten copies of one rule, which is the
 * exact drift `lib/apiAuth.ts` and `lib/e2e-auth.sh` were both written to stop), they
 * spread this. `X-User-Sub` comes from the token so the header and the signature cannot
 * name two different people.
 *
 * `scripts/check-e2e-auth-hygiene.sh` fails the build on a mutating call to a gated route
 * in `studio/e2e` that has neither an Authorization header nor `captureAuthHeaders()`.
 */
export async function adminAuthHeaders(team: string = TEAM): Promise<Record<string, string>> {
  const token = await tokenFor(ADMIN_USER, ADMIN_PASS);
  return {
    Authorization: `Bearer ${token}`,
    "X-User-Sub": subOf(token),
    "X-User-Team": team,
  };
}


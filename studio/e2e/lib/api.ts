// e2e/lib/api.ts — header-auth API contexts + fixtures seeded ahead of time.
//
// The registry-api has no global auth middleware in-cluster; identity comes from the
// X-User-Sub / X-User-Team headers (same pattern every existing spec uses). We seed
// slow-to-build fixtures (the deterministic tool + the eval dataset) via API and let the
// browser test consume them — the user-approved "create the data ahead of time" rule.
import { request as pwRequest, type APIRequestContext } from "@playwright/test";

export const API_BASE = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:8080";

// The two real Keycloak subs (see e2e/lib/README.md). ADMIN seeds team-shared fixtures;
// USER is the sub the browser logs in as (owner-scoped read-backs must match it).
export const ADMIN_SUB = "047fad5f-f38c-430a-bfba-6e4d9009314b";
export const USER_SUB = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6";
export const TEAM = "platform";

async function ctx(sub: string, team?: string): Promise<APIRequestContext> {
  const extraHTTPHeaders: Record<string, string> = { "X-User-Sub": sub };
  if (team) extraHTTPHeaders["X-User-Team"] = team;
  return pwRequest.newContext({ baseURL: API_BASE, ignoreHTTPSErrors: true, extraHTTPHeaders });
}

/** Team-shared seed identity (tools/agents/datasets/versions). */
export const adminApi = () => ctx(ADMIN_SUB, TEAM);
/** The browser's own identity — for owner-scoped seeds the UI must read back. */
export const userApi = () => ctx(USER_SUB, TEAM);

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

// Demo / UX-preview mode. Flag-gated so the real app is completely untouched:
// when VITE_DEMO_MODE !== "true", nothing here runs.
//
// In demo mode we (a) bypass Keycloak with a mock user (see main.tsx),
// (b) swap the shared axios adapter for a benign mock so legacy pages don't
// crash without a backend, and (c) shim window.fetch for the two raw calls.
import type { AxiosAdapter } from "axios";
import type { KcUserInfo } from "../lib/keycloak";
import { MOCK_AGENTS } from "./mockData";
import { buildScheduleFleet, deriveFireState } from "./scheduleFixtures";
import type { ScheduleListItem } from "../api/registryApi";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const DEMO: boolean = (import.meta as any).env?.VITE_DEMO_MODE === "true";

export const MOCK_USER = {
  sub: "demo-user-001",
  preferred_username: "demo",
  email: "demo@agentshield.local",
  given_name: "Demo",
  family_name: "User",
  realm_access: { roles: ["platform-admin"] },
} as unknown as KcUserInfo;

// ── Mutable schedule store ──────────────────────────────────────────────────
// The Schedules page is driven through the REAL api-client method (`listSchedules`),
// not local imports, so the same component code runs against this store now and
// against `GET /api/v1/schedules` in Phase B. That means arm/disarm/toggle/delete
// have to actually mutate something — a read-only fixture would demo a dead page.
// State is module-level and resets on reload; that is called out on the preview
// index so it doesn't read as a persistence bug.
let scheduleStore: ScheduleListItem[] = buildScheduleFleet();

/** Test seam — Vitest resets the store between cases. */
export function resetScheduleStore() {
  scheduleStore = buildScheduleFleet();
}

// Mirrors the Phase-B server refusal: arming is gated on the artifact actually
// being live, and the refusal text is the operator-readable one the dispatch door
// already produces (`agent_endpoints.py:152-155`). Returning a green arm here would
// hide the single most important interaction in the design.
function refuseArm(row: ScheduleListItem): string | null {
  if (row.artifact_kind === "agent" && row.artifact_status !== "active")
    return `agent '${row.artifact_name}' is ${row.artifact_status} — it cannot be armed`;
  if (row.artifact_kind === "workflow" && row.artifact_status !== "published")
    return `workflow '${row.artifact_name}' is not published — publish it before arming a trigger`;
  // The sandbox-only case: no running production deployment. Verbatim from the
  // dispatch door, which is deliberately the same owner for "may this fire?".
  if (row.artifact_name === "weekly-digest")
    return `agent 'weekly-digest' has no running production deployment — it is deployed to sandbox. Schedule and webhook triggers dispatch to production; deploy the agent to production (or publish it) before arming a trigger.`;
  return null;
}

function applyTriggerPatch(triggerId: string, body: Record<string, unknown>): ScheduleListItem {
  const idx = scheduleStore.findIndex((r) => r.trigger_id === triggerId);
  if (idx === -1) throw new Error(`trigger ${triggerId} not found`);
  const row = scheduleStore[idx];

  let next = { ...row };
  if (typeof body.enabled === "boolean") next.enabled = body.enabled;
  if (body.armed === true) {
    const refusal = refuseArm(row);
    if (refusal) throw new Error(refusal);
    next.armed_at = new Date().toISOString();
    next.armed_by = "demo";
    next.disarmed_at = null;
    next.disarm_reason = null;
  }
  if (body.armed === false) {
    next.armed_at = null;
    next.disarmed_at = new Date().toISOString();
    next.disarm_reason = "disarmed by operator";
  }
  next = deriveFireState(next);
  scheduleStore = [...scheduleStore.slice(0, idx), next, ...scheduleStore.slice(idx + 1)];
  return next;
}

// Minimal mock router for the *legacy* pages, so navigating the app without a
// backend shows empty states instead of crashing. The new preview pages use
// local mock data directly and never hit this.
export const mockAdapter: AxiosAdapter = async (config) => {
  const url = config.url ?? "";
  const method = (config.method ?? "get").toLowerCase();
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const res = (data: unknown): any => ({
    data,
    status: 200,
    statusText: "OK",
    headers: {},
    config,
  });

  // ── Schedules (R5) ──
  if (/\/schedules(\?|$)/.test(url)) {
    return res(scheduleStore.filter((r) => r.trigger_type === "schedule"));
  }

  // Trigger writes, both artifact kinds. The page routes to the artifact-scoped
  // endpoint by `artifact_kind`, so both shapes land here and resolve to the same
  // store — which is exactly the Phase-B arrangement (one writer per kind, one
  // read model over both).
  const triggerWrite = url.match(/\/(?:agents|workflows)\/[^/]+\/triggers\/([^/?]+)/);
  if (triggerWrite) {
    const triggerId = triggerWrite[1];
    if (method === "patch") {
      const body = (config.data ? JSON.parse(config.data as string) : {}) as Record<string, unknown>;
      return res(applyTriggerPatch(triggerId, body));
    }
    if (method === "delete") {
      scheduleStore = scheduleStore.filter((r) => r.trigger_id !== triggerId);
      return { data: undefined, status: 204, statusText: "No Content", headers: {}, config };
    }
  }

  // Agents list (landing page + sidebar) — show a few so the app feels alive.
  if (/\/agents\/?(\?|$)/.test(url)) {
    return res({ items: MOCK_AGENTS, total: MOCK_AGENTS.length });
  }
  // Everything else: a valid empty paginated shape.
  return res({ items: [], total: 0 });
};

// Catch the raw fetch() calls that bypass the axios instance.
export function installFetchShim() {
  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof input === "string" ? input : input.toString();
    if (u.includes("/admin/teams-summary")) {
      return new Response("[]", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (u.includes("/config.json")) {
      return new Response(
        JSON.stringify({ keycloakUrl: "", keycloakRealm: "demo", keycloakClientId: "demo" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return orig(input as RequestInfo, init);
  };
}

// Demo / UX-preview mode. Flag-gated so the real app is completely untouched:
// when VITE_DEMO_MODE !== "true", nothing here runs.
//
// In demo mode we (a) bypass Keycloak with a mock user (see main.tsx),
// (b) swap the shared axios adapter for a benign mock so legacy pages don't
// crash without a backend, and (c) shim window.fetch for the two raw calls.
import type { AxiosAdapter } from "axios";
import type { KcUserInfo } from "../lib/keycloak";
import { MOCK_AGENTS } from "./mockData";

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

// Minimal mock router for the *legacy* pages, so navigating the app without a
// backend shows empty states instead of crashing. The new preview pages use
// local mock data directly and never hit this.
export const mockAdapter: AxiosAdapter = async (config) => {
  const url = config.url ?? "";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const res = (data: unknown): any => ({
    data,
    status: 200,
    statusText: "OK",
    headers: {},
    config,
  });
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

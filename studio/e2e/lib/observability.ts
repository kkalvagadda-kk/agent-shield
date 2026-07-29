// e2e/lib/observability.ts — dashboard/traces before/after snapshot.
//
// Only Postgres run-driven numbers (Total Runs, traces-list count) are safe to assert as
// before/after deltas. Cost / spend-by-model / judge-score are Langfuse-derived and often
// zero in the test cluster — assert those panels RENDER, never a numeric delta.
import { expect, type Page, type APIRequestContext } from "@playwright/test";

export interface DashSnapshot {
  totalRuns: number;
  traceCount: number;
}

/** Read the dashboard + traces counts for an environment (via API, same endpoints the UI uses). */
export async function snapshotDashboard(
  api: APIRequestContext, environment: "production" | "sandbox",
): Promise<DashSnapshot> {
  const dash = await api.get(`/api/v1/observability/dashboard?environment=${environment}`);
  const traces = await api.get(`/api/v1/observability/traces?environment=${environment}`);
  const d = dash.ok() ? await dash.json() : {};
  const t = traces.ok() ? await traces.json() : [];
  return {
    totalRuns: Number(d.total_runs ?? d.totalRuns ?? 0),
    traceCount: Array.isArray(t) ? t.length : Number(t?.total ?? 0),
  };
}

/** Assert the dashboard page renders its panels + the sandbox/production scoping fires. */
export async function assertDashboardRenders(
  page: Page, environment: "production" | "sandbox",
): Promise<void> {
  const q = page.waitForResponse(
    (r) => r.request().method() === "GET" &&
      r.url().includes("/api/v1/observability/dashboard") && r.url().includes(`environment=${environment}`),
  );
  await page.goto(`/observability/dashboard/${environment}`);
  await q;
  const label = environment === "production" ? "Production" : "Sandbox";
  await expect(page.getByRole("heading", { name: new RegExp(`${label} Dashboard`, "i") })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Total Runs/i).first()).toBeVisible();
}

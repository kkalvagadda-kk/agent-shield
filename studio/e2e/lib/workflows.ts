// e2e/lib/workflows.ts — create a multi-agent workflow.
//
// NOTE (from the codebase): the workflow canvas has NO pure-UI way to add a second member
// and draw an edge — every reference spec (workflow-builder.spec.ts) SEEDS members + edges
// via API, then asserts the builder renders/persists them. So this helper seeds via API and
// returns the workflow id; the spec drives the builder Save + a reload round-trip on top.
import { expect, type Page, type APIRequestContext } from "@playwright/test";
import { TEAM } from "./api";

export interface WorkflowSeed {
  name: string;
  memberAgents: string[]; // ≥2 existing agent names, in order
  orchestration?: string; // sequential | conditional | supervisor | handoff
}

/** Seed a workflow + its members + a sequential edge (member[0] → member[1]) via API. */
export async function seedWorkflow(api: APIRequestContext, seed: WorkflowSeed): Promise<string> {
  const wf = await api.post("/api/v1/workflows", {
    data: { name: seed.name, team: TEAM, orchestration: seed.orchestration ?? "sequential", agent_class: "user_delegated" },
  });
  if (!wf.ok()) throw new Error(`seedWorkflow ${wf.status()}: ${await wf.text()}`);
  const wid = (await wf.json()).id as string;

  const agentIds: string[] = [];
  for (let i = 0; i < seed.memberAgents.length; i++) {
    // Members reference the agent by UUID, not name — resolve it.
    const a = await api.get(`/api/v1/agents/${seed.memberAgents[i]}`);
    if (!a.ok()) throw new Error(`resolve agent ${seed.memberAgents[i]} ${a.status()}`);
    const agentId = (await a.json()).id as string;
    agentIds.push(agentId);
    const m = await api.post(`/api/v1/workflows/${wid}/members`, {
      data: { agent_id: agentId, position: i },
    });
    if (!m.ok()) throw new Error(`addMember ${m.status()}: ${await m.text()}`);
  }
  // Sequential edge between the first two members (workflow_edges keys on agent ids).
  if (agentIds.length >= 2) {
    const e = await api.post(`/api/v1/workflows/${wid}/edges`, {
      data: { source_agent_id: agentIds[0], target_agent_id: agentIds[1] },
    });
    if (!e.ok()) throw new Error(`addEdge ${e.status()}: ${await e.text()}`);
  }
  return wid;
}

/** Assert the builder renders the seeded workflow's members + an edge, and survives reload. */
export async function assertWorkflowPersists(page: Page, wid: string): Promise<void> {
  await page.goto(`/workflows/${wid}/builder`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".react-flow__node")).toHaveCount(2, { timeout: 15_000 });
  // The edge is an SVG <g>; Playwright reports it "hidden" even when present, so assert
  // it EXISTS in the DOM (toHaveCount) rather than toBeVisible.
  await expect(page.locator(".react-flow__edge")).toHaveCount(1, { timeout: 15_000 });
  // Reload → still there (persistence round-trip, DoD #2).
  await page.reload();
  await expect(page.locator(".react-flow__node")).toHaveCount(2, { timeout: 15_000 });
  await expect(page.locator(".react-flow__edge")).toHaveCount(1, { timeout: 15_000 });
}

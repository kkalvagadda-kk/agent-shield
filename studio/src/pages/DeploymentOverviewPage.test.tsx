import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import DeploymentOverviewPage from "./DeploymentOverviewPage";

vi.mock("../api/registryApi", () => ({
  getAgent: vi.fn(),
  getDeployments: vi.fn(),
  listTriggers: vi.fn(),
  listVersions: vi.fn(),
  getDeploymentStats: vi.fn(),
  listDeploymentRuns: vi.fn(),
  // used by statically-imported child tabs (not exercised on the overview tab)
  listMemory: vi.fn(),
  deleteMemoryThread: vi.fn(),
  clearAgentMemory: vi.fn(),
  listAgentEvents: vi.fn(),
  rotateToken: vi.fn(),
  enableTrigger: vi.fn(),
  disableTrigger: vi.fn(),
}));

import {
  getAgent,
  getDeployments,
  listTriggers,
  listVersions,
  getDeploymentStats,
  listDeploymentRuns,
} from "../api/registryApi";

const NOW = new Date().toISOString();
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function renderPage(depId = "dep-1") {
  return renderWithProviders(
    <Routes>
      <Route path="/agents/:name/d/:depId" element={<DeploymentOverviewPage />} />
    </Routes>,
    { routerEntries: [`/agents/my-agent/d/${depId}`] }
  );
}

describe("DeploymentOverviewPage", () => {
  beforeEach(() => {
    mock(getAgent).mockResolvedValue({ name: "my-agent", execution_shape: "reactive" });
    mock(listTriggers).mockResolvedValue([]);
    mock(listVersions).mockResolvedValue([{ id: "v1", version_number: 1 }]);
    mock(getDeployments).mockResolvedValue([
      {
        id: "dep-1",
        name: "my-agent-ab12",
        status: "running",
        environment: "sandbox",
        version_id: "v1",
        k8s_namespace: "agents-default",
        deployed_at: NOW,
      },
    ]);
    mock(getDeploymentStats).mockResolvedValue({
      run_count: 3,
      p50_latency_ms: 100,
      p95_latency_ms: 200,
      error_rate: 0,
      total_cost_usd: 0,
    });
    mock(listDeploymentRuns).mockResolvedValue([]);
  });

  it("renders the deployment name as the primary title", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("my-agent-ab12");
  });

  it("shows the agent name as secondary metadata and the reactive overview", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/agent:/)).toBeInTheDocument());
    // OverviewReactive renders the API Endpoint card.
    expect(await screen.findByText("API Endpoint")).toBeInTheDocument();
    // Sandbox deployment → endpoint is pinned to THIS deployment id, not the
    // agent-scoped path (which re-resolves the most-recent running pod).
    expect(
      screen.getByText("/api/v1/agents/my-agent/deployments/dep-1/chat", { exact: false })
    ).toBeInTheDocument();
  });

  it("scopes stats to the deployment (playground context)", async () => {
    renderPage();
    await screen.findByText("API Endpoint");
    expect(getDeploymentStats).toHaveBeenCalledWith("dep-1", "playground");
  });

  it("shows a not-found message when the deployment id does not match", async () => {
    renderPage("does-not-exist");
    expect(await screen.findByText(/Deployment not found/i)).toBeInTheDocument();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import CatalogDetailPage from "./CatalogDetailPage";

vi.mock("../api/catalogApi", () => ({
  getCatalogDetail: vi.fn(),
  getCatalogStats: vi.fn(),
  listCatalogRuns: vi.fn(),
  deployVersion: vi.fn(),
  updateDeployment: vi.fn(),
}));

vi.mock("../api/registryApi", () => ({
  listTriggers: vi.fn(),
  // read by the shared Overview* components the page now mounts
  getDeploymentStats: vi.fn(),
  listDeploymentRuns: vi.fn(),
  listAgentEvents: vi.fn(),
  rotateToken: vi.fn(),
  enableTrigger: vi.fn(),
  disableTrigger: vi.fn(),
  getAgentHealth: vi.fn(),
}));

import { getCatalogDetail, getCatalogStats, listCatalogRuns } from "../api/catalogApi";
import {
  listTriggers,
  getDeploymentStats,
  listDeploymentRuns,
  listAgentEvents,
  getAgentHealth,
} from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const NOW = new Date().toISOString();

function detail(opts: { executionShape?: string; type?: string; status?: string } = {}) {
  return {
    artifact: {
      id: "art-1",
      name: "my-agent",
      type: opts.type ?? "agent",
      team: "default",
      description: "a published agent",
    },
    versions: [
      {
        id: "v1",
        version_label: "v1.0.0",
        promoted_at: NOW,
        config_snapshot: { execution_shape: opts.executionShape ?? "reactive" },
      },
    ],
    deployments: [
      {
        id: "prod-dep-1",
        artifact_id: "art-1",
        version_id: "v1",
        version_label: "v1.0.0",
        status: opts.status ?? "running",
        namespace: "production-my-agent-ab12ef34",
        deployed_at: NOW,
        suspended_at: null,
        updated_at: NOW,
      },
    ],
    granted_teams: [],
    member_topology: [],
  };
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/catalog/:artifactId" element={<CatalogDetailPage />} />
    </Routes>,
    { routerEntries: ["/catalog/art-1"] }
  );
}

describe("CatalogDetailPage — overview", () => {
  beforeEach(() => {
    mock(getCatalogDetail).mockResolvedValue(detail());
    mock(getCatalogStats).mockResolvedValue({
      run_count: 5,
      error_rate: 0,
      p50_latency_ms: 120,
      total_cost_usd: 0.01,
    });
    mock(listCatalogRuns).mockResolvedValue([]);
    mock(listTriggers).mockResolvedValue([]);
    mock(getDeploymentStats).mockResolvedValue({
      run_count: 5,
      p50_latency_ms: 120,
      p95_latency_ms: 300,
      error_rate: 0,
      total_cost_usd: 0.01,
    });
    mock(listDeploymentRuns).mockResolvedValue([]);
    mock(listAgentEvents).mockResolvedValue([]);
    mock(getAgentHealth).mockResolvedValue({ status: "healthy" });
  });

  it("mounts the SHARED overview dispatcher, not a hand-written fork", async () => {
    renderPage();
    const overview = await screen.findByTestId("overview-for-shape");
    expect(overview).toHaveAttribute("data-shape", "reactive");
  });

  it("passes context=production explicitly (catalog artifacts are production by definition)", async () => {
    renderPage();
    await screen.findByTestId("overview-for-shape");
    await waitFor(() =>
      expect(getDeploymentStats).toHaveBeenCalledWith("prod-dep-1", "production")
    );
  });

  // THE REGRESSION TEST for the drift this slice fixes. The inline fork dispatched on
  // `config_snapshot.execution_shape` only — a 2-value column — so a webhook-driven
  // published artifact had NO branch to reach and silently rendered nothing
  // shape-specific. It failed SAFE, which is why it survived.
  // NB: the overview mounts as soon as the catalog detail resolves, then re-renders
  // when the triggers query settles. Assert on the ATTRIBUTE settling, not on the
  // element existing — `findByTestId` returns on the first render and would read the
  // pre-triggers shape.
  it("renders the event-driven overview for a webhook-triggered artifact", async () => {
    mock(listTriggers).mockResolvedValue([
      { id: "trg-1", trigger_type: "webhook", enabled: true, filter_conditions: null },
    ]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("overview-for-shape")).toHaveAttribute(
        "data-shape",
        "event_driven"
      )
    );
  });

  it("renders the scheduled overview for a schedule-triggered artifact", async () => {
    mock(listTriggers).mockResolvedValue([
      { id: "trg-1", trigger_type: "schedule", enabled: true, cron_expression: "0 9 * * *" },
    ]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("overview-for-shape")).toHaveAttribute(
        "data-shape",
        "scheduled"
      )
    );
  });

  it("renders the durable overview for a durable artifact", async () => {
    mock(getCatalogDetail).mockResolvedValue(detail({ executionShape: "durable" }));
    renderPage();
    const overview = await screen.findByTestId("overview-for-shape");
    expect(overview).toHaveAttribute("data-shape", "durable");
  });

  it("does not query agent triggers for a workflow artifact (no such route)", async () => {
    mock(getCatalogDetail).mockResolvedValue(detail({ type: "workflow" }));
    renderPage();
    await screen.findByTestId("overview-for-shape");
    expect(listTriggers).not.toHaveBeenCalled();
  });

  it("keeps the catalog-only concerns the shared components do not own", async () => {
    renderPage();
    await screen.findByTestId("overview-for-shape");
    // Artifact-scoped metrics row, agent info, and the production chat entry point are
    // catalog concerns — the shared overview does not own them.
    expect(screen.getByText("Agent Info")).toBeInTheDocument();
    expect(screen.getByText("Production Chat")).toBeInTheDocument();
    expect(screen.getByText(/production-my-agent-ab12ef34/)).toBeInTheDocument();
  });

  it("offers Production Chat only for the reactive shape", async () => {
    mock(listTriggers).mockResolvedValue([
      { id: "trg-1", trigger_type: "webhook", enabled: true, filter_conditions: null },
    ]);
    renderPage();
    // Wait for the shape to settle to event_driven before asserting the chat card is
    // gone — otherwise this passes/fails on the pre-triggers reactive render.
    await waitFor(() =>
      expect(screen.getByTestId("overview-for-shape")).toHaveAttribute(
        "data-shape",
        "event_driven"
      )
    );
    expect(screen.queryByText("Production Chat")).not.toBeInTheDocument();
  });

  it("does not mount an overview when there is no running deployment", async () => {
    mock(getCatalogDetail).mockResolvedValue(detail({ status: "pending" }));
    renderPage();
    expect(await screen.findByText("Agent Info")).toBeInTheDocument();
    expect(screen.queryByTestId("overview-for-shape")).not.toBeInTheDocument();
    // …and it must not fabricate a deployment-scoped read without a deployment.
    expect(getDeploymentStats).not.toHaveBeenCalled();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import WorkflowDeploymentOverviewPage from "./WorkflowDeploymentOverviewPage";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ id: "wf-1", depId: "dep-1" }),
  };
});

vi.mock("../api/registryApi", () => ({
  getCompositeWorkflow: vi.fn(),
  listWorkflowDeployments: vi.fn(),
  listWorkflowVersions: vi.fn(),
  getWorkflowDeploymentStats: vi.fn(),
  listWorkflowDeploymentRuns: vi.fn(),
  updateWorkflowDeployment: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  getCompositeWorkflow,
  listWorkflowDeployments,
  listWorkflowVersions,
  getWorkflowDeploymentStats,
  listWorkflowDeploymentRuns,
} from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const WF = {
  id: "wf-1",
  name: "my-workflow",
  team: "default",
  description: null,
  execution_shape: "durable" as const,
  orchestration: "sequential" as const,
  memory_enabled: false,
  status: "draft" as const,
  publish_status: "private",
  member_count: 2,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  created_by: "dev",
  members: [],
  edges: [],
};

const DEP = {
  id: "dep-1",
  workflow_id: "wf-1",
  version_id: "v-1",
  name: "my-workflow-ab12",
  environment: "sandbox",
  status: "running",
  replicas: 1,
  ttl_hours: null,
  deployed_at: "2026-01-01T00:00:00Z",
  suspended_at: null,
  terminated_at: null,
  error_message: null,
  deployed_by: "dev",
  previous_version_id: null,
};

const VERSION = {
  id: "v-1",
  workflow_id: "wf-1",
  version_number: 1,
  members: [{ agent_id: "a1", agent_name: "bot-a" }],
  edges: [],
  orchestration: "sequential",
  execution_shape: "durable",
  config: {},
  eval_passed: false,
  created_at: "2026-01-01T00:00:00Z",
  created_by: "dev",
};

describe("WorkflowDeploymentOverviewPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(getCompositeWorkflow).mockResolvedValue(WF);
    mock(listWorkflowDeployments).mockResolvedValue([DEP]);
    mock(listWorkflowVersions).mockResolvedValue([VERSION]);
    mock(getWorkflowDeploymentStats).mockResolvedValue({
      run_count: 5,
      error_rate: 0.2,
      p50_latency_ms: 120,
      p95_latency_ms: 400,
      total_cost_usd: 0.01,
    });
    mock(listWorkflowDeploymentRuns).mockResolvedValue([]);
  });

  it("renders the workflow deployment name and status", async () => {
    renderWithProviders(<WorkflowDeploymentOverviewPage />);
    await waitFor(() => {
      expect(screen.getByText("my-workflow-ab12")).toBeInTheDocument();
    });
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("shows Suspend and Terminate actions for a running deployment", async () => {
    renderWithProviders(<WorkflowDeploymentOverviewPage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Suspend/ })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /Terminate/ })).toBeInTheDocument();
  });

  it("shows stats cards", async () => {
    renderWithProviders(<WorkflowDeploymentOverviewPage />);
    await waitFor(() => {
      expect(screen.getByText("5")).toBeInTheDocument();
    });
    expect(screen.getByText("120ms")).toBeInTheDocument();
  });

  it("shows 'not found' when deployment missing", async () => {
    mock(listWorkflowDeployments).mockResolvedValue([]);
    renderWithProviders(<WorkflowDeploymentOverviewPage />);
    await waitFor(() => {
      expect(screen.getByText("Workflow deployment not found.")).toBeInTheDocument();
    });
  });

  it("renders the member topology mini-graph when version has members", async () => {
    renderWithProviders(<WorkflowDeploymentOverviewPage />);
    await waitFor(() => {
      expect(screen.getByText("Member Topology")).toBeInTheDocument();
    });
    expect(screen.getByText("Open Builder")).toBeInTheDocument();
  });
});

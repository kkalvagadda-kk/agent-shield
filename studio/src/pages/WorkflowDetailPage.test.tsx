import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import WorkflowDetailPage from "./WorkflowDetailPage";

vi.mock("../api/registryApi", () => ({
  getCompositeWorkflow: vi.fn(),
  listWorkflowDeployments: vi.fn(),
  listWorkflowVersions: vi.fn(),
  deleteWorkflowVersion: vi.fn(),
  deployWorkflow: vi.fn(),
  updateWorkflowDeployment: vi.fn(),
}));

import { getCompositeWorkflow, listWorkflowDeployments, listWorkflowVersions, deleteWorkflowVersion } from "../api/registryApi";

const NOW = new Date().toISOString();
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/workflows/:id" element={<WorkflowDetailPage />} />
    </Routes>,
    { routerEntries: ["/workflows/wf-123"] }
  );
}

describe("WorkflowDetailPage", () => {
  beforeEach(() => {
    mock(getCompositeWorkflow).mockResolvedValue({
      id: "wf-123",
      name: "my-workflow",
      team: "default",
      orchestration: "sequential",
      execution_shape: "reactive",
      memory_enabled: false,
      status: "draft",
      publish_status: "private",
      member_count: 2,
      created_at: NOW,
      updated_at: NOW,
      created_by: "dev",
      description: null,
      members: [],
      edges: [],
    });
    mock(listWorkflowDeployments).mockResolvedValue([
      { id: "dep-1", name: "wf-dep-ab12", workflow_id: "wf-123", version_id: "v1", environment: "sandbox", status: "running", replicas: 1, ttl_hours: null, deployed_at: NOW, suspended_at: null, terminated_at: null, error_message: null, deployed_by: null, previous_version_id: null },
    ]);
    mock(listWorkflowVersions).mockResolvedValue([
      { id: "v1", workflow_id: "wf-123", version_number: 1, members: [{}, {}], edges: [], orchestration: "sequential", execution_shape: "reactive", config: {}, eval_passed: false, created_at: NOW, created_by: null },
    ]);
    mock(deleteWorkflowVersion).mockResolvedValue({ deleted_version_id: "v1", terminated_deployments: 0 });
  });

  it("renders workflow name and orchestration badge", async () => {
    renderPage();
    expect(await screen.findByText("my-workflow")).toBeInTheDocument();
    expect(screen.getByText("sequential")).toBeInTheDocument();
  });

  it("shows deployments tab with sandbox deployments", async () => {
    renderPage();
    const link = await screen.findByRole("link", { name: /wf-dep-ab12/ });
    expect(link).toHaveAttribute("href", "/workflows/wf-123/d/dep-1");
  });

  it("shows Open Builder link", async () => {
    renderPage();
    const link = await screen.findByRole("link", { name: /open builder/i });
    expect(link).toHaveAttribute("href", "/workflows/wf-123/builder");
  });

  it("switches to versions tab", async () => {
    renderPage();
    await screen.findByText("my-workflow");
    await userEvent.click(screen.getByRole("button", { name: "versions" }));
    expect(await screen.findByText("v1")).toBeInTheDocument();
  });

  it("switches to settings tab", async () => {
    renderPage();
    await screen.findByText("my-workflow");
    await userEvent.click(screen.getByRole("button", { name: "settings" }));
    expect(await screen.findByText("Workflow Configuration")).toBeInTheDocument();
  });

  it("calls deleteWorkflowVersion on confirm", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await screen.findByText("my-workflow");
    await userEvent.click(screen.getByRole("button", { name: "versions" }));
    await screen.findByText("v1");
    await userEvent.click(screen.getByTitle("Delete version"));
    await waitFor(() => expect(deleteWorkflowVersion).toHaveBeenCalledWith("wf-123", "v1"));
    vi.restoreAllMocks();
  });
});

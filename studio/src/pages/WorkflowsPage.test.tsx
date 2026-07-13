import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import WorkflowsPage from "./WorkflowsPage";
import type { CompositeWorkflow } from "../api/registryApi";

vi.mock("../api/registryApi", () => ({
  listCompositeWorkflows: vi.fn(),
}));
import { listCompositeWorkflows } from "../api/registryApi";

const NOW = new Date().toISOString();

const WORKFLOWS: CompositeWorkflow[] = [
  {
    id: "wf1",
    name: "billing-pipeline",
    team: "finance",
    description: "Handles billing",
    execution_shape: "durable",
    orchestration: "sequential",
    agent_class: "user_delegated",
    memory_enabled: false,
    status: "published",
    publish_status: "approved",
    member_count: 3,
    created_at: NOW,
    updated_at: NOW,
    created_by: null,
  },
  {
    id: "wf2",
    name: "support-flow",
    team: "ops",
    description: null,
    execution_shape: "reactive",
    orchestration: "supervisor",
    agent_class: "daemon",
    memory_enabled: true,
    status: "draft",
    publish_status: "pending",
    member_count: 2,
    created_at: NOW,
    updated_at: NOW,
    created_by: null,
  },
];

describe("WorkflowsPage", () => {
  beforeEach(() => {
    (listCompositeWorkflows as ReturnType<typeof vi.fn>).mockResolvedValue(WORKFLOWS);
  });

  it("renders workflow rows with name, team, orchestration, status, member count", async () => {
    renderWithProviders(<WorkflowsPage />);

    expect(await screen.findByText("billing-pipeline")).toBeInTheDocument();
    expect(screen.getByText("finance")).toBeInTheDocument();
    expect(screen.getByText("sequential")).toBeInTheDocument();
    expect(screen.getByText("published")).toBeInTheDocument();
    // member_count column
    expect(screen.getByText("3")).toBeInTheDocument();

    expect(screen.getByText("support-flow")).toBeInTheDocument();
    expect(screen.getByText("supervisor")).toBeInTheDocument();
    expect(screen.getByText("draft")).toBeInTheDocument();
  });

  it("shows the page header and New Workflow button", async () => {
    renderWithProviders(<WorkflowsPage />);
    expect(screen.getByRole("heading", { name: /workflows/i })).toBeInTheDocument();
    // Button exists (may be multiple if we count the header button)
    await screen.findByText("billing-pipeline");
    const buttons = screen.getAllByRole("button", { name: /new workflow/i });
    expect(buttons.length).toBeGreaterThanOrEqual(1);
  });

  it("shows empty-state copy when there are no workflows", async () => {
    (listCompositeWorkflows as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    renderWithProviders(<WorkflowsPage />);
    await waitFor(() =>
      expect(screen.getByText(/no workflows yet/i)).toBeInTheDocument()
    );
    // Empty-state also renders a New Workflow button
    expect(screen.getAllByRole("button", { name: /new workflow/i }).length).toBeGreaterThanOrEqual(1);
  });

  it("shows description below workflow name when present", async () => {
    renderWithProviders(<WorkflowsPage />);
    expect(await screen.findByText("Handles billing")).toBeInTheDocument();
  });

  it("shows error message when the API call fails", async () => {
    (listCompositeWorkflows as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("network error"));
    renderWithProviders(<WorkflowsPage />);
    await waitFor(() =>
      expect(screen.getByText(/failed to load workflows/i)).toBeInTheDocument()
    );
  });
});

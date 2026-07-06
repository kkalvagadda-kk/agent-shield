import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import WorkflowPropertiesPanel from "./WorkflowPropertiesPanel";
import { useWorkflowStore } from "../stores/workflowStore";

vi.mock("../api/registryApi", () => ({
  getAgent: vi.fn(),
  updateAgent: vi.fn(),
}));
import { getAgent } from "../api/registryApi";

const AGENT = {
  id: "a1",
  name: "alpha-agent",
  team: "platform",
  description: "does things",
  execution_shape: "reactive",
  metadata: { instructions: "be helpful", model: "claude-sonnet-4-6" },
};

function seed(state: Partial<ReturnType<typeof useWorkflowStore.getState>>) {
  useWorkflowStore.setState({
    nodes: [],
    edges: [],
    selectedNodeId: null,
    selectedEdgeId: null,
    ...state,
  } as never);
}

describe("WorkflowPropertiesPanel", () => {
  beforeEach(() => {
    (getAgent as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(AGENT);
  });

  it("shows the empty prompt when nothing is selected", () => {
    seed({});
    renderWithProviders(<WorkflowPropertiesPanel />);
    expect(screen.getByText(/Select an agent or edge/i)).toBeInTheDocument();
  });

  it("shows a read-only summary + Edit link for an existing (non-inline) agent node", async () => {
    seed({
      nodes: [{ id: "a1", type: "workflow_member", position: { x: 0, y: 0 }, data: { agent_id: "a1", agent_name: "alpha-agent" } }] as never,
      selectedNodeId: "a1",
    });
    renderWithProviders(<WorkflowPropertiesPanel />);
    // Read-only guidance + link, and NO editable instructions textarea.
    expect(await screen.findByText(/existing shared agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Edit in Agents/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/System prompt for this agent/i)).not.toBeInTheDocument();
  });

  it("shows editable config + deploy warning for an inline agent node", async () => {
    seed({
      nodes: [{ id: "a1", type: "workflow_member", position: { x: 0, y: 0 }, data: { agent_id: "a1", agent_name: "alpha-agent", is_inline: true } }] as never,
      selectedNodeId: "a1",
    });
    renderWithProviders(<WorkflowPropertiesPanel />);
    expect(await screen.findByText(/isn.t deployed/i)).toBeInTheDocument();
    // hydrated editable instructions
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/System prompt for this agent/i)).toHaveValue("be helpful")
    );
    expect(screen.getByText(/Save agent config/i)).toBeInTheDocument();
  });

  it("shows the edge condition input when an edge is selected", () => {
    seed({
      edges: [{ id: "e1", source: "a1", target: "a2", data: { condition: "approved" } }] as never,
      selectedEdgeId: "e1",
    });
    renderWithProviders(<WorkflowPropertiesPanel />);
    expect(screen.getByDisplayValue("approved")).toBeInTheDocument();
    expect(screen.getByText(/default \(fallback\) path/i)).toBeInTheDocument();
  });
});

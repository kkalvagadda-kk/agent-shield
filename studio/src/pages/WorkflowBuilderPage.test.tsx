import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import WorkflowBuilderPage from "./WorkflowBuilderPage";
import { useWorkflowStore } from "../stores/workflowStore";

vi.mock("../api/registryApi", () => ({
  getCompositeWorkflow: vi.fn(),
  createCompositeWorkflow: vi.fn(),
  updateCompositeWorkflow: vi.fn(),
  addWorkflowMember: vi.fn(),
  removeWorkflowMember: vi.fn(),
  addWorkflowEdge: vi.fn(),
  listWorkflowEdges: vi.fn(),
  removeWorkflowEdge: vi.fn(),
  triggerWorkflowRun: vi.fn(),
  getWorkflowRunTree: vi.fn(),
  publishWorkflow: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));
// Isolate the builder's save logic from the data-driven modal/panels.
vi.mock("../components/AddAgentModal", () => ({
  default: ({ onAdd }: { onAdd: (a: { id: string; name: string; team: string }) => void }) => (
    <button onClick={() => onAdd({ id: "a1", name: "agent-one", team: "finance" })}>mock-add-agent</button>
  ),
}));
vi.mock("../components/WorkflowPropertiesPanel", () => ({ default: () => null }));
vi.mock("../components/workflow/WorkflowTriggersPanel", () => ({ default: () => null }));
vi.mock("../components/playground/TraceDrawer", () => ({ default: () => null }));

import { toast } from "sonner";
import {
  createCompositeWorkflow, addWorkflowMember, addWorkflowEdge, getCompositeWorkflow, listWorkflowEdges,
} from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

async function seedOneAgent() {
  await userEvent.click(screen.getByRole("button", { name: /Add Agent/i }));
  await userEvent.click(await screen.findByRole("button", { name: /mock-add-agent/i }));
}

describe("WorkflowBuilderPage — Save modal class (agent_class, R1/D1)", () => {
  beforeEach(() => {
    useWorkflowStore.getState().resetCompositeCanvas();
    mock(createCompositeWorkflow).mockResolvedValue({ id: "wf-1", name: "flow", team: "finance", warnings: [] });
    mock(addWorkflowMember).mockResolvedValue({});
    mock(addWorkflowEdge).mockResolvedValue({});
    mock(listWorkflowEdges).mockResolvedValue([]);
    mock(getCompositeWorkflow).mockResolvedValue({ id: "wf-1", name: "flow", team: "finance", warnings: [] });
  });

  it("selecting Daemon in the Save modal posts agent_class=daemon on create", async () => {
    renderWithProviders(<WorkflowBuilderPage />);
    await seedOneAgent();
    await userEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await userEvent.type(await screen.findByPlaceholderText("my-workflow"), "flow");
    await userEvent.selectOptions(screen.getByLabelText(/Authority/i), "daemon");
    await userEvent.click(screen.getByRole("button", { name: /Save Workflow/i }));
    await waitFor(() => expect(createCompositeWorkflow).toHaveBeenCalled());
    expect(mock(createCompositeWorkflow).mock.calls[0][0]).toEqual(
      expect.objectContaining({ agent_class: "daemon", team: "finance", name: "flow" })
    );
    expect(addWorkflowMember).toHaveBeenCalled();
  });

  it("surfaces save-time approval-gate warnings as a toast (S2)", async () => {
    mock(getCompositeWorkflow).mockResolvedValue({
      id: "wf-1", name: "flow", team: "finance",
      warnings: ["Reactive workflow has high-risk-tool member(s): agent-one. Set shape=durable to allow approvals."],
    });
    renderWithProviders(<WorkflowBuilderPage />);
    await seedOneAgent();
    await userEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await userEvent.type(await screen.findByPlaceholderText("my-workflow"), "flow");
    await userEvent.click(screen.getByRole("button", { name: /Save Workflow/i }));
    await waitFor(() =>
      expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining("Set shape=durable"))
    );
  });
});

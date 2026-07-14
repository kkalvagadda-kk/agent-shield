import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
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
  listPendingApprovals: vi.fn(),
  decideApproval: vi.fn(),
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
  triggerWorkflowRun, getWorkflowRunTree, listPendingApprovals, decideApproval,
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

// ---------------------------------------------------------------------------
// Inline sandbox/playground approval in the run panel (WS-6 operate parity).
// A parked member in a self-service run is approved right here; production
// approvals are NOT fetched (they route to the authority-gated console).
// ---------------------------------------------------------------------------
describe("WorkflowBuilderPage — inline self-service approval", () => {
  const parkedTree = {
    parent: {
      id: "p1", agent_name: "flow", status: "awaiting_approval", context: "sandbox",
      thread_id: "th-parent", trigger_type: null, run_by: null, team: "finance",
      input: null, output: null, error_message: null, latency_ms: null, cost_usd: null,
      started_at: "2026-07-13T00:00:00Z", completed_at: null, langfuse_trace_id: null,
    },
    children: [
      {
        id: "c1", agent_name: "wf-payout", status: "awaiting_approval", context: "sandbox",
        thread_id: "th-child", trigger_type: null, run_by: null, team: "finance",
        input: null, output: null, error_message: null, latency_ms: null, cost_usd: null,
        started_at: "2026-07-13T00:00:00Z", completed_at: null, langfuse_trace_id: null,
      },
    ],
  };
  const sandboxApproval = {
    id: "ap-1", agent_name: "wf-payout", team: "finance", step_name: "payout",
    tool_name: "refund_action", risk_level: "high", tool_args: { amount: 50 },
    thread_context_snippet: null, sla_remaining_seconds: 600, created_at: "2026-07-13T00:00:00Z",
    context: "sandbox", version: 3, thread_id: "th-child",
  };

  // The test router defines no `:id` route, so compositeWorkflowId can only be
  // established through the real save flow (mirrors the suite above). Once
  // saved, the "Run Workflow" toggle appears and the run panel is reachable.
  async function saveAndStartRun() {
    renderWithProviders(<WorkflowBuilderPage />);
    await seedOneAgent();
    await userEvent.click(screen.getByRole("button", { name: /^Save$/i }));
    await userEvent.type(await screen.findByPlaceholderText("my-workflow"), "flow");
    await userEvent.click(screen.getByRole("button", { name: /Save Workflow/i }));
    await waitFor(() => expect(createCompositeWorkflow).toHaveBeenCalled());
    await userEvent.click(await screen.findByRole("button", { name: /Run Workflow/i }));
    await userEvent.type(screen.getByPlaceholderText(/message to pass/i), "pay out");
    await userEvent.click(screen.getByRole("button", { name: /Start Run/i }));
  }

  beforeEach(() => {
    vi.clearAllMocks();
    useWorkflowStore.getState().resetCompositeCanvas();
    mock(createCompositeWorkflow).mockResolvedValue({ id: "wf-1", name: "flow", team: "finance", warnings: [] });
    mock(addWorkflowMember).mockResolvedValue({});
    mock(addWorkflowEdge).mockResolvedValue({});
    mock(getCompositeWorkflow).mockResolvedValue({ id: "wf-1", name: "flow", team: "finance", members: [], warnings: [] });
    mock(listWorkflowEdges).mockResolvedValue([]);
    mock(triggerWorkflowRun).mockResolvedValue({ run_id: "run-1" });
    mock(getWorkflowRunTree).mockResolvedValue(parkedTree);
    mock(listPendingApprovals).mockImplementation((_team?: string, ctx?: string) =>
      Promise.resolve(ctx === "sandbox" ? [sandboxApproval] : []),
    );
    mock(decideApproval).mockResolvedValue(undefined);
  });

  // The run panel polls on a real 3s interval; these tests use real timers and
  // wait past one tick (RTL findBy + fake timers deadlock, so we avoid them).
  it("renders the inline card for a parked member and approves via the console decide endpoint", async () => {
    await saveAndStartRun();

    // First poll tick (~3s) fetches the tree + self-service approvals.
    const card = await screen.findByTestId("workflow-inline-approval", undefined, { timeout: 6000 });
    expect(card).toBeInTheDocument();
    // Only self-service contexts are fetched — production is never listed.
    expect(listPendingApprovals).toHaveBeenCalledWith(undefined, "sandbox");
    expect(listPendingApprovals).toHaveBeenCalledWith(undefined, "playground");
    expect(listPendingApprovals).not.toHaveBeenCalledWith(undefined, "production");

    await userEvent.click(within(card).getByRole("button", { name: /^Approve$/i }));

    // Console decide (PATCH /approvals/{id}) carries the optimistic-lock version;
    // that path triggers _resume_and_advance on the backend.
    await waitFor(() => expect(decideApproval).toHaveBeenCalledWith("ap-1", "approved", 3));
  }, 15000);

  it("does not fetch or render an inline card for a production-context run", async () => {
    mock(getWorkflowRunTree).mockResolvedValue({
      ...parkedTree,
      parent: { ...parkedTree.parent, context: "production" },
      children: [{ ...parkedTree.children[0], context: "production" }],
    });

    await saveAndStartRun();

    // Wait past one poll tick, proven by the tree fetch firing.
    await waitFor(() => expect(getWorkflowRunTree).toHaveBeenCalled(), { timeout: 6000 });
    expect(screen.queryByTestId("workflow-inline-approval")).not.toBeInTheDocument();
    // A production run must not hit the self-service approvals endpoint at all.
    expect(listPendingApprovals).not.toHaveBeenCalled();
  }, 15000);
});

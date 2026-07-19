import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import WorkflowMemoryTab from "./WorkflowMemoryTab";

// G2 reproduce + save→reload guard: the workflow deployment Memory tab was empty
// because it read the per-agent GET /agents/{workflow_name}/memory (list_recent) —
// workflow member rows carry agent_name=member / user_id=NULL, so that read matched
// nothing. The fix reads the workflow-scoped endpoint. This FAILS pre-fix (the
// component file does not exist yet) and passes once WorkflowMemoryTab lands.
vi.mock("../../api/registryApi", () => ({
  listWorkflowMemory: vi.fn(),
  listMemory: vi.fn(),
}));

import { listWorkflowMemory, listMemory } from "../../api/registryApi";
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

describe("WorkflowMemoryTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(listWorkflowMemory).mockResolvedValue([
      { agent_name: "summarizer", thread_id: "wf-t1", role: "assistant",
        content: "Here is the Q3 summary.", message_kind: "agent_output",
        scope: "workflow_run", message_index: 0, created_at: new Date().toISOString() },
    ]);
  });

  it("lists the workflow's memory entries via the workflow endpoint (NOT the per-agent one)", async () => {
    renderWithProviders(<WorkflowMemoryTab workflowId="wf-1" deploymentId="dep-1" />);

    // The reloaded entry renders (save→reload→assert: it comes back from the backend read).
    expect(await screen.findByText("Here is the Q3 summary.")).toBeInTheDocument();
    await waitFor(() =>
      expect(listWorkflowMemory).toHaveBeenCalledWith("wf-1", expect.objectContaining({ limit: 100 }))
    );
    // Must NOT fall back to the per-agent Memory read (the bug that left the tab empty).
    expect(listMemory).not.toHaveBeenCalled();
  });
});

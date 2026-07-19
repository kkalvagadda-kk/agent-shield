import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import WorkflowChatPage from "./WorkflowChatPage";

// Two workflow-chat behaviors:
//  G1  — rehydrate a past session on ?session (was: opened on the empty composer).
//  HITL — after the resumed member RE-PARKS on a 2nd approval, the inline panel
//         re-surfaces the 2nd gate (was: run "completed" with the 2nd gate orphaned).
vi.mock("../api/registryApi", () => ({
  getCompositeWorkflow: vi.fn(),
  getWorkflowRunTree: vi.fn(),
  workflowRunStreamUrl: vi.fn(() => "/stream"),
  listWorkflowMemory: vi.fn(),
  listPendingApprovals: vi.fn(),
  decideSandboxApproval: vi.fn(),
}));
vi.mock("../lib/keycloak", () => ({
  getKeycloak: () => ({ authenticated: true, token: "tok", updateToken: vi.fn() }),
}));

import {
  getCompositeWorkflow,
  listWorkflowMemory,
  getWorkflowRunTree,
  listPendingApprovals,
  decideSandboxApproval,
} from "../api/registryApi";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
}
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
Element.prototype.scrollIntoView = vi.fn();

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function renderChat(url: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/workflows/:id/chat" element={<WorkflowChatPage />} />
    </Routes>,
    { routerEntries: [url] }
  );
}

describe("WorkflowChatPage — POC-5 past-session rehydrate (G1)", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getCompositeWorkflow).mockResolvedValue({
      id: "wf-1", name: "research-summarize", team: "platform", members: [],
    });
    mock(listWorkflowMemory).mockResolvedValue([]);
  });

  it("rehydrates prior member turns when opened with ?session=<tid>", async () => {
    mock(listWorkflowMemory).mockResolvedValue([
      { agent_name: "researcher", thread_id: "thread-42", role: "user",
        content: "summarize the Q3 report", message_kind: "user", scope: "workflow_run", message_index: 0 },
      { agent_name: "summarizer", thread_id: "thread-42", role: "assistant",
        content: "Here is the summary.", message_kind: "agent_output", scope: "workflow_run", message_index: 1 },
    ]);

    renderChat("/workflows/wf-1/chat?session=thread-42");

    await waitFor(() =>
      expect(listWorkflowMemory).toHaveBeenCalledWith(
        "wf-1",
        expect.objectContaining({ thread_id: "thread-42" })
      )
    );
    expect(await screen.findByText("summarize the Q3 report")).toBeInTheDocument();
    expect(screen.getByText("Here is the summary.")).toBeInTheDocument();
    expect(screen.queryByText(/Send a message to run this workflow\./i)).not.toBeInTheDocument();
  });
});

describe("WorkflowChatPage — HITL 2nd-gate re-surface (regression)", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getCompositeWorkflow).mockResolvedValue({
      id: "wf-1", name: "research-summarize", team: "platform", members: [],
    });
    mock(listWorkflowMemory).mockResolvedValue([]);
  });
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it("re-surfaces the 2nd inline approval gate after the resumed member re-parks", async () => {
    // Live stream: park on gate 1, then done (sets parkedRunIdRef=run-1).
    const frames =
      `data: ${JSON.stringify({ type: "approval_requested", approval_id: "ap-1", tool: "web_search", args: { query: "firstquery" }, risk: "high", reasoning: "gate 1", author: "researcher" })}\n\n` +
      `data: ${JSON.stringify({ type: "done", run_id: "run-1" })}\n\n`;
    const enc = new TextEncoder().encode(frames);
    let sent = false;
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: async () =>
            sent ? { done: true, value: undefined } : ((sent = true), { done: false, value: enc }),
        }),
      },
    }) as unknown as typeof fetch;

    // After deciding gate 1, the resumed member RE-PARKS: the tree is awaiting_approval
    // with a parked child, and a 2nd pending approval exists on that child's thread.
    mock(decideSandboxApproval).mockResolvedValue(undefined);
    mock(getWorkflowRunTree).mockResolvedValue({
      parent: { status: "awaiting_approval" },
      children: [{ agent_name: "researcher", status: "awaiting_approval", thread_id: "th-1" }],
    });
    mock(listPendingApprovals).mockResolvedValue([
      { id: "ap-2", agent_name: "researcher", team: "platform", step_name: null,
        tool_name: "web_search", risk_level: "high", tool_args: { query: "secondquery" },
        thread_context_snippet: "gate 2", sla_remaining_seconds: 100,
        created_at: "2026-07-18T00:00:00Z", context: "playground", version: 1, thread_id: "th-1" },
    ]);

    renderChat("/workflows/wf-1/chat");

    // Start the run → the stream parks on gate 1.
    await userEvent.type(screen.getByTestId("workflow-chat-input"), "do a search");
    await userEvent.click(screen.getByTestId("workflow-chat-send"));

    // Gate 1 renders in the inline panel; approve it.
    expect(await screen.findByText(/firstquery/, {}, { timeout: 8000 })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /approve/i }));

    // The resume poll observes the re-park and re-surfaces gate 2 (distinct args).
    await waitFor(
      () => expect(listPendingApprovals).toHaveBeenCalledWith(undefined, "playground"),
      { timeout: 12000 }
    );
    expect(await screen.findByText(/secondquery/, {}, { timeout: 8000 })).toBeInTheDocument();
    expect(screen.getByTestId("sandbox-approval-panel")).toBeInTheDocument();
  }, 25000);
});

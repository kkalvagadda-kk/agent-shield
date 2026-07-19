import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import AgentChatPage from "./AgentChatPage";

vi.mock("../api/registryApi", () => ({
  getAgent: vi.fn(),
  getDeployments: vi.fn(),
  startAgentChat: vi.fn(),
  startDeploymentChat: vi.fn(),
  getChatApprovalStatus: vi.fn(),
  getSessionApprovals: vi.fn(),
  decideSandboxApproval: vi.fn(),
  // POC-5: History dock (ConversationSidebar) + `?session` rehydrate.
  listMemory: vi.fn(),
  listConversations: vi.fn(),
  listMyConversations: vi.fn(),
}));
vi.mock("../lib/keycloak", () => ({
  getKeycloak: () => ({ authenticated: true, token: "tok", updateToken: vi.fn() }),
}));

import {
  getAgent,
  getDeployments,
  startDeploymentChat,
  getChatApprovalStatus,
  getSessionApprovals,
  decideSandboxApproval,
  listMemory,
  listConversations,
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
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) } as MessageEvent);
  }
}
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
  MockEventSource;
Element.prototype.scrollIntoView = vi.fn();

const DEP_URL = ["/agents/serper-agent-4/d/dep-1/chat"];

function renderChat() {
  return renderWithProviders(
    <Routes>
      <Route path="/agents/:name/d/:depId/chat" element={<AgentChatPage />} />
    </Routes>,
    { routerEntries: DEP_URL }
  );
}

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

describe("AgentChatPage — deployment HITL", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getAgent).mockResolvedValue({ name: "serper-agent-4", description: "searches the web" });
    mock(startDeploymentChat).mockResolvedValue({
      run_id: "run-1",
      stream_url: "/api/v1/agents/serper-agent-4/deployments/dep-1/chat/run-1/stream",
    });
    mock(getSessionApprovals).mockResolvedValue([
      { approval_id: "ap-1", run_id: "run-1", status: "pending", tool: "web_search", args: {}, risk: "high", reasoning: "why", requested_by: "platform-admin", requested_by_team: "platform", context: "sandbox", created_at: null, decided: false },
    ]);
    mock(getChatApprovalStatus).mockResolvedValue({ run_id: "run-1", approval_id: "ap-1", status: "pending", decided: false });
    mock(decideSandboxApproval).mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  async function sendAndInterrupt() {
    renderChat();
    // Let the deployments query resolve so the env-aware branch is correct.
    await waitFor(() => expect(getDeployments).toHaveBeenCalled());
    await Promise.resolve();
    await userEvent.type(screen.getByPlaceholderText(/message/i), "weather in Austin");
    await userEvent.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(startDeploymentChat).toHaveBeenCalled());
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    MockEventSource.instances[0].emit({
      type: "approval_requested",
      approval_id: "ap-1",
      tool: "web_search",
      risk: "high",
      args: { query: "weather in Austin" },
    });
  }

  describe("sandbox deployment → self-approve panel", () => {
    beforeEach(() => {
      mock(getDeployments).mockResolvedValue([
        { id: "dep-1", environment: "sandbox", status: "running" },
      ]);
    });

    it("opens the self-approve panel (not the console waiting banner)", async () => {
      await sendAndInterrupt();
      await waitFor(() =>
        expect(screen.getByTestId("sandbox-approval-panel")).toBeInTheDocument()
      );
      expect(screen.getByTestId("sandbox-approval-row")).toHaveTextContent("web_search");
      // Inline Approve/Deny exist here (sandbox self-approve); NO console banner.
      expect(screen.getByRole("button", { name: /^Approve$/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^Deny$/ })).toBeInTheDocument();
      expect(screen.queryByTestId("hitl-waiting-banner")).not.toBeInTheDocument();
    });

    it("Approve calls the decide endpoint and opens the resume stream", async () => {
      await sendAndInterrupt();
      await waitFor(() => expect(screen.getByTestId("sandbox-approval-panel")).toBeInTheDocument());
      await userEvent.click(screen.getByRole("button", { name: /^Approve$/ }));
      await waitFor(() => expect(decideSandboxApproval).toHaveBeenCalledWith("ap-1", "approved"));
      await waitFor(() =>
        expect(MockEventSource.instances.some((es) => es.url.includes("resume-stream"))).toBe(true)
      );
    });

    // T006 — single-agent DOUBLE-approval (verify-first). The workflow regression
    // dropped a member's 2nd gate on resume; the single-agent resume path uses the
    // STREAM (resume-stream re-interrupts on approval_requested, AgentChatPage.tsx:234),
    // so it was never broken. This pins that: after gate 1 the resumed turn trips a
    // second high-risk tool → the 2nd gate re-surfaces in the inline panel (not dropped).
    it("re-surfaces a 2nd gate when the resume stream re-interrupts (double-approval)", async () => {
      await sendAndInterrupt();
      await waitFor(() => expect(screen.getByTestId("sandbox-approval-panel")).toBeInTheDocument());
      expect(screen.getByTestId("sandbox-approval-row")).toHaveTextContent("web_search");

      await userEvent.click(screen.getByRole("button", { name: /^Approve$/ }));
      await waitFor(() => expect(decideSandboxApproval).toHaveBeenCalledWith("ap-1", "approved"));
      const resume = await waitFor(() => {
        const es = MockEventSource.instances.find((e) => e.url.includes("resume-stream"));
        expect(es).toBeTruthy();
        return es!;
      });

      // The resumed turn parks on a SECOND, distinct tool; the session's pending
      // approval is now ap-2 / wire_transfer.
      mock(getSessionApprovals).mockResolvedValue([
        { approval_id: "ap-2", run_id: "run-1", status: "pending", tool: "wire_transfer",
          args: { amount: 500 }, risk: "high", reasoning: "why2", requested_by: "platform-admin",
          requested_by_team: "platform", context: "sandbox", created_at: null, decided: false },
      ]);
      resume.emit({ type: "approval_requested", approval_id: "ap-2", tool: "wire_transfer", risk: "high", args: { amount: 500 } });

      await waitFor(() =>
        expect(screen.getByTestId("sandbox-approval-row")).toHaveTextContent("wire_transfer")
      );
      expect(screen.getByRole("button", { name: /^Approve$/ })).toBeInTheDocument();
    });
  });

  describe("production deployment → console waiting banner", () => {
    beforeEach(() => {
      mock(getDeployments).mockResolvedValue([
        { id: "dep-1", environment: "production", status: "running" },
      ]);
    });

    it("shows the waiting banner and no inline approve/deny", async () => {
      await sendAndInterrupt();
      await waitFor(() =>
        expect(screen.getByTestId("hitl-waiting-banner")).toBeInTheDocument()
      );
      expect(screen.getByTestId("hitl-tool-name")).toHaveTextContent("web_search");
      expect(screen.getByText(/open approval console/i)).toBeInTheDocument();
      expect(screen.queryByTestId("sandbox-approval-panel")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Approve$/ })).not.toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// POC-5 — docked History: `?session` deep-link rehydrate, row select, and New.
// Uses the playground route (no depId) so isSandbox is true and getDeployments
// is never fetched — keeping these focused on the History / session behavior.
// ---------------------------------------------------------------------------
function renderPlaygroundChat(entry: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/agents/:name/chat" element={<AgentChatPage />} />
    </Routes>,
    { routerEntries: [entry] }
  );
}

const memRow = (role: "user" | "assistant", content: string, idx: number) => ({
  id: `m-${idx}`,
  agent_name: "serper-agent-4",
  thread_id: "thread-42",
  role,
  content,
  message_index: idx,
  session_id: "thread-42",
  user_id: "u-a",
  created_at: new Date().toISOString(),
});

const convoRow = (thread_id: string, title: string) => ({
  thread_id,
  session_id: thread_id,
  agent_name: "serper-agent-4",
  title,
  message_count: 2,
  last_activity: new Date().toISOString(),
  deployment_id: null,
  environment: "sandbox" as const,
});

describe("AgentChatPage — POC-5 History & session seed", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getAgent).mockResolvedValue({ name: "serper-agent-4", description: "searches the web" });
    mock(listMemory).mockResolvedValue([]);
    mock(listConversations).mockResolvedValue([]);
  });

  it("rehydrates prior turns from /memory when opened with ?session=<tid>", async () => {
    mock(listMemory).mockResolvedValue([
      memRow("user", "what is the capital of France", 0),
      memRow("assistant", "The capital of France is Paris.", 1),
    ]);

    renderPlaygroundChat("/agents/serper-agent-4/chat?session=thread-42");

    await waitFor(() =>
      expect(listMemory).toHaveBeenCalledWith(
        "serper-agent-4",
        expect.objectContaining({ thread_id: "thread-42" })
      )
    );
    expect(await screen.findByText("what is the capital of France")).toBeInTheDocument();
    expect(screen.getByText("The capital of France is Paris.")).toBeInTheDocument();
  });

  it("clicking a History row seeds that thread via listMemory(thread_id)", async () => {
    mock(listConversations).mockResolvedValue([convoRow("thread-99", "Earlier chat")]);
    mock(listMemory).mockResolvedValue([memRow("user", "resumed hello", 0)]);

    renderPlaygroundChat("/agents/serper-agent-4/chat");
    // No ?session → nothing seeded on mount.
    await waitFor(() => expect(getAgent).toHaveBeenCalled());
    expect(listMemory).not.toHaveBeenCalled();

    // History dock is open by default now — select a row directly.
    await userEvent.click(await screen.findByText("Earlier chat"));

    await waitFor(() =>
      expect(listMemory).toHaveBeenCalledWith(
        "serper-agent-4",
        expect.objectContaining({ thread_id: "thread-99" })
      )
    );
    expect(await screen.findByText("resumed hello")).toBeInTheDocument();
  });

  it("New conversation clears the transcript and starts a fresh session", async () => {
    mock(listMemory).mockResolvedValue([
      memRow("user", "seeded question", 0),
      memRow("assistant", "seeded answer", 1),
    ]);
    mock(listConversations).mockResolvedValue([convoRow("thread-42", "Seeded chat")]);

    renderPlaygroundChat("/agents/serper-agent-4/chat?session=thread-42");
    // Seeded transcript renders first.
    expect(await screen.findByText("seeded question")).toBeInTheDocument();

    // History dock is open by default now.
    await userEvent.click(await screen.findByRole("button", { name: /new conversation/i }));

    await waitFor(() =>
      expect(screen.queryByText("seeded question")).not.toBeInTheDocument()
    );
    // Empty state returns after the reset.
    expect(screen.getByText(/start a conversation/i)).toBeInTheDocument();
  });
});

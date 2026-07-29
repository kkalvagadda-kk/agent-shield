import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import ChatPane from "./ChatPane";

vi.mock("../../api/playgroundApi", () => ({
  startPlaygroundRun: vi.fn(),
  getRunTrace: vi.fn(),
  submitRunFeedback: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { startPlaygroundRun, getRunTrace } from "../../api/playgroundApi";

// Mock EventSource so ChatPane can create it without jsdom errors. Capture the
// last instance so a test can drive its onmessage with SSE frames.
let lastEventSource: MockEventSource | null = null;
class MockEventSource {
  static CLOSED = 2;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor() {
    lastEventSource = this;
  }
}
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;

// Drive an SSE frame into the captured stream, wrapped in act() so React flushes.
function pushFrame(obj: Record<string, unknown>) {
  act(() => {
    lastEventSource!.onmessage!({ data: JSON.stringify(obj) } as MessageEvent);
  });
}

describe("ChatPane", () => {
  beforeEach(() => {
    (startPlaygroundRun as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: "run-123",
      stream_url: "/api/v1/playground/runs/run-123/stream",
    });
    (getRunTrace as ReturnType<typeof vi.fn>).mockResolvedValue({
      run_id: "run-123",
      trace_id: null,
      trace_url: null,
      status: "completed",
    });
  });

  it("shows 'No agent selected' when agentName is null", () => {
    renderWithProviders(
      <ChatPane
        agentName={null}
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    expect(screen.getByText(/no agent selected/i)).toBeInTheDocument();
    expect(screen.getByText(/pick an agent/i)).toBeInTheDocument();
  });

  it("shows the message input and send button when an agent is selected", () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    expect(screen.getByPlaceholderText(/message my-agent/i)).toBeInTheDocument();
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("shows empty state copy before any messages", () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    expect(
      screen.getByText(/send a message to start a playground run/i)
    ).toBeInTheDocument();
  });

  it("disables the send button when input is empty", () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
  });

  it("enables the send button when input has text", async () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    await userEvent.type(screen.getByPlaceholderText(/message my-agent/i), "hello");
    expect(screen.getByRole("button")).not.toBeDisabled();
  });

  it("adds a user message bubble after sending", async () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    await userEvent.type(screen.getByPlaceholderText(/message my-agent/i), "Hello agent");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect(screen.getByText("Hello agent")).toBeInTheDocument()
    );
  });

  // F-F (Issue 1): the playground was single-turn — startPlaygroundRun sent no
  // session_id, so the backend keyed thread_id=run_id per turn and the conversation
  // was lost on leaving the screen. ChatPane must forward the sessionId prop so all
  // turns of a chat share ONE reloadable backend thread. Fails against the pre-fix
  // ChatPane (no sessionId prop / call omits session_id).
  it("threads turns: forwards sessionId as session_id so the backend links the conversation (F-F)", async () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        sessionId="sess-abc"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    await userEvent.type(screen.getByPlaceholderText(/message my-agent/i), "my name is Ada");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect(startPlaygroundRun).toHaveBeenCalledWith({
        agent_name: "my-agent",
        input_message: "my name is Ada",
        session_id: "sess-abc",
      })
    );
  });

  // F-E (Issue 2): each LLM turn must render its own bubble, and reasoning must show
  // separately from the answer. Pre-fix ChatPane ignored message_start (both answers
  // merged into ONE "FIRSTSECOND" bubble) and had no reasoning handler.
  it("F-E: each LLM turn opens its own bubble; reasoning renders separately", async () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        sessionId="s1"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    await userEvent.type(screen.getByPlaceholderText(/message my-agent/i), "go");
    await userEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(lastEventSource).not.toBeNull());

    // A tool-calling shape: reasoning + first answer (turn 1) → new turn → second answer.
    pushFrame({ event: "message_start" });
    pushFrame({ event: "reasoning", content: "thinking hard" });
    pushFrame({ event: "text_delta", content: "FIRST" });
    pushFrame({ event: "message_start" });
    pushFrame({ event: "text_delta", content: "SECOND" });
    pushFrame({ event: "done" });

    // Two DISTINCT bubbles: getByText("FIRST") fails on a merged "FIRSTSECOND" blob.
    await waitFor(() => expect(screen.getByText("FIRST")).toBeInTheDocument());
    expect(screen.getByText("SECOND")).toBeInTheDocument();
    // Reasoning is its own block, not merged into the answer.
    expect(screen.getByTestId("reasoning-block")).toHaveTextContent("thinking hard");
  });

  it("calls startPlaygroundRun with the agent name and message", async () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    await userEvent.type(screen.getByPlaceholderText(/message my-agent/i), "Test message");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect(startPlaygroundRun).toHaveBeenCalledWith({
        agent_name: "my-agent",
        input_message: "Test message",
      })
    );
  });

  it("clears the input field after sending", async () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    const input = screen.getByPlaceholderText(/message my-agent/i);
    await userEvent.type(input, "Hello");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect((input as HTMLInputElement).value).toBe("")
    );
  });

  it("shows error toast when startPlaygroundRun fails", async () => {
    const sonner = await import("sonner");
    const toastError = (sonner.toast as unknown as { error: ReturnType<typeof vi.fn> }).error;
    (startPlaygroundRun as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Network error")
    );

    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        resumeStreamUrl={null}
        onApprovalRequested={vi.fn()}
        onResumeComplete={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    await userEvent.type(screen.getByPlaceholderText(/message my-agent/i), "Hi");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Network error")
    );
  });
});

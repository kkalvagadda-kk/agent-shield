import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
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

// Mock EventSource so ChatPane can create it without jsdom errors
class MockEventSource {
  static CLOSED = 2;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor() {}
}
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;

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
        onApprovalRequested={vi.fn()}
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
        onApprovalRequested={vi.fn()}
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
        onApprovalRequested={vi.fn()}
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
        onApprovalRequested={vi.fn()}
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
        onApprovalRequested={vi.fn()}
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
        onApprovalRequested={vi.fn()}
        onTraceEvent={vi.fn()}
      />
    );
    await userEvent.type(screen.getByPlaceholderText(/message my-agent/i), "Hello agent");
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect(screen.getByText("Hello agent")).toBeInTheDocument()
    );
  });

  it("calls startPlaygroundRun with the agent name and message", async () => {
    renderWithProviders(
      <ChatPane
        agentName="my-agent"
        onApprovalRequested={vi.fn()}
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
        onApprovalRequested={vi.fn()}
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
        onApprovalRequested={vi.fn()}
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

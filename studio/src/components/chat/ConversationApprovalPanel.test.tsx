import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConversationApprovalPanel from "./ConversationApprovalPanel";
import { SessionApproval } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  decideSandboxApproval: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { decideSandboxApproval } from "../../api/registryApi";

const mkApproval = (over: Partial<SessionApproval> = {}): SessionApproval => ({
  approval_id: "ap-1",
  run_id: "run-1",
  status: "pending",
  tool: "web_search",
  args: { query: "weather in Austin" },
  risk: "high",
  reasoning: "Looking up current Austin weather to answer the question.",
  requested_by: "platform-admin",
  requested_by_team: "platform",
  context: "sandbox",
  created_at: null,
  decided: false,
  ...over,
});

describe("ConversationApprovalPanel", () => {
  beforeEach(() => {
    (decideSandboxApproval as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  });
  afterEach(() => vi.clearAllMocks());

  it("renders a pending approval row with tool, risk, and args", () => {
    render(
      <ConversationApprovalPanel approvals={[mkApproval()]} onDecided={vi.fn()} onClose={vi.fn()} />
    );
    expect(screen.getByTestId("sandbox-approval-panel")).toBeInTheDocument();
    const row = screen.getByTestId("sandbox-approval-row");
    expect(row).toHaveTextContent("web_search");
    expect(row).toHaveTextContent(/high/i);
    expect(row).toHaveTextContent("weather in Austin");
    expect(screen.getByText(/Approvals \(1\)/)).toBeInTheDocument();
  });

  it("shows WHO (requester) and WHY (reasoning)", () => {
    render(
      <ConversationApprovalPanel approvals={[mkApproval()]} onDecided={vi.fn()} onClose={vi.fn()} />
    );
    // WHO
    expect(screen.getByText(/platform-admin/)).toBeInTheDocument();
    // WHY
    expect(screen.getByTestId("sandbox-approval-reasoning")).toHaveTextContent(
      /Looking up current Austin weather/i
    );
  });

  it("hides the WHY block when reasoning is empty", () => {
    render(
      <ConversationApprovalPanel
        approvals={[mkApproval({ reasoning: null })]}
        onDecided={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.queryByTestId("sandbox-approval-reasoning")).not.toBeInTheDocument();
  });

  it("Approve calls decideSandboxApproval('approved') and reports the run to resume", async () => {
    const onDecided = vi.fn();
    render(
      <ConversationApprovalPanel approvals={[mkApproval()]} onDecided={onDecided} onClose={vi.fn()} />
    );
    await userEvent.click(screen.getByRole("button", { name: /^Approve$/ }));
    await waitFor(() =>
      expect(decideSandboxApproval).toHaveBeenCalledWith("ap-1", "approved")
    );
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("run-1", "approved"));
  });

  it("Deny calls decideSandboxApproval('denied')", async () => {
    const onDecided = vi.fn();
    render(
      <ConversationApprovalPanel approvals={[mkApproval()]} onDecided={onDecided} onClose={vi.fn()} />
    );
    await userEvent.click(screen.getByRole("button", { name: /^Deny$/ }));
    await waitFor(() =>
      expect(decideSandboxApproval).toHaveBeenCalledWith("ap-1", "denied")
    );
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("run-1", "denied"));
  });

  it("shows the empty state when there are no pending approvals", () => {
    render(
      <ConversationApprovalPanel
        approvals={[mkApproval({ status: "approved", decided: true })]}
        onDecided={vi.fn()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText(/no pending approvals/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Approve$/ })).not.toBeInTheDocument();
  });
});

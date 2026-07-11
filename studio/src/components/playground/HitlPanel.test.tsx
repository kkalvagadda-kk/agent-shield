import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import HitlPanel, { type HitlRequest } from "./HitlPanel";

vi.mock("../../api/playgroundApi", () => ({
  decidePlaygroundApproval: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { decidePlaygroundApproval } from "../../api/playgroundApi";

const REQUEST: HitlRequest = {
  approval_id: "apv-123",
  tool_name: "send_email",
  risk_level: "high",
  args_redacted: { to: "redacted@example.com", subject: "Invoice" },
};

describe("HitlPanel", () => {
  beforeEach(() => {
    (decidePlaygroundApproval as ReturnType<typeof vi.fn>).mockResolvedValue({
      approval_id: "apv-123",
      status: "approved",
      thread_id: "thread-abc",
      agent_name: "test-agent",
      team: "platform",
    });
  });

  it("renders nothing when request is null", () => {
    const { container } = renderWithProviders(
      <HitlPanel request={null} onDecided={vi.fn()} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the tool name and risk level when request is provided", () => {
    renderWithProviders(<HitlPanel request={REQUEST} onDecided={vi.fn()} />);
    expect(screen.getByText(/approval required — send_email/i)).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
  });

  it("renders Approve and Deny buttons", () => {
    renderWithProviders(<HitlPanel request={REQUEST} onDecided={vi.fn()} />);
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /deny/i })).toBeInTheDocument();
  });

  it("renders the redacted args as JSON", () => {
    renderWithProviders(<HitlPanel request={REQUEST} onDecided={vi.fn()} />);
    expect(screen.getByText(/"to":/)).toBeInTheDocument();
  });

  it("calls decidePlaygroundApproval with 'approved' and fires onDecided", async () => {
    const onDecided = vi.fn();
    renderWithProviders(<HitlPanel request={REQUEST} onDecided={onDecided} />);

    await userEvent.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() =>
      expect(decidePlaygroundApproval).toHaveBeenCalledWith("apv-123", "approved")
    );
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("approved", "thread-abc"));
  });

  it("calls decidePlaygroundApproval with 'denied' and fires onDecided", async () => {
    (decidePlaygroundApproval as ReturnType<typeof vi.fn>).mockResolvedValue({
      approval_id: "apv-123",
      status: "denied",
      thread_id: "thread-abc",
      agent_name: "test-agent",
      team: "platform",
    });
    const onDecided = vi.fn();
    renderWithProviders(<HitlPanel request={REQUEST} onDecided={onDecided} />);

    await userEvent.click(screen.getByRole("button", { name: /deny/i }));

    await waitFor(() =>
      expect(decidePlaygroundApproval).toHaveBeenCalledWith("apv-123", "denied")
    );
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("denied", "thread-abc"));
  });

  it("disables buttons while decision is pending", async () => {
    // Make the API call never resolve so the pending state is observable
    (decidePlaygroundApproval as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));

    renderWithProviders(<HitlPanel request={REQUEST} onDecided={vi.fn()} />);
    // Get both buttons before clicking (by position — Approve first, Deny second)
    const [approveBtn, denyBtn] = screen.getAllByRole("button");
    await userEvent.click(approveBtn);

    // When deciding=true, buttons show a spinner (no text) and are disabled
    await waitFor(() => {
      const buttons = screen.getAllByRole("button");
      buttons.forEach((btn) => expect(btn).toBeDisabled());
    });
  });

  it("does not show args section when args_redacted is empty", () => {
    const req: HitlRequest = { ...REQUEST, args_redacted: {} };
    renderWithProviders(<HitlPanel request={req} onDecided={vi.fn()} />);
    // The pre element with JSON args should not appear
    expect(screen.queryByText(/"to":/)).not.toBeInTheDocument();
  });
});

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ApprovalCard, { type ApprovalCardData } from "./ApprovalCard";

const FULL: ApprovalCardData = {
  toolName: "send_email",
  riskLevel: "high",
  args: { to: "redacted@example.com", subject: "Invoice" },
  reasoning: "Emailing the invoice to the customer.",
  requestedBy: "platform-admin",
  requestedByTeam: "platform",
  agentName: "billing-agent",
  stepName: "tool:send_email",
  team: "platform",
  slaLabel: "12m",
  createdAt: "2026-07-13T10:00:00Z",
};

describe("ApprovalCard (M1 — the one approval body)", () => {
  it("renders the tool, risk, WHO, WHY, WHAT and inbox meta all in one place", () => {
    render(<ApprovalCard data={FULL} onApprove={vi.fn()} onDeny={vi.fn()} reasoningTestId="rz" />);
    // tool + risk
    expect(screen.getByText("send_email")).toBeInTheDocument();
    expect(screen.getByTestId("approval-card-risk")).toHaveTextContent("high");
    // agent + step context (inbox)
    expect(screen.getByText("billing-agent")).toBeInTheDocument();
    expect(screen.getByText(/Step: tool:send_email/)).toBeInTheDocument();
    // WHO
    expect(screen.getByText(/platform-admin/)).toBeInTheDocument();
    // WHY
    expect(screen.getByTestId("rz")).toHaveTextContent(/Emailing the invoice/);
    // WHAT
    expect(screen.getByText(/"to":/)).toBeInTheDocument();
    // meta
    expect(screen.getByText(/SLA: 12m/)).toBeInTheDocument();
  });

  it("hides WHY when reasoning is empty and WHAT when args are empty", () => {
    render(
      <ApprovalCard
        data={{ toolName: "noop", riskLevel: "low", args: {}, reasoning: null }}
        onApprove={vi.fn()}
        onDeny={vi.fn()}
        reasoningTestId="rz"
      />
    );
    expect(screen.queryByTestId("rz")).not.toBeInTheDocument();
    expect(screen.queryByText(/"to":/)).not.toBeInTheDocument();
  });

  it("renders the daemon principal_display when present (WS-2 T013)", () => {
    render(
      <ApprovalCard
        data={{
          toolName: "wire_transfer",
          riskLevel: "high",
          principalDisplay: "service:billing-agent on behalf of alice",
        }}
        onApprove={vi.fn()}
        onDeny={vi.fn()}
      />
    );
    expect(screen.getByTestId("approval-card-principal")).toHaveTextContent(
      /service:billing-agent on behalf of alice/
    );
  });

  it("hides the principal line when principal_display is null", () => {
    render(
      <ApprovalCard
        data={{ toolName: "noop", riskLevel: "low", principalDisplay: null }}
        onApprove={vi.fn()}
        onDeny={vi.fn()}
      />
    );
    expect(screen.queryByTestId("approval-card-principal")).not.toBeInTheDocument();
  });

  it("fires onApprove / onDeny", async () => {
    const onApprove = vi.fn();
    const onDeny = vi.fn();
    render(<ApprovalCard data={FULL} onApprove={onApprove} onDeny={onDeny} />);
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(onApprove).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: /deny/i }));
    expect(onDeny).toHaveBeenCalledOnce();
  });

  it("disables both buttons while a decision is in flight", () => {
    render(<ApprovalCard data={FULL} onApprove={vi.fn()} onDeny={vi.fn()} deciding />);
    screen.getAllByRole("button").forEach((b) => expect(b).toBeDisabled());
  });

  it("honors custom approve/deny labels", () => {
    render(
      <ApprovalCard
        data={FULL}
        onApprove={vi.fn()}
        onDeny={vi.fn()}
        approveLabel="Allow"
        denyLabel="Reject"
      />
    );
    expect(screen.getByRole("button", { name: "Allow" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
  });
});

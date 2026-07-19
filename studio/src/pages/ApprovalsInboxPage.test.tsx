import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import ApprovalsInboxPage from "./ApprovalsInboxPage";
import type { ApprovalInboxItem } from "../api/registryApi";

vi.mock("../api/registryApi", () => ({
  listPendingApprovals: vi.fn(),
  decideApproval: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listPendingApprovals, decideApproval } from "../api/registryApi";

const NOW = new Date().toISOString();

function item(over: Partial<ApprovalInboxItem>): ApprovalInboxItem {
  return {
    id: "a1",
    agent_name: "billing-agent",
    team: "platform",
    step_name: "tool:wire_transfer",
    tool_name: "wire_transfer",
    risk_level: "high",
    tool_args: { amount: 100 },
    thread_context_snippet: null,
    sla_remaining_seconds: 600,
    created_at: NOW,
    context: "production",
    version: 1,
    thread_id: "th-1",
    reviewer_scope: null,
    principal_display: null,
    ...over,
  };
}

// A daemon (service-identity) trigger-run approval routed to agent:reviewer,
// plus an interactive approval routed to platform_admin — two distinct scopes.
const DAEMON = item({
  id: "daemon-1",
  tool_name: "wire_transfer",
  reviewer_scope: "agent:reviewer",
  principal_display: "service:billing-agent on behalf of alice",
});
const OTHER = item({
  id: "other-1",
  agent_name: "support-agent",
  step_name: "tool:send_email",
  tool_name: "send_email",
  reviewer_scope: "platform_admin",
  principal_display: null,
});

const ALL = [DAEMON, OTHER];

describe("ApprovalsInboxPage (WS-2 T013 — daemon principal + reviewer-role filter)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Mirror the client-side reviewer-scope filter that listPendingApprovals applies.
    (listPendingApprovals as ReturnType<typeof vi.fn>).mockImplementation(
      (_team?: string, _ctx?: string, scope?: string) =>
        Promise.resolve(scope ? ALL.filter((a) => a.reviewer_scope === scope) : ALL),
    );
  });

  it("renders a daemon approval's principal_display", async () => {
    renderWithProviders(<ApprovalsInboxPage />);
    await waitFor(() =>
      expect(
        screen.getByText("service:billing-agent on behalf of alice"),
      ).toBeInTheDocument(),
    );
    // Both approvals show initially (no filter).
    expect(screen.getByText("wire_transfer")).toBeInTheDocument();
    expect(screen.getByText("send_email")).toBeInTheDocument();
  });

  it("narrows the visible list when a reviewer role is selected", async () => {
    renderWithProviders(<ApprovalsInboxPage />);
    // Wait for the initial (unfiltered) fetch so both scope options are discovered.
    await waitFor(() => expect(screen.getByText("send_email")).toBeInTheDocument());

    const roleSelect = screen.getByLabelText("Filter by reviewer role");
    await userEvent.selectOptions(roleSelect, "agent:reviewer");

    // The daemon (agent:reviewer) approval stays; the platform_admin one drops out.
    await waitFor(() => expect(screen.queryByText("send_email")).not.toBeInTheDocument());
    expect(screen.getByText("wire_transfer")).toBeInTheDocument();
    // The list call was invoked with the selected reviewer scope (T012 param wiring).
    expect(listPendingApprovals).toHaveBeenCalledWith(undefined, undefined, "agent:reviewer");
  });

  // T007 (production surface) — the console queue re-surfaces a re-parked 2nd gate.
  // When a reviewer approves a gate and the resumed run trips a second high-risk tool,
  // the re-park creates a NEW pending approval; the console (refetch on decide) shows it.
  it("re-surfaces a 2nd approval in the queue after the first is decided (re-park)", async () => {
    const FIRST = item({ id: "gate-1", tool_name: "wire_transfer", thread_id: "th-9" });
    const SECOND = item({ id: "gate-2", step_name: "tool:send_email", tool_name: "send_email", thread_id: "th-9" });
    let calls = 0;
    (listPendingApprovals as ReturnType<typeof vi.fn>).mockImplementation(() => {
      calls += 1;
      return Promise.resolve(calls <= 1 ? [FIRST] : [SECOND]);
    });
    (decideApproval as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);

    renderWithProviders(<ApprovalsInboxPage />);
    // Gate 1 is in the console queue.
    expect(await screen.findByText("wire_transfer")).toBeInTheDocument();

    // Decide gate 1 → onSuccess invalidates ["pending-approvals"] → refetch returns the
    // re-parked 2nd gate the reviewer must now also decide.
    await userEvent.click(screen.getByRole("button", { name: /^Approve$/ }));
    await waitFor(() => expect(decideApproval).toHaveBeenCalledWith("gate-1", "approved", 1));

    // The 2nd gate re-appears in the queue — the production/console surface of the re-park.
    expect(await screen.findByText("send_email")).toBeInTheDocument();
  });
});

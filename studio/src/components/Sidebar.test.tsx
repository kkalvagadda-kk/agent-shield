import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import Sidebar from "./Sidebar";
import { STUDIO_BUILD } from "../lib/build";

vi.mock("../api/registryApi", () => ({
  listAgents: vi.fn(),
  listPendingApprovals: vi.fn(),
}));

import { listAgents, listPendingApprovals } from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function approval(id: string) {
  return {
    id,
    agent_name: "my-agent",
    team: "default",
    step_name: null,
    tool_name: "wire_transfer",
    risk_level: "high",
    tool_args: {},
    thread_context_snippet: null,
    sla_remaining_seconds: 900,
    created_at: new Date().toISOString(),
    context: "production",
    version: 1,
    thread_id: `thr-${id}`,
  };
}

describe("Sidebar — approvals badge", () => {
  beforeEach(() => {
    // The sidebar's team-grants section fetches directly; stub it so the nav renders.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ json: () => Promise.resolve([]) })
    );
    mock(listAgents).mockResolvedValue({ items: [] });
    mock(listPendingApprovals).mockResolvedValue([]);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders the count when approvals are pending", async () => {
    mock(listPendingApprovals).mockResolvedValue([approval("a"), approval("b"), approval("c")]);
    renderWithProviders(<Sidebar />);
    const badge = await screen.findByTestId("approvals-badge");
    expect(badge).toHaveTextContent("3");
  });

  it("renders NO badge when nothing is pending (asserting absence, not a '0')", async () => {
    mock(listPendingApprovals).mockResolvedValue([]);
    renderWithProviders(<Sidebar />);
    // The nav item itself must still be there…
    expect(await screen.findByText("Approvals")).toBeInTheDocument();
    // …with no chip. A "0" pill is noise that trains operators to ignore the badge.
    await waitFor(() => expect(listPendingApprovals).toHaveBeenCalled());
    expect(screen.queryByTestId("approvals-badge")).not.toBeInTheDocument();
  });

  it("puts the badge inside the /approvals link, so clicking it routes", async () => {
    mock(listPendingApprovals).mockResolvedValue([approval("a")]);
    renderWithProviders(<Sidebar />);
    const badge = await screen.findByTestId("approvals-badge");
    // The badge's contract is count AND route — a count that does not navigate is
    // half a feature.
    const link = badge.closest("a");
    expect(link).not.toBeNull();
    expect(link).toHaveAttribute("href", "/approvals");
  });

  it("badges ONLY the approvals item — no other nav item grows a chip", async () => {
    mock(listPendingApprovals).mockResolvedValue([approval("a"), approval("b")]);
    renderWithProviders(<Sidebar />);
    await screen.findByTestId("approvals-badge");
    expect(screen.getAllByTestId(/-badge$/)).toHaveLength(1);
  });

  it("survives an approvals outage — nav item renders, no badge, no crash", async () => {
    // An approvals outage must not take out navigation. React Query surfaces the
    // rejection as `data === undefined` → count 0 → no badge.
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mock(listPendingApprovals).mockRejectedValue(new Error("approvals API down"));
    renderWithProviders(<Sidebar />);

    expect(await screen.findByText("Approvals")).toBeInTheDocument();
    expect(await screen.findByText("Marketplace")).toBeInTheDocument();
    await waitFor(() => expect(listPendingApprovals).toHaveBeenCalled());
    expect(screen.queryByTestId("approvals-badge")).not.toBeInTheDocument();
    errSpy.mockRestore();
  });
});

describe("Sidebar — build marker", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ json: () => Promise.resolve([]) })
    );
    mock(listAgents).mockResolvedValue({ items: [] });
    mock(listPendingApprovals).mockResolvedValue([]);
  });

  afterEach(() => vi.unstubAllGlobals());

  // `__STUDIO_BUILD` sat unread for 67 tags and silently lied. This asserts it has a
  // reader — the property that makes a stale bundle observable at all.
  it("renders the studio build marker from the one definition", () => {
    renderWithProviders(<Sidebar />);
    const marker = screen.getByTestId("studio-build");
    expect(marker).toHaveTextContent(STUDIO_BUILD);
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import VersionSelector from "./VersionSelector";
import type { Agent } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listAgents: vi.fn(),
}));

import { listAgents } from "../../api/registryApi";

const NOW = new Date().toISOString();

const AGENTS: Agent[] = [
  {
    id: "a1",
    name: "billing-bot",
    team: "finance",
    description: null,
    status: "active",
    agent_type: "sdk",
    publish_status: "approved",
    agent_class: "daemon",
    execution_shape: "reactive",
    memory_enabled: false,
    created_at: NOW,
    updated_at: NOW,
    created_by: "user1",
    metadata: {},
  },
  {
    id: "a2",
    name: "support-bot",
    team: "ops",
    description: null,
    status: "active",
    agent_type: "sdk",
    publish_status: "approved",
    agent_class: "user_delegated",
    execution_shape: "durable",
    memory_enabled: false,
    created_at: NOW,
    updated_at: NOW,
    created_by: "user2",
    metadata: {},
  },
];

describe("VersionSelector", () => {
  beforeEach(() => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: AGENTS,
      total: AGENTS.length,
    });
  });

  it("renders a 'Select Agent' label", () => {
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );
    expect(screen.getByText(/select agent/i)).toBeInTheDocument();
  });

  it("renders agent options in the dropdown after loading", async () => {
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );

    // Wait for loading to complete
    await waitFor(() =>
      expect(screen.queryByText("Loading agents…")).not.toBeInTheDocument()
    );

    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();
    // Options should include both agents
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toContain("billing-bot");
    expect(options).toContain("support-bot");
  });

  it("shows loading placeholder while fetching", () => {
    // Make the mock never resolve so loading state is visible
    (listAgents as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );
    expect(screen.getByText("Loading agents…")).toBeInTheDocument();
  });

  it("calls onSelect with the agent name when an option is chosen", async () => {
    const onSelect = vi.fn();
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={onSelect} />
    );

    await waitFor(() =>
      expect(screen.queryByText("Loading agents…")).not.toBeInTheDocument()
    );

    await userEvent.selectOptions(screen.getByRole("combobox"), "billing-bot");
    expect(onSelect).toHaveBeenCalledWith("billing-bot");
  });

  it("shows agent class chip when a known agent is selected", async () => {
    renderWithProviders(
      <VersionSelector selectedAgent="billing-bot" onSelect={vi.fn()} />
    );

    // billing-bot has agent_class "daemon"
    expect(await screen.findByText("daemon")).toBeInTheDocument();
  });

  it("shows team label for the selected agent", async () => {
    renderWithProviders(
      <VersionSelector selectedAgent="support-bot" onSelect={vi.fn()} />
    );

    expect(await screen.findByText(/team: ops/i)).toBeInTheDocument();
  });

  it("does not show agent info when no agent is selected", async () => {
    renderWithProviders(
      <VersionSelector selectedAgent="" onSelect={vi.fn()} />
    );

    await waitFor(() =>
      expect(screen.queryByText("Loading agents…")).not.toBeInTheDocument()
    );

    // No team label should appear for an empty selection
    expect(screen.queryByText(/team:/i)).not.toBeInTheDocument();
  });
});

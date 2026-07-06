import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import AgentGraphsPage from "./AgentGraphsPage";
import type { AgentGraph } from "../api/registryApi";

vi.mock("../api/registryApi", () => ({
  listAgentGraphs: vi.fn(),
}));
import { listAgentGraphs } from "../api/registryApi";

const NOW = new Date().toISOString();

const GRAPHS: AgentGraph[] = [
  {
    id: "ag1",
    name: "invoice-processor",
    team: "finance",
    description: "Processes invoices",
    status: "published",
    current_version_number: 2,
    current_definition: null,
    created_at: NOW,
    updated_at: NOW,
  },
  {
    id: "ag2",
    name: "ticket-router",
    team: "ops",
    description: null,
    status: "draft",
    current_version_number: null,
    current_definition: null,
    created_at: NOW,
    updated_at: NOW,
  },
];

describe("AgentGraphsPage", () => {
  beforeEach(() => {
    (listAgentGraphs as ReturnType<typeof vi.fn>).mockResolvedValue(GRAPHS);
  });

  it("renders graph rows with name, team, and status", async () => {
    renderWithProviders(<AgentGraphsPage />);

    expect(await screen.findByText("invoice-processor")).toBeInTheDocument();
    expect(screen.getByText("finance")).toBeInTheDocument();
    expect(screen.getByText("published")).toBeInTheDocument();

    expect(screen.getByText("ticket-router")).toBeInTheDocument();
    expect(screen.getByText("ops")).toBeInTheDocument();
    expect(screen.getByText("draft")).toBeInTheDocument();
  });

  it("shows the page header and New Agent Graph button", () => {
    renderWithProviders(<AgentGraphsPage />);
    expect(screen.getByRole("heading", { name: /agent graphs/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new agent graph/i })).toBeInTheDocument();
  });

  it("shows empty-state copy when there are no agent graphs", async () => {
    (listAgentGraphs as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    renderWithProviders(<AgentGraphsPage />);
    await waitFor(() =>
      expect(screen.getByText(/no agent graphs yet/i)).toBeInTheDocument()
    );
    // The empty-state also renders a New Agent Graph button
    const buttons = screen.getAllByRole("button", { name: /new agent graph/i });
    expect(buttons.length).toBeGreaterThanOrEqual(1);
  });

  it("shows description below graph name when present", async () => {
    renderWithProviders(<AgentGraphsPage />);
    expect(await screen.findByText("Processes invoices")).toBeInTheDocument();
  });

  it("shows error message when the API call fails", async () => {
    (listAgentGraphs as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("network error"));
    renderWithProviders(<AgentGraphsPage />);
    await waitFor(() =>
      expect(screen.getByText(/failed to load agent graphs/i)).toBeInTheDocument()
    );
  });
});

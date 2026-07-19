import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import AgentListPage from "./AgentListPage";
import type { Agent } from "../api/registryApi";

vi.mock("../api/registryApi", () => ({
  listAgents: vi.fn(),
  deleteAgent: vi.fn(),
  updateAgent: vi.fn(),
  listProviders: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listTools: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listVersions: vi.fn().mockResolvedValue([]),
  getAgentHealth: vi.fn().mockResolvedValue({}),
}));
vi.mock("../api/knowledgeApi", () => ({
  listKBs: vi.fn().mockResolvedValue([]),
  getAgentKnowledgeBases: vi.fn().mockResolvedValue([]),
  bindAgent: vi.fn().mockResolvedValue(undefined),
  unbindAgent: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listAgents, deleteAgent, listTools } from "../api/registryApi";
import { listKBs, getAgentKnowledgeBases } from "../api/knowledgeApi";

const NOW = new Date().toISOString();

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "a1",
    name: "my-agent",
    team: "platform",
    description: "A test agent",
    status: "active",
    agent_type: "sdk",
    publish_status: "draft",
    agent_class: null,
    execution_shape: "reactive",
    memory_enabled: false,
    created_at: NOW,
    updated_at: NOW,
    created_by: "user1",
    metadata: {},
    latest_version_number: 1,
    ...overrides,
  };
}

describe("AgentListPage", () => {
  beforeEach(() => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
    });
    (deleteAgent as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  });

  it("renders the page header", () => {
    renderWithProviders(<AgentListPage />);
    expect(screen.getByRole("heading", { name: /^agents$/i })).toBeInTheDocument();
  });

  it("shows empty state copy when there are no agents", async () => {
    renderWithProviders(<AgentListPage />);
    expect(await screen.findByText(/no agents yet/i)).toBeInTheDocument();
    expect(screen.getByText(/create your first agent/i)).toBeInTheDocument();
  });

  it("renders agent rows with name, team, type, and status", async () => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeAgent({ name: "billing-bot", team: "finance", agent_type: "sdk", status: "active" }),
        makeAgent({ id: "a2", name: "support-bot", team: "ops", agent_type: "declarative", status: "archived" }),
      ],
      total: 2,
    });
    renderWithProviders(<AgentListPage />);

    expect(await screen.findByText("billing-bot")).toBeInTheDocument();
    expect(screen.getByText("finance")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();

    expect(screen.getByText("support-bot")).toBeInTheDocument();
    expect(screen.getByText("ops")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
  });

  it("shows Create Agent button", () => {
    renderWithProviders(<AgentListPage />);
    expect(screen.getByRole("button", { name: /create agent/i })).toBeInTheDocument();
  });

  it("shows search input", () => {
    renderWithProviders(<AgentListPage />);
    expect(screen.getByPlaceholderText(/search agents/i)).toBeInTheDocument();
  });

  it("filters agent rows when search text is typed", async () => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeAgent({ name: "alpha-agent" }),
        makeAgent({ id: "a2", name: "beta-agent" }),
      ],
      total: 2,
    });
    renderWithProviders(<AgentListPage />);

    await screen.findByText("alpha-agent");

    await userEvent.type(screen.getByPlaceholderText(/search agents/i), "beta");

    await waitFor(() =>
      expect(screen.queryByText("alpha-agent")).not.toBeInTheDocument()
    );
    expect(screen.getByText("beta-agent")).toBeInTheDocument();
  });

  it("shows description below agent name when present", async () => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeAgent({ description: "Processes invoices automatically" })],
      total: 1,
    });
    renderWithProviders(<AgentListPage />);
    expect(await screen.findByText("Processes invoices automatically")).toBeInTheDocument();
  });

  it("shows error message when the API call fails", async () => {
    (listAgents as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("network error"));
    renderWithProviders(<AgentListPage />);
    await waitFor(() =>
      expect(screen.getByText(/failed to load agents/i)).toBeInTheDocument()
    );
  });

  it("renders action buttons for each agent row", async () => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeAgent()],
      total: 1,
    });
    renderWithProviders(<AgentListPage />);

    await screen.findByText("my-agent");
    expect(screen.getByRole("button", { name: /deploy/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /edit/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete/i })).toBeInTheDocument();
  });

  it("shows agent count in the stats row", async () => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeAgent(), makeAgent({ id: "a2", name: "bot2" })],
      total: 2,
    });
    renderWithProviders(<AgentListPage />);
    await screen.findByText("my-agent");
    expect(screen.getByText(/2 of 2 agents/i)).toBeInTheDocument();
  });

  // Regression for the Edit Agent modal (the 3rd agent-editing surface): it used
  // to render the raw tool list, so knowledge_search leaked in as a checkable tool
  // and there was no Knowledge Bases picker. The shared ToolsPicker/KnowledgeBasePicker
  // fix this on all three surfaces. (The screenshot in the bug report is the repro;
  // this pins it: before the fix, tools-picker contained knowledge_search and there
  // was no kb-picker testid at all — this test would have failed on both assertions.)
  it("Edit Agent modal hides knowledge_search from Tools and shows the KB picker", async () => {
    (listAgents as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeAgent({ metadata: { tools: ["calculator", "knowledge_search"] } })],
      total: 1,
    });
    (listTools as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        { id: "t1", name: "calculator", display_name: "Calculator", description: "math", risk_level: "low" },
        { id: "t2", name: "knowledge_search", display_name: "Knowledge Search", description: "kb", risk_level: "low" },
      ],
      total: 2,
    });
    (listKBs as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: "kb1", team: "platform", name: "Docs KB", description: "team docs", created_by: "u",
        created_at: NOW, updated_at: NOW, source_count: 3, ready_count: 3, attached_agents: [] },
    ]);
    (getAgentKnowledgeBases as ReturnType<typeof vi.fn>).mockResolvedValue([{ kb_id: "kb1", name: "Docs KB" }]);

    renderWithProviders(<AgentListPage />);
    await screen.findByText("my-agent");
    await userEvent.click(screen.getByRole("button", { name: /edit/i }));

    // The modal's Tools list must exclude knowledge_search but keep real tools.
    const toolsPicker = await screen.findByTestId("tools-picker");
    expect(within(toolsPicker).getByText("Calculator")).toBeInTheDocument();
    expect(within(toolsPicker).queryByText("knowledge_search")).not.toBeInTheDocument();
    expect(within(toolsPicker).queryByText("Knowledge Search")).not.toBeInTheDocument();

    // And the KB picker must be present (parity with Create Agent + Settings).
    const kbPicker = await screen.findByTestId("kb-picker");
    expect(within(kbPicker).getByText("Docs KB")).toBeInTheDocument();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import ObservabilityDashboardPage from "./ObservabilityDashboardPage";
import type { DashboardData } from "../api/observabilityApi";

vi.mock("../api/observabilityApi", () => ({
  getDashboard: vi.fn(),
}));
vi.mock("../api/registryApi", () => ({
  listAgents: vi.fn().mockResolvedValue({ items: [] }),
}));
import { getDashboard } from "../api/observabilityApi";

const EMPTY = { up: 0, down: 0, total: 0, ratio: null };

const BASE: DashboardData = {
  latency_series: [],
  score_histogram: [],
  status_counts: [],
  cost_series: [],
  safety_blocks: [],
  feedback: EMPTY,
  tool_calls: [],
  total_runs: 0,
  total_cost_usd: 0,
  avg_cost_per_run: null,
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  spend_by_model: [],
};

function mockDashboard(over: Partial<DashboardData>) {
  (getDashboard as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    ...BASE,
    ...over,
  });
}

describe("ObservabilityDashboardPage — env-scoped dashboard", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders a Production dashboard and requests the production environment", async () => {
    mockDashboard({ feedback: { up: 3, down: 1, total: 4, ratio: 0.75 } });
    renderWithProviders(<ObservabilityDashboardPage environment="production" />);
    expect(screen.getByText("Production Dashboard")).toBeInTheDocument();
    // The whole page is scoped to production via the API param.
    expect(getDashboard).toHaveBeenCalledWith(
      expect.objectContaining({ environment: "production" })
    );
    // Data loads → the Satisfaction card shows the ratio (single-node value).
    await waitFor(() => expect(screen.getByText("75%")).toBeInTheDocument());
  });

  it("renders a Sandbox dashboard scoped to the sandbox environment", async () => {
    mockDashboard({ feedback: EMPTY });
    renderWithProviders(<ObservabilityDashboardPage environment="sandbox" />);
    expect(screen.getByText("Sandbox Dashboard")).toBeInTheDocument();
    expect(getDashboard).toHaveBeenCalledWith(
      expect.objectContaining({ environment: "sandbox" })
    );
    // Empty feedback → the hint appears (Satisfaction card sub + panel row).
    await waitFor(() =>
      expect(screen.getAllByText(/no feedback yet/i).length).toBeGreaterThan(0)
    );
  });

  it("renders the tool-call frequency + latency panel", async () => {
    mockDashboard({
      tool_calls: [
        { tool_name: "web_search", count: 12, avg_latency_ms: 860 },
        { tool_name: "calculator", count: 3, avg_latency_ms: null },
      ],
    });
    renderWithProviders(<ObservabilityDashboardPage environment="production" />);
    await waitFor(() => expect(screen.getByText("web_search")).toBeInTheDocument());
    expect(screen.getByText("12×")).toBeInTheDocument();
    expect(screen.getByText("0.86s")).toBeInTheDocument(); // 860ms -> 0.86s
    expect(screen.getByText("calculator")).toBeInTheDocument();
  });

  it("renders the LLM cost panel with avg/run, tokens, and spend-by-model", async () => {
    mockDashboard({
      total_cost_usd: 1.2345,
      avg_cost_per_run: 0.0102,
      total_prompt_tokens: 15400,
      total_completion_tokens: 3800,
      spend_by_model: [
        { model: "claude-sonnet-4-6", cost_usd: 1.1, calls: 20, prompt_tokens: 14000, completion_tokens: 3500 },
        { model: "claude-haiku-4-5", cost_usd: 0.13, calls: 8, prompt_tokens: 1400, completion_tokens: 300 },
      ],
    });
    renderWithProviders(<ObservabilityDashboardPage environment="production" />);
    await waitFor(() => expect(screen.getByText("claude-sonnet-4-6")).toBeInTheDocument());
    expect(screen.getByText("$0.0102")).toBeInTheDocument(); // avg/run
    expect(screen.getByText("15.4K")).toBeInTheDocument(); // prompt tokens
    expect(screen.getByText("$1.1000")).toBeInTheDocument(); // model spend
    expect(screen.getByText("claude-haiku-4-5")).toBeInTheDocument();
  });
});

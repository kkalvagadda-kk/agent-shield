import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import CostConsolePage from "./CostConsolePage";
import type { CostConsoleData } from "../api/observabilityApi";

vi.mock("../api/observabilityApi", () => ({
  getCosts: vi.fn(),
}));
import { getCosts } from "../api/observabilityApi";

const BASE: CostConsoleData = {
  environment: "production",
  total_cost_usd: 0,
  total_runs: 0,
  runs_with_cost: 0,
  avg_cost_per_run: null,
  total_prompt_tokens: 0,
  total_completion_tokens: 0,
  projected_monthly_usd: null,
  daily_series: [],
  by_model: [],
  by_agent: [],
  top_runs: [],
};

function mockCosts(over: Partial<CostConsoleData>) {
  (getCosts as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ...BASE, ...over });
}

describe("CostConsolePage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders headline totals, model + agent breakdowns, and expensive runs", async () => {
    mockCosts({
      total_cost_usd: 2.5,
      runs_with_cost: 40,
      total_runs: 50,
      avg_cost_per_run: 0.0625,
      total_prompt_tokens: 120000,
      total_completion_tokens: 40000,
      projected_monthly_usd: 10.5,
      by_model: [
        { model: "claude-sonnet-4-6", cost_usd: 2.2, calls: 40, prompt_tokens: 110000, completion_tokens: 38000 },
      ],
      by_agent: [{ agent_name: "serper-agent-4", cost_usd: 2.4, runs: 40 }],
      top_runs: [
        {
          id: "run-1",
          agent_name: "serper-agent-4",
          cost_usd: 0.42,
          prompt_tokens: 8000,
          completion_tokens: 1200,
          started_at: "2026-07-12T10:00:00Z",
          trace_id: "abcdef1234567890",
        },
      ],
    });
    renderWithProviders(<CostConsolePage />);
    await waitFor(() => expect(screen.getByText("$2.5000")).toBeInTheDocument());
    expect(screen.getByText("$0.0625")).toBeInTheDocument(); // avg/run
    expect(screen.getByText("$10.50")).toBeInTheDocument(); // projected monthly
    expect(screen.getByText("claude-sonnet-4-6")).toBeInTheDocument();
    expect(screen.getAllByText("serper-agent-4").length).toBeGreaterThan(0);
    expect(screen.getByText("$0.4200")).toBeInTheDocument(); // top run cost
    expect(screen.getByText("abcdef12…")).toBeInTheDocument(); // trace link
  });

  it("requests the sandbox environment when toggled", async () => {
    mockCosts({});
    renderWithProviders(<CostConsolePage />);
    await waitFor(() => expect(getCosts).toHaveBeenCalled());
    screen.getByRole("button", { name: /sandbox/i }).click();
    await waitFor(() =>
      expect(getCosts).toHaveBeenCalledWith(expect.objectContaining({ environment: "sandbox" }))
    );
  });
});

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
  feedback: { production: EMPTY, sandbox: EMPTY },
  total_runs: 0,
  total_cost_usd: 0,
};

function mockDashboard(over: Partial<DashboardData>) {
  (getDashboard as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
    ...BASE,
    ...over,
  });
}

describe("ObservabilityDashboardPage — user feedback panel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("leads with production satisfaction and splits prod vs sandbox", async () => {
    mockDashboard({
      feedback: {
        production: { up: 3, down: 1, total: 4, ratio: 0.75 },
        sandbox: { up: 1, down: 1, total: 2, ratio: 0.5 },
      },
    });
    renderWithProviders(<ObservabilityDashboardPage />);
    // Prod Satisfaction card shows the PRODUCTION ratio (75%), not sandbox.
    await waitFor(() =>
      expect(screen.getAllByText(/75% positive/).length).toBeGreaterThan(0)
    );
    // Both environment rows render.
    expect(screen.getByText("Production")).toBeInTheDocument();
    expect(screen.getByText(/Sandbox \/ Playground/)).toBeInTheDocument();
    expect(screen.getByText(/50% positive/)).toBeInTheDocument(); // sandbox row
  });

  it("shows production empty state when only sandbox has feedback", async () => {
    mockDashboard({
      feedback: {
        production: EMPTY,
        sandbox: { up: 2, down: 0, total: 2, ratio: 1 },
      },
    });
    renderWithProviders(<ObservabilityDashboardPage />);
    await waitFor(() => expect(screen.getByText("Production")).toBeInTheDocument());
    // Production card + row both signal no production feedback yet.
    expect(screen.getByText(/no production feedback yet/i)).toBeInTheDocument();
  });
});

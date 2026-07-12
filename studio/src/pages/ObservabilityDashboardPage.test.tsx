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

const BASE: DashboardData = {
  latency_series: [],
  score_histogram: [],
  status_counts: [],
  cost_series: [],
  safety_blocks: [],
  feedback: { up: 0, down: 0, total: 0, ratio: null },
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

  it("renders the positive ratio + thumbs breakdown when feedback exists", async () => {
    mockDashboard({ feedback: { up: 3, down: 1, total: 4, ratio: 0.75 } });
    renderWithProviders(<ObservabilityDashboardPage />);
    // Satisfaction metric card + panel both show 75%.
    await waitFor(() =>
      expect(screen.getAllByText(/75%/).length).toBeGreaterThan(0)
    );
    expect(screen.getByText(/4 rated/)).toBeInTheDocument();
  });

  it("shows an empty state when no feedback was submitted", async () => {
    mockDashboard({ feedback: { up: 0, down: 0, total: 0, ratio: null } });
    renderWithProviders(<ObservabilityDashboardPage />);
    await waitFor(() =>
      expect(
        screen.getByText(/No thumbs feedback in this period/i)
      ).toBeInTheDocument()
    );
    expect(screen.getByText(/no feedback yet/i)).toBeInTheDocument();
  });
});

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
  total_runs: 0,
  total_cost_usd: 0,
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
});

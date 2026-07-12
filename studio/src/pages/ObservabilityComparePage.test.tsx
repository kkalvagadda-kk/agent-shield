import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import ObservabilityComparePage from "./ObservabilityComparePage";

vi.mock("../api/observabilityApi", () => ({
  getTraceDetail: vi.fn(),
}));
import { getTraceDetail } from "../api/observabilityApi";

// Two traces: A judge score 0.60, B judge score 0.85 -> delta +0.25.
function mockTraces() {
  (getTraceDetail as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (id: string) => {
      const value = id === "aaaa" ? 0.6 : 0.85;
      return Promise.resolve({
        trace_id: id,
        trace_url: null,
        trace: {
          trace_id: id,
          name: "agent",
          user: null,
          started_at: null,
          tags: [],
          total_cost: null,
          warning: null,
          spans: [{ id: "s1", name: "agent", type: "AGENT", start_time: "2026-07-11T00:00:00Z", end_time: "2026-07-11T00:00:01Z" }],
          scores: [{ name: "llm-judge", value, comment: null }],
        },
      });
    }
  );
}

function render() {
  return renderWithProviders(<ObservabilityComparePage />, {
    route: "/observability/compare?a=aaaa&b=bbbb",
  });
}

describe("ObservabilityComparePage — judge score delta", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows both judge scores and a positive delta", async () => {
    mockTraces();
    render();
    await waitFor(() => expect(screen.getByText("Score Delta")).toBeInTheDocument());
    expect(screen.getByText("0.60 → 0.85")).toBeInTheDocument();
    expect(screen.getByText("+0.25")).toBeInTheDocument();
  });

  it("renders a dash when a trace has no judge score", async () => {
    (getTraceDetail as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      trace_id: "x",
      trace_url: null,
      trace: {
        trace_id: "x", name: null, user: null, started_at: null, tags: [],
        total_cost: null, warning: null, spans: [], scores: [],
      },
    });
    render();
    await waitFor(() => expect(screen.getByText("Score Delta")).toBeInTheDocument());
    // both scores absent -> "— → —" and delta "—"
    expect(screen.getByText("— → —")).toBeInTheDocument();
  });
});

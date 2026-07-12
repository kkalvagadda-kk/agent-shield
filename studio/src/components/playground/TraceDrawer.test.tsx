import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import TraceDrawer from "./TraceDrawer";
import type { TraceDetail } from "../../api/observabilityApi";

function detail(): TraceDetail {
  return {
    trace_id: "t1",
    trace_url: "https://langfuse.example/project/p/traces/t1",
    trace: {
      trace_id: "t1",
      name: "workflow.serper.stream",
      user: "kalyan",
      started_at: "2026-07-12T18:00:00Z",
      tags: ["serper", "production"],
      total_cost: 0.0178,
      warning: null,
      scores: [{ name: "llm-judge", value: 0.85, comment: "good answer" }],
      spans: [
        { id: "root", name: "call_model", type: "CHAIN", parent_id: null,
          start_time: "2026-07-12T18:00:00Z", end_time: "2026-07-12T18:00:02Z" },
        { id: "gen1", name: "ChatAnthropic", type: "GENERATION", parent_id: "root",
          start_time: "2026-07-12T18:00:00Z", end_time: "2026-07-12T18:00:01Z",
          model: "claude-sonnet-4-6", cost_usd: 0.0107, prompt_tokens: 1546, completion_tokens: 401 },
      ],
    },
  };
}

describe("TraceDrawer", () => {
  it("renders the tree, per-generation cost, and trace scores", async () => {
    const fetchFn = vi.fn().mockResolvedValue(detail());
    renderWithProviders(<TraceDrawer traceId="t1" onClose={() => {}} fetchFn={fetchFn} />);

    // Tree: both the root CHAIN and its nested GENERATION span render.
    await waitFor(() => expect(screen.getByText("call_model")).toBeInTheDocument());
    expect(screen.getByText("ChatAnthropic")).toBeInTheDocument();
    expect(screen.getByText("CHAIN")).toBeInTheDocument();
    expect(screen.getByText("GENERATION")).toBeInTheDocument();

    // Per-generation cost shown inline on the row.
    expect(screen.getByText("$0.0107")).toBeInTheDocument();
    // Trace-level total cost + score.
    expect(screen.getByText("$0.0178")).toBeInTheDocument();
    expect(screen.getByText("llm-judge")).toBeInTheDocument();
    expect(screen.getByText("0.85")).toBeInTheDocument();

    // Expanding the generation reveals model + token detail.
    fireEvent.click(screen.getByText("ChatAnthropic"));
    await waitFor(() => expect(screen.getByText("claude-sonnet-4-6")).toBeInTheDocument());
    expect(screen.getByText("1546")).toBeInTheDocument();
    expect(screen.getByText("401")).toBeInTheDocument();
  });

  it("shows the warning when a trace has not ingested yet", async () => {
    const fetchFn = vi.fn().mockResolvedValue({
      trace_id: "t2",
      trace_url: null,
      trace: { trace_id: "t2", name: null, user: null, started_at: null, tags: [],
        total_cost: null, warning: "trace not yet ingested by Langfuse", spans: [], scores: [] },
    });
    renderWithProviders(<TraceDrawer traceId="t2" onClose={() => {}} fetchFn={fetchFn} />);
    await waitFor(() =>
      expect(screen.getByText(/not yet ingested/i)).toBeInTheDocument()
    );
  });
});

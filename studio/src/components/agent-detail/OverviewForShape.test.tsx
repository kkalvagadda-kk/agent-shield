import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import OverviewForShape, { resolveOverviewShape, type OverviewShape } from "./OverviewForShape";

vi.mock("../../api/registryApi", () => ({
  getDeploymentStats: vi.fn(),
  listDeploymentRuns: vi.fn(),
  listTriggers: vi.fn(),
  listAgentEvents: vi.fn(),
  rotateToken: vi.fn(),
  enableTrigger: vi.fn(),
  disableTrigger: vi.fn(),
  getAgentHealth: vi.fn(),
}));

import {
  getDeploymentStats,
  listDeploymentRuns,
  listTriggers,
  listAgentEvents,
  getAgentHealth,
} from "../../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function render(shape: OverviewShape) {
  return renderWithProviders(
    <OverviewForShape
      shape={shape}
      agentName="my-agent"
      deploymentId="dep-1"
      context="production"
    />
  );
}

describe("resolveOverviewShape", () => {
  // The derivation rule is the thing that drifted: CatalogDetailPage dispatched on
  // `execution_shape` alone and therefore could never reach scheduled/event_driven.
  it("maps a webhook trigger to event_driven regardless of execution_shape", () => {
    expect(
      resolveOverviewShape({ hasWebhook: true, hasSchedule: false, executionShape: "durable" })
    ).toBe("event_driven");
  });

  it("maps a schedule trigger to scheduled regardless of execution_shape", () => {
    expect(
      resolveOverviewShape({ hasWebhook: false, hasSchedule: true, executionShape: "durable" })
    ).toBe("scheduled");
  });

  it("prefers event_driven when an agent has BOTH a webhook and a schedule", () => {
    expect(
      resolveOverviewShape({ hasWebhook: true, hasSchedule: true, executionShape: "reactive" })
    ).toBe("event_driven");
  });

  it("maps execution_shape=durable with no triggers to durable", () => {
    expect(
      resolveOverviewShape({ hasWebhook: false, hasSchedule: false, executionShape: "durable" })
    ).toBe("durable");
  });

  it("maps execution_shape=reactive to reactive", () => {
    expect(
      resolveOverviewShape({ hasWebhook: false, hasSchedule: false, executionShape: "reactive" })
    ).toBe("reactive");
  });

  it("treats a missing/unknown execution_shape as reactive (it is a 2-value column)", () => {
    // `execution_shape` is CHECK-constrained to reactive|durable in the DB, but callers
    // read it from untyped JSON (`config_snapshot`), so anything not "durable" is
    // reactive. "scheduled" is NOT a stored value — a version snapshot can never
    // legitimately carry it, and the old fork's `=== "scheduled"` branch was dead code.
    expect(
      resolveOverviewShape({ hasWebhook: false, hasSchedule: false, executionShape: undefined })
    ).toBe("reactive");
    expect(
      resolveOverviewShape({ hasWebhook: false, hasSchedule: false, executionShape: null })
    ).toBe("reactive");
    expect(
      resolveOverviewShape({ hasWebhook: false, hasSchedule: false, executionShape: "scheduled" })
    ).toBe("reactive");
  });
});

describe("OverviewForShape", () => {
  beforeEach(() => {
    mock(getDeploymentStats).mockResolvedValue({
      run_count: 2,
      p50_latency_ms: 100,
      p95_latency_ms: 200,
      error_rate: 0,
      total_cost_usd: 0,
    });
    mock(listDeploymentRuns).mockResolvedValue([]);
    mock(listTriggers).mockResolvedValue([]);
    mock(listAgentEvents).mockResolvedValue([]);
    mock(getAgentHealth).mockResolvedValue({ status: "healthy" });
  });

  it("renders the reactive overview for shape=reactive", async () => {
    render("reactive");
    expect(await screen.findByText("API Endpoint")).toBeInTheDocument();
    expect(screen.getByTestId("overview-for-shape")).toHaveAttribute("data-shape", "reactive");
  });

  it("renders the durable overview for shape=durable", async () => {
    render("durable");
    expect(await screen.findByTestId("overview-for-shape")).toHaveAttribute(
      "data-shape",
      "durable"
    );
    // The durable overview reads deployment-scoped runs; the reactive endpoint card
    // must NOT be what rendered.
    expect(screen.queryByText("API Endpoint")).not.toBeInTheDocument();
  });

  it("renders the scheduled overview for shape=scheduled", async () => {
    render("scheduled");
    expect(await screen.findByTestId("overview-for-shape")).toHaveAttribute(
      "data-shape",
      "scheduled"
    );
  });

  // THE REGRESSION TEST for the exact drift WS-6 fixes: the CatalogDetailPage fork had
  // no event-driven branch at all, so an event-driven artifact silently rendered
  // nothing shape-specific. One dispatcher makes that unrepresentable.
  it("renders the event-driven overview for shape=event_driven", async () => {
    render("event_driven");
    expect(await screen.findByTestId("overview-for-shape")).toHaveAttribute(
      "data-shape",
      "event_driven"
    );
    expect(
      await screen.findByText(/No webhook trigger configured/i)
    ).toBeInTheDocument();
  });

  it("covers every shape the resolver can return (no shape can reach a blank page)", async () => {
    // Exhaustiveness is enforced at compile time by Record<OverviewShape, ...>, but a
    // future edit could widen the union and add a `?? OverviewReactive`. This asserts
    // the behaviour, not the type.
    const shapes: OverviewShape[] = ["reactive", "durable", "scheduled", "event_driven"];
    for (const shape of shapes) {
      const { unmount } = render(shape);
      expect(await screen.findByTestId("overview-for-shape")).toHaveAttribute(
        "data-shape",
        shape
      );
      expect(screen.queryByTestId("overview-unsupported-shape")).not.toBeInTheDocument();
      unmount();
    }
  });

  describe("unknown shape", () => {
    let errSpy: ReturnType<typeof vi.spyOn>;
    beforeEach(() => {
      errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    });
    afterEach(() => errSpy.mockRestore());

    it("fails CLOSED and LOUD — never a silent fallback to Reactive", async () => {
      // A shape from an API/JSON boundary that TypeScript cannot police.
      render("telepathic" as OverviewShape);

      // Loud: a visible error card…
      expect(await screen.findByTestId("overview-unsupported-shape")).toBeInTheDocument();
      expect(screen.getByText(/Unsupported execution shape: telepathic/i)).toBeInTheDocument();
      // …and a console.error naming the fix.
      expect(errSpy).toHaveBeenCalledWith(
        expect.stringContaining('unsupported execution shape "telepathic"')
      );

      // Closed: it must NOT quietly render the reactive overview. A quiet default is
      // exactly how the catalog fork lost event-driven without anyone noticing.
      expect(screen.queryByText("API Endpoint")).not.toBeInTheDocument();
      expect(screen.queryByTestId("overview-for-shape")).not.toBeInTheDocument();
    });
  });
});

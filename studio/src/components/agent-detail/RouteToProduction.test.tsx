import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import RouteToProduction from "./RouteToProduction";
import * as api from "../../api/registryApi";
import type { Agent } from "../../api/registryApi";

vi.mock("../../api/registryApi");

const mock = <T,>(fn: T) => fn as unknown as ReturnType<typeof vi.fn>;

function agentOf(over: Partial<Agent> = {}): Agent {
  return {
    id: "a-1",
    name: "nightly-digest",
    team: "platform",
    agent_type: "declarative",
    status: "active",
    publish_status: "private",
    ...over,
  } as Agent;
}

/** The four inputs the strip reads, defaulted to "nothing done yet". */
function wire(opts: {
  triggers?: unknown[];
  sandbox?: string | null;
  evalPassed?: boolean;
  dispatchError?: string | null;
} = {}) {
  mock(api.listTriggers).mockResolvedValue(
    opts.triggers ?? [{ id: "t-1", trigger_type: "schedule" }],
  );
  mock(api.getDeployments).mockResolvedValue(
    opts.sandbox ? [{ id: "d-1", status: opts.sandbox }] : [],
  );
  mock(api.listVersions).mockResolvedValue([
    { id: "v-1", eval_passed: opts.evalPassed ?? false },
  ]);
  mock(api.getAgentHealth).mockResolvedValue({
    agent_name: "nightly-digest",
    mode: "scheduled",
    health: "failing",
    dispatch_error: opts.dispatchError === undefined ? "no running production deployment" : opts.dispatchError,
  });
}

describe("RouteToProduction", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders nothing for an agent with no trigger", async () => {
    // A reactive chat agent never dispatches to production. Showing it an
    // unfinished checklist would misstate what "done" means for that agent.
    wire({ triggers: [] });
    renderWithProviders(<RouteToProduction agent={agentOf()} />);
    await waitFor(() =>
      expect(screen.queryByTestId("route-to-production")).not.toBeInTheDocument(),
    );
  });

  it("shows all four steps once the agent has a trigger", async () => {
    wire();
    renderWithProviders(<RouteToProduction agent={agentOf()} />);
    await screen.findByTestId("route-to-production");
    for (const k of ["sandbox", "eval", "published", "production"]) {
      expect(screen.getByTestId(`route-step-${k}`)).toBeInTheDocument();
    }
  });

  it("marks the production step done from dispatch_error, not from a deployments list", async () => {
    // The step must agree with the code that actually fires the schedule. Deriving
    // it from anything else would be a second definition of reachability — the bug
    // class this whole workstream exists to delete.
    wire({ sandbox: "running", evalPassed: true, dispatchError: null });
    renderWithProviders(
      <RouteToProduction agent={agentOf({ publish_status: "published" })} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("route-step-production")).toHaveAttribute("data-state", "done"),
    );
    expect(screen.getByTestId("route-to-production-summary")).toHaveTextContent(
      /triggers can fire/i,
    );
  });

  it("keeps production NOT done while a dispatch error stands, even when published", async () => {
    // The defect this strip was built for: publishing produces a catalog listing,
    // and an operator who stops there has a schedule that never fires.
    wire({ sandbox: "running", evalPassed: true, dispatchError: "no running production deployment" });
    renderWithProviders(
      <RouteToProduction agent={agentOf({ publish_status: "published" })} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("route-step-production")).toHaveAttribute("data-state", "current"),
    );
    expect(screen.getByTestId("route-step-published")).toHaveAttribute("data-state", "done");
    // And it names the step everyone misses.
    expect(screen.getByTestId("route-to-production-hint")).toHaveTextContent(
      /only listed it.*Deploy Latest/i,
    );
  });

  it("shows exactly one hint — the step the operator is on", async () => {
    // Four simultaneous warnings, each describing one blocker with no sense of
    // sequence, is the state this component replaces.
    wire({ sandbox: "running" });
    renderWithProviders(<RouteToProduction agent={agentOf()} />);
    await screen.findByTestId("route-to-production");
    expect(screen.getAllByTestId("route-to-production-hint")).toHaveLength(1);
    expect(screen.getByTestId("route-to-production-hint")).toHaveTextContent(/Eval Runs/i);
  });

  it("reports awaiting-review distinctly from published", async () => {
    wire({ sandbox: "running", evalPassed: true });
    renderWithProviders(
      <RouteToProduction agent={agentOf({ publish_status: "pending_review" })} />,
    );
    await screen.findByTestId("route-to-production");
    expect(screen.getByTestId("route-step-published")).toHaveTextContent(/Awaiting review/i);
    expect(screen.getByTestId("route-to-production-hint")).toHaveTextContent(/Publish Queue/i);
  });

  it("counts the remaining steps", async () => {
    wire({ sandbox: "running", evalPassed: true });
    renderWithProviders(<RouteToProduction agent={agentOf()} />);
    // published + production outstanding
    await waitFor(() =>
      expect(screen.getByTestId("route-to-production-summary")).toHaveTextContent("2 steps left"),
    );
  });
});

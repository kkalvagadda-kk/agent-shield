import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import OverviewScheduled from "./OverviewScheduled";
import type { AgentTrigger, AgentRunItem } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listTriggers: vi.fn(),
  listAgentRuns: vi.fn(),
  enableTrigger: vi.fn(),
  disableTrigger: vi.fn(),
}));

import { listTriggers, listAgentRuns, enableTrigger, disableTrigger } from "../../api/registryApi";

const NOW = new Date().toISOString();

const scheduleTrigger: AgentTrigger = {
  id: "t1",
  agent_id: "ag1",
  trigger_type: "schedule",
  cron_expression: "0 9 * * *",
  timezone: "UTC",
  enabled: true,
  filter_conditions: null,
  alert_email: null,
  alert_on_failure: false,
  created_at: NOW,
  updated_at: NOW,
};

const completedRun: AgentRunItem = {
  id: "run1",
  agent_name: "my-agent",
  status: "completed",
  context: "playground",
  trigger_type: "schedule",
  run_by: null,
  team: null,
  input: null,
  output: null,
  error_message: null,
  latency_ms: 1200,
  cost_usd: 0.01,
  started_at: NOW,
  completed_at: NOW,
  langfuse_trace_id: null,
  trace_url: null,
  production_deployment_id: null,
};

describe("OverviewScheduled", () => {
  beforeEach(() => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (listAgentRuns as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (enableTrigger as ReturnType<typeof vi.fn>).mockResolvedValue(scheduleTrigger);
    (disableTrigger as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...scheduleTrigger,
      enabled: false,
    });
  });

  it("shows empty schedule message when no triggers", async () => {
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);
    await waitFor(() =>
      expect(
        screen.getByText(/no schedule configured/i)
      ).toBeInTheDocument()
    );
  });

  it("shows 'No runs yet' when there are no runs", async () => {
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);
    await waitFor(() =>
      expect(screen.getByText(/no runs yet/i)).toBeInTheDocument()
    );
  });

  it("renders schedule card with cron expression and timezone", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);

    expect(await screen.findByText("0 9 * * *")).toBeInTheDocument();
    expect(screen.getByText(/daily at 09:00 · UTC/i)).toBeInTheDocument();
  });

  it("shows enabled button when trigger is enabled", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);
    expect(await screen.findByRole("button", { name: /enabled/i })).toBeInTheDocument();
  });

  it("shows disabled button when trigger is disabled", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...scheduleTrigger, enabled: false },
    ]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);
    expect(await screen.findByRole("button", { name: /disabled/i })).toBeInTheDocument();
  });

  it("calls disableTrigger when Enabled button is clicked", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);

    await userEvent.click(await screen.findByRole("button", { name: /enabled/i }));

    await waitFor(() =>
      expect(disableTrigger).toHaveBeenCalledWith("my-agent", "t1")
    );
  });

  it("shows last-run status badge when runs exist", async () => {
    (listAgentRuns as ReturnType<typeof vi.fn>).mockResolvedValue([completedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);

    // "completed" may appear in both the Last Run badge and the Recent Runs list
    await waitFor(() => {
      const completedEls = screen.getAllByText("completed");
      expect(completedEls.length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText(/via schedule/i)).toBeInTheDocument();
  });

  it("renders recent runs list when multiple runs exist", async () => {
    const failedRun: AgentRunItem = {
      ...completedRun,
      id: "run2",
      status: "failed",
    };
    (listAgentRuns as ReturnType<typeof vi.fn>).mockResolvedValue([completedRun, failedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" />);

    await waitFor(() => {
      const completed = screen.getAllByText("completed");
      expect(completed.length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("failed")).toBeInTheDocument();
    });
  });
});

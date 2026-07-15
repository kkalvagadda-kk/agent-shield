import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import OverviewScheduled from "./OverviewScheduled";
import type { AgentTrigger, AgentRunItem, AgentHealth } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listTriggers: vi.fn(),
  listDeploymentRuns: vi.fn(),
  enableTrigger: vi.fn(),
  disableTrigger: vi.fn(),
  getAgentHealth: vi.fn(),
}));

import {
  listTriggers,
  listDeploymentRuns,
  enableTrigger,
  disableTrigger,
  getAgentHealth,
} from "../../api/registryApi";

const NOW = new Date().toISOString();
const NEXT_FIRE = new Date(Date.now() + 3600_000).toISOString();

const scheduledHealth: AgentHealth = {
  agent_name: "my-agent",
  mode: "scheduled",
  health: "healthy",
  p95_latency_ms: null,
  error_rate: null,
  runs_24h: null,
  cost_24h: null,
  awaiting_approval_count: null,
  failed_24h: null,
  avg_duration_ms: null,
  last_run_status: "completed",
  next_fire_at: NEXT_FIRE,
  missed_fires: 0,
  match_rate_24h: null,
  rejected_count_24h: null,
};

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
  thread_id: null,
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
  sandbox_deployment_id: null,
  workflow_deployment_id: null,
};

describe("OverviewScheduled", () => {
  beforeEach(() => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (listDeploymentRuns as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (enableTrigger as ReturnType<typeof vi.fn>).mockResolvedValue(scheduleTrigger);
    (disableTrigger as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...scheduleTrigger,
      enabled: false,
    });
    (getAgentHealth as ReturnType<typeof vi.fn>).mockResolvedValue(scheduledHealth);
  });

  it("shows empty schedule message when no triggers", async () => {
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);
    await waitFor(() =>
      expect(
        screen.getByText(/no schedule configured/i)
      ).toBeInTheDocument()
    );
  });

  it("shows 'No runs yet' when there are no runs", async () => {
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);
    await waitFor(() =>
      expect(screen.getByText(/no runs yet/i)).toBeInTheDocument()
    );
  });

  it("renders schedule card with cron expression and timezone", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByText("0 9 * * *")).toBeInTheDocument();
    expect(screen.getByText(/daily at 09:00 · UTC/i)).toBeInTheDocument();
  });

  it("shows enabled button when trigger is enabled", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);
    expect(await screen.findByRole("button", { name: /enabled/i })).toBeInTheDocument();
  });

  it("shows disabled button when trigger is disabled", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...scheduleTrigger, enabled: false },
    ]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);
    expect(await screen.findByRole("button", { name: /disabled/i })).toBeInTheDocument();
  });

  it("calls disableTrigger when Enabled button is clicked", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    await userEvent.click(await screen.findByRole("button", { name: /enabled/i }));

    await waitFor(() =>
      expect(disableTrigger).toHaveBeenCalledWith("my-agent", "t1")
    );
  });

  it("shows last-run status badge when runs exist", async () => {
    (listDeploymentRuns as ReturnType<typeof vi.fn>).mockResolvedValue([completedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

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
    (listDeploymentRuns as ReturnType<typeof vi.fn>).mockResolvedValue([completedRun, failedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    await waitFor(() => {
      const completed = screen.getAllByText("completed");
      expect(completed.length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("failed")).toBeInTheDocument();
    });
  });

  it("renders next-fire timestamp from getAgentHealth", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByText(/next fire/i)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByText(new Date(NEXT_FIRE).toLocaleString())
      ).toBeInTheDocument()
    );
  });

  it("renders schedule health badge reflecting getAgentHealth", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (getAgentHealth as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...scheduledHealth,
      health: "failing",
    });
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByText("failing")).toBeInTheDocument();
  });

  it("shows missed-fires warning when missed_fires > 0", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (getAgentHealth as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...scheduledHealth,
      health: "degraded",
      missed_fires: 3,
    });
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByText(/3 missed fires/i)).toBeInTheDocument();
  });

  it("shows alert-config summary with email and on state", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...scheduleTrigger, alert_on_failure: true, alert_email: "ops@example.com" },
    ]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByText(/failure alerts/i)).toBeInTheDocument();
    expect(screen.getByText("On")).toBeInTheDocument();
    expect(screen.getByText(/notifies ops@example.com/i)).toBeInTheDocument();
  });

  it("shows alert-config summary off + no-email state", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByText(/failure alerts/i)).toBeInTheDocument();
    expect(screen.getByText("Off")).toBeInTheDocument();
    expect(screen.getByText(/no alert email set/i)).toBeInTheDocument();
  });

  it("does not render next-fire/health/alert cards when no schedule", async () => {
    // default beforeEach: listTriggers resolves []
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    await waitFor(() =>
      expect(screen.getByText(/no schedule configured/i)).toBeInTheDocument()
    );
    expect(screen.queryByText(/next fire/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/failure alerts/i)).not.toBeInTheDocument();
  });
});

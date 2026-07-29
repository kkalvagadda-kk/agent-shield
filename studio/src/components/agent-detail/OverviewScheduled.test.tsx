import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import OverviewScheduled from "./OverviewScheduled";
import type { AgentTrigger, AgentRunItem, AgentHealth } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listTriggers: vi.fn(),
  // Runs are read by SCHEDULE, not by deployment. `listDeploymentRuns` filters on
  // the two deployment FK columns, both NULL on every trigger-driven run, so it was
  // permanently empty here — the "No runs yet" half of the bug this suite now guards.
  listTriggerRuns: vi.fn(),
  enableTrigger: vi.fn(),
  disableTrigger: vi.fn(),
  getAgentHealth: vi.fn(),
}));

import {
  listTriggers,
  listTriggerRuns,
  enableTrigger,
  disableTrigger,
  getAgentHealth,
} from "../../api/registryApi";

const NOW = new Date().toISOString();
const NEXT_FIRE = new Date(Date.now() + 3600_000).toISOString();

// The real reason string the fixed dispatch path writes. Asserting on this text
// (rather than "some error appeared") is what proves the operator learns the CAUSE.
const ENV_REASON =
  "agent 'my-agent' has no running production deployment — it is deployed to sandbox. " +
  "Schedule and webhook triggers dispatch to production. Publish the agent " +
  "(Studio: agent page → Publish; requires a passing eval) or deploy it to production " +
  "via the API, then re-enable the trigger.";

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
  last_error: null,
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
  context: "production",
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
  // Both NULL, as on every real trigger-driven run — the reason this surface cannot
  // read runs by deployment.
  production_deployment_id: null,
  sandbox_deployment_id: null,
  workflow_deployment_id: null,
};

const failedRun: AgentRunItem = {
  ...completedRun,
  id: "run2",
  status: "failed",
  error_message: ENV_REASON,
};

describe("OverviewScheduled", () => {
  beforeEach(() => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (listTriggerRuns as ReturnType<typeof vi.fn>).mockResolvedValue([]);
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

  it("reads runs by TRIGGER, not by deployment", async () => {
    // The wiring assertion. If this ever reverts to a deployment-scoped read, the
    // card goes permanently empty again while the badge keeps reporting failures —
    // a silent regression that renders as "No runs yet" rather than as an error.
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (listTriggerRuns as ReturnType<typeof vi.fn>).mockResolvedValue([completedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    await waitFor(() =>
      expect(listTriggerRuns).toHaveBeenCalledWith("my-agent", "t1", { limit: 10 })
    );
  });

  it("does not fetch runs before a schedule is known", async () => {
    // Guards against firing the request with an undefined trigger id, which would
    // 404 on every mount for an agent whose triggers have not loaded yet.
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);
    await waitFor(() => expect(screen.getByText(/no schedule configured/i)).toBeInTheDocument());
    expect(listTriggerRuns).not.toHaveBeenCalled();
  });

  it("shows last-run status badge when runs exist", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (listTriggerRuns as ReturnType<typeof vi.fn>).mockResolvedValue([completedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    // "completed" may appear in both the Last Run badge and the Recent Runs list
    await waitFor(() => {
      const completedEls = screen.getAllByText("completed");
      expect(completedEls.length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText(/via schedule/i)).toBeInTheDocument();
  });

  it("renders recent runs list when multiple runs exist", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (listTriggerRuns as ReturnType<typeof vi.fn>).mockResolvedValue([completedRun, failedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    await waitFor(() => {
      const completed = screen.getAllByText("completed");
      expect(completed.length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("failed")).toBeInTheDocument();
    });
  });

  it("renders the failure reason on the last run", async () => {
    // `error_message` was in the payload the whole time and simply never rendered.
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (listTriggerRuns as ReturnType<typeof vi.fn>).mockResolvedValue([failedRun]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    const reason = await screen.findByTestId("last-run-error");
    expect(reason).toHaveTextContent(/no running production deployment/i);
    expect(reason).toHaveTextContent(/deployed to sandbox/i);
  });

  it("explains a failing badge from health.last_error even with no run rows", async () => {
    // Belt and braces: if the run list is empty for any reason, the badge must still
    // carry its own explanation rather than being the only red thing on screen.
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (listTriggerRuns as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (getAgentHealth as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...scheduledHealth,
      health: "failing",
      last_run_status: "failed",
      last_error: ENV_REASON,
    });
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByTestId("schedule-health-reason")).toHaveTextContent(
      /no running production deployment/i
    );
  });

  it("REGRESSION: never shows a failing badge next to 'No runs yet' with no reason", async () => {
    // THE bug, as one assertion. The badge came from getAgentHealth (all runs for the
    // agent) and the list from a deployment-scoped read (always empty for triggers),
    // so the screen said "Failing" directly above "No runs yet" and offered no reason.
    // Both now resolve from the schedule, so a red badge implies a visible failed run
    // AND a visible cause. One mock dataset feeds both; they must not contradict.
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    (listTriggerRuns as ReturnType<typeof vi.fn>).mockResolvedValue([failedRun]);
    (getAgentHealth as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...scheduledHealth,
      health: "failing",
      last_run_status: "failed",
      last_error: ENV_REASON,
    });
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    // Settle BOTH queries before judging. The badge (health) resolves before the run
    // list, so a bare assertion here would trip on the loading frame rather than on
    // the defect — awaiting the reason is what makes this a claim about steady state.
    expect(await screen.findByTestId("last-run-error")).toHaveTextContent(
      /no running production deployment/i
    );
    expect(screen.getByText("failing")).toBeInTheDocument();
    // The contradiction itself: a failing schedule that reports no runs.
    expect(screen.queryByText(/no runs yet/i)).not.toBeInTheDocument();
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

  it("warns when alerts are on but no email is set (undeliverable)", async () => {
    // alerting.py returns at `if not trigger.alert_email` and logs at debug, so this
    // configuration notifies nobody. Rendering it as a plain green "On" told the
    // operator they were covered when no alert could ever be sent.
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...scheduleTrigger, alert_on_failure: true, alert_email: null },
    ]);
    renderWithProviders(<OverviewScheduled agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByTestId("alert-config-incomplete")).toHaveTextContent(
      /not delivered/i
    );
    expect(screen.getByText(/notify nobody/i)).toBeInTheDocument();
    // The misleading bare "On" must be gone, not merely supplemented.
    expect(screen.queryByText(/^On$/)).not.toBeInTheDocument();
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

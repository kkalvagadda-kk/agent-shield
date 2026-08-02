import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import SchedulesPage from "./SchedulesPage";
import type { ScheduleListItem } from "../api/registryApi";
import * as api from "../api/registryApi";

vi.mock("../api/registryApi");
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function row(over: Partial<ScheduleListItem> = {}): ScheduleListItem {
  return {
    trigger_id: "t-1",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "a-1",
    artifact_name: "healthy-agent",
    artifact_team: "platform",
    artifact_status: "active",
    cron_expression: "0 9 * * *",
    timezone: "UTC",
    next_fire_at: new Date(Date.now() + 3_600_000).toISOString(),
    input_payload: null,
    enabled: true,
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    will_fire: true,
    why_not: null,
    last_run_id: "r-1",
    last_run_status: "completed",
    last_run_at: new Date(Date.now() - 3_600_000).toISOString(),
    last_run_error: null,
    recent_runs: [],
    alert_email: null,
    alert_on_failure: false,
    ...over,
  };
}

// The row the page was built for: armed, switched on, and dead — a schedule on an
// archived workflow that nothing will ever run.
const ZOMBIE = row({
  trigger_id: "zw-01",
  artifact_kind: "workflow",
  artifact_id: "wf-01",
  artifact_name: "s71-sequential-5c6c93",
  artifact_status: "archived",
  cron_expression: "0 0 * * *",
  will_fire: false,
  why_not: "workflow is not published",
  next_fire_at: null,
  last_run_status: "failed",
  last_run_error: "",
});

const DISARMED = row({
  trigger_id: "zw-10",
  artifact_kind: "workflow",
  artifact_id: "wf-10",
  artifact_name: "legacy-digest-flow",
  artifact_status: "archived",
  enabled: false,
  disarmed_at: "2026-07-27T14:22:00Z",
  disarm_reason: "workflow archived",
  will_fire: false,
  why_not: "workflow archived",
});

const SANDBOX_FAIL = row({
  trigger_id: "ag-01",
  artifact_name: "deamon-agent-test",
  cron_expression: "0 * * * *",
  last_run_status: "failed",
  last_run_error: "dispatch failed: [Errno -2] Name or service not known",
});

// Disarmed by hand, no recorded reason — off on purpose, so not an alarm.
const PAUSED = row({
  trigger_id: "ag-05",
  artifact_name: "nightly-reconcile",
  enabled: false,
  will_fire: false,
  why_not: "this schedule is disabled",
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listSchedules).mockResolvedValue([]);
});

describe("SchedulesPage", () => {
  it("shows a loading state, then the table", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row()]);
    renderWithProviders(<SchedulesPage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByTestId("schedules-row-t-1")).toBeInTheDocument();
  });

  it("shows an empty state that points at where schedules are authored", async () => {
    renderWithProviders(<SchedulesPage />);
    const empty = await screen.findByTestId("schedules-empty");
    // The old OverviewScheduled empty state said "add one in Settings" and was a
    // dead end. This one at least names both authoring surfaces.
    expect(empty).toHaveTextContent(/Settings tab|workflow builder/i);
  });

  it("renders agent and workflow rows in one table", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row(), ZOMBIE]);
    renderWithProviders(<SchedulesPage />);
    expect(await screen.findByText("healthy-agent")).toBeInTheDocument();
    expect(screen.getByText("s71-sequential-5c6c93")).toBeInTheDocument();
    expect(screen.getByText("archived")).toBeInTheDocument();
  });

  it("distinguishes armed from disarmed and shows the recorded reason", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row(), DISARMED]);
    renderWithProviders(<SchedulesPage />);
    const armed = within(await screen.findByTestId("schedules-row-t-1"));
    expect(armed.getByTestId("schedule-armed-badge")).toHaveTextContent("Armed");
    expect(armed.getByTestId("schedule-armed-detail")).toHaveTextContent("by kalyan");

    const disarmed = within(screen.getByTestId("schedules-row-zw-10"));
    expect(disarmed.getByTestId("schedule-armed-badge")).toHaveTextContent("Disarmed");
    expect(disarmed.getByTestId("schedule-disarm-reason")).toHaveTextContent("workflow archived");
  });

  it("explains why an armed schedule will not fire", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([ZOMBIE]);
    renderWithProviders(<SchedulesPage />);
    const r = within(await screen.findByTestId("schedules-row-zw-01"));
    // Armed AND blocked is the exact state that was invisible before this page.
    expect(r.getByTestId("schedule-armed-badge")).toHaveTextContent("Armed");
    expect(r.getByTestId("schedule-will-not-fire")).toHaveTextContent("workflow is not published");
  });

  it("banners only the schedules nobody chose to switch off", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row(), ZOMBIE, DISARMED, PAUSED]);
    renderWithProviders(<SchedulesPage />);
    const banner = await screen.findByTestId("schedules-attention-banner");
    // Only ZOMBIE counts. The healthy row fires; PAUSED is off on purpose; DISARMED
    // carries a recorded reason, so its state was chosen rather than overlooked.
    expect(banner).toHaveTextContent("1 schedule will not fire");
  });

  it("drops a row out of the attention count once it is disarmed", async () => {
    // Regression: the badge and banner stayed put after a disarm, because a disarmed
    // trigger is still `enabled` and still not firing.
    vi.mocked(api.listSchedules)
      .mockResolvedValueOnce([ZOMBIE])
      .mockResolvedValue([{ ...ZOMBIE, enabled: false, disarm_reason: "disarmed by operator" }]);
    vi.mocked(api.updateWorkflowTrigger).mockResolvedValue({} as never);
    renderWithProviders(<SchedulesPage />);
    const user = userEvent.setup();

    expect(await screen.findByTestId("schedules-attention-banner")).toHaveTextContent(
      "1 schedule will not fire",
    );
    await user.click(screen.getByTestId("schedule-arm-toggle"));
    await waitFor(() =>
      expect(screen.queryByTestId("schedules-attention-banner")).not.toBeInTheDocument(),
    );
  });

  it("hides the banner when every schedule is healthy", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row()]);
    renderWithProviders(<SchedulesPage />);
    await screen.findByTestId("schedules-row-t-1");
    expect(screen.queryByTestId("schedules-attention-banner")).not.toBeInTheDocument();
  });

  it("renders the failure reason on a failed run", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([SANDBOX_FAIL]);
    renderWithProviders(<SchedulesPage />);
    expect(await screen.findByTestId("schedule-last-run-error")).toHaveTextContent(
      "Name or service not known",
    );
  });

  it("says so explicitly when a failed run recorded no reason", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([ZOMBIE]);
    renderWithProviders(<SchedulesPage />);
    // The workflow run path writes an empty error_message. Rendering blank there
    // reproduces the original "Failing / No runs yet" dead end.
    expect(await screen.findByTestId("schedule-last-run-error")).toHaveTextContent(
      /no reason recorded/i,
    );
  });

  it("partitions rows across the four filters", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row(), ZOMBIE, DISARMED, PAUSED]);
    renderWithProviders(<SchedulesPage />);
    const user = userEvent.setup();
    await screen.findByTestId("schedules-row-t-1");

    await user.click(screen.getByTestId("schedules-filter-firing"));
    expect(screen.getByTestId("schedules-row-t-1")).toBeInTheDocument();
    expect(screen.queryByTestId("schedules-row-zw-01")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("schedules-filter-attention"));
    expect(screen.getByTestId("schedules-row-zw-01")).toBeInTheDocument();
    expect(screen.queryByTestId("schedules-row-t-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("schedules-row-ag-05")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("schedules-filter-disarmed"));
    expect(screen.getByTestId("schedules-row-zw-10")).toBeInTheDocument();
    expect(screen.queryByTestId("schedules-row-zw-01")).not.toBeInTheDocument();
  });

  it("shows one arm control per row, labelled for the direction it moves", async () => {
    // ONE control, not two. The page shipped with a separate Disarm button beside
    // this toggle; it PATCHed `{ armed: false }`, a field the API does not declare,
    // so it answered 200 having written nothing while the toast claimed success.
    vi.mocked(api.listSchedules).mockResolvedValue([row(), DISARMED]);
    renderWithProviders(<SchedulesPage />);
    const armed = within(await screen.findByTestId("schedules-row-t-1"));
    expect(armed.getByTestId("schedule-arm-toggle")).toHaveAttribute(
      "title",
      expect.stringContaining("Disarm"),
    );
    expect(armed.queryByTestId("schedule-disarm-btn")).not.toBeInTheDocument();

    const disarmed = within(screen.getByTestId("schedules-row-zw-10"));
    expect(disarmed.getByTestId("schedule-arm-toggle")).toHaveAttribute(
      "title",
      expect.stringContaining("Arm"),
    );
  });

  it("disarms an agent row through the trigger enable/disable endpoint", async () => {
    // `enabled` IS arm state — there is no `armed` field on AgentTriggerUpdate, so a
    // disarm that does not move this column does not move anything.
    vi.mocked(api.listSchedules).mockResolvedValue([row()]);
    vi.mocked(api.disableTrigger).mockResolvedValue({} as never);
    renderWithProviders(<SchedulesPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("schedule-arm-toggle"));
    await waitFor(() => expect(api.disableTrigger).toHaveBeenCalledWith("healthy-agent", "t-1"));
    expect(api.updateWorkflowTrigger).not.toHaveBeenCalled();
  });

  it("re-arms a disarmed row through the enable endpoint", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row({ enabled: false, will_fire: false })]);
    vi.mocked(api.enableTrigger).mockResolvedValue({} as never);
    renderWithProviders(<SchedulesPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("schedule-arm-toggle"));
    await waitFor(() => expect(api.enableTrigger).toHaveBeenCalledWith("healthy-agent", "t-1"));
  });

  it("routes a workflow row to the workflow trigger endpoint, keyed on id", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([ZOMBIE]);
    vi.mocked(api.updateWorkflowTrigger).mockResolvedValue({} as never);
    renderWithProviders(<SchedulesPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("schedule-arm-toggle"));
    // Keyed on artifact_id, not the display name — the workflow router takes an id.
    await waitFor(() =>
      expect(api.updateWorkflowTrigger).toHaveBeenCalledWith("wf-01", "zw-01", { enabled: false }),
    );
    expect(api.disableTrigger).not.toHaveBeenCalled();
  });

  it("requires confirmation before deleting, and then calls deleteTrigger", async () => {
    vi.mocked(api.listSchedules).mockResolvedValue([row()]);
    vi.mocked(api.deleteTrigger).mockResolvedValue(undefined);
    renderWithProviders(<SchedulesPage />);
    const user = userEvent.setup();

    await user.click(await screen.findByTestId("schedule-delete-btn"));
    expect(api.deleteTrigger).not.toHaveBeenCalled();
    expect(screen.getByText("Delete this schedule?")).toBeInTheDocument();

    await user.click(screen.getByTestId("schedule-delete-confirm"));
    await waitFor(() => expect(api.deleteTrigger).toHaveBeenCalledWith("healthy-agent", "t-1"));
  });
});

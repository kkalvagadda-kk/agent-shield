import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import EditScheduleModal from "./EditScheduleModal";
import * as api from "../../api/registryApi";
import type { ScheduleListItem } from "../../api/registryApi";

vi.mock("../../api/registryApi");
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const mock = <T,>(fn: T) => fn as unknown as ReturnType<typeof vi.fn>;

function row(over: Partial<ScheduleListItem> = {}): ScheduleListItem {
  return {
    trigger_id: "t-1",
    trigger_type: "schedule",
    artifact_kind: "agent",
    artifact_id: "a-1",
    artifact_name: "nightly-digest",
    artifact_team: "platform",
    artifact_status: "active",
    cron_expression: "0 9 * * 1",
    timezone: "UTC",
    next_fire_at: null,
    input_payload: null,
    enabled: true,
    armed_by: "kalyan",
    disarmed_at: null,
    disarm_reason: null,
    will_fire: true,
    why_not: null,
    last_run_id: null,
    last_run_status: null,
    last_run_at: null,
    last_run_error: null,
    recent_runs: [],
    alert_email: null,
    alert_on_failure: false,
    ...over,
  } as ScheduleListItem;
}

describe("EditScheduleModal", () => {
  beforeEach(() => vi.clearAllMocks());

  it("saves cron + timezone through the AGENT trigger router", async () => {
    // The schedules endpoint is read-only on purpose: every mutation routes back to
    // the existing artifact-scoped router, so there is never a second writer.
    mock(api.updateTrigger).mockResolvedValue({} as never);
    renderWithProviders(<EditScheduleModal schedule={row()} onClose={() => {}} />);
    const user = userEvent.setup();

    const cron = screen.getByTestId("edit-schedule-cron");
    await user.clear(cron);
    await user.type(cron, "30 6 * * *");
    await user.click(screen.getByTestId("edit-schedule-save"));

    await waitFor(() =>
      expect(api.updateTrigger).toHaveBeenCalledWith("nightly-digest", "t-1", {
        cron_expression: "30 6 * * *",
        timezone: "UTC",
        input_payload: null,
      }),
    );
    expect(api.updateWorkflowTrigger).not.toHaveBeenCalled();
  });

  it("routes a workflow row to the workflow router, keyed on id not name", async () => {
    mock(api.updateWorkflowTrigger).mockResolvedValue({} as never);
    renderWithProviders(
      <EditScheduleModal
        schedule={row({ artifact_kind: "workflow", artifact_id: "wf-9", artifact_name: "digest-flow" })}
        onClose={() => {}}
      />,
    );
    await userEvent.setup().click(screen.getByTestId("edit-schedule-save"));
    await waitFor(() =>
      expect(api.updateWorkflowTrigger).toHaveBeenCalledWith(
        "wf-9", "t-1", expect.objectContaining({ cron_expression: "0 9 * * 1" }),
      ),
    );
    expect(api.updateTrigger).not.toHaveBeenCalled();
  });

  it("refuses to submit malformed JSON, and names the parse error", async () => {
    // Sending it would land as a 422 the operator has to decode. Catching it here
    // keeps the message in the field it belongs to.
    renderWithProviders(<EditScheduleModal schedule={row()} onClose={() => {}} />);
    const user = userEvent.setup();
    // `{` opens a keyboard descriptor in userEvent.type — `{{` types a literal brace.
    await user.type(screen.getByTestId("edit-schedule-payload"), "{{not json");
    await user.click(screen.getByTestId("edit-schedule-save"));

    expect(await screen.findByTestId("edit-schedule-payload-error")).toBeInTheDocument();
    expect(api.updateTrigger).not.toHaveBeenCalled();
  });

  it("blocks save on a cron that is not five fields, and says how many it saw", async () => {
    renderWithProviders(<EditScheduleModal schedule={row()} onClose={() => {}} />);
    const user = userEvent.setup();
    const cron = screen.getByTestId("edit-schedule-cron");
    await user.clear(cron);
    await user.type(cron, "0 9 *");
    expect(screen.getByTestId("edit-schedule-save")).toBeDisabled();
    expect(screen.getByText(/3 fields — a cron expression has 5/i)).toBeInTheDocument();
  });

  it("keeps a timezone that is not in the shortlist selectable", async () => {
    // Opening the modal must not silently rewrite a value the operator never touched.
    renderWithProviders(
      <EditScheduleModal schedule={row({ timezone: "Pacific/Auckland" })} onClose={() => {}} />,
    );
    expect(screen.getByLabelText("Timezone")).toHaveValue("Pacific/Auckland");
  });

  it("round-trips an existing payload rather than dropping it", async () => {
    mock(api.updateTrigger).mockResolvedValue({} as never);
    renderWithProviders(
      <EditScheduleModal schedule={row({ input_payload: { task: "weekly" } })} onClose={() => {}} />,
    );
    await userEvent.setup().click(screen.getByTestId("edit-schedule-save"));
    await waitFor(() =>
      expect(api.updateTrigger).toHaveBeenCalledWith(
        "nightly-digest", "t-1", expect.objectContaining({ input_payload: { task: "weekly" } }),
      ),
    );
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import WorkflowTriggersPanel from "./WorkflowTriggersPanel";
import type { AgentTrigger } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listWorkflowTriggers: vi.fn(),
  createWorkflowTrigger: vi.fn(),
  updateWorkflowTrigger: vi.fn(),
  deleteWorkflowTrigger: vi.fn(),
  rotateWorkflowToken: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listWorkflowTriggers,
  createWorkflowTrigger,
} from "../../api/registryApi";

const NOW = new Date().toISOString();

const scheduleTrigger: AgentTrigger = {
  id: "t1",
  agent_id: null,
  workflow_id: "wf-1",
  trigger_type: "schedule",
  cron_expression: "0 9 * * 1",
  timezone: "UTC",
  enabled: true,
  filter_conditions: null,
  alert_email: null,
  alert_on_failure: false,
  created_at: NOW,
  updated_at: NOW,
};

describe("WorkflowTriggersPanel", () => {
  beforeEach(() => {
    (listWorkflowTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (createWorkflowTrigger as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "t-new",
      agent_id: null,
      workflow_id: "wf-1",
      trigger_type: "schedule",
      cron_expression: "0 9 * * 1",
      timezone: "UTC",
      enabled: true,
      filter_conditions: null,
      alert_email: null,
      alert_on_failure: false,
      created_at: NOW,
      updated_at: NOW,
    });
  });

  it("calls listWorkflowTriggers with workflowId and renders existing trigger", async () => {
    (listWorkflowTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(
      <WorkflowTriggersPanel workflowId="wf-1" workflowName="Test Workflow" onClose={vi.fn()} />
    );
    await waitFor(() =>
      expect(listWorkflowTriggers).toHaveBeenCalledWith("wf-1")
    );
    // The cron expression appears in the ScheduleRow.
    expect(await screen.findByText("0 9 * * 1")).toBeInTheDocument();
  });

  it("creates a schedule trigger via the New schedule form", async () => {
    renderWithProviders(
      <WorkflowTriggersPanel workflowId="wf-1" workflowName="Test Workflow" onClose={vi.fn()} />
    );
    // Wait for panel to finish loading.
    await screen.findByRole("button", { name: /new schedule/i });
    await userEvent.click(screen.getByRole("button", { name: /new schedule/i }));

    // Submit with default values (cron="0 9 * * 1", timezone="UTC").
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() =>
      expect(createWorkflowTrigger).toHaveBeenCalledWith(
        "wf-1",
        expect.objectContaining({
          trigger_type: "schedule",
          cron_expression: "0 9 * * 1",
          timezone: "UTC",
        })
      )
    );
  });

  it("creates a webhook trigger and shows the one-time URL", async () => {
    (createWorkflowTrigger as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "trig-1",
      agent_id: null,
      workflow_id: "wf-1",
      trigger_type: "webhook",
      cron_expression: null,
      timezone: null,
      enabled: true,
      filter_conditions: [],
      alert_email: null,
      alert_on_failure: false,
      webhook_url: "https://example.com/hook/abc",
      created_at: NOW,
      updated_at: NOW,
    });

    renderWithProviders(
      <WorkflowTriggersPanel workflowId="wf-1" workflowName="Test Workflow" onClose={vi.fn()} />
    );

    await screen.findByRole("button", { name: /new webhook/i });
    await userEvent.click(screen.getByRole("button", { name: /new webhook/i }));

    // Submit with the default empty-filter form.
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() =>
      expect(createWorkflowTrigger).toHaveBeenCalledWith(
        "wf-1",
        expect.objectContaining({ trigger_type: "webhook" })
      )
    );
    // The one-time URL is shown after creation.
    expect(
      await screen.findByText("https://example.com/hook/abc")
    ).toBeInTheDocument();
  });
});

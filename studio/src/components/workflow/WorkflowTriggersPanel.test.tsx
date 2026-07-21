import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import WorkflowTriggersPanel from "./WorkflowTriggersPanel";
import type { AgentTrigger, ArtifactRoleGrant, Application } from "../../api/registryApi";

// The panel now composes the SAME shared ArtifactGrantsList + InvokeAccessPanel
// as the agent surface (Decision 30 parity, T019/T025) — so it imports the
// applications + artifact_role_grants API, which the mock must provide.
vi.mock("../../api/registryApi", () => ({
  listWorkflowTriggers: vi.fn(),
  createWorkflowTrigger: vi.fn(),
  updateWorkflowTrigger: vi.fn(),
  deleteWorkflowTrigger: vi.fn(),
  rotateWorkflowToken: vi.fn(),
  listApplications: vi.fn(),
  listArtifactGrants: vi.fn(),
  createArtifactGrant: vi.fn(),
  revokeArtifactGrant: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listWorkflowTriggers,
  createWorkflowTrigger,
  listApplications,
  listArtifactGrants,
  createArtifactGrant,
  revokeArtifactGrant,
} from "../../api/registryApi";

const NOW = new Date().toISOString();
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const renderPanel = () =>
  renderWithProviders(
    <WorkflowTriggersPanel
      workflowId="wf-1"
      workflowName="Test Workflow"
      workflowTeam="platform"
      onClose={vi.fn()}
    />
  );

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

const webhookTrigger: AgentTrigger = {
  id: "t2",
  agent_id: null,
  workflow_id: "wf-1",
  trigger_type: "webhook",
  cron_expression: null,
  timezone: null,
  enabled: true,
  filter_conditions: null,
  alert_email: null,
  alert_on_failure: false,
  auth_mode: "client_signed",
  created_at: NOW,
  updated_at: NOW,
};

const billingApp: Application = {
  id: "app1",
  team_name: "platform",
  name: "billing-service",
  enabled: true,
  created_by: "admin",
  created_at: NOW,
  rotated_at: null,
};

const invokerGrant: ArtifactRoleGrant = {
  id: "g-inv",
  artifact_type: "workflow",
  artifact_id: "wf-1",
  role: "invoker",
  grantee_type: "application",
  grantee_id: "app1",
  granted_by: "admin",
  granted_at: NOW,
  revoked_at: null,
  grantee_label: "billing-service",
};

describe("WorkflowTriggersPanel", () => {
  beforeEach(() => {
    mock(listWorkflowTriggers).mockResolvedValue([]);
    mock(createWorkflowTrigger).mockResolvedValue({
      ...scheduleTrigger,
      id: "t-new",
    });
    mock(listApplications).mockResolvedValue([]);
    mock(listArtifactGrants).mockResolvedValue([]);
    mock(createArtifactGrant).mockResolvedValue(invokerGrant);
    mock(revokeArtifactGrant).mockResolvedValue(undefined);
  });

  it("calls listWorkflowTriggers with workflowId and renders existing trigger", async () => {
    mock(listWorkflowTriggers).mockResolvedValue([scheduleTrigger]);
    renderPanel();
    await waitFor(() => expect(listWorkflowTriggers).toHaveBeenCalledWith("wf-1"));
    expect(await screen.findByText("0 9 * * 1")).toBeInTheDocument();
  });

  it("creates a schedule trigger via the New schedule form", async () => {
    renderPanel();
    await screen.findByRole("button", { name: /new schedule/i });
    await userEvent.click(screen.getByRole("button", { name: /new schedule/i }));
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
    mock(createWorkflowTrigger).mockResolvedValue({
      ...webhookTrigger,
      id: "trig-1",
      filter_conditions: [],
      webhook_url: "https://example.com/hook/abc",
    });

    renderPanel();
    await screen.findByRole("button", { name: /new webhook/i });
    await userEvent.click(screen.getByRole("button", { name: /new webhook/i }));
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() =>
      expect(createWorkflowTrigger).toHaveBeenCalledWith(
        "wf-1",
        expect.objectContaining({ trigger_type: "webhook" })
      )
    );
    expect(await screen.findByText("https://example.com/hook/abc")).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Decision 30 parity — the SAME shared components work under artifactType="workflow"
  // ---------------------------------------------------------------------------
  describe("invoke access parity (Decision 30)", () => {
    it("reads grants for the workflow artifact", async () => {
      mock(listWorkflowTriggers).mockResolvedValue([webhookTrigger]);
      renderPanel();
      await waitFor(() => expect(listArtifactGrants).toHaveBeenCalledWith("workflow", "wf-1"));
    });

    it("InvokeAccessPanel shows the empty state when the team has no applications", async () => {
      mock(listWorkflowTriggers).mockResolvedValue([webhookTrigger]);
      renderPanel();
      expect(await screen.findByTestId("invoke-empty-state")).toHaveTextContent(
        /no applications registered for your team yet/i
      );
    });

    it("lists an application's invoker grant on the workflow", async () => {
      mock(listWorkflowTriggers).mockResolvedValue([webhookTrigger]);
      mock(listApplications).mockResolvedValue([billingApp]);
      mock(listArtifactGrants).mockResolvedValue([invokerGrant]);
      renderPanel();
      expect(await screen.findByTestId("invoker-grant-app1")).toHaveTextContent("billing-service");
    });

    it("revokes a grant via revokeArtifactGrant on the workflow artifact", async () => {
      mock(listArtifactGrants).mockResolvedValue([invokerGrant]);
      renderPanel();
      await userEvent.click(
        await screen.findByRole("button", { name: /revoke invoker for billing-service/i })
      );
      await waitFor(() =>
        expect(revokeArtifactGrant).toHaveBeenCalledWith("workflow", "wf-1", "g-inv")
      );
    });
  });
});

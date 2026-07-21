import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import SettingsTab from "./SettingsTab";
import type { AgentTrigger, ArtifactRoleGrant, Application } from "../../api/registryApi";

// SettingsTab now composes the shared ArtifactGrantsList + InvokeAccessPanel
// (Decision 30). The retired per-trigger ClientPanel (createTriggerClient etc.)
// is gone — its coverage is replaced by the invoke-access block below, which
// drives the new applications + artifact_role_grants surfaces the component
// actually renders now.
vi.mock("../../api/registryApi", () => ({
  listTriggers: vi.fn(),
  updateTrigger: vi.fn(),
  rotateToken: vi.fn(),
  createTrigger: vi.fn(),
  updateAgent: vi.fn(),
  listApplications: vi.fn(),
  listArtifactGrants: vi.fn(),
  createArtifactGrant: vi.fn(),
  revokeArtifactGrant: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listTriggers, updateTrigger, rotateToken, createTrigger, updateAgent,
  listApplications, listArtifactGrants, createArtifactGrant, revokeArtifactGrant,
} from "../../api/registryApi";

const NOW = new Date().toISOString();
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

// Every render needs the two new required props (agentId/agentTeam); a helper
// keeps that in one place so a prop change is one edit, not thirty.
const renderTab = (props: Partial<{ agentName: string; memoryEnabled: boolean }> = {}) =>
  renderWithProviders(
    <SettingsTab agentName="my-agent" agentId="ag1" agentTeam="platform" {...props} />
  );

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

const webhookTrigger: AgentTrigger = {
  id: "t2",
  agent_id: "ag1",
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
  created_by: "75c7c8b3-admin-sub",
  created_at: NOW,
  rotated_at: null,
};

const invokerGrant: ArtifactRoleGrant = {
  id: "g-inv",
  artifact_type: "agent",
  artifact_id: "ag1",
  role: "invoker",
  grantee_type: "application",
  grantee_id: "app1",
  granted_by: "75c7c8b3-admin-sub",
  granted_at: NOW,
  revoked_at: null,
  grantee_label: "billing-service",
};

describe("SettingsTab", () => {
  beforeEach(() => {
    mock(listTriggers).mockResolvedValue([]);
    mock(updateTrigger).mockResolvedValue(scheduleTrigger);
    mock(rotateToken).mockResolvedValue({
      trigger_id: "t2",
      token: "tok123",
      webhook_url: "https://example.com/hooks/my-agent/tok123",
    });
    mock(createTrigger).mockResolvedValue({
      ...webhookTrigger,
      token: "newtok",
      webhook_url: "https://example.com/hooks/my-agent/newtok",
    });
    mock(updateAgent).mockResolvedValue({});
    mock(listApplications).mockResolvedValue([]);
    mock(listArtifactGrants).mockResolvedValue([]);
    mock(createArtifactGrant).mockResolvedValue(invokerGrant);
    mock(revokeArtifactGrant).mockResolvedValue(undefined);
  });

  it("shows empty-state copy when there are no triggers", async () => {
    renderTab();
    await waitFor(() => {
      expect(screen.getByText(/no schedule triggers configured/i)).toBeInTheDocument();
      expect(screen.getByText(/no webhook triggers configured/i)).toBeInTheDocument();
    });
  });

  it("renders a schedule trigger row with cron expression and timezone", async () => {
    mock(listTriggers).mockResolvedValue([scheduleTrigger]);
    renderTab();

    const cronInput = await screen.findByPlaceholderText("* * * * *");
    expect((cronInput as HTMLInputElement).value).toBe("0 9 * * *");

    const tzSelect = screen.getAllByRole("combobox")[0];
    expect((tzSelect as HTMLSelectElement).value).toBe("UTC");

    const enabledCheckbox = screen.getByRole("checkbox", { name: /enabled/i });
    expect((enabledCheckbox as HTMLInputElement).checked).toBe(true);
  });

  it("calls updateTrigger when Save is clicked", async () => {
    mock(listTriggers).mockResolvedValue([scheduleTrigger]);
    renderTab();

    await screen.findByPlaceholderText("* * * * *");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(updateTrigger).toHaveBeenCalledWith(
        "my-agent",
        "t1",
        expect.objectContaining({ cron_expression: "0 9 * * *", timezone: "UTC" })
      )
    );
  });

  it("sends approver_role when updating a schedule trigger (WS-2 T014)", async () => {
    mock(listTriggers).mockResolvedValue([scheduleTrigger]);
    renderTab();

    await screen.findByPlaceholderText("* * * * *");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(updateTrigger).toHaveBeenCalledWith(
        "my-agent",
        "t1",
        expect.objectContaining({ approver_role: "agent:reviewer" })
      )
    );
  });

  it("shows the authorizing human (armed_by) on an armed trigger (WS-2 T014)", async () => {
    mock(listTriggers).mockResolvedValue([
      { ...scheduleTrigger, armed_by: "75c7c8b3-armed-sub", approver_role: "team:reviewer" },
    ]);
    renderTab();

    expect(await screen.findByText(/75c7c8b3-armed-sub/)).toBeInTheDocument();
  });

  it("sends approver_role when creating a schedule trigger (WS-2 T014)", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /new schedule trigger/i }));
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "my-agent",
        expect.objectContaining({ trigger_type: "schedule", approver_role: "agent:reviewer" })
      )
    );
  });

  it("renders a webhook trigger row with Rotate Token button", async () => {
    mock(listTriggers).mockResolvedValue([webhookTrigger]);
    renderTab();

    expect(await screen.findByRole("button", { name: /rotate token/i })).toBeInTheDocument();
  });

  it("reveals webhook URL after Rotate Token is clicked", async () => {
    mock(listTriggers).mockResolvedValue([webhookTrigger]);
    renderTab();

    await userEvent.click(await screen.findByRole("button", { name: /rotate token/i }));

    await waitFor(() => expect(rotateToken).toHaveBeenCalledWith("my-agent", "t2"));
    expect(
      await screen.findByText("https://example.com/hooks/my-agent/tok123")
    ).toBeInTheDocument();
  });

  it("renders both sections (Schedule + Webhook) when both trigger types exist", async () => {
    mock(listTriggers).mockResolvedValue([scheduleTrigger, webhookTrigger]);
    renderTab();

    expect(await screen.findByText(/schedule triggers/i)).toBeInTheDocument();
    expect(screen.getByText(/webhook triggers/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /rotate token/i })).toBeInTheDocument();
  });

  it("has New schedule/webhook trigger buttons", async () => {
    renderTab();
    expect(await screen.findByRole("button", { name: /new schedule trigger/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new webhook trigger/i })).toBeInTheDocument();
  });

  it("creates a schedule trigger from the new-trigger form", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /new schedule trigger/i }));
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "my-agent",
        expect.objectContaining({ trigger_type: "schedule" })
      )
    );
  });

  it("new-schedule form sends input_payload when provided", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /new schedule trigger/i }));
    fireEvent.change(screen.getByPlaceholderText(/weekly-report/), {
      target: { value: '{"task":"nightly"}' },
    });
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "my-agent",
        expect.objectContaining({ trigger_type: "schedule", input_payload: { task: "nightly" } })
      )
    );
  });

  it("creates a webhook trigger and shows the one-time URL", async () => {
    renderTab();
    await userEvent.click(await screen.findByRole("button", { name: /new webhook trigger/i }));
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "my-agent",
        expect.objectContaining({ trigger_type: "webhook" })
      )
    );
    expect(
      await screen.findByText("https://example.com/hooks/my-agent/newtok")
    ).toBeInTheDocument();
  });

  it("toggles memory via updateAgent", async () => {
    renderTab({ memoryEnabled: false });
    const memoryToggle = await screen.findByRole("checkbox", { name: /enable memory/i });
    await userEvent.click(memoryToggle);
    await waitFor(() =>
      expect(updateAgent).toHaveBeenCalledWith("my-agent", { memory_enabled: true })
    );
  });

  // -------------------------------------------------------------------------
  // Decision 30 — auth_mode display (WebhookRow) + invoke-access via
  // applications + artifact_role_grants (InvokeAccessPanel / ArtifactGrantsList)
  // -------------------------------------------------------------------------
  describe("auth mode + invoke access (Decision 30)", () => {
    it("shows the trigger's auth_mode so an operator can see which door is open", async () => {
      mock(listTriggers).mockResolvedValue([webhookTrigger]);
      renderTab();
      expect(await screen.findByText("client_signed")).toBeInTheDocument();
    });

    it("falls back to token mode display for a pre-cutover trigger", async () => {
      mock(listTriggers).mockResolvedValue([{ ...webhookTrigger, auth_mode: undefined }]);
      renderTab();
      expect(await screen.findByText("token")).toBeInTheDocument();
    });

    it("InvokeAccessPanel shows the empty state when the team has no applications", async () => {
      mock(listTriggers).mockResolvedValue([webhookTrigger]);
      renderTab();
      expect(await screen.findByTestId("invoke-empty-state")).toHaveTextContent(
        /no applications registered for your team yet/i
      );
    });

    it("lists an application's invoker grant and its disabled badge", async () => {
      mock(listTriggers).mockResolvedValue([webhookTrigger]);
      mock(listApplications).mockResolvedValue([{ ...billingApp, enabled: false }]);
      mock(listArtifactGrants).mockResolvedValue([invokerGrant]);
      renderTab();

      expect(await screen.findByTestId("invoker-grant-app1")).toHaveTextContent("billing-service");
      // The disabled badge depends on the applications query, which may resolve
      // after the grant row — wait for it rather than reading synchronously.
      expect(await screen.findByText(/application disabled/i)).toBeInTheDocument();
      await waitFor(() => expect(listArtifactGrants).toHaveBeenCalledWith("agent", "ag1"));
    });

    it("grants invoke access to a picked application", async () => {
      mock(listTriggers).mockResolvedValue([webhookTrigger]);
      mock(listApplications).mockResolvedValue([billingApp]);
      renderTab();

      await userEvent.click(await screen.findByRole("button", { name: /grant access/i }));
      await userEvent.selectOptions(
        screen.getByLabelText(/application to grant invoke access/i),
        "app1"
      );
      // The unattended-execution acknowledgment appears before confirming.
      expect(screen.getByTestId("invoke-ack")).toHaveTextContent(/without a human present/i);
      await userEvent.click(screen.getByRole("button", { name: /^grant access$/i }));

      await waitFor(() =>
        expect(createArtifactGrant).toHaveBeenCalledWith("agent", "ag1", {
          grantee_type: "application",
          grantee_id: "app1",
          role: "invoker",
        })
      );
    });

    it("ArtifactGrantsList renders mixed roles (agent-admin / approver / invoker)", async () => {
      mock(listArtifactGrants).mockResolvedValue([
        { ...invokerGrant, id: "g1", role: "agent-admin", grantee_type: "user", grantee_id: "alice", grantee_label: null },
        { ...invokerGrant, id: "g2", role: "approver", grantee_type: "team", grantee_id: "sec", grantee_label: null },
        invokerGrant,
      ]);
      renderTab();

      expect(await screen.findByText("agent-admin")).toBeInTheDocument();
      expect(screen.getByText("approver")).toBeInTheDocument();
      // "invoker" appears in the grants list (ArtifactGrantsList).
      expect(screen.getAllByText("invoker").length).toBeGreaterThan(0);
    });

    it("revokes a grant via revokeArtifactGrant", async () => {
      mock(listArtifactGrants).mockResolvedValue([invokerGrant]);
      renderTab();

      await userEvent.click(
        await screen.findByRole("button", { name: /revoke invoker for billing-service/i })
      );
      await waitFor(() =>
        expect(revokeArtifactGrant).toHaveBeenCalledWith("agent", "ag1", "g-inv")
      );
    });
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import SettingsTab from "./SettingsTab";
import type { AgentTrigger } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listTriggers: vi.fn(),
  updateTrigger: vi.fn(),
  rotateToken: vi.fn(),
  createTrigger: vi.fn(),
  updateAgent: vi.fn(),
  createTriggerClient: vi.fn(),
  listTriggerClients: vi.fn(),
  setClientEnabled: vi.fn(),
  deleteTriggerClient: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listTriggers, updateTrigger, rotateToken, createTrigger, updateAgent,
  createTriggerClient, listTriggerClients, setClientEnabled, deleteTriggerClient,
} from "../../api/registryApi";

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

const registeredClient = {
  client_id: "billing-service",
  enabled: true,
  created_by: "75c7c8b3-admin-sub",
  created_at: NOW,
};

describe("SettingsTab", () => {
  beforeEach(() => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (updateTrigger as ReturnType<typeof vi.fn>).mockResolvedValue(scheduleTrigger);
    (rotateToken as ReturnType<typeof vi.fn>).mockResolvedValue({
      trigger_id: "t2",
      token: "tok123",
      webhook_url: "https://example.com/hooks/my-agent/tok123",
    });
    (createTrigger as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...webhookTrigger,
      token: "newtok",
      webhook_url: "https://example.com/hooks/my-agent/newtok",
    });
    (updateAgent as ReturnType<typeof vi.fn>).mockResolvedValue({});
    (listTriggerClients as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (createTriggerClient as ReturnType<typeof vi.fn>).mockResolvedValue({
      client_id: "billing-service",
      secret: "whsec_abc123secret",
      created_at: NOW,
    });
    (setClientEnabled as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...registeredClient,
      enabled: false,
    });
    (deleteTriggerClient as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  });

  it("shows empty-state copy when there are no triggers", async () => {
    renderWithProviders(<SettingsTab agentName="my-agent" />);
    await waitFor(() => {
      expect(
        screen.getByText(/no schedule triggers configured/i)
      ).toBeInTheDocument();
      expect(
        screen.getByText(/no webhook triggers configured/i)
      ).toBeInTheDocument();
    });
  });

  it("renders a schedule trigger row with cron expression and timezone", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<SettingsTab agentName="my-agent" />);

    // Cron input pre-filled
    const cronInput = await screen.findByPlaceholderText("* * * * *");
    expect((cronInput as HTMLInputElement).value).toBe("0 9 * * *");

    // Timezone select pre-set (first combobox in the row; the second is the
    // WS-2 approver-role select).
    const tzSelect = screen.getAllByRole("combobox")[0];
    expect((tzSelect as HTMLSelectElement).value).toBe("UTC");

    // Enabled checkbox
    const enabledCheckbox = screen.getByRole("checkbox", { name: /enabled/i });
    expect((enabledCheckbox as HTMLInputElement).checked).toBe(true);
  });

  it("calls updateTrigger when Save is clicked", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<SettingsTab agentName="my-agent" />);

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
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([scheduleTrigger]);
    renderWithProviders(<SettingsTab agentName="my-agent" />);

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
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...scheduleTrigger, armed_by: "75c7c8b3-armed-sub", approver_role: "team:reviewer" },
    ]);
    renderWithProviders(<SettingsTab agentName="my-agent" />);

    expect(await screen.findByText(/75c7c8b3-armed-sub/)).toBeInTheDocument();
  });

  it("sends approver_role when creating a schedule trigger (WS-2 T014)", async () => {
    renderWithProviders(<SettingsTab agentName="my-agent" />);
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
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    renderWithProviders(<SettingsTab agentName="my-agent" />);

    expect(await screen.findByRole("button", { name: /rotate token/i })).toBeInTheDocument();
  });

  it("reveals webhook URL after Rotate Token is clicked", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    renderWithProviders(<SettingsTab agentName="my-agent" />);

    await userEvent.click(await screen.findByRole("button", { name: /rotate token/i }));

    await waitFor(() =>
      expect(rotateToken).toHaveBeenCalledWith("my-agent", "t2")
    );
    expect(
      await screen.findByText("https://example.com/hooks/my-agent/tok123")
    ).toBeInTheDocument();
  });

  it("renders both sections (Schedule + Webhook) when both trigger types exist", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
      scheduleTrigger,
      webhookTrigger,
    ]);
    renderWithProviders(<SettingsTab agentName="my-agent" />);

    expect(await screen.findByText(/schedule triggers/i)).toBeInTheDocument();
    expect(screen.getByText(/webhook triggers/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /rotate token/i })).toBeInTheDocument();
  });

  it("has New schedule/webhook trigger buttons", async () => {
    renderWithProviders(<SettingsTab agentName="my-agent" />);
    expect(await screen.findByRole("button", { name: /new schedule trigger/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new webhook trigger/i })).toBeInTheDocument();
  });

  it("creates a schedule trigger from the new-trigger form", async () => {
    renderWithProviders(<SettingsTab agentName="my-agent" />);
    await userEvent.click(await screen.findByRole("button", { name: /new schedule trigger/i }));
    // form appears with a Create button
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "my-agent",
        expect.objectContaining({ trigger_type: "schedule" })
      )
    );
  });

  it("new-schedule form sends input_payload when provided", async () => {
    renderWithProviders(<SettingsTab agentName="my-agent" />);
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
    renderWithProviders(<SettingsTab agentName="my-agent" />);
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
    renderWithProviders(<SettingsTab agentName="my-agent" memoryEnabled={false} />);
    const memoryToggle = await screen.findByRole("checkbox", { name: /enable memory/i });
    await userEvent.click(memoryToggle);
    await waitFor(() =>
      expect(updateAgent).toHaveBeenCalledWith("my-agent", { memory_enabled: true })
    );
  });

  // -------------------------------------------------------------------------
  // WS-4 — webhook signing-client panel
  // -------------------------------------------------------------------------
  describe("signing clients (WS-4)", () => {
    const openAddForm = async () => {
      await userEvent.click(await screen.findByRole("button", { name: /add client/i }));
      await userEvent.type(screen.getByPlaceholderText("billing-service"), "billing-service");
    };

    it("shows the trigger's auth_mode so an operator can see which door is open", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);
      expect(await screen.findByText("client_signed")).toBeInTheDocument();
    });

    it("falls back to token mode display for a pre-WS-4 trigger", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
        { ...webhookTrigger, auth_mode: undefined },
      ]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);
      expect(await screen.findByText("token")).toBeInTheDocument();
    });

    it("renders the registered client list with its audit fields", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      (listTriggerClients as ReturnType<typeof vi.fn>).mockResolvedValue([registeredClient]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);

      expect(await screen.findByText("billing-service")).toBeInTheDocument();
      expect(screen.getByText(/75c7c8b3-admin-sub/)).toBeInTheDocument();
      await waitFor(() => expect(listTriggerClients).toHaveBeenCalledWith("t2"));
    });

    it("shows the loading state while clients are in flight", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      (listTriggerClients as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
      renderWithProviders(<SettingsTab agentName="my-agent" />);
      expect(await screen.findByText(/loading clients/i)).toBeInTheDocument();
    });

    it("shows the empty state when no clients are registered", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);
      expect(await screen.findByText(/no clients registered/i)).toBeInTheDocument();
    });

    it("shows an error state when the client list fails to load", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      (listTriggerClients as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("boom"));
      renderWithProviders(<SettingsTab agentName="my-agent" />);
      expect(await screen.findByText(/failed to load clients/i)).toBeInTheDocument();
    });

    it("registers a client and reveals the secret exactly once", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);

      await openAddForm();
      await userEvent.click(screen.getByRole("button", { name: /^register$/i }));

      await waitFor(() =>
        expect(createTriggerClient).toHaveBeenCalledWith("t2", { client_id: "billing-service" })
      );
      expect(await screen.findByTestId("client-secret")).toHaveTextContent("whsec_abc123secret");
      expect(screen.getByText(/won't be shown again/i)).toBeInTheDocument();
    });

    it("does not show the secret again once the reveal is dismissed", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      // The refetch after registering returns the read model — which structurally
      // has no secret field, so there is nothing for the panel to re-render.
      (listTriggerClients as ReturnType<typeof vi.fn>).mockResolvedValue([registeredClient]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);

      await openAddForm();
      await userEvent.click(screen.getByRole("button", { name: /^register$/i }));
      await screen.findByTestId("client-secret");

      await userEvent.click(screen.getByRole("button", { name: /^done$/i }));

      await waitFor(() => expect(screen.queryByTestId("client-secret")).not.toBeInTheDocument());
      expect(screen.queryByText("whsec_abc123secret")).not.toBeInTheDocument();
      // The client itself survives — only the secret is gone.
      expect(await screen.findByText("billing-service")).toBeInTheDocument();
    });

    it("surfaces the 409 duplicate-client-id error inline", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      (createTriggerClient as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: {
          status: 409,
          data: { detail: "Client 'billing-service' is already registered on this trigger" },
        },
      });
      renderWithProviders(<SettingsTab agentName="my-agent" />);

      await openAddForm();
      await userEvent.click(screen.getByRole("button", { name: /^register$/i }));

      expect(await screen.findByText(/already registered on this trigger/i)).toBeInTheDocument();
      // No secret is invented on a failed registration.
      expect(screen.queryByTestId("client-secret")).not.toBeInTheDocument();
    });

    it("disables a client via setClientEnabled(false)", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      (listTriggerClients as ReturnType<typeof vi.fn>).mockResolvedValue([registeredClient]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);

      await userEvent.click(
        await screen.findByRole("checkbox", { name: /enabled billing-service/i })
      );
      await waitFor(() =>
        expect(setClientEnabled).toHaveBeenCalledWith("t2", "billing-service", false)
      );
    });

    it("revokes a client via deleteTriggerClient", async () => {
      (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
      (listTriggerClients as ReturnType<typeof vi.fn>).mockResolvedValue([registeredClient]);
      renderWithProviders(<SettingsTab agentName="my-agent" />);

      await userEvent.click(await screen.findByRole("button", { name: /revoke billing-service/i }));
      await waitFor(() =>
        expect(deleteTriggerClient).toHaveBeenCalledWith("t2", "billing-service")
      );
    });
  });
});

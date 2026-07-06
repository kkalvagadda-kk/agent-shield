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
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  listTriggers, updateTrigger, rotateToken, createTrigger, updateAgent,
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
  created_at: NOW,
  updated_at: NOW,
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

    // Timezone select pre-set
    const tzSelect = screen.getByRole("combobox");
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
});

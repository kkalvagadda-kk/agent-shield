import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import OverviewEventDriven from "./OverviewEventDriven";
import type { AgentTrigger, AgentEvent } from "../../api/registryApi";

vi.mock("../../api/registryApi", () => ({
  listTriggers: vi.fn(),
  listAgentEvents: vi.fn(),
  rotateToken: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listTriggers, listAgentEvents, rotateToken } from "../../api/registryApi";

const NOW = new Date().toISOString();

const webhookTrigger: AgentTrigger = {
  id: "t1",
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

const makeEvent = (id: string, status: AgentEvent["status"]): AgentEvent => ({
  id,
  trigger_id: "t1",
  agent_name: "my-agent",
  status,
  filter_reason: status === "filtered" ? "missing required field" : null,
  payload: null,
  run_id: null,
  source_ip: null,
  received_at: NOW,
});

describe("OverviewEventDriven", () => {
  beforeEach(() => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (listAgentEvents as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (rotateToken as ReturnType<typeof vi.fn>).mockResolvedValue({
      trigger_id: "t1",
      token: "tok456",
      webhook_url: "https://example.com/hooks/my-agent/tok456",
    });
  });

  it("shows 'no webhook trigger' copy when there are no webhook triggers", async () => {
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);
    await waitFor(() =>
      expect(
        screen.getByText(/no webhook trigger configured/i)
      ).toBeInTheDocument()
    );
  });

  it("renders webhook endpoint card with masked URL", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);

    // The masked placeholder should be visible before rotation
    expect(await screen.findByText(/token hidden/i)).toBeInTheDocument();
  });

  it("renders Rotate Token button for webhook trigger", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);

    expect(await screen.findByRole("button", { name: /rotate token/i })).toBeInTheDocument();
  });

  it("reveals webhook URL after Rotate Token is clicked", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);

    await userEvent.click(await screen.findByRole("button", { name: /rotate token/i }));

    await waitFor(() => expect(rotateToken).toHaveBeenCalledWith("my-agent", "t1"));

    expect(
      await screen.findByText("https://example.com/hooks/my-agent/tok456")
    ).toBeInTheDocument();
  });

  it("shows '—' match rate when there are no events", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);

    await waitFor(() => {
      const heading = screen.getByText(/activity \(last 0 events\)/i);
      expect(heading).toBeInTheDocument();
    });

    // Both matchRate and lastEvent show "—" — just verify at least one appears
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(1);
  });

  it("shows computed match rate when events exist", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    (listAgentEvents as ReturnType<typeof vi.fn>).mockResolvedValue([
      makeEvent("e1", "matched"),
      makeEvent("e2", "matched"),
      makeEvent("e3", "filtered"),
      makeEvent("e4", "rejected"),
    ]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);

    // 2 matched out of 4 = 50%
    expect(await screen.findByText("50%")).toBeInTheDocument();
  });

  it("renders event log rows for matched, filtered, and rejected events", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    (listAgentEvents as ReturnType<typeof vi.fn>).mockResolvedValue([
      makeEvent("e1", "matched"),
      makeEvent("e2", "filtered"),
      makeEvent("e3", "rejected"),
    ]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);

    await waitFor(() => {
      expect(screen.getByText("matched")).toBeInTheDocument();
      expect(screen.getByText("filtered")).toBeInTheDocument();
      expect(screen.getByText("rejected")).toBeInTheDocument();
    });

    // filter_reason shown for filtered events
    expect(screen.getByText(/missing required field/i)).toBeInTheDocument();
  });

  it("shows enabled badge for an enabled trigger", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([webhookTrigger]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);
    expect(await screen.findByText("enabled")).toBeInTheDocument();
  });

  it("shows disabled badge for a disabled trigger", async () => {
    (listTriggers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...webhookTrigger, enabled: false },
    ]);
    renderWithProviders(<OverviewEventDriven agentName="my-agent" deploymentId="d1" context="playground" />);
    expect(await screen.findByText("disabled")).toBeInTheDocument();
  });
});

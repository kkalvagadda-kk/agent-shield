import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import CreateAgentPage from "./CreateAgentPage";

vi.mock("../api/registryApi", () => ({
  createAgent: vi.fn(),
  createTrigger: vi.fn(),
  listProviders: vi.fn(),
  listTools: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import { createAgent, createTrigger, listProviders, listTools } from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

async function openNoCode() {
  renderWithProviders(<CreateAgentPage />);
  await userEvent.click(screen.getByRole("button", { name: /no-code/i }));
}

// The wizard now exposes three INDEPENDENT axes (R1): Shape · Trigger · Class — not the
// old flattened 4-way "Agent type" picker.
describe("CreateAgentPage — Shape · Trigger · Class selectors (R1)", () => {
  beforeEach(() => {
    mock(listProviders).mockResolvedValue({ items: [], total: 0 });
    mock(listTools).mockResolvedValue({ items: [], total: 0 });
    mock(createAgent).mockResolvedValue({ name: "wiz-agent", team: "default" });
    mock(createTrigger).mockResolvedValue({ token: "t", webhook_url: "https://x/hooks/wiz-agent/t" });
  });

  it("renders all three selectors: shape radios, trigger checkboxes, class radios", async () => {
    await openNoCode();
    // Shape (radiogroup)
    expect(await screen.findByRole("radio", { name: /Ephemeral/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Durable/i })).toBeInTheDocument();
    // Trigger (checkboxes)
    expect(screen.getByRole("checkbox", { name: /Schedule/i })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Webhook/i })).toBeInTheDocument();
    // Class (radiogroup)
    expect(screen.getByRole("radio", { name: /User-delegated/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Daemon/i })).toBeInTheDocument();
  });

  it("checking Schedule reveals cron fields AND auto-defaults class to daemon", async () => {
    await openNoCode();
    expect(screen.queryByPlaceholderText("0 9 * * 1")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /User-delegated/i })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("checkbox", { name: /Schedule/i }));
    expect(screen.getByPlaceholderText("0 9 * * 1")).toBeInTheDocument();
    // class auto-defaulted to daemon (trigger present, user hasn't overridden)
    expect(screen.getByRole("radio", { name: /Daemon/i })).toHaveAttribute("aria-checked", "true");
  });

  it("checking Webhook reveals filter conditions AND auto-defaults class to daemon", async () => {
    await openNoCode();
    await userEvent.click(screen.getByRole("checkbox", { name: /Webhook/i }));
    expect(screen.getByText(/Filter conditions/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("event_type")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Daemon/i })).toHaveAttribute("aria-checked", "true");
  });

  it("Durable shape with no trigger posts execution_shape=durable + agent_class=user_delegated, no trigger", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("radio", { name: /Durable/i }));
    await userEvent.click(screen.getByRole("button", { name: /^Create Agent$/i }));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(mock(createAgent).mock.calls[0][0]).toEqual(
      expect.objectContaining({ execution_shape: "durable", agent_class: "user_delegated" })
    );
    expect(createTrigger).not.toHaveBeenCalled();
  });

  it("submitting a Scheduled agent posts agent_class=daemon then createTrigger(schedule)", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("checkbox", { name: /Schedule/i }));
    await userEvent.click(screen.getByRole("button", { name: /^Create Agent$/i }));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(mock(createAgent).mock.calls[0][0]).toEqual(
      expect.objectContaining({ execution_shape: "reactive", agent_class: "daemon", agent_type: "declarative" })
    );
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "wiz-agent",
        expect.objectContaining({ trigger_type: "schedule" })
      )
    );
  });

  it("durable + scheduled together is authorable (the cube cell the 4-way picker blocked)", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("radio", { name: /Durable/i }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Schedule/i }));
    await userEvent.click(screen.getByRole("button", { name: /^Create Agent$/i }));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(mock(createAgent).mock.calls[0][0]).toEqual(
      expect.objectContaining({ execution_shape: "durable", agent_class: "daemon" })
    );
    expect(createTrigger).toHaveBeenCalledWith("wiz-agent", expect.objectContaining({ trigger_type: "schedule" }));
  });

  it("a manual class override survives the trigger auto-default", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("checkbox", { name: /Schedule/i })); // auto → daemon
    await userEvent.click(screen.getByRole("radio", { name: /User-delegated/i })); // user overrides back
    await userEvent.click(screen.getByRole("button", { name: /^Create Agent$/i }));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(mock(createAgent).mock.calls[0][0]).toEqual(
      expect.objectContaining({ agent_class: "user_delegated" })
    );
  });

  it("swaps the instructions template as triggers change", async () => {
    await openNoCode();
    const hasArea = (marker: string) =>
      screen.getAllByRole("textbox").some((a) => (a as HTMLTextAreaElement).value.includes(marker));
    expect(hasArea("[Expert Profession/Role]")).toBe(true);
    await userEvent.click(screen.getByRole("checkbox", { name: /Schedule/i }));
    expect(hasArea("You run on a schedule")).toBe(true);
    await userEvent.click(screen.getByRole("checkbox", { name: /Webhook/i })); // webhook has priority
    expect(hasArea("triggered by an external")).toBe(true);
  });

  it("Scheduled sends the input_payload to createTrigger", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("checkbox", { name: /Schedule/i }));
    fireEvent.change(screen.getByPlaceholderText(/weekly-report/), {
      target: { value: '{"task":"q3-report"}' },
    });
    await userEvent.click(screen.getByRole("button", { name: /^Create Agent$/i }));
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "wiz-agent",
        expect.objectContaining({ trigger_type: "schedule", input_payload: { task: "q3-report" } })
      )
    );
  });
});

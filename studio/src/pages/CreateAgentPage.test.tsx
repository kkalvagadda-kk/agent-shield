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
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { createAgent, createTrigger, listProviders, listTools } from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

async function openNoCode() {
  renderWithProviders(<CreateAgentPage />);
  await userEvent.click(screen.getByRole("button", { name: /no-code/i }));
}

describe("CreateAgentPage — 4-way type picker", () => {
  beforeEach(() => {
    mock(listProviders).mockResolvedValue({ items: [], total: 0 });
    mock(listTools).mockResolvedValue({ items: [], total: 0 });
    mock(createAgent).mockResolvedValue({ name: "wiz-agent", team: "default" });
    mock(createTrigger).mockResolvedValue({ token: "t", webhook_url: "https://x/hooks/wiz-agent/t" });
  });

  it("shows all four agent-type cards on the no-code form", async () => {
    await openNoCode();
    expect(await screen.findByRole("button", { name: /Reactive/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Durable/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Scheduled/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Event-Driven/i })).toBeInTheDocument();
  });

  it("reveals cron/timezone fields only when Scheduled is picked", async () => {
    await openNoCode();
    // not shown for the default (reactive)
    expect(screen.queryByPlaceholderText("0 9 * * 1")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Scheduled/i }));
    expect(screen.getByPlaceholderText("0 9 * * 1")).toBeInTheDocument();
  });

  it("reveals filter-condition fields when Event-Driven is picked", async () => {
    await openNoCode();
    await userEvent.click(screen.getByRole("button", { name: /Event-Driven/i }));
    expect(screen.getByText(/Filter conditions/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("event_type")).toBeInTheDocument();
  });

  it("submitting a Scheduled agent calls createAgent then createTrigger(schedule)", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("button", { name: /Scheduled/i }));
    await userEvent.click(screen.getByRole("button", { name: /^Create Agent$/i }));

    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(mock(createAgent).mock.calls[0][0]).toEqual(
      expect.objectContaining({ execution_shape: "reactive", agent_type: "declarative" })
    );
    await waitFor(() =>
      expect(createTrigger).toHaveBeenCalledWith(
        "wiz-agent",
        expect.objectContaining({ trigger_type: "schedule" })
      )
    );
  });

  it("swaps the instructions template when the agent type changes", async () => {
    await openNoCode();
    const hasArea = (marker: string) =>
      screen.getAllByRole("textbox").some((a) => (a as HTMLTextAreaElement).value.includes(marker));
    // default (reactive) template
    expect(hasArea("[Expert Profession/Role]")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: /Scheduled/i }));
    expect(hasArea("You run on a schedule")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: /Event-Driven/i }));
    expect(hasArea("triggered by an external")).toBe(true);
  });

  it("Scheduled sends the input_payload to createTrigger", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("button", { name: /Scheduled/i }));
    // set a valid JSON job spec via the payload textarea
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

  it("Durable maps to execution_shape=durable with no trigger", async () => {
    await openNoCode();
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "wiz-agent");
    await userEvent.click(screen.getByRole("button", { name: /Durable/i }));
    await userEvent.click(screen.getByRole("button", { name: /^Create Agent$/i }));
    await waitFor(() => expect(createAgent).toHaveBeenCalled());
    expect(mock(createAgent).mock.calls[0][0]).toEqual(
      expect.objectContaining({ execution_shape: "durable" })
    );
    expect(createTrigger).not.toHaveBeenCalled();
  });
});

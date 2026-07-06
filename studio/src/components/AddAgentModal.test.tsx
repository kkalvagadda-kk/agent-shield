import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import AddAgentModal from "./AddAgentModal";

// Mock the API module the component imports.
vi.mock("../api/registryApi", () => ({
  listAgents: vi.fn(),
  createAgent: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { listAgents, createAgent } from "../api/registryApi";

const AGENTS = {
  items: [
    { id: "a1", name: "alpha-agent", team: "platform", description: "first", execution_shape: "reactive" },
    { id: "a2", name: "beta-agent", team: "platform", description: "second", execution_shape: "durable" },
    { id: "a3", name: "gamma-agent", team: "other", description: "wrong team", execution_shape: "reactive" },
  ],
  total: 3,
};

describe("AddAgentModal", () => {
  beforeEach(() => {
    (listAgents as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(AGENTS);
  });

  it("lists agents filtered to the given team", async () => {
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={vi.fn()} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    expect(await screen.findByText("alpha-agent")).toBeInTheDocument();
    expect(screen.getByText("beta-agent")).toBeInTheDocument();
    // gamma-agent is in a different team → hidden
    expect(screen.queryByText("gamma-agent")).not.toBeInTheDocument();
  });

  it("filters by search text", async () => {
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={vi.fn()} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    await screen.findByText("alpha-agent");
    await userEvent.type(screen.getByPlaceholderText("Search agents…"), "beta");
    expect(screen.queryByText("alpha-agent")).not.toBeInTheDocument();
    expect(screen.getByText("beta-agent")).toBeInTheDocument();
  });

  it("fires onAdd with the agent when '+ Add' is clicked", async () => {
    const onAdd = vi.fn();
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={onAdd} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    await screen.findByText("alpha-agent");
    await userEvent.click(screen.getAllByRole("button", { name: /add/i })[0]);
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ id: "a1", name: "alpha-agent" }));
  });

  it("shows an already-added agent as disabled 'Added'", async () => {
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={vi.fn()} onClose={vi.fn()} alreadyAddedIds={["a1"]} />
    );
    await screen.findByText("alpha-agent");
    const added = screen.getByRole("button", { name: "Added" });
    expect(added).toBeDisabled();
  });

  it("shows empty-state copy when no agents match the team", async () => {
    (listAgents as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ items: [], total: 0 });
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={vi.fn()} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    await waitFor(() =>
      expect(screen.getByText(/No agents found for team/i)).toBeInTheDocument()
    );
  });

  it("creates a new inline agent from the 'Create New Agent' tab and adds it", async () => {
    (createAgent as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "new1", name: "fresh-agent", team: "platform", execution_shape: "reactive",
    });
    const onAdd = vi.fn();
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={onAdd} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    await screen.findByText("alpha-agent");
    await userEvent.click(screen.getByRole("button", { name: /create new agent/i }));
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "fresh-agent");
    await userEvent.click(screen.getByRole("button", { name: /create & add/i }));

    await waitFor(() =>
      expect(createAgent).toHaveBeenCalledWith(
        expect.objectContaining({ name: "fresh-agent", team: "platform", agent_type: "declarative" })
      )
    );
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ id: "new1", is_inline: true }));
  });

  it("calls listAgents with composable:true and shows the composable hint", async () => {
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={vi.fn()} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    // Wait for the list to populate, confirming the query ran.
    await screen.findByText("alpha-agent");
    expect(listAgents).toHaveBeenCalledWith(100, 0, undefined, { composable: true });
    expect(
      screen.getByText(/only composable agents are listed/i)
    ).toBeInTheDocument();
  });

  it("reactive shape is the default and submits with execution_shape:reactive", async () => {
    (createAgent as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "r1", name: "reactive-agent", team: "platform", execution_shape: "reactive",
    });
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={vi.fn()} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    await screen.findByText("alpha-agent");
    await userEvent.click(screen.getByRole("button", { name: /create new agent/i }));

    // Both shape buttons are rendered.
    expect(screen.getByRole("button", { name: /reactive/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /durable/i })).toBeInTheDocument();

    // Fill required name field and submit without changing shape.
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "reactive-agent");
    await userEvent.click(screen.getByRole("button", { name: /create & add/i }));

    await waitFor(() =>
      expect(createAgent).toHaveBeenCalledWith(
        expect.objectContaining({ execution_shape: "reactive" })
      )
    );
  });

  it("selecting Durable shape passes execution_shape:durable to createAgent", async () => {
    (createAgent as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: "d1", name: "durable-agent", team: "platform", execution_shape: "durable",
    });
    renderWithProviders(
      <AddAgentModal team="platform" onAdd={vi.fn()} onClose={vi.fn()} alreadyAddedIds={[]} />
    );
    await screen.findByText("alpha-agent");
    await userEvent.click(screen.getByRole("button", { name: /create new agent/i }));
    await userEvent.click(screen.getByRole("button", { name: /durable/i }));
    await userEvent.type(screen.getByPlaceholderText("my-agent"), "durable-agent");
    await userEvent.click(screen.getByRole("button", { name: /create & add/i }));

    await waitFor(() =>
      expect(createAgent).toHaveBeenCalledWith(
        expect.objectContaining({ execution_shape: "durable" })
      )
    );
  });
});

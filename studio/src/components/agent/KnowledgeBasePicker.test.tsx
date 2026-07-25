import { describe, it, expect, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import KnowledgeBasePicker from "./KnowledgeBasePicker";
import type { KnowledgeBase } from "../../api/knowledgeApi";

const NOW = "2026-07-25T00:00:00Z";

const kb = (over: Partial<KnowledgeBase> = {}): KnowledgeBase =>
  ({
    id: "kb-1",
    team: "default",
    name: "Product Docs",
    description: "Public product documentation.",
    created_by: "u",
    created_at: NOW,
    updated_at: NOW,
    source_count: 3,
    ready_count: 3,
    attached_agents: [],
    ...over,
  }) as KnowledgeBase;

const KBS = [
  kb(),
  kb({
    id: "kb-2",
    name: "Support Tickets",
    description: "Historical customer escalations.",
    source_count: 4,
    ready_count: 2,
  }),
];

function render(selected: string[] = [], onToggle = vi.fn(), kbs = KBS) {
  renderWithProviders(
    <KnowledgeBasePicker kbs={kbs} selected={selected} onToggle={onToggle} />,
  );
  return onToggle;
}

const openDrawer = async () => {
  const picker = screen.getByTestId("kb-picker");
  await userEvent.click(within(picker).getByRole("button", { name: /add from catalog/i }));
  return picker;
};

describe("KnowledgeBasePicker — tile drawer", () => {
  it("shows selected KBs as chips and keeps the catalog behind the drawer", async () => {
    render(["kb-1"]);
    const picker = screen.getByTestId("kb-picker");
    expect(within(picker).getByText("Product Docs")).toBeInTheDocument();
    expect(within(picker).queryByText("Support Tickets")).not.toBeInTheDocument();
    expect(screen.queryByTestId("kb-picker-drawer")).not.toBeInTheDocument();
  });

  it("renders an empty-selection hint when nothing is picked", () => {
    render([]);
    expect(screen.getByText(/no knowledge bases selected/i)).toBeInTheDocument();
  });

  it("opens the drawer and lists KBs as tiles", async () => {
    render([]);
    const picker = await openDrawer();
    expect(screen.getByTestId("kb-picker-drawer")).toBeInTheDocument();
    expect(within(picker).getByText("Product Docs")).toBeInTheDocument();
    expect(within(picker).getByText("Support Tickets")).toBeInTheDocument();
  });

  // KBs carry no risk_level — the tile's meta slot exists precisely so we show
  // readiness here instead of a meaningless "low" risk badge.
  it("shows ready/source counts and no risk badge", async () => {
    render([]);
    const picker = await openDrawer();
    expect(within(picker).getByText("3/3 ready")).toBeInTheDocument();
    expect(within(picker).getByText("2/4 ready")).toBeInTheDocument();
    for (const risk of ["low", "medium", "high"]) {
      expect(within(picker).queryByText(risk)).not.toBeInTheDocument();
    }
  });

  it("selecting a tile calls onToggle with the KB id", async () => {
    const onToggle = render([]);
    const picker = await openDrawer();
    await userEvent.click(within(picker).getAllByRole("checkbox")[1]);
    expect(onToggle).toHaveBeenCalledWith("kb-2");
  });

  it("reflects current selection as checked inside the drawer", async () => {
    render(["kb-2"]);
    const picker = await openDrawer();
    const boxes = within(picker).getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.filter((b) => b.checked)).toHaveLength(1);
  });

  it("filters tiles by the drawer search", async () => {
    render([]);
    const picker = await openDrawer();
    await userEvent.type(
      within(picker).getByPlaceholderText(/search by name or description/i),
      "escalations",
    );
    expect(within(picker).getByText("Support Tickets")).toBeInTheDocument();
    expect(within(picker).queryByText("Product Docs")).not.toBeInTheDocument();
  });

  it("removes a selected KB from its chip", async () => {
    const onToggle = render(["kb-1"]);
    await userEvent.click(screen.getByRole("button", { name: /remove product docs/i }));
    expect(onToggle).toHaveBeenCalledWith("kb-1");
  });

  it("offers no edit, delete, or create affordance", async () => {
    render([]);
    const picker = await openDrawer();
    expect(within(picker).queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
    expect(within(picker).queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
    expect(within(picker).queryByRole("button", { name: /create/i })).not.toBeInTheDocument();
  });

  it("names where KBs are managed when the team has none", async () => {
    render([], vi.fn(), []);
    const picker = await openDrawer();
    expect(
      within(picker).getByText(/knowledge bases are managed under knowledge/i),
    ).toBeInTheDocument();
  });
});

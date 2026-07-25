import { describe, it, expect, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import ToolsPicker, { KNOWLEDGE_SEARCH_TOOL } from "./ToolsPicker";
import type { RegistryTool } from "../../api/registryApi";

const tool = (over: Partial<RegistryTool> = {}): RegistryTool =>
  ({
    id: over.name ?? "t1",
    name: "web_search",
    display_name: "Web Search",
    description: "Searches the public web.",
    type: "http",
    risk_level: "low",
    ...over,
  }) as RegistryTool;

const TOOLS = [
  tool({ id: "t1", name: "web_search", display_name: "Web Search", risk_level: "low" }),
  tool({
    id: "t2",
    name: "wire_transfer",
    display_name: "Wire Transfer",
    description: "Moves money between accounts.",
    type: "python",
    risk_level: "high",
  }),
  tool({ id: "t3", name: KNOWLEDGE_SEARCH_TOOL, display_name: "Knowledge Search" }),
];

function render(selected: string[] = [], onToggle = vi.fn()) {
  renderWithProviders(<ToolsPicker tools={TOOLS} selected={selected} onToggle={onToggle} />);
  return onToggle;
}

const openDrawer = async () => {
  const picker = screen.getByTestId("tools-picker");
  await userEvent.click(within(picker).getByRole("button", { name: /add from catalog/i }));
  return picker;
};

describe("ToolsPicker — tile drawer", () => {
  it("shows selected tools as chips and does not render the catalog inline", async () => {
    render(["web_search"]);
    const picker = screen.getByTestId("tools-picker");
    expect(within(picker).getByText("Web Search")).toBeInTheDocument();
    // Un-selected tools must NOT be visible until the drawer opens — that is the
    // whole point of moving the list off the builder surface.
    expect(within(picker).queryByText("Wire Transfer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tools-picker-drawer")).not.toBeInTheDocument();
  });

  it("renders an empty-selection hint when nothing is picked", () => {
    render([]);
    expect(screen.getByText(/no tools selected/i)).toBeInTheDocument();
  });

  it("opens the drawer and lists pickable tools as tiles with risk + type", async () => {
    render([]);
    const picker = await openDrawer();
    expect(screen.getByTestId("tools-picker-drawer")).toBeInTheDocument();
    expect(within(picker).getByText("Web Search")).toBeInTheDocument();
    expect(within(picker).getByText("Wire Transfer")).toBeInTheDocument();
    // Risk is the governance signal — it must survive on the tile.
    expect(within(picker).getByText("high")).toBeInTheDocument();
    expect(within(picker).getByText("python")).toBeInTheDocument();
  });

  // The structural guard: knowledge_search is attached server-side when a KB is
  // bound, so it must never be hand-pickable on ANY editing surface. Filtered in
  // ToolsPicker alone, deliberately — scattering it to callers is how it gets lost.
  it("never lists knowledge_search, even inside the drawer", async () => {
    render([]);
    const picker = await openDrawer();
    expect(within(picker).queryByText("Knowledge Search")).not.toBeInTheDocument();
    expect(within(picker).queryByText(KNOWLEDGE_SEARCH_TOOL)).not.toBeInTheDocument();
    // Exactly the two legitimate tools are offered.
    expect(within(picker).getAllByRole("checkbox")).toHaveLength(2);
  });

  it("selecting a tile calls onToggle with the tool name", async () => {
    const onToggle = render([]);
    const picker = await openDrawer();
    await userEvent.click(within(picker).getAllByRole("checkbox")[1]);
    expect(onToggle).toHaveBeenCalledWith("wire_transfer");
  });

  it("reflects current selection as checked inside the drawer", async () => {
    render(["wire_transfer"]);
    const picker = await openDrawer();
    const boxes = within(picker).getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.find((b) => b.checked)).toBeTruthy();
    expect(boxes.filter((b) => b.checked)).toHaveLength(1);
  });

  it("filters tiles by the drawer search across name and description", async () => {
    render([]);
    const picker = await openDrawer();
    await userEvent.type(
      within(picker).getByPlaceholderText(/search by name or description/i),
      "money",
    );
    expect(within(picker).getByText("Wire Transfer")).toBeInTheDocument();
    expect(within(picker).queryByText("Web Search")).not.toBeInTheDocument();
  });

  it("tells the user where tools are managed when the search matches nothing", async () => {
    render([]);
    const picker = await openDrawer();
    await userEvent.type(
      within(picker).getByPlaceholderText(/search by name or description/i),
      "zzzz",
    );
    expect(within(picker).getByText(/tools are managed under tools/i)).toBeInTheDocument();
  });

  it("removes a selected tool from its chip", async () => {
    const onToggle = render(["web_search"]);
    await userEvent.click(screen.getByRole("button", { name: /remove web search/i }));
    expect(onToggle).toHaveBeenCalledWith("web_search");
  });

  // Design decision worth pinning: tools are shared team resources, so a picker
  // tile must never carry a destructive action. A mis-click while assembling an
  // agent would otherwise delete a tool other agents depend on.
  it("offers no edit or delete affordance anywhere in the picker", async () => {
    render([]);
    const picker = await openDrawer();
    expect(within(picker).queryByRole("button", { name: /edit/i })).not.toBeInTheDocument();
    expect(within(picker).queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
    // Nor a create-new path — browse-and-select only.
    expect(within(picker).queryByRole("button", { name: /new tool|create tool/i })).not.toBeInTheDocument();
  });

  it("closes the drawer via Done", async () => {
    render([]);
    const picker = await openDrawer();
    await userEvent.click(within(picker).getByRole("button", { name: /^done$/i }));
    expect(screen.queryByTestId("tools-picker-drawer")).not.toBeInTheDocument();
  });

  it("shows the empty-catalog text when there are no pickable tools", async () => {
    renderWithProviders(
      <ToolsPicker
        tools={[tool({ id: "t3", name: KNOWLEDGE_SEARCH_TOOL })]}
        selected={[]}
        onToggle={vi.fn()}
        emptyText="No tools available."
      />,
    );
    const picker = await openDrawer();
    expect(within(picker).getByText("No tools available.")).toBeInTheDocument();
  });
});

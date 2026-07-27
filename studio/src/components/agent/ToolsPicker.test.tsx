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

/** The tile grid alone. The source-filter buttons repeat the server names, so a
 *  text query over the whole drawer cannot tell a filter chip from a tile. */
const grid = () => screen.getByTestId("tools-picker-drawer-grid");

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
    await userEvent.type(within(picker).getByPlaceholderText(/search by name/i), "money");
    expect(within(picker).getByText("Wire Transfer")).toBeInTheDocument();
    expect(within(picker).queryByText("Web Search")).not.toBeInTheDocument();
  });

  it("tells the user where tools are managed when the search matches nothing", async () => {
    render([]);
    const picker = await openDrawer();
    await userEvent.type(within(picker).getByPlaceholderText(/search by name/i), "zzzz");
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

// ---------------------------------------------------------------------------
// MCP-sourced tools
// ---------------------------------------------------------------------------

const MCP_TOOLS = [
  tool({ id: "n1", name: "http_echo", display_name: "HTTP Echo", type: "http" }),
  tool({
    id: "m1",
    name: "github-mcp__search",
    display_name: "search",
    description: "Search GitHub.",
    type: "mcp_tool",
    mcp_server_name: "github-mcp",
  }),
  tool({
    id: "m2",
    name: "tavily__extract",
    display_name: "extract",
    description: "Extract page content.",
    type: "mcp_tool",
    mcp_server_name: "tavily",
  }),
];

const renderMcp = (selected: string[] = [], onToggle = vi.fn()) => {
  renderWithProviders(<ToolsPicker tools={MCP_TOOLS} selected={selected} onToggle={onToggle} />);
  return onToggle;
};

describe("ToolsPicker — MCP source attribution", () => {
  it("labels an mcp_tool tile with its source server", async () => {
    renderMcp();
    await openDrawer();
    expect(within(grid()).getByText("github-mcp")).toBeInTheDocument();
    expect(within(grid()).getByText("tavily")).toBeInTheDocument();
  });

  // The regression the merge would otherwise have shipped: the tile's meta slot
  // rendered `tool.type` verbatim, so a discovered tool wore a chip reading
  // "mcp_tool" — the plumbing name, and identical on every server's tools.
  it("never renders the raw mcp_tool type string on a tile", async () => {
    renderMcp();
    await openDrawer();
    expect(within(grid()).queryByText("mcp_tool")).not.toBeInTheDocument();
  });

  it("shows no source-server chip for a native tool", async () => {
    renderWithProviders(<ToolsPicker tools={[MCP_TOOLS[0]]} selected={[]} onToggle={vi.fn()} />);
    const picker = await openDrawer();
    expect(within(picker).queryByText("github-mcp")).not.toBeInTheDocument();
    expect(within(picker).getByText("http")).toBeInTheDocument();
  });

  it("finds a tool by its source-server name in the search", async () => {
    renderMcp();
    const picker = await openDrawer();
    await userEvent.type(within(picker).getByPlaceholderText(/search by name/i), "tavily");
    expect(within(grid()).getByText("extract")).toBeInTheDocument();
    expect(within(grid()).queryByText("HTTP Echo")).not.toBeInTheDocument();
  });

  it("names the source server on the selected chip too", () => {
    renderMcp(["github-mcp__search"]);
    const picker = screen.getByTestId("tools-picker");
    expect(within(picker).getByText("· github-mcp")).toBeInTheDocument();
  });
});

describe("ToolsPicker — source filter", () => {
  it("is hidden when no MCP server contributes tools", async () => {
    render([]);
    await openDrawer();
    expect(screen.queryByTestId("tools-source-filter")).not.toBeInTheDocument();
  });

  it("offers All, Native and one bucket per server", async () => {
    renderMcp();
    await openDrawer();
    const filter = screen.getByTestId("tools-source-filter");
    expect(within(filter).getByRole("button", { name: /^All/ })).toBeInTheDocument();
    expect(within(filter).getByRole("button", { name: /^Native/ })).toBeInTheDocument();
    expect(within(filter).getByRole("button", { name: /^github-mcp/ })).toBeInTheDocument();
    expect(within(filter).getByRole("button", { name: /^tavily/ })).toBeInTheDocument();
  });

  it("narrows the grid to one server", async () => {
    renderMcp();
    await openDrawer();
    await userEvent.click(
      within(screen.getByTestId("tools-source-filter")).getByRole("button", { name: /^tavily/ }),
    );
    expect(within(grid()).getByText("extract")).toBeInTheDocument();
    expect(within(grid()).queryByText("search")).not.toBeInTheDocument();
    expect(within(grid()).queryByText("HTTP Echo")).not.toBeInTheDocument();
  });

  // Why this filter exists at all: 54 of the 82 tools on the live cluster come
  // from four MCP servers, so an unfiltered grid buries the native ones.
  it("narrows the grid to native tools only", async () => {
    renderMcp();
    await openDrawer();
    await userEvent.click(
      within(screen.getByTestId("tools-source-filter")).getByRole("button", { name: /^Native/ }),
    );
    expect(within(grid()).getByText("HTTP Echo")).toBeInTheDocument();
    expect(within(grid()).queryByText("extract")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Only ACTIVE tools are offerable
// ---------------------------------------------------------------------------

const RETIRED = [
  tool({ id: "a1", name: "live_tool", display_name: "Live Tool", status: "active" }),
  tool({ id: "d1", name: "old_tool", display_name: "Old Tool", status: "deprecated" }),
  tool({
    id: "g1",
    name: "github-mcp__gone",
    display_name: "Gone",
    type: "mcp_tool",
    mcp_server_name: "github-mcp",
    status: "inactive",
  }),
];

describe("ToolsPicker — retired tools", () => {
  it("does not offer a deprecated tool in the drawer", async () => {
    renderWithProviders(<ToolsPicker tools={RETIRED} selected={[]} onToggle={vi.fn()} />);
    const picker = await openDrawer();
    expect(within(picker).getByText("Live Tool")).toBeInTheDocument();
    expect(within(picker).queryByText("Old Tool")).not.toBeInTheDocument();
  });

  // MCP discovery marks a tool that vanished upstream `inactive` and never
  // deletes the row. Offering it produces an agent that fails at run time on a
  // tool its server no longer advertises.
  it("does not offer an mcp_tool whose upstream server dropped it", async () => {
    renderWithProviders(<ToolsPicker tools={RETIRED} selected={[]} onToggle={vi.fn()} />);
    await openDrawer();
    expect(within(grid()).queryByText("Gone")).not.toBeInTheDocument();
    expect(within(grid()).getAllByRole("checkbox")).toHaveLength(1);
  });

  // The other half of the rule, and the reason the catalog is fetched WITHOUT a
  // status filter: an agent that already binds a now-deprecated tool must still
  // show it. Hiding the chip would leave the binding in the database while the
  // user could neither see it nor remove it.
  it("still shows an already-bound deprecated tool as a removable chip", async () => {
    const onToggle = vi.fn();
    renderWithProviders(<ToolsPicker tools={RETIRED} selected={["old_tool"]} onToggle={onToggle} />);
    const picker = screen.getByTestId("tools-picker");
    expect(within(picker).getByText("Old Tool")).toBeInTheDocument();
    expect(within(picker).getByText("(unavailable)")).toBeInTheDocument();
    await userEvent.click(within(picker).getByRole("button", { name: /remove old tool/i }));
    expect(onToggle).toHaveBeenCalledWith("old_tool");
  });
});

// ---------------------------------------------------------------------------
// valueKey — the reason the duplicate pickers could be deleted
// ---------------------------------------------------------------------------

describe("ToolsPicker — valueKey", () => {
  it("keys selection by name by default", async () => {
    const onToggle = render([]);
    const picker = await openDrawer();
    await userEvent.click(within(picker).getAllByRole("checkbox")[0]);
    expect(onToggle).toHaveBeenCalledWith("web_search");
  });

  it('keys selection by id when valueKey="id"', async () => {
    const onToggle = vi.fn();
    renderWithProviders(
      <ToolsPicker tools={TOOLS} selected={[]} onToggle={onToggle} valueKey="id" />,
    );
    const picker = await openDrawer();
    await userEvent.click(within(picker).getAllByRole("checkbox")[0]);
    expect(onToggle).toHaveBeenCalledWith("t1");
  });

  it('resolves chips by id when valueKey="id"', () => {
    renderWithProviders(
      <ToolsPicker tools={TOOLS} selected={["t2"]} onToggle={vi.fn()} valueKey="id" />,
    );
    const picker = screen.getByTestId("tools-picker");
    expect(within(picker).getByText("Wire Transfer")).toBeInTheDocument();
    expect(within(picker).queryByText("Web Search")).not.toBeInTheDocument();
  });
});

import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import ToolsPicker, { KNOWLEDGE_SEARCH_TOOL } from "./ToolsPicker";
import type { RegistryTool } from "../../api/registryApi";

const tool = (over: Partial<RegistryTool>): RegistryTool => ({
  id: over.id ?? "t",
  name: over.name ?? "a_tool",
  display_name: over.display_name ?? null,
  description: over.description ?? null,
  type: over.type ?? "http",
  config: {},
  ...over,
});

describe("ToolsPicker", () => {
  it("structurally filters out knowledge_search", () => {
    renderWithProviders(
      <ToolsPicker
        tools={[tool({ id: "k", name: KNOWLEDGE_SEARCH_TOOL }), tool({ id: "h", name: "http_tool" })]}
        selected={[]}
        onToggle={vi.fn()}
      />
    );
    expect(screen.queryByText(KNOWLEDGE_SEARCH_TOOL)).toBeNull();
    expect(screen.getByText("http_tool")).toBeInTheDocument();
  });

  it("calls onToggle with the tool name when checked", async () => {
    const onToggle = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ToolsPicker tools={[tool({ id: "h", name: "http_tool" })]} selected={[]} onToggle={onToggle} />);
    await user.click(screen.getByRole("checkbox"));
    expect(onToggle).toHaveBeenCalledWith("http_tool");
  });

  it("renders the empty state when there are no pickable tools", () => {
    renderWithProviders(<ToolsPicker tools={[]} selected={[]} onToggle={vi.fn()} emptyText="Nothing here." />);
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
  });

  it("shows a source-server badge for an mcp_tool", () => {
    renderWithProviders(
      <ToolsPicker
        tools={[tool({ id: "m", name: "github-mcp__search", type: "mcp_tool", mcp_server_name: "github-mcp" })]}
        selected={[]}
        onToggle={vi.fn()}
      />
    );
    expect(screen.getByText("github-mcp")).toBeInTheDocument();
  });

  it("shows no source-server badge for a native tool", () => {
    renderWithProviders(<ToolsPicker tools={[tool({ id: "h", name: "http_tool" })]} selected={[]} onToggle={vi.fn()} />);
    expect(screen.queryByText("github-mcp")).toBeNull();
  });
});

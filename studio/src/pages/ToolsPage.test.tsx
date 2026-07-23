import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import ToolsPage from "./ToolsPage";

vi.mock("../api/registryApi", () => ({
  listTools: vi.fn(),
  createTool: vi.fn(),
  updateTool: vi.fn(),
  deleteTool: vi.fn(),
  listAuthConfigs: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listTools, createTool, updateTool } from "../api/registryApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const HTTP_TOOL = {
  id: "http-1",
  name: "get_order",
  display_name: "Get Order",
  description: "Fetch an order",
  type: "http",
  risk_level: "low" as const,
  owner_team: "platform",
  status: "active",
  http_method: "GET",
  http_url: "https://api.example.com/orders/{{id}}",
  pii_deanonymize_allowed: true,
  config: {},
};

const MCP_TOOL = {
  id: "mcp-1",
  name: "github-mcp__search_issues",
  display_name: null,
  description: "Search issues",
  type: "mcp_tool",
  risk_level: "low" as const,
  owner_team: "platform",
  status: "active",
  mcp_server_id: "srv-1",
  mcp_tool_name: "search_issues",
  mcp_server_name: "github-mcp",
  config: {},
};

describe("ToolsPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("creates an HTTP tool with the pii_deanonymize_allowed flag in the payload", async () => {
    mk(listTools).mockResolvedValue({ items: [HTTP_TOOL], total: 1 });
    mk(createTool).mockResolvedValue({ ...HTTP_TOOL, id: "new" });

    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);
    await screen.findByText("Get Order");

    await user.click(screen.getByRole("button", { name: /new tool/i }));
    await user.type(screen.getByPlaceholderText("get_order_status"), "make_payment");
    await user.type(screen.getByPlaceholderText(/api.example.com/), "https://pay/charge");
    await user.click(screen.getByRole("checkbox", { name: /receive real PII values/i }));
    await user.click(screen.getByRole("button", { name: /create tool/i }));

    await waitFor(() =>
      expect(createTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "make_payment",
          type: "http",
          http_method: "GET",
          http_url: "https://pay/charge",
          pii_deanonymize_allowed: true,
        })
      )
    );
  });

  it("creates a Python tool carrying python_code + the pii flag (default false)", async () => {
    mk(listTools).mockResolvedValue({ items: [HTTP_TOOL], total: 1 });
    mk(createTool).mockResolvedValue({ ...HTTP_TOOL, id: "py", type: "python" });

    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);
    await screen.findByText("Get Order");

    await user.click(screen.getByRole("button", { name: /new tool/i }));
    await user.click(screen.getByRole("radio", { name: /python/i }));
    await user.type(screen.getByPlaceholderText("get_order_status"), "crunch");
    await user.click(screen.getByRole("button", { name: /create tool/i }));

    await waitFor(() =>
      expect(createTool).toHaveBeenCalledWith(
        expect.objectContaining({ name: "crunch", type: "python", pii_deanonymize_allowed: false })
      )
    );
    expect(mk(createTool).mock.calls[0][0]).toHaveProperty("python_code");
  });

  it("pre-fills the edit form (name + pii checkbox) and sends pii in the update payload", async () => {
    mk(listTools).mockResolvedValue({ items: [HTTP_TOOL], total: 1 });
    mk(updateTool).mockResolvedValue(HTTP_TOOL);

    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);
    await screen.findByText("Get Order");

    await user.click(screen.getByRole("button", { name: /^edit$/i }));
    // Name is prefilled + read-only in edit mode.
    expect(screen.getByDisplayValue("get_order")).toBeInTheDocument();
    // The pii flag round-trips: this tool had it on → checkbox is checked.
    expect(screen.getByRole("checkbox", { name: /receive real PII values/i })).toBeChecked();

    await user.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() =>
      expect(updateTool).toHaveBeenCalledWith(
        "http-1",
        expect.objectContaining({ pii_deanonymize_allowed: true })
      )
    );
  });

  it("renders an mcp_tool row read-only: no Edit/Delete, a link to its source server", async () => {
    mk(listTools).mockResolvedValue({ items: [MCP_TOOL], total: 1 });
    renderWithProviders(<ToolsPage />);

    const nameCell = await screen.findByText("github-mcp__search_issues");
    const row = nameCell.closest("tr")!;
    expect(within(row).queryByRole("button", { name: /^edit$/i })).toBeNull();
    expect(within(row).queryByRole("button", { name: /^delete$/i })).toBeNull();

    const link = within(row).getByRole("link", { name: /view source server/i });
    expect(link).toHaveAttribute("href", "/mcp-servers/srv-1");
    // The MCP type badge is shown.
    expect(within(row).getByText("MCP")).toBeInTheDocument();
  });
});

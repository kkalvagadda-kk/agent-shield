import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import ToolsPage from "./ToolsPage";

vi.mock("../api/registryApi", () => ({
  createTool: vi.fn(),
  deleteTool: vi.fn(),
  listAuthConfigs: vi.fn(),
  listTools: vi.fn(),
  updateTool: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import { createTool, listAuthConfigs, listTools, updateTool } from "../api/registryApi";

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

// A description a user would actually write: several lines with a blank line.
// The single-line <input> this replaced could not hold it.
const MULTILINE = [
  "Retrieves the current status of an order.",
  "",
  "Args: order_id (str) — the customer-facing order number.",
  "Use for status lookups only; does not modify the order.",
].join("\n");

const EXISTING_TOOL = {
  id: "t1",
  name: "get_order_status",
  display_name: "Get Order Status",
  description: MULTILINE,
  type: "http",
  risk_level: "low" as const,
  owner_team: "default",
  enabled: true,
  // An http tool without a URL fails the form's superRefine, so an edit save
  // would never reach updateTool — the fixture has to be a valid tool.
  http_method: "GET" as const,
  http_url: "https://api.example.com/orders/{order_id}",
};

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

// Placeholders are the only stable handles — Field renders a <label> with no
// htmlFor, so getByLabelText cannot associate them.
const DESC = /Retrieves the current status of an order/i;
const NAME = "get_order_status";

beforeEach(() => {
  vi.clearAllMocks();
  mock(listTools).mockResolvedValue({ items: [EXISTING_TOOL], total: 1 });
  mock(listAuthConfigs).mockResolvedValue({ items: [], total: 0 });
  mock(createTool).mockResolvedValue({ ...EXISTING_TOOL, id: "t2" });
  mock(updateTool).mockResolvedValue(EXISTING_TOOL);
});

const clickNewTool = async () =>
  userEvent.click((await screen.findAllByRole("button", { name: /new tool/i }))[0]);

async function openCreateForm() {
  renderWithProviders(<ToolsPage />);
  await clickNewTool();
}

describe("ToolsPage — multi-line tool description", () => {
  it("renders Description as a growable textarea, not a single-line input", async () => {
    await openCreateForm();
    const field = screen.getByPlaceholderText(DESC);
    expect(field.tagName).toBe("TEXTAREA");
    expect(Number((field as HTMLTextAreaElement).rows)).toBeGreaterThan(1);
    expect(field.className).toContain("resize-y");
  });

  it("preserves newlines in the field value", async () => {
    await openCreateForm();
    const desc = screen.getByPlaceholderText(DESC) as HTMLTextAreaElement;
    await userEvent.click(desc);
    // paste, not type — type() would submit/interpret the newlines
    await userEvent.paste(MULTILINE);
    expect(desc.value).toBe(MULTILINE);
    expect(desc.value.split("\n")).toHaveLength(4);
  });

  it("submits the multi-line description through to createTool intact", async () => {
    await openCreateForm();
    await userEvent.type(screen.getByPlaceholderText(NAME), "order_status");
    const desc = screen.getByPlaceholderText(DESC);
    await userEvent.click(desc);
    await userEvent.paste(MULTILINE);

    // http tools require a URL before the form will submit
    await userEvent.type(
      screen.getByPlaceholderText("https://api.example.com/orders/{{order_id}}"),
      "https://api.example.com/orders",
    );
    await userEvent.click(screen.getByRole("button", { name: /create tool/i }));

    await waitFor(() => expect(mock(createTool)).toHaveBeenCalled());
    const payload = mock(createTool).mock.calls[0][0];
    expect(payload.description).toBe(MULTILINE);
    expect(payload.description).toContain("\n");
  });

  // The round-trip guard: a stored multi-line description must come BACK into
  // the edit form intact. If it were flattened on the way in, the next save
  // would silently overwrite the stored value with a single line.
  it("rehydrates a stored multi-line description into the edit form unchanged", async () => {
    renderWithProviders(<ToolsPage />);
    await waitFor(() => expect(screen.getByText("Get Order Status")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /edit/i }));

    const desc = screen.getByPlaceholderText(DESC) as HTMLTextAreaElement;
    expect(desc.tagName).toBe("TEXTAREA");
    expect(desc.value).toBe(MULTILINE);
  });

  it("keeps the multi-line value on an edit save (no flattening round-trip)", async () => {
    renderWithProviders(<ToolsPage />);
    await waitFor(() => expect(screen.getByText("Get Order Status")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /edit/i }));

    const desc = screen.getByPlaceholderText(DESC) as HTMLTextAreaElement;
    await userEvent.click(desc);
    await userEvent.paste("\nExtra line appended.");

    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(mock(updateTool)).toHaveBeenCalled());
    const [, payload] = mock(updateTool).mock.calls[0];
    expect(payload.description).toContain("Retrieves the current status of an order.");
    expect(payload.description).toContain("Extra line appended.");
    expect(payload.description.split("\n").length).toBeGreaterThan(4);
  });
});

describe("ToolsPage — PII de-anonymize flag", () => {
  it("creates an HTTP tool with the pii_deanonymize_allowed flag in the payload", async () => {
    mock(listTools).mockResolvedValue({ items: [HTTP_TOOL], total: 1 });
    mock(createTool).mockResolvedValue({ ...HTTP_TOOL, id: "new" });

    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);
    await screen.findByText("Get Order");

    await clickNewTool();
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
        }),
      ),
    );
  });

  it("creates a Python tool carrying python_code + the pii flag (default false)", async () => {
    mock(listTools).mockResolvedValue({ items: [HTTP_TOOL], total: 1 });
    mock(createTool).mockResolvedValue({ ...HTTP_TOOL, id: "py", type: "python" });

    const user = userEvent.setup();
    renderWithProviders(<ToolsPage />);
    await screen.findByText("Get Order");

    await clickNewTool();
    await user.click(screen.getByRole("radio", { name: /python/i }));
    await user.type(screen.getByPlaceholderText("get_order_status"), "crunch");
    await user.click(screen.getByRole("button", { name: /create tool/i }));

    await waitFor(() =>
      expect(createTool).toHaveBeenCalledWith(
        expect.objectContaining({ name: "crunch", type: "python", pii_deanonymize_allowed: false }),
      ),
    );
    expect(mock(createTool).mock.calls[0][0]).toHaveProperty("python_code");
  });

  it("pre-fills the edit form (name + pii checkbox) and sends pii in the update payload", async () => {
    mock(listTools).mockResolvedValue({ items: [HTTP_TOOL], total: 1 });
    mock(updateTool).mockResolvedValue(HTTP_TOOL);

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
        expect.objectContaining({ pii_deanonymize_allowed: true }),
      ),
    );
  });
});

describe("ToolsPage — MCP-sourced rows", () => {
  it("renders an mcp_tool row read-only: no Edit/Delete, a link to its source server", async () => {
    mock(listTools).mockResolvedValue({ items: [MCP_TOOL], total: 1 });
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

  // Why a Source column: a discovered tool's display_name is the BARE upstream
  // name, so two servers each exposing `search` render as two identical rows.
  // Without the origin on the row they are indistinguishable.
  it("names the source server in its own column", async () => {
    mock(listTools).mockResolvedValue({ items: [MCP_TOOL], total: 1 });
    renderWithProviders(<ToolsPage />);

    const row = (await screen.findByText("github-mcp__search_issues")).closest("tr")!;
    expect(within(row).getByText("github-mcp")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /source/i })).toBeInTheDocument();
  });

  it("leaves the Source cell blank for a native tool", async () => {
    mock(listTools).mockResolvedValue({ items: [HTTP_TOOL], total: 1 });
    renderWithProviders(<ToolsPage />);

    const row = (await screen.findByText("Get Order")).closest("tr")!;
    expect(within(row).getByTestId("tool-source")).toHaveTextContent("—");
  });

  // An mcp_tool must never reach the edit form. The form's type field is a
  // two-value enum, and its prefill maps anything that is not `python` to
  // `http` — so an MCP row opened for edit would silently present itself as an
  // HTTP tool and save back as one. The row being read-only is what makes that
  // unreachable; this pins it.
  it("offers no path to edit an mcp_tool as an http tool", async () => {
    mock(listTools).mockResolvedValue({ items: [MCP_TOOL], total: 1 });
    renderWithProviders(<ToolsPage />);
    await screen.findByText("github-mcp__search_issues");

    expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();
    expect(screen.queryByDisplayValue("github-mcp__search_issues")).toBeNull();
  });
});

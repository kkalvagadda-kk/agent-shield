import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
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

// Placeholders are the only stable handles — Field renders a <label> with no
// htmlFor, so getByLabelText cannot associate them.
const DESC = /Retrieves the current status of an order/i;
const NAME = "get_order_status";

beforeEach(() => {
  vi.clearAllMocks();
  mock(listTools).mockResolvedValue({ items: [EXISTING_TOOL] });
  mock(listAuthConfigs).mockResolvedValue({ items: [] });
  mock(createTool).mockResolvedValue({ ...EXISTING_TOOL, id: "t2" });
  mock(updateTool).mockResolvedValue(EXISTING_TOOL);
});

async function openCreateForm() {
  renderWithProviders(<ToolsPage />);
  await userEvent.click((await screen.findAllByRole("button", { name: /new tool/i }))[0]);
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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import ToolsPage from "./ToolsPage";

vi.mock("../api/registryApi", () => ({
  createTool: vi.fn(),
  deleteTool: vi.fn(),
  listAuthConfigs: vi.fn(),
  listAllTools: vi.fn(),
  updateTool: vi.fn(),
  getMyTeam: vi.fn(),
  // Reached only through UnpublishToolDialog. The mock factory replaces the WHOLE
  // module, so an omitted export is `undefined` at the call site rather than a
  // missing-mock error — the dialog would fail with "not a function" and the failure
  // would name the dialog, not this list.
  listAgentsForTool: vi.fn(),
  unpublishTool: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

// Decision 46: the Team field is admin-only. useAuth's default context already answers
// isAtLeast() => false, so the DEFAULT for every test here is the non-admin path — which
// is the one that regressed. The admin case overrides this mock explicitly.
const isAtLeastMock = vi.fn<(r: string) => boolean>(() => false);
// Decision 47 step E: the unpublish affordance reads sub/team/role off the same context.
// Mutable so a test can BE the creator, a teammate, or an admin. Defaults to none of the
// three, so every pre-existing test keeps the "no Unpublish button" world it was written in.
const authState: { sub: string | null; team: string | null; role: string | null } = {
  sub: null, team: null, role: null,
};
vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({
    isAtLeast: isAtLeastMock,
    user: authState.sub ? { sub: authState.sub } : null,
    team: authState.team,
    role: authState.role,
    logout: vi.fn(),
  }),
}));

import {
  createTool, listAuthConfigs, listAllTools, updateTool, getMyTeam,
  listAgentsForTool, unpublishTool,
} from "../api/registryApi";

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
  mock(listAllTools).mockResolvedValue([EXISTING_TOOL]);
  mock(listAuthConfigs).mockResolvedValue({ items: [], total: 0 });
  mock(getMyTeam).mockResolvedValue({ team: "platform", namespace: "agents-platform", grants: [] });
  isAtLeastMock.mockReturnValue(false);
  authState.sub = null;
  authState.team = null;
  authState.role = null;
  mock(createTool).mockResolvedValue({ ...EXISTING_TOOL, id: "t2" });
  mock(updateTool).mockResolvedValue(EXISTING_TOOL);
  mock(listAgentsForTool).mockResolvedValue({ items: [], total: 0 });
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
    mock(listAllTools).mockResolvedValue([HTTP_TOOL]);
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
    mock(listAllTools).mockResolvedValue([HTTP_TOOL]);
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
    mock(listAllTools).mockResolvedValue([HTTP_TOOL]);
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
    mock(listAllTools).mockResolvedValue([MCP_TOOL]);
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
    mock(listAllTools).mockResolvedValue([MCP_TOOL]);
    renderWithProviders(<ToolsPage />);

    const row = (await screen.findByText("github-mcp__search_issues")).closest("tr")!;
    expect(within(row).getByText("github-mcp")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /source/i })).toBeInTheDocument();
  });

  it("leaves the Source cell blank for a native tool", async () => {
    mock(listAllTools).mockResolvedValue([HTTP_TOOL]);
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
    mock(listAllTools).mockResolvedValue([MCP_TOOL]);
    renderWithProviders(<ToolsPage />);
    await screen.findByText("github-mcp__search_issues");

    expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();
    expect(screen.queryByDisplayValue("github-mcp__search_issues")).toBeNull();
  });
});


// ---------------------------------------------------------------------------
// Decision 46 — the creating team owns the tool, and the backend DERIVES it.
//
// 0.2.267 made `owner_team` come from the caller's team assignment and started
// answering 403 when a non-admin asks for another team. The form kept offering a
// free-text Team input to everybody, so a contributor who typed anything but their own
// team hit a 403 they could not act on — the UI was still presenting a choice the
// server had stopped honouring. These two cases pin both halves: the field is not
// editable for a non-admin AND the payload stops carrying it, because a read-only input
// that still submits its value is the same bug wearing a disguise.
// ---------------------------------------------------------------------------
describe("ToolsPage — tool ownership (Decision 46)", () => {
  it("shows a NON-ADMIN their own team read-only and omits owner_team from the payload", async () => {
    await openCreateForm();

    const teamField = await screen.findByTestId("tool-owner-team-readonly");
    expect(teamField).toHaveValue("platform");
    expect(teamField).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText(NAME), "order_status");
    await userEvent.type(
      screen.getByPlaceholderText("https://api.example.com/orders/{{order_id}}"),
      "https://api.example.com/orders",
    );
    await userEvent.click(screen.getByRole("button", { name: /create tool/i }));

    await waitFor(() => expect(mock(createTool)).toHaveBeenCalled());
    // Not "owner_team is empty" — absent. The server derives it; sending any value is the
    // client asserting something it does not get to choose.
    expect(mock(createTool).mock.calls[0][0]).not.toHaveProperty("owner_team");
  });

  it("lets a PLATFORM-ADMIN type a team and sends it", async () => {
    isAtLeastMock.mockImplementation((r: string) => r === "platform-admin");
    await openCreateForm();

    expect(screen.queryByTestId("tool-owner-team-readonly")).toBeNull();
    const teamInput = screen.getByPlaceholderText("platform-team");
    await userEvent.type(teamInput, "operations");

    await userEvent.type(screen.getByPlaceholderText(NAME), "order_status");
    await userEvent.type(
      screen.getByPlaceholderText("https://api.example.com/orders/{{order_id}}"),
      "https://api.example.com/orders",
    );
    await userEvent.click(screen.getByRole("button", { name: /create tool/i }));

    await waitFor(() => expect(mock(createTool)).toHaveBeenCalled());
    expect(mock(createTool).mock.calls[0][0]).toMatchObject({ owner_team: "operations" });
  });
});

// ---------------------------------------------------------------------------
// Decision 47 step E — catalog visibility and the reverse of the cascade
// ---------------------------------------------------------------------------
const PUBLISHED_TOOL = {
  id: "pub-1",
  name: "issue_refund",
  display_name: "Issue Refund",
  description: "Refund a payment",
  type: "http",
  risk_level: "high" as const,
  owner_team: "platform",
  status: "active",
  publish_status: "published",
  created_by: "alice",
  http_method: "POST",
  http_url: "https://payments.internal/refund",
  config: {},
};

const PRIVATE_TOOL = {
  ...PUBLISHED_TOOL,
  id: "priv-1",
  name: "draft_tool",
  display_name: "Draft Tool",
  publish_status: "private",
};

describe("ToolsPage — catalog visibility (Decision 47)", () => {
  it("distinguishes Visibility from Status — two columns, two questions", async () => {
    mock(listAllTools).mockResolvedValue([PUBLISHED_TOOL, PRIVATE_TOOL]);
    renderWithProviders(<ToolsPage />);

    // The screen carried `status` (active/deprecated) alone until step E. After
    // migration 0080 made private the default, a user could not tell which of their
    // rows were org-wide — the field existed on the wire and nowhere on the page.
    expect(await screen.findByTestId("tool-visibility-issue_refund")).toHaveTextContent(
      "Published"
    );
    expect(screen.getByTestId("tool-visibility-draft_tool")).toHaveTextContent("Private");
  });

  it("offers no Unpublish to someone who is neither creator, teammate, nor admin", async () => {
    mock(listAllTools).mockResolvedValue([PUBLISHED_TOOL]);
    authState.sub = "bob";
    authState.team = "operations";
    authState.role = "contributor";
    renderWithProviders(<ToolsPage />);

    await screen.findByTestId("tool-visibility-issue_refund");
    expect(screen.queryByTestId("tool-unpublish-issue_refund")).toBeNull();
  });

  it("offers Unpublish to the CREATOR", async () => {
    mock(listAllTools).mockResolvedValue([PUBLISHED_TOOL]);
    authState.sub = "alice";
    authState.team = "operations"; // deliberately the WRONG team — the creator arm alone
    authState.role = "contributor";
    renderWithProviders(<ToolsPage />);
    expect(await screen.findByTestId("tool-unpublish-issue_refund")).toBeInTheDocument();
  });

  it("offers Unpublish to a member of the OWNING TEAM who did not create it", async () => {
    mock(listAllTools).mockResolvedValue([PUBLISHED_TOOL]);
    authState.sub = "carol";
    authState.team = "platform";
    authState.role = "contributor";
    renderWithProviders(<ToolsPage />);
    // Ownership is team-level (Decision 46) precisely so a tool does not become
    // unmaintainable when its creator leaves.
    expect(await screen.findByTestId("tool-unpublish-issue_refund")).toBeInTheDocument();
  });

  it("never offers Unpublish on an ALREADY-PRIVATE tool, even to its creator", async () => {
    mock(listAllTools).mockResolvedValue([PRIVATE_TOOL]);
    authState.sub = "alice";
    authState.team = "platform";
    authState.role = "platform-admin";
    renderWithProviders(<ToolsPage />);

    // The server answers 409 here, not 403. Rendering the control would put an error
    // in front of a user who did nothing wrong.
    await screen.findByTestId("tool-visibility-draft_tool");
    expect(screen.queryByTestId("tool-unpublish-draft_tool")).toBeNull();
  });

  it("names the published agents still bound, and unpublishes anyway", async () => {
    mock(listAllTools).mockResolvedValue([PUBLISHED_TOOL]);
    mock(listAgentsForTool).mockResolvedValue({
      items: [
        { id: "a1", name: "refund-bot", team: "platform", publish_status: "published" },
        { id: "a2", name: "draft-bot", team: "platform", publish_status: "private" },
      ],
      total: 2,
    });
    mock(unpublishTool).mockResolvedValue({ ...PUBLISHED_TOOL, publish_status: "private" });
    authState.sub = "alice";
    authState.team = "platform";
    authState.role = "contributor";
    renderWithProviders(<ToolsPage />);

    await userEvent.click(await screen.findByTestId("tool-unpublish-issue_refund"));
    const dialog = await screen.findByTestId("unpublish-tool-dialog");

    // The PUBLISHED one is the one that matters — a private agent's binding is not a
    // discoverability consequence anybody else can see.
    //
    // findBy, NOT getBy. The dialog mounts before its bound-agents query resolves, so a
    // synchronous getBy here passes only because the mock happens to settle within the
    // awaits above — a timing coincidence, not a logical guarantee. Same class as the
    // false pass in PublishReviewDrawer.test.tsx, where `toBeDisabled()` was green
    // because the drawer was still LOADING.
    expect(await within(dialog).findByTestId("unpublish-agent-refund-bot")).toBeInTheDocument();
    // Only meaningful AFTER the list has rendered — before that everything is absent.
    expect(within(dialog).queryByTestId("unpublish-agent-draft-bot")).toBeNull();

    // COURTESY, NOT A GATE. This assertion is the whole point of the case: a bound
    // published agent must not disable the button. If it ever does, any team can freeze
    // another team's tool in the catalog forever by binding it.
    const confirmBtn = within(dialog).getByTestId("unpublish-confirm");
    expect(confirmBtn).toBeEnabled();
    await userEvent.click(confirmBtn);
    await waitFor(() => expect(mock(unpublishTool)).toHaveBeenCalledWith("pub-1"));
  });

  it("surfaces the 403 sentence rather than a generic failure", async () => {
    mock(listAllTools).mockResolvedValue([PUBLISHED_TOOL]);
    mock(unpublishTool).mockRejectedValue({
      response: { data: { detail: "Cannot unpublish 'issue_refund': it is owned by team 'platform'." } },
    });
    authState.sub = "alice";
    authState.team = "platform";
    authState.role = "contributor";
    renderWithProviders(<ToolsPage />);

    await userEvent.click(await screen.findByTestId("tool-unpublish-issue_refund"));
    await userEvent.click(await screen.findByTestId("unpublish-confirm"));

    // The server's detail names the owning team; "Failed to unpublish" would strip the
    // one piece of information that tells the user what to do next.
    const { toast } = await import("sonner");
    await waitFor(() =>
      expect(mock(toast.error)).toHaveBeenCalledWith(
        expect.stringContaining("owned by team 'platform'")
      )
    );
  });
});

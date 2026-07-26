import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import McpServersPage from "./McpServersPage";

vi.mock("../api/mcpServersApi", () => ({
  listMcpServers: vi.fn(),
  createMcpServer: vi.fn(),
}));
vi.mock("../api/registryApi", () => ({
  listAuthConfigs: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listMcpServers, createMcpServer } from "../api/mcpServersApi";
import { toast } from "sonner";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const NOW = new Date().toISOString();

const SERVER = {
  id: "srv-1",
  name: "github-mcp",
  description: "GitHub's MCP server",
  server_url: "https://mcp.example.com/mcp",
  transport: "streamable_http",
  auth_config_id: null,
  owner_team: "platform",
  identity_mode: "none",
  is_external: true,
  external_auth_mode: "static",
  transport_config: null,
  health_detail: { last_error: null, last_success_at: NOW, consecutive_failures: 0, schema_drift: [] },
  list_changed_supported: false,
  scan_results: true,
  status: "connected",
  last_synced_at: NOW,
  discovered_tool_count: 7,
  created_at: NOW,
  updated_at: NOW,
};

describe("McpServersPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("lists MCP servers from the API with status + tool count", async () => {
    mk(listMcpServers).mockResolvedValue([SERVER]);
    renderWithProviders(<McpServersPage />);

    expect(await screen.findByText("github-mcp")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("External")).toBeInTheDocument();
  });

  it("shows an empty state when there are no servers", async () => {
    mk(listMcpServers).mockResolvedValue([]);
    renderWithProviders(<McpServersPage />);

    expect(await screen.findByText(/No MCP servers registered yet/i)).toBeInTheDocument();
  });

  it("registers a server with the right createMcpServer payload (internal + identity mode)", async () => {
    mk(listMcpServers).mockResolvedValue([]);
    mk(createMcpServer).mockResolvedValue({ ...SERVER, id: "srv-new", is_external: false, discovered_tool_count: 2 });

    const user = userEvent.setup();
    renderWithProviders(<McpServersPage />);
    await screen.findByText(/No MCP servers registered yet/i);

    await user.click(screen.getByRole("button", { name: /register server/i }));
    await user.type(screen.getByPlaceholderText("github-mcp"), "internal-mcp");
    await user.type(screen.getByPlaceholderText("https://mcp.example.com/mcp"), "http://svc.internal/mcp");
    await user.selectOptions(screen.getByLabelText("Identity mode"), "service_identity");
    await user.click(screen.getByRole("button", { name: /^register$/i }));

    await waitFor(() =>
      expect(createMcpServer).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "internal-mcp",
          server_url: "http://svc.internal/mcp",
          transport: "streamable_http",
          is_external: false,
          identity_mode: "service_identity",
          scan_results: true,
        })
      )
    );
  });

  it("surfaces a 409 name-taken error as a toast", async () => {
    mk(listMcpServers).mockResolvedValue([]);
    mk(createMcpServer).mockRejectedValue({ response: { data: { detail: "MCP server name already taken" } } });

    const user = userEvent.setup();
    renderWithProviders(<McpServersPage />);
    await screen.findByText(/No MCP servers registered yet/i);

    await user.click(screen.getByRole("button", { name: /register server/i }));
    await user.type(screen.getByPlaceholderText("github-mcp"), "dup");
    await user.type(screen.getByPlaceholderText("https://mcp.example.com/mcp"), "http://x/mcp");
    await user.click(screen.getByRole("button", { name: /^register$/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("MCP server name already taken"));
  });

  // --- Phase 4 (WS-2) — register modal OAuth toggle ----------------------
  it("hides the credential picker + sends external_auth_mode='oauth' when the OAuth toggle is on", async () => {
    mk(listMcpServers).mockResolvedValue([]);
    mk(createMcpServer).mockResolvedValue({ ...SERVER, id: "srv-oauth", external_auth_mode: "oauth", discovered_tool_count: 0 });

    const user = userEvent.setup();
    renderWithProviders(<McpServersPage />);
    await screen.findByText(/No MCP servers registered yet/i);

    await user.click(screen.getByRole("button", { name: /register server/i }));
    await user.type(screen.getByPlaceholderText("github-mcp"), "oauth-mcp");
    await user.type(screen.getByPlaceholderText("https://mcp.example.com/mcp"), "https://mcp.example.com/mcp");

    // The OAuth toggle appears only after External is picked.
    expect(screen.queryByLabelText(/OAuth 2.1 authorization/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "External" }));
    // Credential picker is present for external + static…
    expect(screen.getByLabelText("Credential")).toBeInTheDocument();

    // Turn OAuth on → credential picker disappears (OAuth replaces the static cred).
    await user.click(screen.getByLabelText(/OAuth 2.1 authorization/i));
    expect(screen.queryByLabelText("Credential")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^register$/i }));

    await waitFor(() =>
      expect(createMcpServer).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "oauth-mcp",
          is_external: true,
          external_auth_mode: "oauth",
        })
      )
    );
    // No static credential is sent alongside an OAuth server.
    expect(mk(createMcpServer).mock.calls[0][0]).not.toHaveProperty("auth_config_id");
  });

  it("sends external_auth_mode='static' + keeps the credential picker for an external non-OAuth server", async () => {
    mk(listMcpServers).mockResolvedValue([]);
    mk(createMcpServer).mockResolvedValue({ ...SERVER, id: "srv-ext", external_auth_mode: "static" });

    const user = userEvent.setup();
    renderWithProviders(<McpServersPage />);
    await screen.findByText(/No MCP servers registered yet/i);

    await user.click(screen.getByRole("button", { name: /register server/i }));
    await user.type(screen.getByPlaceholderText("github-mcp"), "ext-mcp");
    await user.type(screen.getByPlaceholderText("https://mcp.example.com/mcp"), "https://x/mcp");
    await user.click(screen.getByRole("radio", { name: "External" }));

    // OAuth left OFF → the credential picker stays visible.
    expect(screen.getByLabelText("Credential")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^register$/i }));

    await waitFor(() =>
      expect(createMcpServer).toHaveBeenCalledWith(
        expect.objectContaining({ is_external: true, external_auth_mode: "static" })
      )
    );
  });

  it("never shows the OAuth toggle for an internal server", async () => {
    mk(listMcpServers).mockResolvedValue([]);

    const user = userEvent.setup();
    renderWithProviders(<McpServersPage />);
    await screen.findByText(/No MCP servers registered yet/i);

    await user.click(screen.getByRole("button", { name: /register server/i }));
    // Default scope is Internal — no OAuth toggle, credential picker present.
    expect(screen.queryByLabelText(/OAuth 2.1 authorization/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Credential")).toBeInTheDocument();
  });

  it("registers, then a fresh list GET re-render shows the server (save → reload)", async () => {
    const created = { ...SERVER, id: "srv-new", name: "fresh-mcp", is_external: false };
    // Empty first; after create + invalidate, the refetch returns the new server
    // — the row comes from a SECOND listMcpServers call, not from client state.
    mk(listMcpServers).mockResolvedValueOnce([]).mockResolvedValue([created]);
    mk(createMcpServer).mockResolvedValue(created);

    const user = userEvent.setup();
    renderWithProviders(<McpServersPage />);
    await screen.findByText(/No MCP servers registered yet/i);

    await user.click(screen.getByRole("button", { name: /register server/i }));
    await user.type(screen.getByPlaceholderText("github-mcp"), "fresh-mcp");
    await user.type(screen.getByPlaceholderText("https://mcp.example.com/mcp"), "http://x/mcp");
    await user.click(screen.getByRole("button", { name: /^register$/i }));

    await waitFor(() => expect(createMcpServer).toHaveBeenCalled());
    // The invalidated list refetches → the new server row renders from the backend.
    expect(await screen.findByText("fresh-mcp")).toBeInTheDocument();
    expect(mk(listMcpServers).mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});

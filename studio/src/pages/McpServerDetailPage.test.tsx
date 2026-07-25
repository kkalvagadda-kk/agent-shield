import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import McpServerDetailPage from "./McpServerDetailPage";

vi.mock("../api/mcpServersApi", () => ({
  getMcpServer: vi.fn(),
  updateMcpServer: vi.fn(),
  syncMcpServer: vi.fn(),
  deleteMcpServer: vi.fn(),
}));
vi.mock("../api/registryApi", () => ({
  listAuthConfigs: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { getMcpServer, deleteMcpServer, syncMcpServer } from "../api/mcpServersApi";
import { toast } from "sonner";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const NOW = new Date().toISOString();

const DETAIL = {
  id: "srv-1",
  name: "github-mcp",
  description: "GitHub MCP",
  server_url: "https://mcp.example.com/mcp",
  transport: "streamable_http",
  auth_config_id: null,
  owner_team: "platform",
  identity_mode: "none",
  is_external: true,
  transport_config: null,
  health_detail: { last_error: null, last_success_at: NOW, consecutive_failures: 0, schema_drift: [] },
  list_changed_supported: false,
  scan_results: true,
  status: "connected",
  last_synced_at: NOW,
  discovered_tool_count: 2,
  created_at: NOW,
  updated_at: NOW,
  tools: [
    { id: "t1", name: "github-mcp__search_issues", display_name: null, description: null, mcp_tool_name: "search_issues", input_schema: null, risk_level: "low", status: "active", pii_deanonymize_allowed: false },
    { id: "t2", name: "github-mcp__old_tool", display_name: null, description: null, mcp_tool_name: "old_tool", input_schema: null, risk_level: "medium", status: "inactive", pii_deanonymize_allowed: false },
  ],
};

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/mcp-servers/:id" element={<McpServerDetailPage />} />
    </Routes>,
    { routerEntries: ["/mcp-servers/srv-1"] }
  );
}

describe("McpServerDetailPage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders discovered tools, greying inactive ones", async () => {
    mk(getMcpServer).mockResolvedValue(DETAIL);
    renderDetail();

    expect(await screen.findByText("github-mcp__search_issues")).toBeInTheDocument();
    // The vanished tool is still listed (never hard-deleted) with an inactive badge.
    expect(screen.getByText("github-mcp__old_tool")).toBeInTheDocument();
    expect(screen.getByText("inactive")).toBeInTheDocument();
  });

  it("shows the error banner + Retry for a status='error' server", async () => {
    mk(getMcpServer).mockResolvedValue({
      ...DETAIL,
      status: "error",
      health_detail: { last_error: "connection refused", last_success_at: null, consecutive_failures: 1, schema_drift: [] },
      tools: [],
    });
    renderDetail();

    expect(await screen.findByText(/This server is unreachable/i)).toBeInTheDocument();
    // Phase 2: last_error now renders in BOTH the banner and the Health panel.
    expect(screen.getAllByText("connection refused").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("surfaces the 409 blocking-agents message when delete is refused", async () => {
    mk(getMcpServer).mockResolvedValue(DETAIL);
    mk(deleteMcpServer).mockRejectedValue({
      response: {
        data: {
          detail: {
            message: "Cannot delete — discovered tools from this server are bound to agents.",
            blocking_tools: ["github-mcp__search_issues"],
            blocking_agents: ["support-bot", "triage-agent"],
          },
        },
      },
    });

    const user = userEvent.setup();
    renderDetail();
    await screen.findByText("github-mcp__search_issues");

    await user.click(screen.getByRole("button", { name: /settings/i }));
    await user.click(await screen.findByRole("button", { name: /delete server/i }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("support-bot")
      )
    );
    expect(mk(toast.error).mock.calls[0][0]).toContain("Cannot delete");
  });

  it("re-runs discovery via Retry (sync)", async () => {
    mk(getMcpServer).mockResolvedValue({
      ...DETAIL,
      status: "error",
      health_detail: { last_error: "timeout", last_success_at: null, consecutive_failures: 1, schema_drift: [] },
      tools: [],
    });
    mk(syncMcpServer).mockResolvedValue({
      server: { ...DETAIL, status: "connected" },
      tools_added: 2, tools_updated: 0, tools_inactivated: 0, schema_drift_detected: [],
    });

    const user = userEvent.setup();
    renderDetail();
    await user.click(await screen.findByRole("button", { name: /retry/i }));

    await waitFor(() => expect(syncMcpServer).toHaveBeenCalledWith("srv-1"));
  });

  // --- Phase 2 (WS-A) — Health panel -------------------------------------
  it("renders the Health panel for a status='error' server (pill + failures + last_error)", async () => {
    mk(getMcpServer).mockResolvedValue({
      ...DETAIL,
      status: "error",
      health_detail: { last_error: "boom", last_success_at: null, consecutive_failures: 3, schema_drift: [] },
      tools: [],
    });
    renderDetail();

    // The Health pill reflects the error status.
    expect(await screen.findByText("Error")).toBeInTheDocument();
    // Consecutive failures surface (only shown when > 0).
    expect(screen.getByText("3")).toBeInTheDocument();
    // last_error renders in the Health panel too (and the banner) — hence getAllByText.
    expect(screen.getAllByText("boom").length).toBeGreaterThanOrEqual(1);
  });

  it("shows 'subscribed' + a connected pill when list_changed_supported", async () => {
    mk(getMcpServer).mockResolvedValue({ ...DETAIL, status: "connected", list_changed_supported: true });
    renderDetail();

    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("subscribed")).toBeInTheDocument();
  });

  it("flags on_behalf_of identity as pending Decision 29", async () => {
    mk(getMcpServer).mockResolvedValue({ ...DETAIL, identity_mode: "on_behalf_of" });
    renderDetail();

    expect(await screen.findByText(/pending — Decision 29/)).toBeInTheDocument();
  });
});

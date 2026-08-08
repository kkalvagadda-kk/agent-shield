import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import AgentDetailPage from "./AgentDetailPage";

vi.mock("../api/registryApi", () => ({
  getAgent: vi.fn(),
  getDeployments: vi.fn(),
  listVersions: vi.fn(),
  publishAgent: vi.fn(),
  deleteAgentVersion: vi.fn(),
  updateAgent: vi.fn(),
  listProviders: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  listAllTools: vi.fn().mockResolvedValue([]),
  // SettingsTab (settings tab) statically imports these; not exercised here.
  listTriggers: vi.fn(),
  createTrigger: vi.fn(),
  deleteTrigger: vi.fn(),
  updateAgentMemory: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
vi.mock("../api/knowledgeApi", () => ({
  listKBs: vi.fn().mockResolvedValue([]),
  getAgentKnowledgeBases: vi.fn().mockResolvedValue([]),
  bindAgent: vi.fn().mockResolvedValue({}),
  unbindAgent: vi.fn().mockResolvedValue(undefined),
}));

import { getAgent, getDeployments, listVersions, deleteAgentVersion, updateAgent, listTriggers } from "../api/registryApi";
import { listKBs, getAgentKnowledgeBases, bindAgent } from "../api/knowledgeApi";
import { publishAgent } from "../api/registryApi";
import { toast } from "sonner";

const NOW = new Date().toISOString();
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/agents/:name" element={<AgentDetailPage />} />
    </Routes>,
    { routerEntries: ["/agents/my-agent"] }
  );
}

describe("AgentDetailPage — Sandbox Deployments tab", () => {
  beforeEach(() => {
    mock(getAgent).mockResolvedValue({
      name: "my-agent",
      team: "default",
      agent_type: "declarative",
      status: "active",
      publish_status: "private",
      execution_shape: "reactive",
      created_at: NOW,
      updated_at: NOW,
      created_by: "dev",
      memory_enabled: false,
    });
    mock(listVersions).mockResolvedValue([{ id: "v1", version_number: 1, eval_passed: false, created_at: NOW, notes: null }]);
    mock(getDeployments).mockResolvedValue([
      {
        id: "dep-1",
        name: "my-agent-ab12",
        status: "running",
        environment: "sandbox",
        version_id: "v1",
        k8s_namespace: "agents-default",
        deployed_at: NOW,
      },
    ]);
  });

  it("lists sandbox deployments with a link to the deployment overview", async () => {
    renderPage();
    const link = await screen.findByRole("link", { name: /my-agent-ab12/ });
    expect(link).toHaveAttribute("href", "/agents/my-agent/d/dep-1");
  });

  it("hides non-sandbox deployments", async () => {
    mock(getDeployments).mockResolvedValue([
      { id: "p1", name: "my-agent-prod", status: "running", environment: "production",
        version_id: "v1", k8s_namespace: "agents-default", deployed_at: NOW },
    ]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/No sandbox deployments yet/i)).toBeInTheDocument()
    );
  });

  it("shows empty state when there are no deployments", async () => {
    mock(getDeployments).mockResolvedValue([]);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/No sandbox deployments yet/i)).toBeInTheDocument()
    );
  });

  it("switches to the versions tab and shows version list", async () => {
    renderPage();
    await screen.findByRole("link", { name: /my-agent-ab12/ });
    await userEvent.click(screen.getByRole("button", { name: "versions" }));
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /version/i })).toBeInTheDocument();
  });
});

describe("AgentDetailPage — Versions tab", () => {
  beforeEach(() => {
    mock(getAgent).mockResolvedValue({
      name: "my-agent",
      team: "default",
      agent_type: "declarative",
      status: "active",
      publish_status: "private",
      execution_shape: "reactive",
      created_at: NOW,
      updated_at: NOW,
      created_by: "dev",
      memory_enabled: false,
    });
    mock(getDeployments).mockResolvedValue([]);
    mock(listVersions).mockResolvedValue([
      { id: "v1", version_number: 1, eval_passed: true, created_at: NOW, notes: "initial" },
      { id: "v2", version_number: 2, eval_passed: false, created_at: NOW, notes: null },
    ]);
    mock(deleteAgentVersion).mockResolvedValue({ deleted_version_id: "v2", terminated_deployments: 0 });
  });

  function renderVersionsTab() {
    renderPage();
  }

  it("shows eval passed/not-passed indicators", async () => {
    renderVersionsTab();
    await userEvent.click(await screen.findByRole("button", { name: "versions" }));
    expect(await screen.findByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("Not passed")).toBeInTheDocument();
  });

  it("renders two version rows", async () => {
    renderVersionsTab();
    await userEvent.click(await screen.findByRole("button", { name: "versions" }));
    expect(await screen.findByText("v1")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
  });

  it("shows delete button per version row", async () => {
    renderVersionsTab();
    await userEvent.click(await screen.findByRole("button", { name: "versions" }));
    await screen.findByText("v1");
    const deleteBtns = screen.getAllByTitle("Delete version");
    expect(deleteBtns.length).toBe(2);
  });

  it("calls deleteAgentVersion on confirm", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderVersionsTab();
    await userEvent.click(await screen.findByRole("button", { name: "versions" }));
    await screen.findByText("v2");
    const deleteBtns = screen.getAllByTitle("Delete version");
    await userEvent.click(deleteBtns[1]);
    await waitFor(() => expect(deleteAgentVersion).toHaveBeenCalledWith("my-agent", "v2"));
    vi.restoreAllMocks();
  });

  it("shows notes column value", async () => {
    renderVersionsTab();
    await userEvent.click(await screen.findByRole("button", { name: "versions" }));
    expect(await screen.findByText("initial")).toBeInTheDocument();
  });
});

describe("AgentDetailPage — Settings tab (agent_class)", () => {
  beforeEach(() => {
    mock(getAgent).mockResolvedValue({
      id: "agent-uuid-1",
      name: "my-agent",
      team: "default",
      agent_type: "declarative",
      status: "active",
      publish_status: "private",
      execution_shape: "reactive",
      agent_class: "user_delegated",
      created_at: NOW,
      updated_at: NOW,
      created_by: "dev",
      memory_enabled: false,
      metadata: {},
    });
    mock(getDeployments).mockResolvedValue([]);
    mock(listVersions).mockResolvedValue([]);
    mock(listTriggers).mockResolvedValue([]);
    mock(updateAgent).mockResolvedValue({ name: "my-agent" });
    mock(listKBs).mockResolvedValue([]);
    mock(getAgentKnowledgeBases).mockResolvedValue([]);
  });

  it("changing the Authority class to daemon and saving PATCHes agent_class", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "settings" }));
    const classSelect = await screen.findByLabelText(/Authority/i);
    await userEvent.selectOptions(classSelect, "daemon");
    await userEvent.click(screen.getByRole("button", { name: /Save Changes/i }));
    await waitFor(() => expect(updateAgent).toHaveBeenCalled());
    expect(mock(updateAgent).mock.calls[0][1]).toEqual(
      expect.objectContaining({ agent_class: "daemon" })
    );
  });
});

describe("AgentDetailPage — Settings tab (Knowledge Bases)", () => {
  beforeEach(() => {
    mock(getAgent).mockResolvedValue({
      id: "agent-uuid-1",
      name: "my-agent",
      team: "default",
      agent_type: "declarative",
      status: "active",
      publish_status: "private",
      execution_shape: "reactive",
      agent_class: "user_delegated",
      created_at: NOW,
      updated_at: NOW,
      created_by: "dev",
      memory_enabled: false,
      metadata: {},
    });
    mock(getDeployments).mockResolvedValue([]);
    mock(listVersions).mockResolvedValue([]);
    mock(listTriggers).mockResolvedValue([]);
    mock(updateAgent).mockResolvedValue({ name: "my-agent" });
    // The team's tool list INCLUDES knowledge_search; the picker must hide it.
    mock(getAgent).mockResolvedValue({
      id: "agent-uuid-1", name: "my-agent", team: "default", agent_type: "declarative",
      status: "active", publish_status: "private", execution_shape: "reactive",
      agent_class: "user_delegated", created_at: NOW, updated_at: NOW, created_by: "dev",
      memory_enabled: false, metadata: {},
    });
    mock(listKBs).mockResolvedValue([
      { id: "kb-1", team: "default", name: "Product Docs", description: "", created_by: "u", created_at: NOW, updated_at: NOW, source_count: 2, ready_count: 2, attached_agents: [] },
    ]);
  });

  it("pre-selects the agent's currently-bound KB and reconciles bind/unbind on save", async () => {
    // Agent starts bound to kb-1 → the checkbox is checked on load.
    mock(getAgentKnowledgeBases).mockResolvedValue([{ kb_id: "kb-1", name: "Product Docs" }]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "settings" }));
    const picker = await screen.findByTestId("kb-picker");
    // KBs are now browsed in a tile drawer — the list is not inline.
    await userEvent.click(within(picker).getByRole("button", { name: /add from catalog/i }));
    const cb = within(picker).getByRole("checkbox") as HTMLInputElement;
    await waitFor(() => expect(cb.checked).toBe(true));
    // Unbind it → save must call updateAgent AND unbindAgent for the removed KB.
    await userEvent.click(cb);
    await userEvent.click(within(picker).getByRole("button", { name: /^done$/i }));
    await userEvent.click(screen.getByRole("button", { name: /Save Changes/i }));
    await waitFor(() => expect(updateAgent).toHaveBeenCalled());
    const { unbindAgent } = await import("../api/knowledgeApi");
    await waitFor(() => expect(unbindAgent).toHaveBeenCalledWith("kb-1", "agent-uuid-1"));
    // knowledge_search is never written into metadata.tools.
    expect(mock(updateAgent).mock.calls[0][1].metadata.tools).not.toContain("knowledge_search");
  });

  it("binding a new KB on save calls bindAgent (auto-attaches knowledge_search)", async () => {
    mock(getAgentKnowledgeBases).mockResolvedValue([]); // starts unbound
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "settings" }));
    const picker = await screen.findByTestId("kb-picker");
    await userEvent.click(within(picker).getByRole("button", { name: /add from catalog/i }));
    await userEvent.click(within(picker).getByRole("checkbox"));
    await userEvent.click(within(picker).getByRole("button", { name: /^done$/i }));
    await userEvent.click(screen.getByRole("button", { name: /Save Changes/i }));
    await waitFor(() => expect(bindAgent).toHaveBeenCalledWith("kb-1", "agent-uuid-1"));
  });
});


// ---------------------------------------------------------------------------
// Decision 47 option C — publishing an agent cascades its own-team tools, and a bound
// tool that is still private and owned by ANOTHER team blocks the request (422).
//
// The 422 `detail` is an OBJECT, not a string. The handler's fallback stringifies only
// strings, so without a branch for this code the user would get the generic
// "Failed to submit publish request." — and the fix ("ask that team to publish it, or
// unbind it") is impossible to act on without knowing which tool and whose. An error
// path nobody tests is how "[object Object]" reaches a screen.
// ---------------------------------------------------------------------------
describe("AgentDetailPage — cross-team tool blocks publish (Decision 47)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(getAgent).mockResolvedValue({
      name: "my-agent", team: "platform", agent_type: "declarative", status: "active",
      publish_status: "private", execution_shape: "reactive",
      created_at: NOW, updated_at: NOW, created_by: "dev", memory_enabled: false,
    });
    mock(listVersions).mockResolvedValue([
      { id: "v1", version_number: 1, eval_passed: true, created_at: NOW, notes: null },
    ]);
    mock(getDeployments).mockResolvedValue([]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("names the blocking tools and their owning team", async () => {
    mock(publishAgent).mockRejectedValue({
      response: {
        data: {
          detail: {
            error: "tool_not_publishable_cross_team",
            agent_team: "platform",
            tools: [
              { id: "t1", name: "issue_refund", owner_team: "finance", publish_status: "private" },
              { id: "t2", name: "lookup_ledger", owner_team: "finance", publish_status: "private" },
            ],
          },
        },
      },
    });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /publish/i }));

    await waitFor(() => expect(mock(toast.error)).toHaveBeenCalled());
    const msg = String(mock(toast.error).mock.calls[0][0]);
    expect(msg).toContain("issue_refund");
    expect(msg).toContain("lookup_ledger");
    expect(msg).toContain("finance");
    // The generic fallback must NOT be what the user sees.
    expect(msg).not.toBe("Failed to submit publish request.");
  });

  it("still degrades sensibly when the payload carries no tool list", async () => {
    mock(publishAgent).mockRejectedValue({
      response: { data: { detail: { error: "tool_not_publishable_cross_team" } } },
    });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /publish/i }));

    await waitFor(() => expect(mock(toast.error)).toHaveBeenCalled());
    const msg = String(mock(toast.error).mock.calls[0][0]);
    expect(msg).toContain("another team");
    expect(msg).not.toContain("undefined");
  });
});

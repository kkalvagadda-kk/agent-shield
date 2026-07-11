import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import AgentChatPage from "./AgentChatPage";

vi.mock("../api/registryApi", () => ({
  getAgent: vi.fn(),
  getDeployments: vi.fn(),
  startAgentChat: vi.fn(),
  startDeploymentChat: vi.fn(),
  getChatApprovalStatus: vi.fn(),
  getSessionApprovals: vi.fn(),
  decideSandboxApproval: vi.fn(),
}));
vi.mock("../lib/keycloak", () => ({
  getKeycloak: () => ({ authenticated: true, token: "tok", updateToken: vi.fn() }),
}));

import {
  getAgent,
  getDeployments,
  startDeploymentChat,
  getChatApprovalStatus,
  getSessionApprovals,
  decideSandboxApproval,
} from "../api/registryApi";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) } as MessageEvent);
  }
}
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
  MockEventSource;
Element.prototype.scrollIntoView = vi.fn();

const DEP_URL = ["/agents/serper-agent-4/d/dep-1/chat"];

function renderChat() {
  return renderWithProviders(
    <Routes>
      <Route path="/agents/:name/d/:depId/chat" element={<AgentChatPage />} />
    </Routes>,
    { routerEntries: DEP_URL }
  );
}

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

describe("AgentChatPage — deployment HITL", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getAgent).mockResolvedValue({ name: "serper-agent-4", description: "searches the web" });
    mock(startDeploymentChat).mockResolvedValue({
      run_id: "run-1",
      stream_url: "/api/v1/agents/serper-agent-4/deployments/dep-1/chat/run-1/stream",
    });
    mock(getSessionApprovals).mockResolvedValue([
      { approval_id: "ap-1", run_id: "run-1", status: "pending", tool: "web_search", args: {}, risk: "high", reasoning: "why", requested_by: "platform-admin", requested_by_team: "platform", context: "sandbox", created_at: null, decided: false },
    ]);
    mock(getChatApprovalStatus).mockResolvedValue({ run_id: "run-1", approval_id: "ap-1", status: "pending", decided: false });
    mock(decideSandboxApproval).mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  async function sendAndInterrupt() {
    renderChat();
    // Let the deployments query resolve so the env-aware branch is correct.
    await waitFor(() => expect(getDeployments).toHaveBeenCalled());
    await Promise.resolve();
    await userEvent.type(screen.getByPlaceholderText(/message/i), "weather in Austin");
    await userEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(startDeploymentChat).toHaveBeenCalled());
    await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(0));
    MockEventSource.instances[0].emit({
      type: "approval_requested",
      approval_id: "ap-1",
      tool: "web_search",
      risk: "high",
      args: { query: "weather in Austin" },
    });
  }

  describe("sandbox deployment → self-approve panel", () => {
    beforeEach(() => {
      mock(getDeployments).mockResolvedValue([
        { id: "dep-1", environment: "sandbox", status: "running" },
      ]);
    });

    it("opens the self-approve panel (not the console waiting banner)", async () => {
      await sendAndInterrupt();
      await waitFor(() =>
        expect(screen.getByTestId("sandbox-approval-panel")).toBeInTheDocument()
      );
      expect(screen.getByTestId("sandbox-approval-row")).toHaveTextContent("web_search");
      // Inline Approve/Deny exist here (sandbox self-approve); NO console banner.
      expect(screen.getByRole("button", { name: /^Approve$/ })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^Deny$/ })).toBeInTheDocument();
      expect(screen.queryByTestId("hitl-waiting-banner")).not.toBeInTheDocument();
    });

    it("Approve calls the decide endpoint and opens the resume stream", async () => {
      await sendAndInterrupt();
      await waitFor(() => expect(screen.getByTestId("sandbox-approval-panel")).toBeInTheDocument());
      await userEvent.click(screen.getByRole("button", { name: /^Approve$/ }));
      await waitFor(() => expect(decideSandboxApproval).toHaveBeenCalledWith("ap-1", "approved"));
      await waitFor(() =>
        expect(MockEventSource.instances.some((es) => es.url.includes("resume-stream"))).toBe(true)
      );
    });
  });

  describe("production deployment → console waiting banner", () => {
    beforeEach(() => {
      mock(getDeployments).mockResolvedValue([
        { id: "dep-1", environment: "production", status: "running" },
      ]);
    });

    it("shows the waiting banner and no inline approve/deny", async () => {
      await sendAndInterrupt();
      await waitFor(() =>
        expect(screen.getByTestId("hitl-waiting-banner")).toBeInTheDocument()
      );
      expect(screen.getByTestId("hitl-tool-name")).toHaveTextContent("web_search");
      expect(screen.getByText(/open approval console/i)).toBeInTheDocument();
      expect(screen.queryByTestId("sandbox-approval-panel")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Approve$/ })).not.toBeInTheDocument();
    });
  });
});

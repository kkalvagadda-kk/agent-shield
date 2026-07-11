import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import CatalogChatPage from "./CatalogChatPage";

vi.mock("../api/catalogApi", () => ({ getCatalogDetail: vi.fn() }));
vi.mock("../api/registryApi", () => ({
  startAgentChat: vi.fn(),
  triggerWorkflowRun: vi.fn(),
  getWorkflowRunTree: vi.fn(),
  getChatApprovalStatus: vi.fn(),
}));
vi.mock("../lib/keycloak", () => ({
  getKeycloak: () => ({ authenticated: true, token: "tok", updateToken: vi.fn() }),
}));

import { getCatalogDetail } from "../api/catalogApi";
import { startAgentChat, getChatApprovalStatus } from "../api/registryApi";

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
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
Element.prototype.scrollIntoView = vi.fn();

const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function renderChat() {
  return renderWithProviders(
    <Routes>
      <Route path="/catalog/:artifactId/chat" element={<CatalogChatPage />} />
    </Routes>,
    { routerEntries: ["/catalog/art-1/chat"] }
  );
}

describe("CatalogChatPage — production consumer HITL auto-resume", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getCatalogDetail).mockResolvedValue({
      artifact: { name: "serper-agent-4", type: "agent", description: "searches the web", source_id: "src-1" },
      deployments: [{ status: "running", version_label: "v4" }],
    });
    mock(startAgentChat).mockResolvedValue({
      run_id: "run-1",
      stream_url: "/api/v1/agents/serper-agent-4/chat/run-1/stream",
    });
    mock(getChatApprovalStatus).mockResolvedValue({ run_id: "run-1", decided: false, status: "pending" });
  });
  afterEach(() => vi.useRealTimers());

  it("polls the console and auto-opens the resume stream once approved — no click", async () => {
    const user = userEvent.setup();
    renderChat();

    const input = await screen.findByPlaceholderText(/message/i);
    await user.type(input, "search the weather");
    await user.keyboard("{Enter}");

    // The initial chat stream opens.
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));

    // The agent requests approval → the waiting banner appears (and polling starts).
    act(() => MockEventSource.instances[0].emit({
      type: "approval_requested", approval_id: "ap-1", tool: "web_search", risk: "high",
    }));
    await screen.findByText(/Awaiting approval/i);

    // A reviewer approves → the next poll returns decided.
    mock(getChatApprovalStatus).mockResolvedValue({ run_id: "run-1", decided: true, status: "approved" });

    // The 3s poll fires and auto-opens a resume-stream EventSource — WITHOUT any click.
    await waitFor(
      () => expect(MockEventSource.instances.some((e) => e.url.includes("resume-stream"))).toBe(true),
      { timeout: 5000 }
    );
    expect(getChatApprovalStatus).toHaveBeenCalledWith("serper-agent-4", "run-1");
  });
});

describe("CatalogChatPage — deployment pinning", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getCatalogDetail).mockResolvedValue({
      artifact: { name: "serper-agent-4", type: "agent", description: "searches the web", source_id: "src-1" },
      deployments: [
        { id: "dep-old", status: "running", version_label: "v3" },
        { id: "dep-new", status: "running", version_label: "v4" },
      ],
    });
    mock(startAgentChat).mockResolvedValue({
      run_id: "run-1",
      stream_url: "/api/v1/agents/serper-agent-4/chat/run-1/stream",
    });
  });
  afterEach(() => vi.useRealTimers());

  it("pins the run to the ?dep deployment, not the first running one", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/catalog/:artifactId/chat" element={<CatalogChatPage />} />
      </Routes>,
      // dep-old is NOT the first running deployment — the pin must still win.
      { routerEntries: ["/catalog/art-1/chat?dep=dep-old"] }
    );

    const input = await screen.findByPlaceholderText(/message/i);
    await user.type(input, "search the weather");
    await user.keyboard("{Enter}");

    await waitFor(() =>
      expect(startAgentChat).toHaveBeenCalledWith(
        "serper-agent-4",
        expect.objectContaining({ deployment_id: "dep-old", context: "production" })
      )
    );
  });
});

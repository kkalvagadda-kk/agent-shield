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
vi.mock("../api/playgroundApi", () => ({ submitRunFeedback: vi.fn().mockResolvedValue({}) }));

import { getCatalogDetail } from "../api/catalogApi";
import {
  startAgentChat,
  getChatApprovalStatus,
  triggerWorkflowRun,
  getWorkflowRunTree,
} from "../api/registryApi";
import { submitRunFeedback } from "../api/playgroundApi";

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

  it("shows a thumbs control after a turn completes and submits production feedback", async () => {
    const user = userEvent.setup();
    renderChat();

    const input = await screen.findByPlaceholderText(/message/i);
    await user.type(input, "what's the weather");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(MockEventSource.instances.length).toBe(1));

    // Stream a token then complete the turn — the done event carries the run id.
    act(() => MockEventSource.instances[0].emit({ type: "token", content: "Sunny." }));
    act(() => MockEventSource.instances[0].emit({ type: "done", run_id: "run-1" }));

    // The thumbs control appears now that the turn has a run id.
    const thumbsUp = await screen.findByTitle("Thumbs up");
    await user.click(thumbsUp);

    expect(submitRunFeedback).toHaveBeenCalledWith("run-1", 1);
    // Locked after submitting.
    await waitFor(() => expect(screen.getByTitle("Thumbs down")).toBeDisabled());
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

describe("CatalogChatPage — workflow per-member attribution", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    mock(getCatalogDetail).mockResolvedValue({
      artifact: {
        name: "trigger-demo-flow",
        type: "workflow",
        description: "demo workflow",
        source_id: "wf-1",
      },
      deployments: [{ status: "running", version_label: "v1" }],
    });
    mock(triggerWorkflowRun).mockResolvedValue({ run_id: "run-w" });
  });
  afterEach(() => vi.useRealTimers());

  // Regression guard for the poll race the live Playwright journey caught: the
  // PARENT run flips terminal a poll before the CHILD rows record, so a naive
  // "return on parent terminal" captured a child-less tree and collapsed the run
  // into ONE unlabeled bubble. The poll must wait for the members to appear.
  it("waits for members to populate after the parent is terminal, then renders one attributed bubble per member", async () => {
    mock(getWorkflowRunTree)
      // First poll: parent already terminal but children not recorded yet.
      .mockResolvedValueOnce({
        parent: { status: "completed", output: "final summary" },
        children: [],
      })
      // Subsequent polls: the members are now present.
      .mockResolvedValue({
        parent: { status: "completed", output: "final summary" },
        children: [
          { id: "c1", agent_name: "member-a", output: "A did its part", status: "completed" },
          { id: "c2", agent_name: "member-b", output: "B did its part", status: "completed" },
        ],
      });

    const user = userEvent.setup();
    renderChat();

    // findByPlaceholderText(/message/i) resolves only once the artifact has loaded
    // and the input is enabled (agentName set) — grabbing the still-"Loading agent..."
    // textbox would type into a disabled field and never send.
    const input = await screen.findByPlaceholderText(/message/i);
    await user.type(input, "run it");
    await user.keyboard("{Enter}");

    // The child-less first tree must NOT collapse the turn: once children settle
    // (~a poll later) each member renders as its own attributed label.
    await screen.findByText("member-a", {}, { timeout: 12000 });
    await screen.findByText("member-b", {}, { timeout: 12000 });
    expect(triggerWorkflowRun).toHaveBeenCalledWith(
      "wf-1",
      expect.objectContaining({ trigger_type: "api" })
    );
  }, 15000);
});

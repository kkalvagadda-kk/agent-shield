import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import CatalogChatPage from "./CatalogChatPage";

vi.mock("../api/catalogApi", () => ({ getCatalogDetail: vi.fn() }));
vi.mock("../api/registryApi", () => ({
  startAgentChat: vi.fn(),
  getWorkflowRunTree: vi.fn(),
  getCompositeWorkflow: vi.fn(),
  getChatApprovalStatus: vi.fn(),
  workflowRunStreamUrl: (id: string) => `/api/v1/workflows/${id}/runs/stream`,
  // POC-5 docked History: seedFromThread reads listMemory; ConversationSidebar
  // fetches listConversations (agent scope). listMyConversations is imported by
  // the sidebar too, so it must exist on the mocked module even when unused here.
  listMemory: vi.fn(),
  listConversations: vi.fn(),
  listMyConversations: vi.fn(),
}));
vi.mock("../lib/keycloak", () => ({
  getKeycloak: () => ({ authenticated: true, token: "tok", updateToken: vi.fn() }),
}));
vi.mock("../api/playgroundApi", () => ({ submitRunFeedback: vi.fn().mockResolvedValue({}) }));

import { getCatalogDetail } from "../api/catalogApi";
import {
  startAgentChat,
  getChatApprovalStatus,
  getCompositeWorkflow,
  getWorkflowRunTree,
  listMemory,
  listConversations,
} from "../api/registryApi";
import { submitRunFeedback } from "../api/playgroundApi";

// Build a Response-like whose body streams the given frames as SSE `data:`
// records, so we can drive the workflow console's fetch+ReadableStream reader.
function sseResponse(frames: unknown[]) {
  const enc = new TextEncoder();
  const chunks = frames.map((f) => enc.encode(`data: ${JSON.stringify(f)}\n\n`));
  let i = 0;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read() {
            if (i < chunks.length) {
              return Promise.resolve({ value: chunks[i++], done: false });
            }
            return Promise.resolve({ value: undefined, done: true });
          },
        };
      },
    },
  };
}

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

describe("CatalogChatPage — workflow live console", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    MockEventSource.instances = [];
    sessionStorage.clear();
    mock(getCatalogDetail).mockResolvedValue({
      artifact: {
        name: "refund-flow",
        type: "workflow",
        description: "demo workflow",
        source_id: "wf-1",
      },
      deployments: [{ status: "running", version_label: "v1" }],
    });
    mock(getCompositeWorkflow).mockResolvedValue({
      id: "wf-1",
      name: "refund-flow",
      member_count: 2,
      orchestration: "sequential",
    });
    fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        { type: "agent_start", author: "member-a" },
        { type: "token", author: "member-a", content: "A did its part" },
        { type: "tool_call", author: "member-a", tool: "lookup", status: "ok" },
        { type: "rationale", author: "member-a", content: "checking the record first" },
        { type: "agent_end", author: "member-a" },
        { type: "agent_start", author: "member-b" },
        { type: "token", author: "member-b", content: "B did its part" },
        { type: "agent_end", author: "member-b" },
        { type: "done", run_id: "run-w" },
      ]),
    );
    (globalThis as unknown as { fetch: typeof fetchMock }).fetch = fetchMock;
  });
  afterEach(() => vi.useRealTimers());

  it("renders the console shell (member count + shared-thread subtitle + rationale toggle)", async () => {
    renderChat();
    // Shell header carries "· N agents" and the shared-thread subtitle.
    await screen.findByText(/·\s*2\s*agents/);
    expect(screen.getByText(/shared conversation/i)).toBeInTheDocument();
    expect(screen.getByText(/Show rationale/i)).toBeInTheDocument();
  });

  // The headline 2b-0 journey: the live SSE stream opens on the /runs/stream
  // endpoint and each member frame drives its own attributed bubble, with a
  // tool chip + amber rationale, via the SAME author-keyed reducers.
  it("streams member frames into per-author bubbles with tool chip + rationale", async () => {
    const user = userEvent.setup();
    renderChat();

    const input = await screen.findByPlaceholderText(/message/i);
    await user.type(input, "run it");
    await user.keyboard("{Enter}");

    // Both members render as their own attributed bubble (name label).
    await screen.findByText("member-a", {}, { timeout: 8000 });
    await screen.findByText("member-b", {}, { timeout: 8000 });
    // The tool chip and the member's rationale surface under member-a.
    expect(screen.getByText("lookup")).toBeInTheDocument();
    expect(screen.getByText(/checking the record first/)).toBeInTheDocument();

    // The stream opened the POST /runs/stream endpoint with the message body.
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workflows/wf-1/runs/stream",
      expect.objectContaining({
        method: "POST",
        body: expect.stringContaining("run it"),
      }),
    );
  }, 12000);

  it("hides the amber rationale box when the Show-rationale toggle is off", async () => {
    const user = userEvent.setup();
    const { container } = renderChat();

    const input = await screen.findByPlaceholderText(/message/i);
    await user.type(input, "run it");
    await user.keyboard("{Enter}");
    await screen.findByText(/checking the record first/, {}, { timeout: 8000 });
    expect(container.querySelector(".bg-amber-50")).not.toBeNull();

    // Toggle Show-rationale off → the amber boxes disappear.
    await user.click(screen.getByLabelText(/Show rationale/i));
    await waitFor(() => expect(container.querySelector(".bg-amber-50")).toBeNull());
  }, 12000);

  // save→reload→survives: a fresh mount rehydrates the last run's per-member
  // bubbles (with tool_calls + rationale) straight from the backend tree — not
  // from the client store — via the stored run id.
  it("rehydrates member bubbles + tool chip + rationale from the backend on reload", async () => {
    sessionStorage.setItem("wf-lastrun-art-1", "run-w");
    mock(getWorkflowRunTree).mockResolvedValue({
      parent: { status: "completed", output: "final summary" },
      children: [
        {
          id: "c1",
          agent_name: "member-a",
          output: "A did its part",
          status: "completed",
          latency_ms: 120,
          tool_calls: [{ tool_name: "lookup", status: "ok" }],
          rationale: "checking the record first",
        },
      ],
    });

    renderChat();

    // The member bubble, its tool chip, and its rationale all come from the tree
    // read (getWorkflowRunTree), proving the persisted round-trip.
    await screen.findByText("member-a", {}, { timeout: 10000 });
    expect(screen.getByText("lookup")).toBeInTheDocument();
    expect(screen.getByText(/checking the record first/)).toBeInTheDocument();
    expect(getWorkflowRunTree).toHaveBeenCalledWith("wf-1", "run-w");
  }, 12000);
});

// ---------------------------------------------------------------------------
// POC-5 — docked History + resumable session (production surface).
// Proves: (a) a ?session deep link rehydrates prior turns from /memory;
// (b) clicking a History row seeds that thread via seedFromThread → listMemory;
// (c) New conversation clears the transcript and mints a fresh session.
// ---------------------------------------------------------------------------
describe("CatalogChatPage — docked History + resumable session (POC-5)", () => {
  const NOW = new Date().toISOString();

  // A single prior thread's transcript (plain user/assistant — no rich slots).
  const memoryRows = [
    {
      id: "m1",
      agent_name: "serper-agent-4",
      thread_id: "thread-42",
      role: "user",
      content: "capital of France?",
      message_index: 0,
      session_id: "sess-1",
      user_id: "u1",
      created_at: NOW,
    },
    {
      id: "m2",
      agent_name: "serper-agent-4",
      thread_id: "thread-42",
      role: "assistant",
      content: "The capital of France is Paris.",
      message_index: 1,
      session_id: "sess-1",
      user_id: "u1",
      created_at: NOW,
    },
  ];

  const summary = {
    thread_id: "thread-42",
    session_id: "sess-1",
    agent_name: "serper-agent-4",
    title: "capital of France?",
    message_count: 2,
    last_activity: NOW,
    deployment_id: null,
    environment: "production" as const,
  };

  beforeEach(() => {
    MockEventSource.instances = [];
    sessionStorage.clear();
    mock(getCatalogDetail).mockResolvedValue({
      artifact: { name: "serper-agent-4", type: "agent", description: "searches the web", source_id: "src-1" },
      deployments: [{ id: "dep-1", status: "running", version_label: "v4" }],
    });
    mock(listMemory).mockResolvedValue(memoryRows);
    mock(listConversations).mockResolvedValue([summary]);
  });
  afterEach(() => vi.useRealTimers());

  // (a) ?session deep link → seedFromThread on mount rehydrates from the backend.
  it("rehydrates prior turns from /memory when opened with ?session", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/catalog/:artifactId/chat" element={<CatalogChatPage />} />
      </Routes>,
      { routerEntries: ["/catalog/art-1/chat?session=thread-42"] }
    );

    // The assistant turn from the stored transcript appears — read from listMemory,
    // not from any live stream (save→reload→survives).
    await screen.findByText(/The capital of France is Paris\./i, {}, { timeout: 8000 });
    expect(listMemory).toHaveBeenCalledWith(
      "serper-agent-4",
      expect.objectContaining({ thread_id: "thread-42" })
    );
  });

  // (b) Clicking a History row seeds that thread (listMemory with its thread_id).
  it("seeds the transcript when a History row is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/catalog/:artifactId/chat" element={<CatalogChatPage />} />
      </Routes>,
      { routerEntries: ["/catalog/art-1/chat"] }
    );

    // No ?session → nothing seeded on mount.
    await screen.findByPlaceholderText(/message/i);
    expect(listMemory).not.toHaveBeenCalled();

    // History dock is open by default; the row (from listConversations) renders.
    const row = await screen.findByText("capital of France?");
    await user.click(row);

    await waitFor(() =>
      expect(listMemory).toHaveBeenCalledWith(
        "serper-agent-4",
        expect.objectContaining({ thread_id: "thread-42" })
      )
    );
    // The rehydrated assistant turn is now in the transcript.
    await screen.findByText(/The capital of France is Paris\./i, {}, { timeout: 8000 });
  });

  // (c) New conversation clears the transcript and starts a fresh session.
  it("clears the transcript and starts fresh on New conversation", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route path="/catalog/:artifactId/chat" element={<CatalogChatPage />} />
      </Routes>,
      { routerEntries: ["/catalog/art-1/chat?session=thread-42"] }
    );

    // Seeded from ?session — the prior assistant turn is visible.
    await screen.findByText(/The capital of France is Paris\./i, {}, { timeout: 8000 });

    // History dock is open by default → hit New conversation → transcript resets.
    await user.click(await screen.findByRole("button", { name: /new conversation/i }));

    await waitFor(() =>
      expect(screen.queryByText(/The capital of France is Paris\./i)).not.toBeInTheDocument()
    );
    expect(screen.getByText(/Start a conversation/i)).toBeInTheDocument();
  });
});

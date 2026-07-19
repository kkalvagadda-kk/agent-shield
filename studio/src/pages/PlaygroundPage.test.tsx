import { describe, it, expect, vi, beforeEach } from "vitest";
import { useEffect } from "react";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "../test/utils";
import PlaygroundPage from "./PlaygroundPage";
import * as api from "../api/registryApi";

// Auto-select an agent version on mount so the "Promote" panel renders.
vi.mock("../components/playground/VersionSelector", () => ({
  default: ({ onSelect }: { onSelect: (name: string, sel: unknown) => void }) => {
    // Run once on mount. PlaygroundPage passes a fresh inline onSelect each render,
    // so depending on it would loop (select → setState → re-render → new fn → refire).
    useEffect(() => {
      onSelect("risky-agent", { agentName: "risky-agent", versionId: "ver-1", deploymentId: "dep-1" });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return <div data-testid="version-selector-stub" />;
  },
}));

// Inert stubs for the heavy playground children (streaming/canvas/etc.). The
// ConversationSidebar is intentionally NOT mocked — the History test drives the
// real component so it exercises listConversations + the row → listMemory wiring.
vi.mock("../components/playground/ChatPane", () => ({ default: () => <div /> }));
vi.mock("../components/playground/InteractionSurface", () => ({ default: () => <div /> }));
vi.mock("../components/playground/HitlPanel", () => ({ default: () => <div /> }));
vi.mock("../components/playground/TracePanel", () => ({ default: () => <div /> }));
vi.mock("../components/playground/WorkflowSelector", () => ({ default: () => <div /> }));

vi.mock("../api/registryApi", () => ({
  getAgent: vi.fn().mockResolvedValue({ name: "risky-agent" }),
  listTriggers: vi.fn().mockResolvedValue([]),
  patchVersion: vi.fn().mockResolvedValue({}),
  patchWorkflowVersion: vi.fn().mockResolvedValue({}),
  publishAgent: vi.fn().mockResolvedValue({ publish_request_id: "pr-1" }),
  publishWorkflow: vi.fn().mockResolvedValue({}),
  // POC-5 History: the ConversationSidebar + PlaygroundPage.seedFromThread reads.
  listConversations: vi.fn().mockResolvedValue([]),
  listMyConversations: vi.fn().mockResolvedValue([]),
  listMemory: vi.fn().mockResolvedValue([]),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("PlaygroundPage — adversarial-eval promotion", () => {
  beforeEach(() => vi.clearAllMocks());

  it("'Mark Adversarial Passed' PATCHes adversarial_eval_passed=true on the selected version", async () => {
    renderWithProviders(<PlaygroundPage />);

    const btn = await screen.findByRole("button", { name: /Mark Adversarial Passed/i });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(api.patchVersion).toHaveBeenCalledWith("risky-agent", "ver-1", {
        adversarial_eval_passed: true,
      }),
    );
  });

  it("keeps the ordinary eval mark separate (does not set adversarial_eval_passed)", async () => {
    renderWithProviders(<PlaygroundPage />);

    const btn = await screen.findByRole("button", { name: /Mark Version Passed/i });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(api.patchVersion).toHaveBeenCalledWith("risky-agent", "ver-1", {
        eval_passed: true,
      }),
    );
    // The eval mark must NOT smuggle the adversarial flag — it's a distinct sign-off.
    expect(api.patchVersion).not.toHaveBeenCalledWith(
      "risky-agent",
      "ver-1",
      expect.objectContaining({ adversarial_eval_passed: true }),
    );
  });
});

describe("PlaygroundPage — History sidebar (POC-5)", () => {
  const CONV = {
    thread_id: "thread-abc",
    session_id: null,
    agent_name: "risky-agent",
    title: "Past sandbox run",
    message_count: 2,
    last_activity: new Date().toISOString(),
    deployment_id: "dep-1",
    environment: "sandbox" as const,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listConversations).mockResolvedValue([CONV]);
    vi.mocked(api.listMemory).mockResolvedValue([
      { id: "m1", agent_name: "risky-agent", thread_id: "thread-abc", role: "user", content: "hi", message_index: 0, session_id: null, user_id: "u", created_at: new Date().toISOString() },
      { id: "m2", agent_name: "risky-agent", thread_id: "thread-abc", role: "assistant", content: "hello", message_index: 1, session_id: null, user_id: "u", created_at: new Date().toISOString() },
    ]);
  });

  it("shows a History toggle once an agent is selected", async () => {
    renderWithProviders(<PlaygroundPage />);
    expect(await screen.findByTestId("playground-history-toggle")).toBeInTheDocument();
  });

  it("opens the docked sidebar and seeds listMemory with the row's thread_id on select", async () => {
    renderWithProviders(<PlaygroundPage />);

    // Dock is open by default once an agent is selected — wait for that state
    // (no toggle click needed; clicking would now CLOSE it).
    await screen.findByTestId("playground-history-toggle");

    // The dock queries listConversations for the selected sandbox deployment.
    await waitFor(() =>
      expect(api.listConversations).toHaveBeenCalledWith(
        "risky-agent",
        expect.objectContaining({ deployment_id: "dep-1" }),
      ),
    );

    // Selecting the row rehydrates that thread's transcript from the backend.
    fireEvent.click(await screen.findByText("Past sandbox run"));

    await waitFor(() =>
      expect(api.listMemory).toHaveBeenCalledWith(
        "risky-agent",
        expect.objectContaining({ thread_id: "thread-abc", deployment_id: "dep-1" }),
      ),
    );
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import WorkflowConversationsTab from "./WorkflowConversationsTab";
import type { ConversationSummary } from "../../api/registryApi";

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => navigateSpy,
}));

vi.mock("../../api/registryApi", () => ({
  listConversations: vi.fn(),
  listMyConversations: vi.fn(),
  listWorkflowConversations: vi.fn(),
}));

import { listWorkflowConversations, listConversations } from "../../api/registryApi";
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    thread_id: "wf-thread-1",
    session_id: "wf-thread-1",
    agent_name: "research-summarize",
    title: "what is the weather in austin",
    message_count: 4,
    last_activity: new Date().toISOString(),
    deployment_id: null,
    environment: "sandbox",
    ...overrides,
  };
}

describe("WorkflowConversationsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(listWorkflowConversations).mockResolvedValue([summary()]);
  });

  it("lists the workflow's conversations via the workflow endpoint (NOT the agent one)", async () => {
    renderWithProviders(<WorkflowConversationsTab workflowId="wf-1" deploymentId="dep-1" />);

    expect(await screen.findByText("what is the weather in austin")).toBeInTheDocument();
    await waitFor(() => expect(listWorkflowConversations).toHaveBeenCalledWith("wf-1"));
    // Must NOT fall back to the per-agent list (that's the bug that left it empty).
    expect(listConversations).not.toHaveBeenCalled();
  });

  it("clicking a row navigates to the workflow chat seeded with that session", async () => {
    renderWithProviders(<WorkflowConversationsTab workflowId="wf-1" deploymentId="dep-1" />);

    await userEvent.click(await screen.findByText("what is the weather in austin"));

    expect(navigateSpy).toHaveBeenCalledWith(
      "/workflows/wf-1/d/dep-1/chat?session=wf-thread-1"
    );
  });

  it("New conversation navigates to a fresh workflow chat", async () => {
    renderWithProviders(<WorkflowConversationsTab workflowId="wf-1" deploymentId="dep-1" />);

    await userEvent.click(await screen.findByRole("button", { name: /new conversation/i }));

    expect(navigateSpy).toHaveBeenCalledWith("/workflows/wf-1/d/dep-1/chat");
  });
});

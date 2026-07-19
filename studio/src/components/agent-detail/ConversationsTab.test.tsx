import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import ConversationsTab from "./ConversationsTab";
import type { ConversationSummary } from "../../api/registryApi";

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => navigateSpy,
}));

vi.mock("../../api/registryApi", () => ({
  listConversations: vi.fn(),
  listMyConversations: vi.fn(),
}));

import { listConversations } from "../../api/registryApi";
const mock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function summary(overrides: Partial<ConversationSummary> = {}): ConversationSummary {
  return {
    thread_id: "thread-abc",
    session_id: "sess-1",
    agent_name: "my-agent",
    title: "Refund for order 42",
    message_count: 4,
    last_activity: new Date().toISOString(),
    deployment_id: "dep-1",
    environment: "sandbox",
    ...overrides,
  };
}

describe("ConversationsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mock(listConversations).mockResolvedValue([summary()]);
  });

  it("lists the deployment's conversations scoped to this agent + deployment", async () => {
    renderWithProviders(<ConversationsTab agentName="my-agent" deploymentId="dep-1" />);

    expect(await screen.findByText("Refund for order 42")).toBeInTheDocument();
    await waitFor(() =>
      expect(listConversations).toHaveBeenCalledWith("my-agent", { deployment_id: "dep-1" })
    );
  });

  it("clicking a row navigates to the deployment chat seeded with that session", async () => {
    renderWithProviders(<ConversationsTab agentName="my-agent" deploymentId="dep-1" />);

    await userEvent.click(await screen.findByText("Refund for order 42"));

    expect(navigateSpy).toHaveBeenCalledWith(
      "/agents/my-agent/d/dep-1/chat?session=thread-abc"
    );
  });

  it("New conversation navigates to a fresh deployment chat", async () => {
    renderWithProviders(<ConversationsTab agentName="my-agent" deploymentId="dep-1" />);

    await userEvent.click(await screen.findByRole("button", { name: /new conversation/i }));

    expect(navigateSpy).toHaveBeenCalledWith("/agents/my-agent/d/dep-1/chat");
  });
});

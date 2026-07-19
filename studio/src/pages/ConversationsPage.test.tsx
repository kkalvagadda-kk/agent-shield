import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import ConversationsPage from "./ConversationsPage";

const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => navigateSpy,
}));

vi.mock("../api/registryApi", async () => {
  const actual = await vi.importActual<typeof import("../api/registryApi")>(
    "../api/registryApi",
  );
  return {
    ...actual,
    listMyConversations: vi.fn(),
    listConversations: vi.fn(),
    listMemory: vi.fn(),
  };
});

import {
  listMyConversations,
  listMemory,
  type ConversationSummary,
  type MemoryMessage,
} from "../api/registryApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const NOW = new Date().toISOString();

const CONVERSATIONS: ConversationSummary[] = [
  {
    thread_id: "t-sandbox",
    session_id: "t-sandbox",
    agent_name: "support-bot",
    title: "Where is my order",
    message_count: 4,
    last_activity: NOW,
    deployment_id: null,
    environment: "sandbox",
  },
  {
    thread_id: "t-prod",
    session_id: "t-prod",
    agent_name: "billing-bot",
    title: "Refund question",
    message_count: 2,
    last_activity: NOW,
    deployment_id: "dep-1",
    environment: "production",
  },
];

const TRANSCRIPT: MemoryMessage[] = [
  {
    id: "m1",
    agent_name: "support-bot",
    thread_id: "t-sandbox",
    role: "user",
    content: "Where is my order 12345?",
    message_index: 0,
    session_id: "t-sandbox",
    user_id: "u1",
    created_at: NOW,
  },
  {
    id: "m2",
    agent_name: "support-bot",
    thread_id: "t-sandbox",
    role: "assistant",
    content: "Your order shipped yesterday.",
    message_index: 1,
    session_id: "t-sandbox",
    user_id: "u1",
    created_at: NOW,
  },
];

describe("ConversationsPage (POC-5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mk(listMyConversations).mockResolvedValue(CONVERSATIONS);
    mk(listMemory).mockResolvedValue(TRANSCRIPT);
  });

  it("lists the caller's conversations across agents", async () => {
    renderWithProviders(<ConversationsPage />);

    expect(await screen.findByText("Where is my order")).toBeInTheDocument();
    expect(screen.getByText("Refund question")).toBeInTheDocument();
    expect(listMyConversations).toHaveBeenCalled();
  });

  it("shows the All/Sandbox/Production env filter pills", async () => {
    renderWithProviders(<ConversationsPage />);

    // Wait for the list to render so the sidebar is fully mounted.
    await screen.findByText("Where is my order");

    expect(screen.getByRole("button", { name: "all" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "sandbox" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "production" })).toBeInTheDocument();
  });

  it("filters the list client-side by environment", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ConversationsPage />);

    await screen.findByText("Where is my order");
    expect(screen.getByText("Refund question")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "sandbox" }));

    expect(screen.getByText("Where is my order")).toBeInTheDocument();
    expect(screen.queryByText("Refund question")).not.toBeInTheDocument();
  });

  it("selecting a row loads and renders its transcript", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ConversationsPage />);

    await user.click(await screen.findByText("Where is my order"));

    await waitFor(() =>
      expect(listMemory).toHaveBeenCalledWith(
        "support-bot",
        expect.objectContaining({ thread_id: "t-sandbox" }),
      ),
    );
    expect(
      await screen.findByText("Your order shipped yesterday."),
    ).toBeInTheDocument();
    expect(screen.getByText("Where is my order 12345?")).toBeInTheDocument();
  });

  it("Continue navigates to the seeded agent chat", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ConversationsPage />);

    await user.click(await screen.findByText("Where is my order"));
    await user.click(await screen.findByRole("button", { name: /continue/i }));

    expect(navigateSpy).toHaveBeenCalledWith(
      "/agents/support-bot/chat?session=t-sandbox",
    );
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import ConversationSidebar, { filterConversationsByEnv } from "./ConversationSidebar";
import type { ConversationSummary } from "../../api/registryApi";

vi.mock("../../api/registryApi", async () => {
  const actual = await vi.importActual<typeof import("../../api/registryApi")>(
    "../../api/registryApi",
  );
  return {
    ...actual,
    listConversations: vi.fn(),
    listMyConversations: vi.fn(),
  };
});

import { listConversations, listMyConversations } from "../../api/registryApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const sandboxConvo: ConversationSummary = {
  thread_id: "t-sbx",
  session_id: "t-sbx",
  agent_name: "support-bot",
  title: "Refund question",
  message_count: 4,
  last_activity: new Date().toISOString(),
  deployment_id: null,
  environment: "sandbox",
};

const prodConvo: ConversationSummary = {
  thread_id: "t-prod",
  session_id: "t-prod",
  agent_name: "billing-bot",
  title: null, // exercises the "Untitled conversation" fallback
  message_count: 2,
  last_activity: new Date().toISOString(),
  deployment_id: "dep-1",
  environment: "production",
};

describe("ConversationSidebar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mk(listMyConversations).mockResolvedValue([sandboxConvo, prodConvo]);
    mk(listConversations).mockResolvedValue([sandboxConvo]);
  });

  it("renders the fetched conversation rows", async () => {
    renderWithProviders(
      <ConversationSidebar
        scope={{ kind: "me" }}
        activeThreadId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    );

    expect(await screen.findByText("Refund question")).toBeInTheDocument();
    // null title falls back to the placeholder
    expect(screen.getByText("Untitled conversation")).toBeInTheDocument();
    // meta line renders agent name + turn count
    expect(screen.getByText(/support-bot · 4 turns/)).toBeInTheDocument();
  });

  it("shows the empty state when there are no conversations", async () => {
    mk(listMyConversations).mockResolvedValue([]);
    renderWithProviders(
      <ConversationSidebar
        scope={{ kind: "me" }}
        activeThreadId={null}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    );

    expect(await screen.findByText("No conversations yet.")).toBeInTheDocument();
  });

  it("calls onSelect with the summary when a row is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderWithProviders(
      <ConversationSidebar
        scope={{ kind: "me" }}
        activeThreadId={null}
        onSelect={onSelect}
        onNew={vi.fn()}
      />,
    );

    await user.click(await screen.findByText("Refund question"));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(sandboxConvo);
  });

  it("calls onNew when the New conversation button is clicked", async () => {
    const user = userEvent.setup();
    const onNew = vi.fn();
    renderWithProviders(
      <ConversationSidebar
        scope={{ kind: "me" }}
        activeThreadId={null}
        onSelect={vi.fn()}
        onNew={onNew}
      />,
    );

    await user.click(await screen.findByRole("button", { name: /new conversation/i }));

    expect(onNew).toHaveBeenCalledTimes(1);
  });
});

describe("filterConversationsByEnv", () => {
  const items = [sandboxConvo, prodConvo];

  it("returns all items for 'all'", () => {
    expect(filterConversationsByEnv(items, "all")).toEqual(items);
  });

  it("returns only sandbox items for 'sandbox'", () => {
    expect(filterConversationsByEnv(items, "sandbox")).toEqual([sandboxConvo]);
  });

  it("returns only production items for 'production'", () => {
    expect(filterConversationsByEnv(items, "production")).toEqual([prodConvo]);
  });

  it("returns an empty array when nothing matches", () => {
    expect(filterConversationsByEnv([sandboxConvo], "production")).toEqual([]);
  });
});

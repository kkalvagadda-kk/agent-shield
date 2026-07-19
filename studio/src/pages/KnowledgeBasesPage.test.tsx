import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/utils";
import KnowledgeBasesPage from "./KnowledgeBasesPage";

vi.mock("../api/knowledgeApi", () => ({
  listKBs: vi.fn(),
  createKB: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { listKBs, createKB } from "../api/knowledgeApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const NOW = new Date().toISOString();

const KB = {
  id: "kb-1",
  team: "platform",
  name: "Company Policies",
  description: "Refund + security policies",
  created_by: "dev",
  created_at: NOW,
  updated_at: NOW,
  source_count: 3,
  ready_count: 2,
  attached_agents: ["policy-qa"],
};

describe("KnowledgeBasesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists knowledge bases from the API with a ready-count status", async () => {
    mk(listKBs).mockResolvedValue([KB]);
    renderWithProviders(<KnowledgeBasesPage />);

    expect(await screen.findByText("Company Policies")).toBeInTheDocument();
    // 2/3 sources ready → the amber "2/3 ready" badge (not "Ready").
    expect(screen.getByText("2/3 ready")).toBeInTheDocument();
  });

  it("shows an empty state when there are no knowledge bases", async () => {
    mk(listKBs).mockResolvedValue([]);
    renderWithProviders(<KnowledgeBasesPage />);

    expect(await screen.findByText(/No knowledge bases yet/i)).toBeInTheDocument();
  });

  it("creates a KB via the modal and it appears after the list refetch (save → reload)", async () => {
    const created = { ...KB, id: "kb-new", name: "New KB", description: null, source_count: 0, ready_count: 0, attached_agents: [] };
    // First load empty; after create + invalidate, the refetch returns the new KB.
    mk(listKBs).mockResolvedValueOnce([]).mockResolvedValue([created]);
    mk(createKB).mockResolvedValue(created);

    const user = userEvent.setup();
    renderWithProviders(<KnowledgeBasesPage />);

    // Empty first.
    await screen.findByText(/No knowledge bases yet/i);

    await user.click(screen.getByRole("button", { name: /new knowledge base/i }));
    await user.type(screen.getByPlaceholderText("Company Policies"), "New KB");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => expect(createKB).toHaveBeenCalledWith({ name: "New KB" }));
    // The invalidated list refetches → the new KB row renders.
    expect(await screen.findByText("New KB")).toBeInTheDocument();
  });
});

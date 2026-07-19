import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/utils";
import KnowledgeBaseDetailPage from "./KnowledgeBaseDetailPage";

vi.mock("../api/knowledgeApi", () => ({
  getKB: vi.fn(),
  updateKB: vi.fn(),
  deleteKB: vi.fn(),
  uploadSource: vi.fn(),
  listSources: vi.fn(),
  getChunks: vi.fn(),
  reprocessSource: vi.fn(),
  deleteSource: vi.fn(),
  testRetrieval: vi.fn(),
  listBoundAgents: vi.fn(),
  bindAgent: vi.fn(),
  unbindAgent: vi.fn(),
}));
vi.mock("../api/registryApi", () => ({
  listAgents: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  getKB, listSources, getChunks, testRetrieval, listBoundAgents, uploadSource,
} from "../api/knowledgeApi";
import { listAgents } from "../api/registryApi";

const mk = (fn: unknown) => fn as ReturnType<typeof vi.fn>;
const NOW = new Date().toISOString();

const KB = {
  id: "kb-1", team: "platform", name: "Company Policies",
  description: "Refund + security policies", created_by: "dev",
  created_at: NOW, updated_at: NOW, source_count: 4, ready_count: 1, attached_agents: [],
};

// One source per status → proves the display map (pending→Queued,
// indexing→Processing, ready→Ready, failed→Failed).
const SOURCES = [
  { id: "s1", kb_id: "kb-1", filename: "refund-policy.pdf", content_type: "application/pdf", size_bytes: 2048, status: "ready", error: null, chunk_count: 3, created_by: "dev", created_at: NOW },
  { id: "s2", kb_id: "kb-1", filename: "travel.md", content_type: "text/markdown", size_bytes: 1024, status: "indexing", error: null, chunk_count: 0, created_by: "dev", created_at: NOW },
  { id: "s3", kb_id: "kb-1", filename: "queued.txt", content_type: "text/plain", size_bytes: 512, status: "pending", error: null, chunk_count: 0, created_by: "dev", created_at: NOW },
  { id: "s4", kb_id: "kb-1", filename: "broken.pdf", content_type: "application/pdf", size_bytes: 900, status: "failed", error: "Extraction failed", chunk_count: 0, created_by: "dev", created_at: NOW },
];

const CHUNKS = [
  { id: "c1", chunk_index: 0, content: "Refunds over $500 need manager approval." },
];

const HITS = [
  { chunk_id: "c1", source_id: "s1", source_filename: "refund-policy.pdf", content: "Refunds over $500 need manager approval.", score: 0.91 },
];

function seedApis() {
  mk(getKB).mockResolvedValue(KB);
  mk(listSources).mockResolvedValue(SOURCES);
  mk(listBoundAgents).mockResolvedValue([]);
  mk(getChunks).mockResolvedValue(CHUNKS);
  mk(testRetrieval).mockResolvedValue({ hits: HITS });
  mk(uploadSource).mockResolvedValue(SOURCES[2]);
  mk(listAgents).mockResolvedValue({ items: [], total: 0 });
}

function renderPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/knowledge/:id" element={<KnowledgeBaseDetailPage />} />
    </Routes>,
    { routerEntries: ["/knowledge/kb-1"] }
  );
}

describe("KnowledgeBaseDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedApis();
  });

  it("renders the sources table with the status display map", async () => {
    renderPage();
    expect(await screen.findByText("refund-policy.pdf")).toBeInTheDocument();
    // Display map (F-6): pending→Queued, indexing→Processing, ready→Ready, failed→Failed.
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    // The failed source surfaces its error.
    expect(screen.getByText("Extraction failed")).toBeInTheDocument();
  });

  it("uploads a file via the real file input", async () => {
    const user = userEvent.setup();
    const { container } = renderPage();
    await screen.findByText("refund-policy.pdf");

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["hello world"], "notes.txt", { type: "text/plain" });
    await user.upload(fileInput, file);

    await waitFor(() => expect(uploadSource).toHaveBeenCalledTimes(1));
    expect(mk(uploadSource).mock.calls[0][0]).toBe("kb-1");
    expect((mk(uploadSource).mock.calls[0][1] as File).name).toBe("notes.txt");
  });

  it("opens the chunk viewer for a ready source and shows its chunks", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("refund-policy.pdf");

    // The first (ready) source's View button is enabled.
    const viewButtons = screen.getAllByRole("button", { name: /view/i });
    await user.click(viewButtons[0]);

    await waitFor(() => expect(getChunks).toHaveBeenCalledWith("kb-1", "s1"));
    expect(await screen.findByText("Refunds over $500 need manager approval.")).toBeInTheDocument();
  });

  it("runs test-retrieval and shows ranked chunks", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("refund-policy.pdf");

    await user.click(screen.getByRole("button", { name: /test retrieval/i }));
    await user.type(screen.getByPlaceholderText(/type a query to test retrieval/i), "refund");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => expect(testRetrieval).toHaveBeenCalledWith("kb-1", "refund"));
    // The ranked hit renders its content + score.
    expect(await screen.findByText("Refunds over $500 need manager approval.")).toBeInTheDocument();
    expect(screen.getByText("0.91")).toBeInTheDocument();
  });
});

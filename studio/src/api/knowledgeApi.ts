// ---------------------------------------------------------------------------
// knowledgeApi.ts — typed client for the POC-4 Knowledge Base / RAG endpoints.
//
// Rides the SHARED axios `http` instance from registryApi (baseURL `/api/v1`,
// Bearer-token interceptor, DEMO mock adapter) so auth + team scoping come for
// free. Every field name mirrors the Pydantic response models in
// services/registry-api/schemas.py (contracts/endpoints.md is the authority).
//
// Two routers back these calls:
//   * public  routers/knowledge.py  (JWT, team-scoped) — everything here.
//   * internal routers/internal.py  (cluster-only) — NOT called from the UI;
//     the `knowledge_search` HTTP tool hits it from inside an agent pod.
// ---------------------------------------------------------------------------

import { http } from "./registryApi";

// ---------------------------------------------------------------------------
// Types (field names EXACTLY per contracts/endpoints.md)
// ---------------------------------------------------------------------------

/** `KnowledgeBaseResponse` — a team's collection of Sources. `source_count` /
 *  `ready_count` / `attached_agents` are server-computed rollups. */
export interface KnowledgeBase {
  id: string;
  team: string;
  name: string;
  description: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  source_count: number;
  ready_count: number;
  attached_agents: string[];
}

/** Ingest lifecycle. Display map (F-6): pending→"Queued", indexing→"Processing",
 *  ready→"Ready", failed→"Failed". */
export type KBSourceStatus = "pending" | "indexing" | "ready" | "failed";

/** `SourceResponse` — one uploaded file and its ingest state. */
export interface KBSource {
  id: string;
  kb_id: string;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  status: KBSourceStatus;
  error: string | null;
  chunk_count: number;
  created_by: string;
  created_at: string;
}

/** `ChunkResponse` — a retrievable text segment (no embedding on the wire). */
export interface KBChunk {
  id: string;
  chunk_index: number;
  content: string;
}

/** `SearchResponse.hits[]` — one ranked chunk from test-retrieval. */
export interface KBHit {
  chunk_id: string;
  source_id: string;
  source_filename: string;
  content: string;
  score: number;
}

/** `BindingResponse` — an agent bound to a KB (the `knowledge_search` scope). */
export interface KBBinding {
  agent_id: string;
  agent_name: string;
  kb_id: string;
  team: string;
}

/** `AgentKBRef` — one KB an agent is bound to (reverse lookup for agent config). */
export interface AgentKBRef {
  kb_id: string;
  name: string;
}

// ---------------------------------------------------------------------------
// Knowledge Bases (CRUD)
// ---------------------------------------------------------------------------

/** GET /knowledge-bases — only the caller's team's KBs. */
export const listKBs = async (): Promise<KnowledgeBase[]> => {
  const { data } = await http.get<KnowledgeBase[]>("/knowledge-bases");
  return data;
};

/** POST /knowledge-bases — `team`/`created_by` are set server-side. */
export const createKB = async (body: {
  name: string;
  description?: string;
}): Promise<KnowledgeBase> => {
  const { data } = await http.post<KnowledgeBase>("/knowledge-bases", body);
  return data;
};

/** GET /knowledge-bases/{id} — 404 if not the caller's team. */
export const getKB = async (id: string): Promise<KnowledgeBase> => {
  const { data } = await http.get<KnowledgeBase>(`/knowledge-bases/${id}`);
  return data;
};

/** PATCH /knowledge-bases/{id} — Settings tab (rename / re-describe). */
export const updateKB = async (
  id: string,
  body: { name?: string; description?: string }
): Promise<KnowledgeBase> => {
  const { data } = await http.patch<KnowledgeBase>(`/knowledge-bases/${id}`, body);
  return data;
};

/** DELETE /knowledge-bases/{id} — cascades sources + chunks. */
export const deleteKB = async (id: string): Promise<void> => {
  await http.delete(`/knowledge-bases/${id}`);
};

// ---------------------------------------------------------------------------
// Sources (ingestion)
// ---------------------------------------------------------------------------

/** POST /knowledge-bases/{kbId}/sources — multipart upload. Returns the row
 *  immediately in `pending`; ingest runs in a BackgroundTask (poll listSources). */
export const uploadSource = async (
  kbId: string,
  file: File
): Promise<KBSource> => {
  const form = new FormData();
  form.append("file", file);
  const { data } = await http.post<KBSource>(
    `/knowledge-bases/${kbId}/sources`,
    form
  );
  return data;
};

/** GET /knowledge-bases/{kbId}/sources — newest-first; the polling read. */
export const listSources = async (kbId: string): Promise<KBSource[]> => {
  const { data } = await http.get<KBSource[]>(`/knowledge-bases/${kbId}/sources`);
  return data;
};

/** GET /knowledge-bases/{kbId}/sources/{sourceId}/chunks — chunk-viewer drawer. */
export const getChunks = async (
  kbId: string,
  sourceId: string
): Promise<KBChunk[]> => {
  const { data } = await http.get<KBChunk[]>(
    `/knowledge-bases/${kbId}/sources/${sourceId}/chunks`
  );
  return data;
};

/** POST …/sources/{sourceId}/reprocess — re-run ingest (recover stuck/failed). */
export const reprocessSource = async (
  kbId: string,
  sourceId: string
): Promise<void> => {
  await http.post(`/knowledge-bases/${kbId}/sources/${sourceId}/reprocess`);
};

/** DELETE …/sources/{sourceId} — removes the source + its chunks. */
export const deleteSource = async (
  kbId: string,
  sourceId: string
): Promise<void> => {
  await http.delete(`/knowledge-bases/${kbId}/sources/${sourceId}`);
};

// ---------------------------------------------------------------------------
// Test retrieval (team-scoped — can only ever hit the caller's team's KB)
// ---------------------------------------------------------------------------

/** POST /knowledge-bases/{kbId}/search — prove retrieval before attaching. */
export const testRetrieval = async (
  kbId: string,
  query: string,
  k?: number
): Promise<{ hits: KBHit[] }> => {
  const { data } = await http.post<{ hits: KBHit[] }>(
    `/knowledge-bases/${kbId}/search`,
    { query, ...(k !== undefined ? { k } : {}) }
  );
  return data;
};

// ---------------------------------------------------------------------------
// Agent ↔ KB binding (the "attach knowledge_search" picker)
// ---------------------------------------------------------------------------

/** GET /knowledge-bases/{kbId}/agents — agents currently bound to this KB. */
export const listBoundAgents = async (kbId: string): Promise<KBBinding[]> => {
  const { data } = await http.get<KBBinding[]>(`/knowledge-bases/${kbId}/agents`);
  return data;
};

/** PUT /knowledge-bases/{kbId}/agents/{agentId} — upsert the binding AND ensure
 *  the `knowledge_search` tool is attached (server-side, idempotent). */
export const bindAgent = async (
  kbId: string,
  agentId: string
): Promise<KBBinding> => {
  const { data } = await http.put<KBBinding>(
    `/knowledge-bases/${kbId}/agents/${agentId}`
  );
  return data;
};

/** DELETE /knowledge-bases/{kbId}/agents/{agentId} — unbind (tool stays attached). */
export const unbindAgent = async (
  kbId: string,
  agentId: string
): Promise<void> => {
  await http.delete(`/knowledge-bases/${kbId}/agents/${agentId}`);
};

/** GET /knowledge-bases/agent-bindings/{agentId} — reverse lookup: every KB this
 *  agent is bound to. Pre-selects the agent-config multi-select. [] if unbound. */
export const getAgentKnowledgeBases = async (
  agentId: string
): Promise<AgentKBRef[]> => {
  const { data } = await http.get<AgentKBRef[]>(
    `/knowledge-bases/agent-bindings/${agentId}`
  );
  return data;
};

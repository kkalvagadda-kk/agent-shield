# Contracts — REST endpoints

Two routers:
- `routers/knowledge.py` — public (JWT, team-scoped) KB/Source/binding CRUD + test-retrieval.
  Prefix `/api/v1/knowledge-bases`. Registered in `main.py`.
- Internal search endpoint — added to the existing `routers/internal.py`
  (`/api/v1/internal`, cluster-internal only). Called by the `knowledge_search` HTTP tool.

Auth: public router uses `require_user` (`auth_middleware.py`); the caller's team comes from
`/me` resolution (`user_team_assignments`). Every read/write is filtered to that team. The
embedding sidecar (`services/embedding-sidecar`) exposes `POST /embed` (internal, no auth).

Pydantic schemas live in `schemas.py` (names below are the response/request model names).

---

## Public — Knowledge Bases

### `POST /api/v1/knowledge-bases`  → 201 `KnowledgeBaseResponse`
Body `KnowledgeBaseCreate`: `{ name: str, description?: str }`. `team` = caller's team
(server-side; body cannot set it). `created_by` = caller sub. 409 on `(team, name)` conflict.

### `GET /api/v1/knowledge-bases` → 200 `list[KnowledgeBaseResponse]`
Only the caller's team's KBs. Each item includes `source_count`, `ready_count`,
`attached_agents: list[str]` (from `agent_knowledge_bindings` → agent names).

### `GET /api/v1/knowledge-bases/{kb_id}` → 200 `KnowledgeBaseResponse` | 404
404 if not found or not the caller's team (no cross-team leak).

### `PATCH /api/v1/knowledge-bases/{kb_id}` → 200 | 404
Body: `{ name?: str, description?: str }`.

### `DELETE /api/v1/knowledge-bases/{kb_id}` → 204 | 404
Cascades sources + chunks (FK ON DELETE CASCADE) and blobs (best-effort `BlobStore` deletes
skipped in POC — orphan blobs acceptable, noted in gap ledger).

`KnowledgeBaseResponse`:
`{ id, team, name, description, created_by, created_at, updated_at, source_count,
ready_count, attached_agents }`.

---

## Public — Sources (ingestion)

### `POST /api/v1/knowledge-bases/{kb_id}/sources`  (multipart) → 201 `SourceResponse`
`multipart/form-data` with `file`. Steps (handler):
1. 404 if KB not caller's team.
2. Reject unsupported types → 415 (POC supports `text/plain`, `text/markdown`,
   `application/pdf`; `.txt/.md/.pdf` by extension when content-type is generic). DOCX
   deferred (gap ledger).
3. Read bytes; `blob_key = f"kb/{kb_id}/{source_id}/{filename}"`; `BlobStore.put(...)`.
4. Insert `knowledge_sources` row `status='pending'`, `team` from KB.
5. Schedule `BackgroundTasks` → `ingest.ingest_source(source_id)` (F-5).
6. Return `201` immediately with the `pending` source.

### `GET /api/v1/knowledge-bases/{kb_id}/sources` → 200 `list[SourceResponse]`
The polling read; ordered newest-first. `SourceResponse`:
`{ id, kb_id, filename, content_type, size_bytes, status, error, chunk_count, created_by,
created_at }`.

### `GET /api/v1/knowledge-bases/{kb_id}/sources/{source_id}/chunks` → 200 `list[ChunkResponse]`
Backs the **chunk viewer** drawer. `ChunkResponse: { id, chunk_index, content }` (no embedding
on the wire). 409 if source not `ready`.

### `POST /api/v1/knowledge-bases/{kb_id}/sources/{source_id}/reprocess` → 202
Re-runs ingest (recover a stuck `indexing` or a `failed` source). Deletes the source's chunks,
sets `status='pending'`, re-schedules the background task. Backs the **Reprocess** button.

### `DELETE /api/v1/knowledge-bases/{kb_id}/sources/{source_id}` → 204
Removes the source + its chunks (cascade). Backs the **Delete** row action.

---

## Public — Test retrieval (prove retrieval before attaching to an agent)

### `POST /api/v1/knowledge-bases/{kb_id}/search` → 200 `SearchResponse`
Body `SearchRequest: { query: str, k?: int = 5 }`. Handler: 404 if KB not caller's team →
`embed([query])` → `VectorStore.search(db, team=caller_team, kb_id=kb_id, query_embedding, k)`.
This is the **team-scoped** UI test box; `team` is the caller's, so it can only ever search
the caller's team's KB. `SearchResponse: { hits: [{ chunk_id, source_id, source_filename,
content, score }] }`.

---

## Public — Agent ↔ KB binding (the "attach knowledge_search" picker)

### `PUT /api/v1/knowledge-bases/{kb_id}/agents/{agent_id}` → 200 `BindingResponse`
Upsert the binding (POC = one KB per agent: delete any existing binding for `agent_id`, then
insert). 404 if KB or agent not caller's team. Also **ensures the agent has the
`knowledge_search` tool attached** (idempotent `agent_tools` insert) so attaching a KB wires
the tool in one action. `BindingResponse: { agent_id, agent_name, kb_id, team }`.

### `GET /api/v1/knowledge-bases/{kb_id}/agents` → 200 `list[BindingResponse]`
Agents currently bound to this KB (drives the detail page "Attached to …" line).

### `DELETE /api/v1/knowledge-bases/{kb_id}/agents/{agent_id}` → 204
Unbind (leaves the tool attached; unbinding just removes the KB scope → search returns empty).

---

## Internal — the `knowledge_search` backend (cluster-internal, no public ingress)

### `POST /api/v1/internal/knowledge/search` → 200 `KnowledgeSearchResult`
Added to `routers/internal.py`. Called only by the `knowledge_search` HTTP tool from inside an
agent pod. **Reads identity from headers set server-side by the tool** (F-1/F-2):
- `X-Agent-Team` — the pod's `AGENTSHIELD_AGENT_TEAM` (unspoofable by prompt).
- `X-Agent-Name` — the pod's `AGENT_NAME`.

Body: `{ query: str, k?: int = 5 }`.

Handler:
1. `team = header["X-Agent-Team"]`, `agent_name = header["X-Agent-Name"]`. If either missing →
   422 (fail-closed; never default the team).
2. Resolve the bound KB **server-side**: `SELECT kb_id FROM agent_knowledge_bindings
   b JOIN agents a ON a.id=b.agent_id WHERE a.name=:agent_name AND b.team=:team`. If none →
   return `{ chunks: [], citations: [], note: "no knowledge base attached" }` (fail-closed:
   an unbound agent gets nothing, never a broad search).
3. `q = (await embed([query]))[0]`.
4. `hits = VectorStore.search(db, team=team, kb_id=kb_id, query_embedding=q, k=k)`.
5. Build the response.

`KnowledgeSearchResult` (JSON the tool returns to the model AND the frontend parses for
citations):
```json
{
  "chunks": [
    { "content": "Refunds over $500 need manager approval.",
      "source": "refund-policy.pdf", "kb": "Company Policies", "score": 0.89 }
  ],
  "citations": [ { "source": "refund-policy.pdf", "kb": "Company Policies" } ]
}
```
- `chunks[].content` is what the model reads to ground its answer.
- `citations` is the de-duplicated `{source, kb}` list — the exact shape
  `AttributedBubble.citations` wants (`{source, kb}[]`). The frontend extracts THIS from the
  `tool_call_end.result` (F-4). `kb` is the KB `name`; `source` is the `knowledge_sources.filename`.
- Empty `chunks` ⇒ empty `citations` ⇒ no chip row (the bubble's `hasCitations` guard).

`kb_id`/`team` never appear in the request body or the model-facing args — only `query`.

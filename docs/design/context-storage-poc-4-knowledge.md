# POC-4 — Team Knowledge Base / RAG

**Status**: Proposed (2026-07-16) — **largest slice; needs its own detailed `/plan`+`/tasks` and its own e2e suite**
**Branch**: `worktree-ux-preview-context-storage` (commit only here; never merge to main)
**Companion**: [`context-storage-ux-roadmap.md`](./context-storage-ux-roadmap.md) §5 · [`context-storage-architecture.md`](./context-storage-architecture.md) §9
**Live baseline** (after POC-3): registry-api / studio / declarative-runner at their POC-3 tags

---

## 1. Why this exists

Agents should answer from a team's *own* documents, with a citation, and never leak another team's content. POC-4 is the retrieval-augmented layer: upload Sources → chunk/embed/index → a governed `knowledge_search` tool → answers grounded in Sources with **runtime citations**. It **fills the citation slot POC-2b built** (2b-iv) — that empty chip row becomes real here.

**Reason from the running product.** What exists: pgvector migration `0022` (defensive — **skips the vector column if the Postgres image lacks the `vector` extension**, so semantic search stays disabled until a pgvector-capable Postgres is provisioned — a hard prerequisite for this POC); a boto3/S3 access pattern (`judge.py:626`); Knowledge pages as **preview mocks** (`studio/src/pages/preview/KnowledgeBasesPage.tsx`, `KnowledgeBaseDetailPage.tsx`) with the real nav link already present (`Knowledge` → `/knowledge`, `BUILD_ITEMS`). What's missing: all knowledge backend (tables, ingest, tool, retrieval) and the real UI.

---

## 2. Prerequisites to verify at `/plan` time (potential blockers — surface early)

1. **pgvector-capable Postgres on the EKS cluster.** Migration 0022 auto-skips vectors if absent. If the cluster Postgres has no `vector` extension, retrieval can't work — must provision a pgvector image or accept a fallback (keyword search only, degraded). **Verify before building.**
2. **Embedding provider.** Anthropic models don't embed. **Default decision (keeps POC self-contained, no external credential/egress):** a **local embedding sidecar** (e.g. a small sentence-transformers model) exposed as an internal HTTP endpoint. Alternatives (Voyage AI / an OpenAI-compatible endpoint) need a credential + egress decision — deferred unless the local path is inadequate. Lock this in `/plan`.
3. **MinIO bucket + `BlobStore` port** for Source blobs (the architecture names a `BlobStore` port mirroring `ConversationStore`/`VectorStore`).

---

## 3. Scope decisions

1. **Team-scoped, tenant-isolated by construction (S5).** Every chunk row carries `(team, kb_id)`; **every retrieval query has a mandatory `(team, kb_id)` predicate baked into the store method** — not an optional filter a caller can forget. A query can never return another team's chunks; this is the security crux and is enforced in the `VectorStore` port, fail-closed.
2. **Retrieval is a governed platform tool.** `knowledge_search` is a normal platform-managed tool, so OPA + HITL wrap it for free (no new governance path). Attach it to an agent like any tool.
3. **Synthetic Sources only in the POC.** S7 ingest content-scanning (untrusted upload → prompt-injection via retrieved chunks) is deferred to the Tighten line; the POC uses trusted synthetic documents. Documented, not hidden.
4. **Citations are first-class.** `knowledge_search` returns chunks WITH source refs `{source, kb}`; the agent cites them; the POC-2b citation slot renders them. Closes the 2b-iv loop.

---

## 4. Architecture

### 4.1 Data model (new migration, next number at build time)

- `knowledge_bases (id, team, name, description, created_by, created_at)` — team-scoped KB.
- `knowledge_sources (id, kb_id, team, filename, blob_key, status, error, chunk_count, created_at)` — one uploaded file; `status ∈ {pending, indexing, ready, failed}`.
- `knowledge_chunks (id, kb_id, team, source_id, chunk_index, content, embedding vector(N), created_at)` — pgvector column (guarded like 0022); index `ivfflat`/`hnsw` on `embedding`; composite index on `(team, kb_id)`.

### 4.2 Ports (mirror the POC-0 `ConversationStore` pattern via `store_factory`)

- `BlobStore` — `put(key, bytes)`, `get(key)` → MinIO (S3) impl.
- `VectorStore` — `index(chunks)`, `search(team, kb_id, query_embedding, k)` — **`(team, kb_id)` is a required, non-optional argument** (S5); pgvector impl. One construction choke point in `store_factory`.

### 4.3 Ingest pipeline

`POST /knowledge-bases/{id}/sources` (multipart upload) → `BlobStore.put` → enqueue/inline ingest: extract text → chunk (bounded size/overlap) → embed (embedding sidecar) → `VectorStore.index` → status `ready` (or `failed` with error). Status polled by the UI. Keep it a single well-instrumented path; a background task or a small worker (decide scale in `/plan` — inline is fine for POC volumes).

### 4.4 `knowledge_search` tool

Platform tool (Python type) that, given the calling agent's `(team, kb_id)` binding + the query, embeds the query and calls `VectorStore.search(team, kb_id, …)`, returning the top-k chunks + source refs. The `(team, kb_id)` comes from the agent's server-side binding, **never** from tool args (so a prompt can't widen the tenant scope).

### 4.5 Frontend

- Real **Knowledge** page (lift the preview mocks): KB list; a KB detail with a **Sources** tab (upload, per-source ingestion status, chunk viewer) and a **Test retrieval** box (query → ranked chunks, proving retrieval before wiring to an agent).
- Attach-`knowledge_search`-to-agent picker (KB binding).
- Runtime **citations** in chat — fill the POC-2b `citations` slot from `knowledge_search` results.

---

## 5. Verification (Definition of Done gate) — likely its own `suite-77`

- Upload a synthetic Source → poll to `ready` → **Test retrieval** returns the expected chunk.
- An agent with `knowledge_search` **answers from the Source WITH a citation** (assert the citation chip renders — the POC-2b slot).
- **Tenant isolation** — team A's query never returns team B's chunks (the headline security test; assert at the store + API layer, fail-closed).
- Playwright: upload → status → test-retrieval → attach → chat answer shows a citation.
- Vitest: Knowledge page (list/upload/status/chunk viewer/test-retrieval); citation rendering.
- No orphan code; image bumps (registry-api + studio + embedding/ingest worker) in all three files; deploy user-gated EKS step.

## 6. Known gaps (ledger)

- **S7 ingest content-scanning** (untrusted-upload → injection via retrieved chunk) — **deferred (Tighten)**; POC uses synthetic trusted Sources.
- **pgvector absent** → retrieval degrades to keyword-only or is disabled (prerequisite #1); surfaced, not silent.
- **Embedding provider** — local sidecar default; external providers deferred.
- **Ingest scale** — inline/background-task for POC; a durable ingest worker is a later hardening.

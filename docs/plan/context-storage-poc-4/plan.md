# POC-4 — Team Knowledge Base / RAG — Implementation Plan

**Branch:** `worktree-ux-preview-context-storage` (commit here ONLY — never merge to main).
**Spec (authoritative):** `docs/design/context-storage-poc-4-knowledge.md`.
**Companions:** `context-storage-ux-roadmap.md` §5, `context-storage-architecture.md` §9.
**Read first:** `research.md` (prerequisite dispositions + the 6 grounded findings F-1…F-6),
`data-model.md`, `contracts/`.

> **Alignment Check:** The ultimate goal is *agents answer from a team's own documents, with a
> real citation, and never leak another team's content*. Every task below serves one of:
> (a) making retrieval real (embed/index/search over pgvector+MinIO), (b) making tenant
> isolation structural (mandatory `(team, kb_id)` predicate, server-side binding), or
> (c) closing the citation loop into the POC-2b slot. No task degrades the S5 isolation goal
> to make something compile; the isolation predicate is a required arg, not an optional filter.

---

## 1. Goal

Ship the retrieval-augmented layer end-to-end as a vertical slice: upload a Source → chunk +
embed + index → a governed `knowledge_search` tool → an agent answers grounded in the Source
**with a runtime citation chip** — and prove team A can never retrieve team B's chunks. Fill
the empty POC-2b `AttributedBubble.citations` slot with real data.

## 2. Architecture

```
Studio (Knowledge page)                         Agent pod (SDK/declarative runner)
  │ upload / poll / test-retrieval / attach       │ model calls knowledge_search (HTTP tool)
  ▼                                                ▼ headers X-Agent-Team/Name from pod env
registry-api  routers/knowledge.py  ─────────► routers/internal.py
  │  BlobStore.put ─► MinIO (knowledge-sources)     POST /internal/knowledge/search
  │  ingest.py: extract→chunk→embed→index           1 resolve kb_id from agent_knowledge_bindings
  │     │            │                               2 embed(query) via sidecar
  │     │            ▼ embedding_client.embed        3 VectorStore.search(team,kb_id,…)  ◄─ S5
  │     │        embedding-sidecar (/embed, bge-small, 384-dim, ONNX)
  │     ▼ VectorStore.index
  │  Postgres (pgvector)  knowledge_chunks.embedding vector(384) + hnsw
  ▼
tool_call_end SSE (result carries citations) ─► ChatPane/chatStream ─► AttributedBubble.citations
```

Key architectural decisions (grounded in research.md):
- **F-1** `knowledge_search` = HTTP tool → cluster-internal registry-api endpoint; `(team,
  kb_id)` are server-side (env header + DB binding), never model args.
- **F-3** agent→KB binding lives in `agent_knowledge_bindings` (agent_tools has no config col).
- **F-4** citations are frontend-only wiring off the existing `tool_call_end.result` — **no
  declarative-runner / SDK change or bump.**
- **F-5** ingest = FastAPI `BackgroundTasks` (non-blocking upload, real status lifecycle).
- Ports (`BlobStore`, `VectorStore`, `embedding_client`) behind `store_factory`, mirroring the
  POC-0 `ConversationStore` seam.

## 3. Tech Stack

| Concern | Choice | Note |
|---|---|---|
| Object storage | MinIO (deployed) via boto3 | research.md Prereq 3 |
| Vector store | Postgres + pgvector (deployed, portable image) | research.md Prereq 1 |
| Embeddings | `bge-small-en-v1.5` (384-dim) via `fastembed`/ONNX in a new sidecar | research.md Prereq 2 |
| Text extraction | native for txt/md; `pypdf` for PDF | docx deferred |
| Ingest | `fastapi.BackgroundTasks` | F-5 |
| Tool | HTTP-type platform tool | F-1 |
| Backend | FastAPI (registry-api), SQLAlchemy async, Alembic | existing |
| Frontend | React + Vite + React Query + Tailwind | existing |
| Citations | existing `AttributedBubble.citations` slot | POC-2b |

New registry-api deps (add to `services/registry-api/requirements.txt`): `pypdf>=4.0`.
(boto3, httpx already present.) Sidecar deps: `fastapi`, `uvicorn`, `fastembed`.

## 4. Constitution Check (CLAUDE.md Definition of Done)

| Gate | How this plan satisfies it |
|---|---|
| 1. Real user journey (Playwright) | **T-020** `studio/e2e/knowledge.spec.ts`: upload → poll status → test-retrieval → attach → chat shows a citation chip. |
| 2. Save→reload→assert survived | **T-019** suite-77 uploads a Source, polls to `ready`, then **re-reads** `GET …/sources` + `…/chunks` from the backend and asserts persistence; **T-018** Vitest asserts the KB list re-fetches. |
| 3. No orphan code | Each new symbol has a live caller — checklist in T-021; ports/tool/endpoint greps in contracts. |
| 4. Vertical slice | Phases build one path (blob→embed→index→search→cite) and prove it at CP-2/CP-3 before UI polish. |
| 5. Honest gap ledger | Updated in T-021 (docs/testing manual plan header + spec §6): docx, durable ingest worker, multi-KB, orphan-blob GC, signed service token, S7 content-scan. |
| 6. Reason from running product | Plan is built from verified code (research.md fact table), not the design doc's "(Python type)". |
| No-Bandaid | HTTP-tool + server-side binding is the structural fix (illegal "model sets team" state unrepresentable), not an `if team==` guard. |
| Migrations idempotent | 0067 guarded like 0022; `IF NOT EXISTS`; guarded vector col. |
| Image bumps (both files + values) | T-021 bumps registry-api 0.2.195, studio 0.1.146, new embedding-sidecar 0.1.0 in deploy-cpe2e.sh + deploy-eks.sh + values.yaml + values-eks.yaml. |
| Experience docs | T-021 updates `docs/experience/playground.md` (new citation chip + knowledge page). |

## 5. File Structure

| Path | New/Edit | Task | Purpose |
|---|---|---|---|
| `scripts/plan-poc4/verify-prereqs.sh` | New | T-001 | assert pgvector+MinIO live on the deployed cluster |
| `services/embedding-sidecar/main.py` | New | T-002 | `POST /embed` (fastembed, 384-dim) |
| `services/embedding-sidecar/Dockerfile` | New | T-002 | bake model weights at build |
| `services/embedding-sidecar/requirements.txt` | New | T-002 | fastapi/uvicorn/fastembed |
| `charts/agentshield/templates/embedding-sidecar.yaml` | New | T-003 | Deployment + Service |
| `charts/agentshield/values.yaml` | Edit | T-003,T-021 | sidecar toggle + image tags |
| `charts/agentshield/values-eks.yaml` | Edit | T-003,T-021 | sidecar ECR repo + tags |
| `services/registry-api/alembic/versions/0067_knowledge_base_rag.py` | New | T-004 | 4 tables, guarded vector col+index |
| `services/registry-api/models.py` | Edit | T-005 | 4 ORM models |
| `services/registry-api/blob_store.py` | New | T-006 | `BlobStore` + `MinioBlobStore` |
| `services/registry-api/vector_store.py` | New | T-007 | `VectorStore` + `PgVectorStore` (S5) |
| `services/registry-api/embedding_client.py` | New | T-008 | `embed()` + `EMBEDDING_DIM` |
| `services/registry-api/store_factory.py` | Edit | T-008 | `get_blob_store` / `get_vector_store` |
| `services/registry-api/schemas.py` | Edit | T-009 | KB/source/chunk/binding/search models |
| `services/registry-api/ingest.py` | New | T-010 | extract→chunk→embed→index pipeline |
| `services/registry-api/routers/knowledge.py` | New | T-011 | public KB/source/binding/test-retrieval API |
| `services/registry-api/main.py` | Edit | T-011 | register knowledge router |
| `services/registry-api/routers/internal.py` | Edit | T-012 | `POST /internal/knowledge/search` |
| `scripts/seed-defaults.sh` | Edit | T-013 | seed `knowledge_search` HTTP tool |
| `studio/src/api/knowledgeApi.ts` | New | T-014 | typed client for the KB endpoints |
| `studio/src/pages/KnowledgeBasesPage.tsx` | New | T-015 | real KB list + create (replaces preview) |
| `studio/src/pages/KnowledgeBaseDetailPage.tsx` | New | T-016 | real detail: sources/retrieval/settings/attach |
| `studio/src/App.tsx` | Edit | T-015 | route imports → real pages |
| `studio/src/lib/chatStream.ts` | Edit | T-017 | parse `knowledge_search` result → citations |
| `studio/src/components/playground/ChatPane.tsx` | Edit | T-017 | pass `citations` to `AttributedBubble` |
| `studio/src/pages/AgentChatPage.tsx` | Edit | T-017 | same citation wiring on deployed-agent chat |
| `studio/src/pages/KnowledgeBasesPage.test.tsx` | New | T-018 | Vitest list/create |
| `studio/src/pages/KnowledgeBaseDetailPage.test.tsx` | New | T-018 | Vitest sources/status/chunk/retrieval |
| `studio/src/components/chat/AttributedBubble.test.tsx` | New/Edit | T-018 | citation rendering |
| `scripts/e2e/suite-77-knowledge-rag.sh` | New | T-019 | e2e incl. tenant isolation |
| `scripts/e2e/run-all.sh` | Edit | T-019 | register suite-77 |
| `studio/e2e/knowledge.spec.ts` | New | T-020 | Playwright journey |
| `scripts/deploy-cpe2e.sh` | Edit | T-021 | image tag bumps + sidecar build |
| `scripts/deploy-eks.sh` | Edit | T-021 | tag bumps + sidecar build/deploy |
| `docs/experience/playground.md` | Edit | T-021 | citation chip + knowledge flow |
| `docs/testing/manual-ui-e2e-test-plan.md` | Edit | T-021 | gap ledger |
| `scripts/plan-poc4/smoke-knowledge.sh` | New | T-011,T-021 | checkpoint smoke |

## 6. Key Interfaces (exact signatures — consistent across all tasks)

Constants (defined once, imported everywhere): `EMBEDDING_DIM = 384` (`embedding_client.py`),
`CHUNK_SIZE = 1000`, `CHUNK_OVERLAP = 150`, `DEFAULT_TOP_K = 5`.

```python
# embedding_client.py
EMBEDDING_DIM = 384
async def embed(texts: list[str]) -> list[list[float]]: ...

# blob_store.py   (Protocol + MinioBlobStore)
async def put(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
async def get(self, key: str) -> bytes: ...

# vector_store.py (Protocol + PgVectorStore) — (team, kb_id) REQUIRED, fail-closed
async def index(self, db, *, team: str, kb_id: str, chunks: list[ChunkToIndex]) -> int: ...
async def search(self, db, *, team: str, kb_id: str,
                 query_embedding: list[float], k: int = 5) -> list[SearchHit]: ...

# store_factory.py
def get_blob_store() -> BlobStore: ...
def get_vector_store() -> VectorStore: ...

# ingest.py
async def ingest_source(source_id: str) -> None:
    """Own session. pending→indexing; extract(blob)→chunk→embed→VectorStore.index;
    set chunk_count + status=ready, or status=failed+error (fail-loud, no silent skip)."""
def extract_text(filename: str, content_type: str | None, data: bytes) -> str: ...
def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]: ...
```

```typescript
// knowledgeApi.ts
export interface KnowledgeBase { id; team; name; description; source_count; ready_count; attached_agents: string[]; created_at; updated_at }
export interface KBSource { id; kb_id; filename; content_type; size_bytes; status: "pending"|"indexing"|"ready"|"failed"; error?; chunk_count; created_at }
export interface KBChunk { id; chunk_index; content: string }
export interface KBHit { chunk_id; source_id; source_filename; content; score: number }
export const listKBs: () => Promise<KnowledgeBase[]>;
export const createKB: (b: {name; description?}) => Promise<KnowledgeBase>;
export const getKB: (id: string) => Promise<KnowledgeBase>;
export const uploadSource: (kbId: string, file: File) => Promise<KBSource>;
export const listSources: (kbId: string) => Promise<KBSource[]>;
export const getChunks: (kbId: string, sourceId: string) => Promise<KBChunk[]>;
export const reprocessSource: (kbId, sourceId) => Promise<void>;
export const deleteSource: (kbId, sourceId) => Promise<void>;
export const testRetrieval: (kbId: string, query: string, k?: number) => Promise<{hits: KBHit[]}>;
export const listBoundAgents: (kbId: string) => Promise<{agent_id; agent_name; kb_id; team}[]>;
export const bindAgent: (kbId: string, agentId: string) => Promise<{agent_id; agent_name; kb_id; team}>;
export const unbindAgent: (kbId, agentId) => Promise<void>;

// AttributedBubble citations prop (ALREADY EXISTS — feed it):
citations?: { source: string; kb: string }[];
```

---

## 7. Tasks

Legend — each task: **Files** (≤3) · **Interface** · **Acceptance** · **Deps** · **Tests** ·
**Verify**. Checkpoints (CP-N) are mandatory gates with executable scripts. No task references a
symbol defined in a later task (no forward deps).

### Phase 0 — Prerequisites (executable disposition)

**T-001 — Prereq verification script**
- Files: `scripts/plan-poc4/verify-prereqs.sh`
- Interface: `kubectl exec` into the postgres pod → `SELECT extname FROM pg_extension WHERE
  extname='vector'` (must be present on EKS) AND `SELECT 1 FROM pg_available_extensions WHERE
  name='vector'`; check the MinIO Service resolves + `head`/list bucket via an in-cluster
  `mc`/boto3 one-liner; print PASS/FAIL per prereq.
- Acceptance: exits 0 with "pgvector: PRESENT" and "minio: REACHABLE" on the deployed EKS
  cluster; exits non-zero + a clear remediation line otherwise.
- Deps: none.
- Tests: the script IS the test (checkpoint CP-0).
- Verify: `bash scripts/plan-poc4/verify-prereqs.sh`.

> **CP-0 (checkpoint):** `bash scripts/plan-poc4/verify-prereqs.sh` green. If pgvector is
> absent, STOP and escalate (retrieval can't be semantic) — do not silently proceed to
> keyword-only on EKS. On EKS it is present (research.md), so CP-0 passes.

### Phase 1 — Embedding sidecar (new service)

**T-002 — embedding-sidecar service**
- Files: `services/embedding-sidecar/main.py`, `.../Dockerfile`, `.../requirements.txt`
- Interface: `POST /embed {texts: list[str]} → {embeddings: list[list[float]], dim: 384,
  model: "bge-small-en-v1.5"}`; `GET /ready → 200` once the ONNX model is loaded. Uses
  `fastembed.TextEmbedding("BAAI/bge-small-en-v1.5")`. Dockerfile runs one dummy embed at
  build so weights are cached in the image (no runtime download/egress).
- Acceptance: `POST /embed {"texts":["hello"]}` returns one 384-float vector; startup loads the
  model once (module-level singleton), not per request.
- Deps: none.
- Tests: a tiny `services/embedding-sidecar/smoke.sh` (curl `/embed`, assert `dim==384`).
- Verify: `docker build services/embedding-sidecar/` then `python -c "import ast;
  ast.parse(open('services/embedding-sidecar/main.py').read())"`.

**T-003 — sidecar chart + deploy wiring**
- Files: `charts/agentshield/templates/embedding-sidecar.yaml`, `charts/agentshield/values.yaml`,
  `charts/agentshield/values-eks.yaml`
- Interface: Deployment (1 replica, `resources.requests cpu 250m/mem 512Mi`, readiness on
  `/ready`) + ClusterIP Service `agentshield-embedding-sidecar:8000`; `values.yaml`
  `embeddingSidecar.enabled: true` + image tag `0.1.0`; `values-eks.yaml` ECR repo string.
- Acceptance: `helm template` renders the Deployment+Service; no other component changes.
- Deps: T-002.
- Tests: covered by CP-1 smoke.
- Verify: `helm template charts/agentshield -f charts/agentshield/values-eks.yaml | grep embedding-sidecar`.

> **CP-1 (checkpoint):** build + deploy the sidecar (`bash scripts/deploy-eks.sh` builds the
> new image; or a scoped `kubectl apply`), then
> `kubectl exec deploy/…registry-api -- python -c "import httpx;print(len(httpx.post('http://agentshield-embedding-sidecar…:8000/embed',json={'texts':['x']}).json()['embeddings'][0]))"`
> prints `384`. Gate: sidecar `/ready` 200 and dim 384.

### Phase 2 — Data model

**T-004 — migration 0067**
- Files: `services/registry-api/alembic/versions/0067_knowledge_base_rag.py`
- Interface: creates `knowledge_bases`, `knowledge_sources`, `knowledge_chunks`,
  `agent_knowledge_bindings` (data-model.md); guarded `vector(384)` column + `hnsw` index via
  `_pgvector_available` probe; `ix_knowledge_chunks_team_kb` always created; idempotent.
- Acceptance: `alembic upgrade head` on a pgvector DB creates the embedding column + hnsw
  index; on a stock DB it skips them without error; re-running is a no-op.
- Deps: none (independent of the sidecar).
- Tests: covered by CP-2 (suite-77 upload path exercises the tables).
- Verify: `python -c "import ast; ast.parse(open('.../0067_knowledge_base_rag.py').read())"`;
  `down_revision=="0065"`.

**T-005 — ORM models**
- Files: `services/registry-api/models.py`
- Interface: `KnowledgeBase`, `KnowledgeSource`, `KnowledgeChunk` (WITHOUT the `embedding`
  attr — data-model.md), `AgentKnowledgeBinding`; relationships + cascade as specified.
- Acceptance: `from routers... ; sqlalchemy.orm.configure_mappers()` runs clean.
- Deps: T-004 (columns exist to map).
- Tests: mapper-configure check.
- Verify: `cd services/registry-api && python -c "import models, sqlalchemy.orm as o;
  o.configure_mappers(); print('ok')"`.

### Phase 3 — Ports

**T-006 — BlobStore (MinIO)**
- Files: `services/registry-api/blob_store.py`
- Interface: `contracts/ports.md` `BlobStore` Protocol + `MinioBlobStore` (boto3, path-style,
  bucket-create-on-first-put, blocking calls via `run_in_executor`).
- Acceptance: `put` then `get` round-trips bytes against a live MinIO; missing key → `KeyError`.
- Deps: none.
- Tests: exercised by suite-77 upload (blob then chunk read).
- Verify: `python -c "import ast; ast.parse(open('.../blob_store.py').read())"`.

**T-007 — VectorStore (pgvector, S5)**
- Files: `services/registry-api/vector_store.py`
- Interface: `contracts/ports.md` `VectorStore` Protocol + `PgVectorStore.index/search` with
  **required** `(team, kb_id)`; `ValueError` on empty team/kb_id; keyword fallback when the
  embedding column is absent (still team+kb scoped).
- Acceptance: `search(team=A, kb_id=<B kb>)` returns `[]`; `search(team=A, kb_id=<A kb>)`
  returns A's chunks ordered by score; passing `team=""` raises `ValueError`.
- Deps: T-004.
- Tests: unit-style assertions inside suite-77 (store-layer isolation).
- Verify: syntax parse; grep confirms no code path calls `search` without both kwargs.

**T-008 — store_factory + embedding client**
- Files: `services/registry-api/embedding_client.py`, `services/registry-api/store_factory.py`
- Interface: `embed()` + `EMBEDDING_DIM=384`; `get_blob_store()`/`get_vector_store()` choke
  points (contracts/ports.md).
- Acceptance: `get_blob_store()` returns a cached `MinioBlobStore`; `get_vector_store()` a
  `PgVectorStore`; unknown backend env → `ValueError`; `embed` raises on dim mismatch.
- Deps: T-006, T-007.
- Tests: CP-2.
- Verify: syntax parse; `grep get_blob_store store_factory.py`.

### Phase 4 — Ingest + public API

**T-009 — schemas**
- Files: `services/registry-api/schemas.py`
- Interface: `KnowledgeBaseCreate/Response`, `SourceResponse`, `ChunkResponse`,
  `SearchRequest/Response` (+`KBHit`), `BindingResponse` (contracts/endpoints.md field lists).
- Acceptance: models import; status is a `Literal["pending","indexing","ready","failed"]`.
- Deps: none.
- Tests: import check.
- Verify: `python -c "import schemas"` in registry-api dir.

**T-010 — ingest pipeline**
- Files: `services/registry-api/ingest.py`
- Interface: `ingest_source(source_id)`, `extract_text(...)`, `chunk_text(...)` (§6). Opens its
  own `AsyncSessionLocal`; `pending→indexing` up front; `BlobStore.get` → `extract_text`
  (txt/md native, pdf via `pypdf`) → `chunk_text` → `embed(chunks)` → `VectorStore.index` →
  set `chunk_count`+`ready`; on any exception set `status='failed'`, `error=str(exc)` (fail-loud,
  logged) and re-raise-swallow so the background task ends cleanly.
- Acceptance: a 3-paragraph synthetic .txt yields ≥1 chunk, each with a 384-vector row; a
  corrupt/empty file → `status='failed'` with a non-null `error`.
- Deps: T-005, T-008; add `pypdf` to `requirements.txt`.
- Tests: suite-77 asserts `ready` + `chunk_count>0`, and a failed-case assertion.
- Verify: syntax parse; `grep -n "VectorStore\|embed\|BlobStore" ingest.py`.

**T-011 — public knowledge router + register + smoke**
- Files: `services/registry-api/routers/knowledge.py`, `services/registry-api/main.py`,
  `scripts/plan-poc4/smoke-knowledge.sh`
- Interface: every public endpoint in `contracts/endpoints.md` (KB CRUD, source upload
  multipart + `BackgroundTasks`, list/chunks/reprocess/delete, test-retrieval, binding
  PUT/GET/DELETE). All reads/writes team-scoped via `require_user`/`/me`. Register the router
  in `main.py` (`include_router`). Binding PUT also ensures the `knowledge_search` `agent_tools`
  row.
- Acceptance: create KB → upload .txt → poll `sources` to `ready` → `chunks` returns text →
  `search` returns a hit → bind an agent → `GET agents` shows it. All via `smoke-knowledge.sh`.
- Deps: T-008, T-009, T-010.
- Tests: `smoke-knowledge.sh` (kubectl-exec curl chain) is the CP-2 driver.
- Verify: `python -c "import ast; ast.parse(open('.../routers/knowledge.py').read())"`;
  mapper-configure; `grep knowledge_router main.py`.

> **CP-2 (checkpoint):** deploy registry-api (tag 0.2.195) + sidecar, then
> `bash scripts/plan-poc4/smoke-knowledge.sh`: upload→ready→chunks→test-retrieval all green,
> **and** a two-team isolation probe (team A `search` on a KB seeded under team B returns `[]`
> at BOTH the `/knowledge-bases/{kb}/search` API and `VectorStore.search`). Gate: retrieval
> works + isolation holds. This is the vertical-slice proof before any UI work.

### Phase 5 — Tool + internal endpoint

**T-012 — internal knowledge/search endpoint**
- Files: `services/registry-api/routers/internal.py`
- Interface: `POST /api/v1/internal/knowledge/search` per `contracts/endpoints.md`: read
  `X-Agent-Team`/`X-Agent-Name` (422 if missing), resolve `kb_id` from
  `agent_knowledge_bindings`, `embed(query)`, `VectorStore.search(team,kb_id,…)`, return
  `KnowledgeSearchResult{chunks,citations}`; unbound agent → empty (fail-closed).
- Acceptance: with headers for a bound agent, returns chunks+citations; wrong team header →
  empty (can't cross tenants); missing header → 422.
- Deps: T-008; T-011 (bindings written).
- Tests: suite-77 drives it via a real agent run AND a direct curl with spoofed team header
  (must return empty).
- Verify: syntax parse; `grep -n "knowledge/search" routers/internal.py`.

**T-013 — seed knowledge_search tool**
- Files: `scripts/seed-defaults.sh`
- Interface: idempotent `post_idempotent /api/v1/tools/` with the exact body in
  `contracts/knowledge-search-tool.md` (HTTP tool, internal URL, env-substituted headers).
- Acceptance: after seed, `GET /api/v1/tools?name=knowledge_search` returns one HTTP tool with
  the `X-Agent-Team`/`X-Agent-Name` headers and `input_schema.query`.
- Deps: none (but tool is only USEFUL after T-012).
- Tests: suite-77 asserts the tool exists + is attachable.
- Verify: `bash -n scripts/seed-defaults.sh`; `grep knowledge_search scripts/seed-defaults.sh`.

### Phase 6 — Frontend

**T-014 — knowledgeApi client**
- Files: `studio/src/api/knowledgeApi.ts`
- Interface: the typed client in §6 (uses the shared `http` axios instance from `registryApi`).
- Acceptance: typechecks; multipart upload uses `FormData`.
- Deps: none (types mirror T-009).
- Tests: consumed by T-018.
- Verify: `cd studio && npm run typecheck`.

**T-015 — real Knowledge list page**
- Files: `studio/src/pages/KnowledgeBasesPage.tsx`, `studio/src/App.tsx`
- Interface: lift `pages/preview/KnowledgeBasesPage.tsx` markup; swap `MOCK_KBS` for
  `useQuery(listKBs)`; wire the New-KB modal to `createKB` + invalidate; update `App.tsx`
  route imports to the real pages.
- Acceptance: page lists KBs from the API, creates one, and the new KB appears after
  invalidation (save→reload).
- Deps: T-014.
- Tests: T-018.
- Verify: `npm run typecheck`; `grep -n "KnowledgeBasesPage" studio/src/App.tsx` points at
  `pages/` not `pages/preview/`.

**T-016 — real Knowledge detail page**
- Files: `studio/src/pages/KnowledgeBaseDetailPage.tsx`
- Interface: lift `pages/preview/KnowledgeBaseDetailPage.tsx`; Sources tab → `uploadSource`
  (real file input) + `listSources` polling (`refetchInterval` while any source is
  `pending|indexing`) + chunk drawer via `getChunks` + `reprocessSource`/`deleteSource`;
  Retrieval tab → `testRetrieval`; Settings tab → PATCH; **Attach agent picker** →
  `listBoundAgents`/`bindAgent`/`unbindAgent`. Map `pending→Queued`, `indexing→Processing`
  (F-6).
- Acceptance: upload a file → status advances to Ready via polling → View shows real chunks →
  test-retrieval returns ranked hits → attach an agent shows in "Attached to".
- Deps: T-014.
- Tests: T-018.
- Verify: `npm run typecheck`.

**T-017 — runtime citation wiring**
- Files: `studio/src/lib/chatStream.ts`, `studio/src/components/playground/ChatPane.tsx`,
  `studio/src/pages/AgentChatPage.tsx`
- Interface: in the `tool_call_end` handler, when `tool === "knowledge_search"`,
  `JSON.parse(result)` and read `citations: {source, kb}[]`; attach to the current assistant
  message (`M.citations`, mirroring `attachToolCall`). Pass `citations={m.citations}` to
  `AttributedBubble` on both surfaces. (No SDK/runner change — F-4.)
- Acceptance: after a chat turn where the agent calls `knowledge_search`, the assistant bubble
  renders a citation chip `{source · kb}`.
- Deps: T-012 (endpoint returns citations); T-014 optional.
- Tests: T-018 (unit) + T-020 (Playwright).
- Verify: `npm run typecheck`; `grep -n "citations" studio/src/components/playground/ChatPane.tsx`.

**T-018 — Vitest**
- Files: `studio/src/pages/KnowledgeBasesPage.test.tsx`,
  `studio/src/pages/KnowledgeBaseDetailPage.test.tsx`,
  `studio/src/components/chat/AttributedBubble.test.tsx`
- Interface: `renderWithProviders` + `vi.mock('../api/knowledgeApi')`; assert list/empty/create,
  upload+status+chunk-viewer+test-retrieval, and that `AttributedBubble` renders a chip when
  `citations` is non-empty and none when empty.
- Acceptance: `npm run test` green.
- Deps: T-015, T-016, T-017.
- Tests: self.
- Verify: `cd studio && npm run test`.

### Phase 7 — E2E, image bumps, docs

**T-019 — suite-77 backend e2e**
- Files: `scripts/e2e/suite-77-knowledge-rag.sh`, `scripts/e2e/run-all.sh`
- Interface: kubectl-exec/curl suite. Test cases:
  `T-S77-001` create KB + upload synthetic .txt (known fact) → poll to `ready`, `chunk_count>0`;
  `T-S77-002` **save→reload**: re-read `sources` + `chunks` from backend, assert survived;
  `T-S77-003` test-retrieval returns the chunk containing the known fact (top hit);
  `T-S77-004` seed+attach `knowledge_search`, run the agent, assert the answer includes the
  fact AND the response/tool-result carries a `{source,kb}` citation;
  `T-S77-005` **tenant isolation** (headline): team-B KB + chunk; team-A `search` (API) → `[]`;
  direct `/internal/knowledge/search` with `X-Agent-Team: A` against B's binding → `[]`;
  assert at store + API, fail-closed.
- Acceptance: suite exits 0 with all 5 cases; registered + executable in `run-all.sh`.
- Deps: all backend tasks (T-004…T-013).
- Tests: self.
- Verify: `bash scripts/e2e/suite-77-knowledge-rag.sh`; `grep 77 scripts/e2e/run-all.sh`.

**T-020 — Playwright journey**
- Files: `studio/e2e/knowledge.spec.ts`
- Interface: real Keycloak login (global-setup); navigate `/knowledge`; create KB; upload a
  fixture file (`file_upload`/`setInputFiles`); wait for status Ready (poll UI);
  `waitForResponse` on the sources + chunks calls; run test-retrieval; attach an agent; open a
  chat, send a question, assert a citation chip renders (`getByText(source)`). Assert wiring +
  persistence + network, not agent completion (few agent pods — same boundary as bash suites).
- Acceptance: `bash scripts/studio-e2e.sh` green (or the spec passes for the reachable steps;
  agent-run assertion tolerant of no-pod like existing specs).
- Deps: T-015…T-017.
- Tests: self.
- Verify: `bash scripts/studio-e2e.sh`.

**T-021 — image bumps, docs, gap ledger, no-orphan gate**
- Files: `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`,
  `charts/agentshield/values-eks.yaml`, `docs/experience/playground.md`,
  `docs/testing/manual-ui-e2e-test-plan.md` (≤3 per commit — split as needed)
- Interface: bump `REGISTRY_API_TAG 0.2.194→0.2.195`, `STUDIO_TAG 0.1.145→0.1.146`, add
  `EMBEDDING_SIDECAR_TAG=0.1.0` + build entry (both deploy scripts); mirror tags in
  `values.yaml` and `values-eks.yaml`; **do NOT bump declarative-runner** (F-4 — note the
  reason in the header comment). Update `docs/experience/playground.md` with the citation chip +
  the Knowledge page flow. Add gap-ledger entries (docx, durable ingest worker, multi-KB,
  orphan-blob GC, signed pod↔registry token, S7 content-scan). Run the **no-orphan greps**:
  `BlobStore`, `VectorStore`, `get_blob_store`, `get_vector_store`, `embed`, `knowledge_search`,
  `ingest_source`, `agent_knowledge_bindings`, `citations` — each has a live caller.
- Acceptance: both deploy scripts reference the new tags; values files match; experience doc +
  gap ledger updated; every new symbol greps to a caller.
- Deps: everything.
- Tests: n/a (gate).
- Verify: `grep -n "0.2.195\|0.1.146\|EMBEDDING_SIDECAR_TAG" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml charts/agentshield/values-eks.yaml`.

> **CP-3 (final checkpoint):** `bash scripts/deploy-eks.sh` (user-gated) builds+deploys
> registry-api 0.2.195, studio 0.1.146, embedding-sidecar 0.1.0; then
> `bash scripts/e2e/suite-77-knowledge-rag.sh` green (incl. tenant isolation) **and**
> `bash scripts/studio-e2e.sh` green **and** `cd studio && npm run test && npm run typecheck`
> green. Definition-of-Done statement written (which Playwright step proves the journey, what
> the reload test asserts, no orphan). Only then is POC-4 done.

---

## 8. Task dependency graph (no forward refs)

```
T-001 ─CP0
T-002 ─► T-003 ─CP1
T-004 ─► T-005
T-004 ─► T-007
T-006, T-007 ─► T-008 ─► T-010 ─► T-011 ─CP2
T-009 ─► T-011
T-011 ─► T-012 ; T-013 (independent seed)
T-014 ─► T-015, T-016 ; T-012 ─► T-017
T-015,T-016,T-017 ─► T-018
(all backend) ─► T-019 ; (all frontend) ─► T-020 ; all ─► T-021 ─CP3
```

## 9. Known gaps (seed for the ledger — T-021)
- **S7 ingest content-scanning** (untrusted upload → injection via retrieved chunk) — deferred
  (Tighten); POC uses synthetic trusted Sources.
- **DOCX extraction** — deferred; txt/md/pdf only.
- **Durable ingest worker** — background task for POC; a pod restart mid-ingest leaves a source
  `indexing` (recover via Reprocess). Later hardening.
- **Multi-KB per agent** — one binding per agent in POC.
- **Orphan blob GC** — deleting a KB/source cascades DB rows but leaves MinIO blobs; GC deferred.
- **Signed pod↔registry service token** — internal endpoint trusts the cluster boundary + the
  env-sourced `X-Agent-Team` header (isolation still re-enforced fail-closed in the store).
- **External embedding providers** (Voyage/OpenAI) — deferred; local sidecar default.
- **pgvector absent (dev)** — retrieval degrades to keyword; surfaced, not silent.

# POC-4 — Team Knowledge Base / RAG — Executable Tasks

**Branch:** `worktree-ux-preview-context-storage` (commit here ONLY — never merge to main).
**Spec:** `docs/design/context-storage-poc-4-knowledge.md` · **Plan:** `./plan.md` · **Research:** `./research.md`
**Contracts:** `./contracts/{endpoints.md,ports.md,knowledge-search-tool.md}` · **Data model:** `./data-model.md`

> **Alignment Check:** every task serves one of — (a) make retrieval real (embed/index/search over
> pgvector+MinIO), (b) make tenant isolation structural (mandatory `(team, kb_id)` predicate,
> server-side binding), (c) close the citation loop into the POC-2b `AttributedBubble.citations`
> slot. No task degrades the S5 isolation goal to make something compile.

## Conventions

- **[P]** = parallel-safe (files disjoint from its sibling tasks AND its deps are met). No shared file.
- Each task lists **Files (≤3)**, **Acceptance** (one line), **Deps**, **Verify** (an executable command).
- **Checkpoints** `CP-0…CP-3` are mandatory gates with executable scripts — do not proceed past a
  red checkpoint. Their heavy deploy steps (`scripts/deploy-eks.sh`) are **user-gated**.
- Shared constant **`EMBEDDING_DIM = 384`** (`bge-small-en-v1.5`) appears in exactly three places
  (embedding-sidecar, migration `0067` `vector(384)`, `PgVectorStore`) and must never drift.
- Image bumps (T-BUMP-*): **registry-api `0.2.194→0.2.195`**, **studio `0.1.145→0.1.146`**,
  **NEW embedding-sidecar `0.1.0`**. **declarative-runner is NOT bumped** (F-4 — HTTP tools + the
  existing `tool_call_end` SSE carry POC-4; no runner/SDK change).

---

## Phase 0 — Prerequisites

### T-001 — Prereq verification script  [P]
- Files: `scripts/plan-poc4/verify-prereqs.sh`
- Acceptance: `kubectl exec` into the postgres pod asserts `SELECT extname FROM pg_extension WHERE
  extname='vector'` present AND `SELECT 1 FROM pg_available_extensions WHERE name='vector'`; probes
  the MinIO Service resolves + a bucket `head`/list via an in-cluster boto3/`mc` one-liner; prints
  `pgvector: PRESENT` + `minio: REACHABLE` and exits 0, else non-zero with a remediation line.
- Deps: none.
- Verify: `bash -n scripts/plan-poc4/verify-prereqs.sh`

### [CP-0] Prerequisite gate
- Files: (runs T-001)
- Acceptance: `bash scripts/plan-poc4/verify-prereqs.sh` green on the deployed EKS cluster.
  If pgvector is **absent**, STOP and escalate — retrieval can't be semantic; do NOT silently drop
  to keyword-only on EKS. Research.md confirms it is present, so CP-0 passes on EKS.
- Deps: T-001.
- Verify: `bash scripts/plan-poc4/verify-prereqs.sh`

---

## Phase 1 — Embedding sidecar (NEW service `services/embedding-sidecar`)

### T-002 — embedding-sidecar service (app + Dockerfile + deps)
- Files: `services/embedding-sidecar/main.py`, `services/embedding-sidecar/Dockerfile`,
  `services/embedding-sidecar/requirements.txt`
- Acceptance: `POST /embed {"texts":["hello"]}` → `{embeddings:[[…384 floats]], dim:384,
  model:"bge-small-en-v1.5"}`; `GET /ready` → 200 once the ONNX model loads; model is a
  module-level `fastembed.TextEmbedding("BAAI/bge-small-en-v1.5")` singleton (loaded once, not per
  request). Dockerfile runs one dummy embed at build so weights bake into the image (no runtime
  egress). `requirements.txt` = `fastapi`, `uvicorn`, `fastembed`.
- Deps: none.
- Verify: `python3 -c "import ast; ast.parse(open('services/embedding-sidecar/main.py').read())"`
  then `docker build services/embedding-sidecar/`

### T-003 — sidecar chart template + values wiring  [P]
- Files: `charts/agentshield/templates/embedding-sidecar.yaml`, `charts/agentshield/values.yaml`,
  `charts/agentshield/values-eks.yaml`
- Acceptance: Deployment (1 replica, `requests: cpu 250m / mem 512Mi`, readiness probe `/ready`) +
  ClusterIP Service `agentshield-embedding-sidecar:8000`; `values.yaml`
  `embeddingSidecar.enabled: true` + tag `0.1.0`; `values-eks.yaml` sidecar ECR repo string.
  `helm template` renders both; no other component changes.
- Deps: T-002.
- Verify: `helm template charts/agentshield -f charts/agentshield/values-eks.yaml | grep -c embedding-sidecar`

### [CP-1] Sidecar deployed + /embed smoke
- Files: (deploy sidecar; scoped `kubectl apply` or `bash scripts/deploy-eks.sh` build step — user-gated)
- Acceptance: sidecar `/ready` returns 200, and
  `kubectl exec deploy/…registry-api -- python -c "import httpx;print(len(httpx.post('http://agentshield-embedding-sidecar.agentshield-platform.svc.cluster.local:8000/embed',json={'texts':['x']}).json()['embeddings'][0]))"`
  prints `384`.
- Deps: T-002, T-003, [CP-0].
- Verify: (command above prints `384`)

---

## Phase 2 — Data model

### T-004 — migration 0067 (4 tables + guarded vector col/index)  [P]
- Files: `services/registry-api/alembic/versions/0067_knowledge_base_rag.py`
- Acceptance: creates `knowledge_bases`, `knowledge_sources` (status CHECK), `knowledge_chunks`
  (WITHOUT the embedding column in `create_table`), `agent_knowledge_bindings`;
  `ix_knowledge_chunks_team_kb (team, kb_id)` **always** created; `vector(384)` column + `hnsw
  (embedding vector_cosine_ops)` index added only when `_pgvector_available(conn)` (mirror 0022);
  idempotent (`IF [NOT] EXISTS`), `down_revision="0065"`, `downgrade()` drops in reverse.
- Deps: none.
- Verify: `python3 -c "import ast; ast.parse(open('services/registry-api/alembic/versions/0067_knowledge_base_rag.py').read())"`
  and `grep -n 'down_revision' services/registry-api/alembic/versions/0067_knowledge_base_rag.py`

### T-005 — ORM models
- Files: `services/registry-api/models.py`
- Acceptance: `KnowledgeBase`, `KnowledgeSource`, `KnowledgeChunk` (declares
  `content,team,kb_id,source_id,chunk_index` but **NOT** `embedding` — vector is raw-SQL only),
  `AgentKnowledgeBinding`; `KnowledgeBase.sources`/`KnowledgeSource.chunks` cascade-delete
  relationships; no relationship onto `Agent`. Mappers configure clean on a stock DB.
- Deps: T-004.
- Verify: `cd services/registry-api && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`

---

## Phase 3 — Ports (behind `store_factory`, mirroring POC-0 `ConversationStore`)

### T-006 — BlobStore (MinIO)  [P]
- Files: `services/registry-api/blob_store.py`
- Acceptance: `BlobStore` Protocol + `MinioBlobStore` (boto3, path-style, region `us-east-1`,
  bucket-create-on-first-`put` via `head_bucket`→`create_bucket` on 404, blocking calls wrapped in
  `run_in_executor`); `put`→`get` round-trips bytes; missing key → `KeyError`. Env per
  contracts/ports.md (`BLOB_STORE_ENDPOINT`/`BUCKET`/`ACCESS_KEY`/`SECRET_KEY`).
- Deps: none.
- Verify: `python3 -c "import ast; ast.parse(open('services/registry-api/blob_store.py').read())"`

### T-007 — VectorStore (pgvector, S5 enforcement point)  [P]
- Files: `services/registry-api/vector_store.py`
- Acceptance: `VectorStore` Protocol + `PgVectorStore.index/search` with **required keyword**
  `(team, kb_id)`; empty/None `team` or `kb_id` → `ValueError` **before** SQL (fail-closed); search
  SQL carries `WHERE team=:team AND kb_id=:kb_id` with no "search all" path; validates
  `len(embedding)==EMBEDDING_DIM`; keyword `ILIKE` fallback (still team+kb scoped, `score=0.0`) when
  the embedding column is absent. `search(team=A, kb_id=<B's kb>)` → `[]`.
- Deps: T-004.
- Verify: `python3 -c "import ast; ast.parse(open('services/registry-api/vector_store.py').read())"`
  and `grep -n "def search" services/registry-api/vector_store.py` (confirm `team`/`kb_id` required)

### T-008 — embedding client + store_factory choke points
- Files: `services/registry-api/embedding_client.py`, `services/registry-api/store_factory.py`
- Acceptance: `embedding_client.embed(texts)` POSTs `EMBEDDING_SIDECAR_URL/embed`, order-preserving,
  raises `RuntimeError` on non-200 or dim mismatch (fail-loud); `EMBEDDING_DIM=384` defined here;
  `store_factory.get_blob_store()` returns a cached `MinioBlobStore`, `get_vector_store()` a
  `PgVectorStore`; unknown `BLOB_STORE`/`VECTOR_STORE` env → `ValueError`.
- Deps: T-006, T-007.
- Verify: `cd services/registry-api && python3 -c "import ast; ast.parse(open('embedding_client.py').read()); ast.parse(open('store_factory.py').read())"`
  and `grep -n "get_blob_store\|get_vector_store" store_factory.py`

---

## Phase 4 — Ingest + public API

### T-009 — schemas  [P]
- Files: `services/registry-api/schemas.py`
- Acceptance: adds `KnowledgeBaseCreate`, `KnowledgeBaseResponse`, `SourceResponse`,
  `ChunkResponse`, `SearchRequest`/`SearchResponse` (+`KBHit`), `BindingResponse` with the exact
  field lists in contracts/endpoints.md; `status` is `Literal["pending","indexing","ready","failed"]`.
- Deps: none.
- Verify: `cd services/registry-api && python3 -c "import schemas; print('ok')"`

### T-010 — ingest pipeline
- Files: `services/registry-api/ingest.py`, `services/registry-api/requirements.txt`
- Acceptance: `ingest_source(source_id)` opens its own `AsyncSessionLocal`, flips
  `pending→indexing`, `BlobStore.get`→`extract_text` (txt/md native, pdf via `pypdf`)→`chunk_text`
  (size 1000 / overlap 150)→`embed(chunks)`→`VectorStore.index`→ sets `chunk_count`+`status=ready`;
  any exception → `status='failed'`, `error=str(exc)` (fail-loud, logged), task ends cleanly.
  `pypdf>=4.0` added to requirements. A 3-paragraph .txt yields ≥1 chunk (each a 384-vector row);
  a corrupt file → `failed` with non-null error.
- Deps: T-005, T-008.
- Verify: `python3 -c "import ast; ast.parse(open('services/registry-api/ingest.py').read())"`
  and `grep -n "VectorStore\|embed\|BlobStore" services/registry-api/ingest.py`

### T-011 — public knowledge router + register + smoke script
- Files: `services/registry-api/routers/knowledge.py`, `services/registry-api/main.py`,
  `scripts/plan-poc4/smoke-knowledge.sh`
- Acceptance: every public endpoint in contracts/endpoints.md (KB CRUD; source upload multipart +
  `BackgroundTasks`; list/chunks/reprocess/delete; `POST …/search` test-retrieval; binding
  PUT/GET/DELETE), all team-scoped via `require_user`/`/me`; binding PUT also ensures the
  `knowledge_search` `agent_tools` row (idempotent). Router registered in `main.py`
  (`include_router`). `smoke-knowledge.sh` drives create→upload→poll `ready`→chunks→search→bind.
- Deps: T-008, T-009, T-010.
- Verify: `python3 -c "import ast; ast.parse(open('services/registry-api/routers/knowledge.py').read())"`;
  `cd services/registry-api && python3 -c "import main, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`;
  `grep -n "knowledge" services/registry-api/main.py`; `bash -n scripts/plan-poc4/smoke-knowledge.sh`

### [CP-2] Backend vertical slice + tenant isolation
- Files: (deploy registry-api 0.2.195 + sidecar — user-gated; runs `smoke-knowledge.sh`)
- Acceptance: `bash scripts/plan-poc4/smoke-knowledge.sh` green (upload→ready→chunks→test-retrieval),
  **AND** a two-team isolation probe: team A `search` on a KB seeded under team B returns `[]` at
  BOTH `POST /knowledge-bases/{kb}/search` (API) AND `VectorStore.search` (store), fail-closed. This
  is the vertical-slice proof before any UI work.
- Deps: T-004…T-011, [CP-1].
- Verify: `bash scripts/plan-poc4/smoke-knowledge.sh`

---

## Phase 5 — Tool + internal endpoint (the HTTP-tool decision, F-1)

### T-012 — internal `knowledge/search` endpoint
- Files: `services/registry-api/routers/internal.py`
- Acceptance: `POST /api/v1/internal/knowledge/search` (cluster-internal): reads `X-Agent-Team` /
  `X-Agent-Name` (missing → 422, never default the team); resolves `kb_id` **server-side** from
  `agent_knowledge_bindings` by `(agent_name, team)` (never from body/model); `embed(query)` →
  `VectorStore.search(team, kb_id, …)` → returns `KnowledgeSearchResult{chunks, citations}`; an
  unbound agent or a mismatched team header → `{chunks:[],citations:[]}` (fail-closed, no widening).
- Deps: T-008, T-011.
- Verify: `python3 -c "import ast; ast.parse(open('services/registry-api/routers/internal.py').read())"`
  and `grep -n "knowledge/search" services/registry-api/routers/internal.py`

### T-013 — seed `knowledge_search` as an HTTP-type platform tool  [P]
- Files: `scripts/seed-defaults.sh`
- Acceptance: idempotent `POST /api/v1/tools/` with the exact body in
  contracts/knowledge-search-tool.md — **`type: "http"`** (NOT python), `http_url` = the internal
  endpoint, headers `X-Agent-Team: {{AGENTSHIELD_AGENT_TEAM}}` / `X-Agent-Name: {{AGENT_NAME}}`
  (server-side env substitution — model can't set them), body `{"query":"{{query}}","k":5}`,
  `input_schema` exposing only `query`. `GET /api/v1/tools?name=knowledge_search` → one HTTP tool.
- Deps: none (useful only after T-012).
- Verify: `bash -n scripts/seed-defaults.sh` and `grep -n 'knowledge_search' scripts/seed-defaults.sh`

---

## Phase 6 — Frontend

### T-014 — knowledgeApi typed client  [P]
- Files: `studio/src/api/knowledgeApi.ts`
- Acceptance: the typed client in plan §6 (`listKBs`/`createKB`/`getKB`/`uploadSource`/`listSources`/
  `getChunks`/`reprocessSource`/`deleteSource`/`testRetrieval`/`listBoundAgents`/`bindAgent`/
  `unbindAgent`, interfaces `KnowledgeBase`/`KBSource`/`KBChunk`/`KBHit`) on the shared `http` axios
  instance; multipart upload uses `FormData`.
- Deps: none (types mirror T-009).
- Verify: `cd studio && npm run typecheck`

### T-015 — real Knowledge list page + routes
- Files: `studio/src/pages/KnowledgeBasesPage.tsx`, `studio/src/App.tsx`
- Acceptance: lift `pages/preview/KnowledgeBasesPage.tsx` markup; replace `MOCK_KBS` with
  `useQuery(listKBs)`; New-KB modal → `createKB` + query invalidate (new KB appears after refetch —
  save→reload); `App.tsx` route imports point at `pages/` not `pages/preview/`.
- Deps: T-014.
- Verify: `cd studio && npm run typecheck` and `grep -n "KnowledgeBasesPage" studio/src/App.tsx`

### T-016 — real Knowledge detail page  [P]
- Files: `studio/src/pages/KnowledgeBaseDetailPage.tsx`
- Acceptance: lift the preview detail; Sources tab → `uploadSource` (real file input) + `listSources`
  with `refetchInterval` while any source is `pending|indexing` + chunk drawer via `getChunks` +
  `reprocessSource`/`deleteSource`; Retrieval tab → `testRetrieval`; Settings tab → PATCH; **Attach
  agent picker** → `listBoundAgents`/`bindAgent`/`unbindAgent`; display map `pending→"Queued"`,
  `indexing→"Processing"` (F-6). Upload → status advances to Ready → chunks render → retrieval ranks
  → attached agent shows in "Attached to".
- Deps: T-014.
- Verify: `cd studio && npm run typecheck`

### T-017 — runtime citation wiring (fill the POC-2b slot)
- Files: `studio/src/lib/chatStream.ts`, `studio/src/components/playground/ChatPane.tsx`,
  `studio/src/pages/AgentChatPage.tsx`
- Acceptance: in the `tool_call_end` handler, when `tool === "knowledge_search"`,
  `JSON.parse(result)` → read `citations: {source,kb}[]` → attach to the current assistant message
  (`M.citations`, mirroring `attachToolCall`); pass `citations={m.citations}` to `AttributedBubble`
  on BOTH the playground (`ChatPane`) and deployed-agent (`AgentChatPage`) surfaces. After a turn
  where the agent calls `knowledge_search`, a `{source · kb}` chip renders. **No SDK/runner change** (F-4).
- Deps: T-012.
- Verify: `cd studio && npm run typecheck` and `grep -n "citations" studio/src/components/playground/ChatPane.tsx studio/src/pages/AgentChatPage.tsx`

### T-018 — Vitest (Knowledge pages + citation render)
- Files: `studio/src/pages/KnowledgeBasesPage.test.tsx`,
  `studio/src/pages/KnowledgeBaseDetailPage.test.tsx`,
  `studio/src/components/chat/AttributedBubble.test.tsx`
- Acceptance: `renderWithProviders` + `vi.mock('../api/knowledgeApi')`; assert list/empty/create,
  upload+status-poll+chunk-viewer+test-retrieval, and that `AttributedBubble` renders a chip when
  `citations` is non-empty and none when empty. `npm run test` green.
- Deps: T-015, T-016, T-017.
- Verify: `cd studio && npm run test`

---

## Phase 7 — E2E, image bumps, docs

### T-019 — suite-77 backend e2e + register
- Files: `scripts/e2e/suite-77-knowledge-rag.sh`, `scripts/e2e/run-all.sh`
- Acceptance: kubectl-exec/curl suite, registered + executable in `run-all.sh`. Cases:
  `T-S77-001` create KB + upload synthetic .txt (known fact) → poll `ready`, `chunk_count>0`;
  `T-S77-002` **save→reload**: re-read `sources`+`chunks` from backend, assert survived;
  `T-S77-003` test-retrieval returns the chunk with the known fact (top hit);
  `T-S77-004` seed+attach `knowledge_search`, run the agent, answer includes the fact AND the
  tool-result carries a `{source,kb}` citation;
  `T-S77-005` **tenant isolation (headline)**: team-B KB+chunk; team-A `/knowledge-bases/{kb}/search`
  → `[]`; direct `/internal/knowledge/search` with `X-Agent-Team: A` against B's binding → `[]`;
  asserted at store AND API, fail-closed.
- Deps: T-004…T-013.
- Verify: `bash -n scripts/e2e/suite-77-knowledge-rag.sh` and `grep -n "77" scripts/e2e/run-all.sh`

### T-020 — Playwright journey
- Files: `studio/e2e/knowledge.spec.ts`
- Acceptance: real Keycloak login (global-setup) → navigate `/knowledge` → create KB → upload a
  fixture file (`setInputFiles`) → poll UI to Ready + `waitForResponse` on sources/chunks calls →
  test-retrieval → attach an agent → open chat, send a question, assert a citation chip renders
  (`getByText(source)`). Assert wiring+persistence+network, not agent completion (no-pod tolerant,
  same boundary as bash suites).
- Deps: T-015, T-016, T-017.
- Verify: `bash scripts/studio-e2e.sh`

### T-BUMP-1 — deploy-script image bumps + sidecar build entry  [P]
- Files: `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`
- Acceptance: `REGISTRY_API_TAG 0.2.194→0.2.195`, `STUDIO_TAG 0.1.145→0.1.146`, add
  `EMBEDDING_SIDECAR_TAG=0.1.0` + a build entry that builds `services/embedding-sidecar/` (both
  scripts; deploy-eks pushes to ECR); **declarative-runner tag unchanged** — note the reason (F-4)
  in the header comment.
- Deps: T-002 (image exists to build/tag).
- Verify: `grep -n "0.2.195\|0.1.146\|EMBEDDING_SIDECAR_TAG" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh`

### T-BUMP-2 — mirror tags in chart values  [P]
- Files: `charts/agentshield/values.yaml`, `charts/agentshield/values-eks.yaml`
- Acceptance: `values.yaml` registry-api `0.2.195` + studio `0.1.146` + `embeddingSidecar` tag
  `0.1.0`; `values-eks.yaml` mirrors the same tags + sidecar ECR repo. declarative-runner tag
  unchanged. (Bumping only deploy-cpe2e.sh leaves the chart on the old tag — mirror both.)
- Deps: T-003.
- Verify: `grep -n "0.2.195\|0.1.146\|embedding" charts/agentshield/values.yaml charts/agentshield/values-eks.yaml`

### T-DOCS — experience doc + gap ledger  [P]
- Files: `docs/experience/playground.md`, `docs/testing/manual-ui-e2e-test-plan.md`
- Acceptance: `playground.md` documents the new citation chip + the Knowledge page flow (upload →
  status → test-retrieval → attach → cited chat). Gap-ledger entries added: S7 ingest content-scan,
  DOCX extraction, durable ingest worker, multi-KB per agent, orphan-blob GC, signed pod↔registry
  service token, external embedding providers, pgvector-absent keyword degrade — each tagged
  deferred (intentional).
- Deps: T-011, T-017 (behavior finalized).
- Verify: `grep -n "citation\|Knowledge" docs/experience/playground.md` and `grep -n "knowledge\|citation" docs/testing/manual-ui-e2e-test-plan.md`

### T-ORPHAN — no-orphan wiring gate (per new symbol)
- Files: (verification gate — no code files)
- Acceptance: every new symbol greps to a **live caller/reader** in this change:
  - `BlobStore` / `MinioBlobStore` → called by the source-upload handler (T-011) + ingest (T-010).
    `grep -rn "get_blob_store\|BlobStore" services/registry-api/{routers/knowledge.py,ingest.py}`
  - `VectorStore` / `PgVectorStore` → `index` from ingest (T-010), `search` from
    test-retrieval (T-011) + internal endpoint (T-012).
    `grep -rn "get_vector_store\|VectorStore" services/registry-api/{ingest.py,routers/knowledge.py,routers/internal.py}`
  - `embed` / embedding client → ingest (T-010) + internal search (T-012).
    `grep -rn "embed(" services/registry-api/{ingest.py,routers/internal.py}`
  - `ingest_source` → scheduled by the upload handler's `BackgroundTasks` (T-011).
    `grep -rn "ingest_source" services/registry-api/routers/knowledge.py`
  - `knowledge_search` tool → seeded (T-013) + endpoint (T-012).
    `grep -rn "knowledge_search" scripts/seed-defaults.sh` and `grep -rn "knowledge/search" services/registry-api/routers/internal.py`
  - `agent_knowledge_bindings` → written by binding PUT (T-011), read by internal endpoint (T-012).
    `grep -rn "agent_knowledge_bindings" services/registry-api`
  - citations wiring → `chatStream.ts` sets `M.citations`, `ChatPane`/`AgentChatPage` pass it to
    `AttributedBubble` (T-017). `grep -rn "citations" studio/src`
- Deps: all backend + frontend tasks (T-004…T-018).
- Verify: run each grep above — every one returns ≥1 caller line (no orphan).

### [CP-3] Final gate — full deploy + suites
- Files: (user-gated `bash scripts/deploy-eks.sh` — builds/deploys registry-api 0.2.195,
  studio 0.1.146, embedding-sidecar 0.1.0)
- Acceptance: after deploy — `bash scripts/e2e/suite-77-knowledge-rag.sh` green (incl. T-S77-005
  tenant isolation), `bash scripts/studio-e2e.sh` green, `cd studio && npm run test && npm run
  typecheck` green, T-ORPHAN clean. Write the Definition-of-Done statement (which Playwright step
  proves the journey, what the reload test asserts, no orphan). Only then is POC-4 done.
- Deps: T-014…T-020, T-BUMP-1, T-BUMP-2, T-DOCS, T-ORPHAN.
- Verify: `bash scripts/e2e/suite-77-knowledge-rag.sh && bash scripts/studio-e2e.sh && cd studio && npm run test && npm run typecheck`

---

## Dependency graph (no forward refs)

```
T-001 ─► [CP-0]
T-002 ─► T-003 ─► [CP-1]
T-004 ─► T-005
T-004 ─► T-007
T-006, T-007 ─► T-008 ─► T-010 ─► T-011 ─► [CP-2]
T-009 ─► T-011
T-011 ─► T-012 ;  T-013 (independent seed)
T-014 ─► T-015, T-016 ;  T-012 ─► T-017
T-015, T-016, T-017 ─► T-018
(all backend T-004…T-013) ─► T-019
(all frontend T-015…T-017) ─► T-020
T-002 ─► T-BUMP-1 ;  T-003 ─► T-BUMP-2 ;  T-011,T-017 ─► T-DOCS
(all) ─► T-ORPHAN ─► [CP-3]
```

## MVP critical path (retrieval + isolation + citation spine)

`T-004 → T-007 → T-008 → T-010 → T-011 → [CP-2] → T-012 → T-017 → T-018/T-019/T-020 → T-BUMP-* → T-ORPHAN → [CP-3]`
(sidecar spur `T-002 → T-003 → [CP-1]` and `T-013` seed feed in before CP-2/CP-3.)

## Parallel batches ([P] = disjoint files, deps met)

- Batch A (kickoff): **T-001**, **T-002**, **T-004** — no shared files.
- Batch B (after T-004): **T-006**, **T-007** — disjoint; **T-009** also [P].
- Batch C (frontend): **T-014** then **T-016** [P] alongside T-015; **T-013** [P] anytime after seed infra.
- Batch D (wrap): **T-BUMP-1**, **T-BUMP-2**, **T-DOCS** — disjoint files.

## Known gaps (seed the T-DOCS ledger)

S7 ingest content-scanning (deferred/Tighten) · DOCX extraction (txt/md/pdf only) · durable ingest
worker (BackgroundTasks for POC; Reprocess recovers a stuck `indexing`) · multi-KB per agent (one
binding in POC) · orphan-blob GC (DB cascades, MinIO blobs linger) · signed pod↔registry service
token (cluster boundary + fail-closed store re-check for now) · external embedding providers
(Voyage/OpenAI deferred) · pgvector absent on dev → keyword degrade (surfaced, not silent).

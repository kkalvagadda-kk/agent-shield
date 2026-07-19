# POC-4 Research — Prerequisites & Grounded Decisions

Source of truth: `docs/design/context-storage-poc-4-knowledge.md`. This file records
what was **verified against the running code/cluster config** (not the design doc's
intent) and locks the three flagged prerequisites. Every claim below was checked in
the worktree `worktree-ux-preview-context-storage` on 2026-07-16.

---

## Prerequisite 1 — pgvector on the EKS Postgres — **RESOLVED / PRESENT (not a blocker)**

**Finding.** The EKS deploy ships a **custom portable pgvector Postgres image**, so the
`vector` extension IS available and migration `0022`'s probe (`_pgvector_available` →
`SELECT 1 FROM pg_available_extensions WHERE name='vector'`) returns true on this cluster.

Evidence:
- `scripts/deploy-eks.sh:76` → `PGVECTOR_TAG="17.6.0-portable"`; build entry at
  `scripts/deploy-eks.sh:146` builds `services/postgresql-pgvector/`.
- `charts/agentshield/values-eks.yaml:52-55` → `postgresql.image.repository:
  agentshield/postgresql-pgvector`, `tag: 17.6.0-portable`.
- `scripts/deploy-eks.sh:19-24` header documents *why* it's a custom image: Bitnami's
  stock pgvector 0.8.0 emits AVX-512 and SIGILLs on nodes without it; `services/
  postgresql-pgvector/Dockerfile` rebuilds pgvector with `OPTFLAGS=""` (portable).
- Memory note "Migration 0022 taken (pgvector)" corroborates.

**Disposition.** pgvector is **present on EKS**. POC-4's `knowledge_chunks.embedding
vector(384)` column and its ANN index WILL be created by migration `0067`. We keep the
**same defensive guard pattern as 0022** (probe before `CREATE EXTENSION`/adding the
vector column) so the migration still applies cleanly on a stock-Postgres dev box —
there it degrades to keyword search, surfaced not silent (gap ledger). **Task T-001 makes
this executable**: a prereq smoke script asserts `vector` is installed on the deployed DB
and fails the checkpoint loudly if not. Not a launch blocker for EKS.

**Note on dimension.** `agent_memory.content_embedding` is `vector(1536)` (0022, sized for
an OpenAI-style embedder that is never actually called — `routers/memory.py:172` uses a
`[0.0]*1536` placeholder). POC-4 uses a **new table**, so we size it to the model we
actually run: **`vector(384)`** for `bge-small-en-v1.5` (see Prerequisite 2). The two
tables are independent; no conflict.

---

## Prerequisite 2 — Embedding provider — **DECIDED: local ONNX sidecar (`services/embedding-sidecar`)**

**Constraint.** Anthropic has no embeddings endpoint (confirmed: `judge.py` calls
`/v1/messages` and Bedrock `invoke_model` for the LLM judge only — no embedding path
anywhere). Repo-wide grep for `embedding|sentence|voyage|openai-embed` finds **no existing
embedding provider** — `memory.py:268 search_memory` accepts a `query_embedding` but nobody
computes one (`routers/memory.py:172` hardcodes a zero vector). So POC-4 must introduce
the embedding capability itself.

**Decision — a new lightweight local sidecar `services/embedding-sidecar`**, an internal
HTTP service exposing `POST /embed`. This is the design's default (§2.2) and keeps the POC
self-contained: **no external credential, no egress**. Concretely:
- Model: **`BAAI/bge-small-en-v1.5`** (384-dim), served via **`fastembed`** (ONNX Runtime,
  CPU). Chosen over `sentence-transformers` because fastembed pulls **onnxruntime, not
  PyTorch** → image is ~hundreds of MB instead of ~2 GB, CPU-only, no GPU, faster cold
  start on the same AVX-limited nodes that forced the portable pgvector rebuild. Interface
  is identical to the design's "small sentence-transformers model"; only the runtime is
  lighter. The model weights are **baked into the image at build time** (a build step runs
  one dummy embed to populate the ONNX cache) so runtime has zero model-download egress.
- `EMBEDDING_DIM = 384` is the single shared constant across the sidecar, the migration's
  `vector(384)` column, and `PgVectorStore`. **They must never drift** — the migration and
  the port both reference the same 384.

**Alternatives (deferred, documented).** Voyage AI / an OpenAI-compatible embeddings
endpoint would need a stored credential + egress allow — deferred to the Tighten line
unless the local model proves inadequate for retrieval quality. Recorded in the gap ledger.

**Consumers.** Two callers embed, both server-side, both via one client (`embedding_client.py`
→ `EMBEDDING_SIDECAR_URL`): (a) the ingest pipeline (embeds each chunk), and (b) the
`knowledge_search` internal endpoint (embeds the query). Query and document use the **same
model** — required for cosine to be meaningful.

---

## Prerequisite 3 — MinIO bucket + `BlobStore` port — **RESOLVED / REAL CONFIG**

**Finding.** MinIO is a first-class deployed component on EKS, and boto3 (the S3 client) is
already a registry-api dependency.

Evidence:
- `scripts/deploy-eks.sh:75` `MINIO_CP1_TAG="0.1.0"`; build entry `:145` builds
  `services/minio-cp1/`; the deploy is listed in the component wait-loop (`:113`).
- `scripts/deploy-eks.sh:214-215` creates the `minio-credentials` secret
  (`root-user`/`root-password`) with `MINIO_USER` / `MINIO_PASS`
  (defaults `agentshield-admin` / `MinioPass2024`).
- `charts/agentshield/templates/minio-raw.yaml` + `charts/agentshield/values-eks.yaml:28`
  (`minioImage`) render the MinIO Deployment/Service; standard MinIO S3 port **9000**.
- `services/registry-api/requirements.txt:16` → `boto3>=1.35.0` already present.
- `judge.py:646-661` is the existing boto3 access pattern (Bedrock, but same
  `boto3.client(...)` idiom) — we mirror its "import boto3 lazily + explicit creds" style
  for the S3 client.

**Disposition.** `BlobStore` is backed by the **real deployed MinIO** over the S3 API.
Concrete config the impl binds to:
- Endpoint: `http://agentshield-minio.agentshield-platform.svc.cluster.local:9000`
  (in-cluster Service; exact Service name confirmed at build from
  `charts/agentshield/templates/minio-raw.yaml`), via env `BLOB_STORE_ENDPOINT`.
- Creds: mounted from the existing `minio-credentials` secret into registry-api env as
  `BLOB_STORE_ACCESS_KEY` / `BLOB_STORE_SECRET_KEY` (chart wiring in T-011/T-021).
- Bucket: **`knowledge-sources`** (new), created idempotently by the impl on first `put`
  (`head_bucket` → `create_bucket` on 404), so no manual bucket step. boto3 client uses
  `endpoint_url=…`, `region_name="us-east-1"`, path-style addressing (MinIO requirement).

**Not a blocker.** MinIO and boto3 both exist; POC-4 only adds a bucket + a thin port.

---

## Grounded architecture findings (drive the plan; deviations from the design doc noted)

### F-1 — `knowledge_search` is an **HTTP-type** platform tool, not a Python-type one (reasoned deviation)

The design §4.4 says "Platform tool (Python type)". **Verified reality forces HTTP type**,
and it satisfies every stated requirement better:

- **Python-type tools run sandboxed with only model args.** `sdk/agentshield_sdk/
  tool_executor.py::PythonToolExecutor` ships `python_code` to the `python-executor`
  microservice (`/execute`, a forked subprocess). That sandbox has **no DB access, no
  server env, no team context** — its `args` come straight from the model. So a Python-type
  `knowledge_search` could ONLY receive `(team, kb_id)` as **model-supplied args** — exactly
  the S5 tenant-widening hole the design forbids. It also can't reach pgvector.
- **HTTP-type tools already have the server-side binding channel.** `HttpToolExecutor`
  (`tool_executor.py:135-165`) substitutes **request headers from `os.environ`**
  (`_substitute_vars(v, dict(os.environ))`). Agent pods are stamped with `AGENTSHIELD_AGENT_TEAM`
  and `AGENT_NAME` env by the deploy-controller (`services/deploy-controller/
  manifest_builder.py:167,170`). So a header like `X-Agent-Team: {{AGENTSHIELD_AGENT_TEAM}}`
  resolves to the pod's **real team from server-side env — the model cannot set it.** This
  is the *exact* pattern `web_search` uses for its `{{serper_api_key}}` header
  (`scripts/seed-defaults.sh:67`) and `http_echo` for its in-cluster URL (`:91`).
- **Governance is identical either way.** OPA + HITL wrap every tool in
  `graph_builder.governed_tool` regardless of type, so "governed platform tool" (design §3.2)
  holds for HTTP tools too.

**Decision (No-Bandaid, per CLAUDE.md):** `knowledge_search` is an **HTTP tool** pointing at a
new **cluster-internal** registry-api endpoint `POST /api/v1/internal/knowledge/search`, with:
- body `{"query": "{{query}}"}` — `query` is the only model-controlled input;
- header `X-Agent-Team: {{AGENTSHIELD_AGENT_TEAM}}` (server-side env, unspoofable by prompt);
- header `X-Agent-Name: {{AGENT_NAME}}` (server-side env).

The endpoint resolves `kb_id` **server-side** from `agent_knowledge_bindings` keyed by
`(agent_name, team)` — so `kb_id` is never on the wire from the model either. This honors the
design's intent (governed tool, server-side `(team, kb_id)` binding, returns chunks + refs,
fills the citation slot) while being architecturally correct. Deviation from "(Python type)"
is deliberate and recorded here.

### F-2 — Trust boundary for the internal endpoint

`routers/internal.py` is **cluster-internal only (no public ingress)** — the existing trust
model (scheduler / event-gateway call it over ClusterIP). `knowledge_search`'s internal
endpoint joins the SAME boundary. Within the cluster, the `X-Agent-Team` header is trusted
exactly as `/internal/runs/start`'s body is. **Tenant isolation does not rely on that trust
alone** — it is enforced a second time, fail-closed, in `PgVectorStore.search`, whose
`(team, kb_id)` predicate is a **required positional arg** with no "search all" path. A signed
service token between pod and registry-api is a Tighten hardening (gap ledger).

### F-3 — The agent→KB binding needs a home; add `agent_knowledge_bindings`

`agent_tools` (the agent↔tool join, `models.py:1099`) has **no config column**, so the KB
binding can't hang off it. Add a small table `agent_knowledge_bindings(agent_id, kb_id,
team)` written by the "attach knowledge_search" picker and read server-side by the internal
endpoint. POC scope: **one KB per agent** (multi-KB fan-out deferred → gap ledger). Because
the binding is resolved from the DB per call, changing it needs **no agent redeploy** (unlike
stuffing `kb_id` into pod env, which would).

### F-4 — Runtime citations are **frontend-only wiring** (no runner/SDK bump)

The SSE `tool_call_end` event **already carries the full tool `result`**
(`sdk/agentshield_sdk/streaming.py:118-138` — `result = output.content / json.dumps(output)`).
So when `knowledge_search` returns JSON `{"chunks":[…], "citations":[{"source","kb"}]}`, that
payload reaches the browser through the existing stream. Citation wiring is therefore:
- `knowledge_search` internal endpoint returns a `citations: [{source, kb}]` array;
- the frontend SSE consumer (`studio/src/components/playground/ChatPane.tsx` +
  `studio/src/lib/chatStream.ts`, which already handle `tool_call_start/end` and populate
  `AttributedBubble.toolCalls`) parses the `knowledge_search` `tool_call_end.result`,
  extracts `citations`, and attaches them to the current assistant bubble's `citations`;
- `AttributedBubble` **already renders `citations: {source, kb}[]`** with a chip row below
  the content (`studio/src/components/chat/AttributedBubble.tsx:42,106-118`) — the POC-2b
  slot. We only need to FEED it.

**Consequence:** `declarative-runner` and the SDK need **no code change and no image bump**
for POC-4 (contrary to the task-prompt's tentative "declarative-runner 0.1.57 if it
composes"). HTTP tools already execute through the existing SDK path, and citations flow via
the existing `tool_call_end`. Only **registry-api**, **studio**, and the **new embedding-sidecar**
image change. Recorded so the cold implementer does not needlessly bump the runner.

### F-5 — Source ingest runs as a FastAPI **background task** (justified for POC volume)

Design §3.4/§4.3 permits inline or background. Decision: **`fastapi.BackgroundTasks`** kicked
from the upload handler. Upload returns `201` immediately with `status="pending"`; the
background task flips `indexing → ready|failed`; the UI polls `GET …/sources`. Rationale:
synthetic POC Sources are small (a handful of pages), so a durable worker/queue is
over-engineering; but doing it *inline* would block the HTTP upload for the embed round-trip.
Background task = non-blocking upload + a real status lifecycle the UI can show, with the
whole path in one well-instrumented function. A durable ingest worker is the later hardening
(gap ledger). If the registry-api pod restarts mid-ingest, a Source can be stuck `indexing`;
the UI exposes a **Reprocess** action (re-runs ingest) to recover — no data loss (blob is
already in MinIO).

### F-6 — Status enum reconciliation

Design §4.1 DB enum: `pending | indexing | ready | failed`. The preview mock
(`studio/src/demo/mockData.ts:11`) used `queued | processing | ready | failed`. **DB is
canonical** (`pending|indexing|ready|failed`); the real detail page maps `pending→"Queued"`,
`indexing→"Processing"` for display continuity with the mock's look. Documented so names
stay consistent across tasks.

---

## Verified fact table (names/paths/versions the tasks reuse verbatim)

| Fact | Value | Source |
|---|---|---|
| Latest migration | `0066_drop_llm_provider_check.py` → **new = `0067`** | `alembic/versions/` |
| pgvector on EKS | **present** (`postgresql-pgvector:17.6.0-portable`) | deploy-eks.sh:76, values-eks.yaml:54 |
| MinIO on EKS | **deployed**, port 9000, secret `minio-credentials` | deploy-eks.sh:145,214 |
| boto3 available | `boto3>=1.35.0` | registry-api/requirements.txt:16 |
| httpx available | `httpx==0.27.*` | registry-api/requirements.txt:9 |
| Embedding dim | **384** (`bge-small-en-v1.5`) | this doc, Prereq 2 |
| Agent team env | `AGENTSHIELD_AGENT_TEAM` | manifest_builder.py:170 |
| Agent name env | `AGENT_NAME` | manifest_builder.py:167 |
| HTTP tool header src | `os.environ` substitution | tool_executor.py:145 |
| SSE tool result | `tool_call_end.result` carries full output | streaming.py:118-138 |
| Citations slot | `AttributedBubble` `citations:{source,kb}[]` (renders) | AttributedBubble.tsx:42,106 |
| Frontend SSE parser | `ChatPane.tsx` + `lib/chatStream.ts` | grep tool_call_end |
| registry-api tag now | `0.2.194` → **0.2.195** | deploy-cpe2e.sh:266 |
| studio tag now | `0.1.145` → **0.1.146** | deploy-cpe2e.sh:273 |
| embedding-sidecar tag | **new = `0.1.0`** | this doc |
| declarative-runner | **no bump** (F-4) | this doc |
| Latest e2e suite | suite-76 → **new = suite-77** | scripts/e2e/ |

---

## BLOCKERS needing a user decision

**None.** All three flagged prerequisites resolved in-repo: pgvector present, MinIO present,
embedding via a new self-contained local sidecar (no external credential/egress). The one
deviation from the design doc — `knowledge_search` as HTTP-type rather than Python-type
(F-1) — is a planner call made under the No-Bandaid rule and does not require user sign-off,
but is surfaced here so it is not a surprise at review.

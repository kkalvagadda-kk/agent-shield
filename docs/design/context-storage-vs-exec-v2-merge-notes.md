# Context Storage ↔ Execution-Models-v2 — Parallel-Work Merge Notes

**Purpose:** a shared reference for running the context-storage workstream (`docs/design/context-storage-architecture.md`) in parallel with the remaining execution-models-v2 work (`docs/plan/execution-models-v2/`). It maps the collision surface and names the two coordination decisions that remove almost all of the risk.

**Date:** 2026-07-15.

---

## TL;DR

Overhead is **low mechanical + one concentrated semantic cost**.

- Most of exec-v2 (**WS-0/1/2/3**) is **already merged to main** — context-storage branches on top of it, so it's not a parallel merge at all for those. The genuinely parallel (unbuilt) work is **WS-4, WS-5, WS-6, and Eval-v2 E-2…E-6**.
- The **majority of context-storage** (Knowledge Base, user profile, T1 conversation memory, all the UX) touches files **no exec-v2 plan touches** — clean lane.
- **One guaranteed mechanical conflict:** Alembic migration numbers (both grab 0063+). Trivial to fix, must be sequenced.
- **One real cost, and it's semantic not textual:** context-storage's shared-`thread_id` change lands in the exact `workflow_orchestrator.py` / `declarative-runner` / checkpointer machinery that **WS-1 (already merged) restructured** for durable resume. Reconciling the two designs — not merging two diffs — is where the effort is.

---

## Two coordination decisions (do these up front)

1. **Sequence the migrations.** Head is **0062**. Agree who takes `0063+` first; the other rebases `down_revision` + renumbers. Cheap if decided now, annoying if found at merge. (WS-2's own tasks.md already hit and documented this exact hazard.)
2. **Coordinate the `thread_id` change with the WS-1 durable-engine owner.** Context-storage Phase 1 gives all workflow members **one shared `thread_id`**, replacing the per-member `uuid4()` mint in `workflow_orchestrator.py::_dispatch`/`_run_step`. WS-1 made **PostgresSaver-keyed-by-`thread_id` the single checkpoint-of-record**, with per-member durable resume re-entering by that key. Confirm the shared-thread design composes with per-member durable resume **before** building Phase 1; ideally one reviewer owns both.

---

## Collision matrix (context-storage vs *unbuilt* exec-v2)

| Shared file | Context-storage change | Parallel exec-v2 change | Type | Severity |
|---|---|---|---|---|
| **Alembic `versions/`** | agent_memory alter, `user_profiles`, `knowledge_bases`/`_sources`/`_chunks`, pgvector (several, 0063+) | WS-4 `webhook_clients`; WS-5 `source_url`/`build_status`; Eval `tools.side_effecting` (also 0063+) | Mechanical | **Guaranteed** — renumber + re-chain. ~2 min each; sequence it. |
| **`models.py`** | agent_memory cols; `user_profiles`; `knowledge_*` models | webhook_clients; source_url/build_status; side_effecting | Mechanical | Low — different tables, adjacent additions, usually auto-merges |
| **`routers/playground.py`** | `thread_id = session_id` (small) | Eval-v2 heavy: `/eval/score`, `eval_mode` threading, per-mode branches | Mechanical | **Medium** — Eval rewrites the run-create region where `thread_id` is set |
| **`declarative-runner/main.py`** | `/chat/stream` memory, transcript load/inject, summarizer, write-back | Eval-v2 E-2: `eval_mode` ContextVar + `DurableRunRequest` field | Mechanical | Medium — same run entry, likely different regions |
| **`workflow_orchestrator.py`** | shared `thread_id` to members; transcript replaces string-passing | *(WS-0/1/2 already merged — not parallel)* | **Semantic** | **High** — must compose with WS-1's per-member durable resume (see decision 2) |
| **`declarative-runner/checkpoint.py`, `sdk/agentshield_sdk/durable.py`** | (shared-thread interaction) | *(WS-1 merged — not parallel)* | **Semantic** | **High** — WS-1 owns this; build on it, don't fight it |
| **`pages/CreateAgentPage.tsx`** | memory config field + knowledge attach picker | WS-5 Monaco code editor (replaces source_code stub) | Mechanical | Medium — same file, different regions |
| **`pages/AgentDetailPage.tsx`** | knowledge attach (SettingsContent) + memory config (SettingsTab) | WS-4 webhook client panel | Mechanical | Medium — same file, different tabs |
| **`pages/EvalResultsPage.tsx`** | shared-thread transcript view | Eval-v2 per-dimension score render | Mechanical | Medium — same file |
| **`api/registryApi.ts`** | knowledge CRUD, memory/session, profile calls | additive across WS-4/5/6 | Mechanical | Low — additive |
| **`App.tsx` / Sidebar / `components/layout/*`** | `/knowledge`, `/preferences` routes; "Knowledge" nav | WS-6 nav badge (`components/layout/*`) | Mechanical | Low — additive / different files |

---

## Where there is ZERO overlap (proceed fully parallel)

No exec-v2 plan touches any of these — grep-confirmed across the whole `docs/plan/execution-models-v2/` tree:

- `services/registry-api/memory.py`, the `agent_memory` table, `memory_enabled` semantics
- `DIRECT_DATABASE_URL` wiring, session/memory scoping, "context storage", "shared thread"
- The entire **Knowledge Base / RAG** subsystem (new models, MinIO/pgvector, `knowledge_search`, ingest)
- The **user profile** subsystem (`user_profiles`, Preferences page)
- The **storage-abstraction ports** (§4.1: `ConversationStore` / `BlobStore` / `VectorStore`) — all net-new files

`routers/chat.py` and `deploy-controller/manifest_builder.py` are only touched by **already-merged** WS-0/WS-2, so context-storage builds on top — no parallel branch there either.

---

## Recommended split of labor

- **Fully parallel, no coordination needed:** Knowledge Base (POC-4), user profile (POC-3), the storage-abstraction ports (§4.1), and all UX (POC-2/5). Ship these against main anytime.
- **Coordinate before starting:** Phase-1 shared workflow thread (the `thread_id`/orchestrator/checkpointer axis) — gate it on decision 2.
- **Sequence, don't parallelize, the migrations** — decision 1.

Net: with the two decisions made up front, ~80% of context-storage runs with zero merge cost; the remaining ~20% is one design-coherence conversation plus a mechanical migration rebase.

---

## Related

- `docs/design/context-storage-architecture.md` — the context-storage design (§4.1 storage abstraction; §5 shared thread; §11 phasing).
- `docs/plan/execution-models-v2/` — WS-0…WS-6 + Eval-v2 (WS-0/1/2/3 done; WS-4/5/6 + Eval E-2…E-6 planned).

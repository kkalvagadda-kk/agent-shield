# AgentShield — Context-Storage UX Roadmap (POC-2 → POC-5)

**Status**: DRAFT v1 — 2026-07-16. Sequenced UX build plan. Not yet implemented.
**Author**: Karthik + Claude
**Companion to**: [`context-storage-architecture.md`](./context-storage-architecture.md) — that doc is the authoritative subsystem spec (data model, ports, security §7, phasing §11). **This doc is the execution roadmap** for the user-facing phases POC-2→POC-5, in the build order the team committed to on 2026-07-16. Where the two overlap, the architecture doc's design wins; this doc sequences and details the *build*.

---

## 1. Where we are

POC-0 (functional foundation — chat `session_id = thread_id`, persistent fail-loud checkpointer, memory on `/chat/stream`, `user_id`/`deployment_id` propagation + ownership) and POC-1 (shared workflow thread — one `thread_id` across all members, `workflow_run`-scoped transcript read, verbatim-output write-back) are **implemented, deployed on the EKS test-cluster, and proven end-to-end** by `scripts/e2e/suite-75-context-storage.sh` (**5 passed / 0 failed / 1 skipped**, the skip being a capacity-only durable-HITL case). Live images: `registry-api:0.2.188`, `declarative-runner:0.1.52`.

What POC-0/1 did **not** do is make any of it *visible*. The memory works; the product doesn't show it. POC-2→5 are that UX layer.

**Deferred, intentionally** (not part of POC-2→5): the **Haiku rationale summarizer** (POC-1b — scaffolded in schema as `message_kind='rationale'`, no writer yet; the shared thread currently carries the user query + verbatim agent output). It builds *after* POC-4 per user direction. All Tighten items (isolation/erasure/injection/PII, architecture doc §7) stay after the POC line.

## 2. Build order & rhythm

**Order (committed 2026-07-16): POC-2 → POC-3 → POC-4 → POC-5.** Numeric, not by dependency (POC-5 is independent of 2–4 and could have been pulled forward; the team chose order).

**Rhythm — re-ground every phase before building it:**

> This roadmap is the north star, but code drifts between phases (this session alone moved `registry-api` 0.2.186→0.2.188 and rewrote the memory read path + `ConversationStore`). So immediately before starting a phase:
> 1. **`/plan`** — produces `plan.md` + `research.md` + `data-model.md` + `contracts/` grounded against the *current* code, not this snapshot.
> 2. **`/tasks`** — dependency-ordered `tasks.md`.
> 3. **`/implement`** — execute the tasks.
>
> Never build a phase straight from this doc. The design here is the intent; the per-phase `/plan` is the grounded truth at build time.

**Every phase is a vertical slice:** UI control → API → DB → read back in UI, proven (Playwright + save→reload) before the next phase starts. No horizontal layering. Image bumps in all three files (`scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`) + changelog; deploy via the sanctioned Helm path (`SKIP_BUILD=1 bash scripts/deploy-eks.sh`) — never ad-hoc `kubectl set image/env` (that is drift). Update the gap ledger (`docs/testing/manual-ui-e2e-test-plan.md`) and `docs/experience/*` for the surfaces each phase touches.

| Phase | What ships | Prove | Size |
|---|---|---|---|
| **POC-2** | Per-agent attributed chat bubbles + SSE author routing (ChatPane / AgentChatPage / CatalogChatPage) + eval transcript + workflow share-context toggle | Playwright: multi-agent workflow renders attributed; toggle persists (save→reload) | Medium |
| **POC-3** | `user_profiles` structured presets + Preferences page + advisory directive + precedence | Two users → different formatting; survives reload | Small–Medium |
| **POC-4** | Team Knowledge Base + Sources upload → MinIO + chunk/embed/index (pgvector) + `knowledge_search` tool + Knowledge page + citations | Agent answers from a Source with a citation; never returns another team's chunks | Large |
| **POC-5** | Conversation list + new/continue + memory viewer (in-chat sidebar **and** standalone page) | Reload → continue a prior conversation with context | Medium |

---

## 3. POC-2 — Proper UX: per-agent attribution + eval transcript + share-context toggle  *(NEXT)*

**Goal** (architecture doc §10): every chat/eval surface shows *which agent* said what, so a multi-agent workflow reads as a real multi-speaker conversation. Without this, the POC-1 shared thread is invisible. **Priority fix: `CatalogChatPage`**, which runs workflows but collapses the whole run into one final-output bubble (throws away `children`).

### 3.1 Backend & attribution source — corrected against the running code (2026-07-16 `/plan`)
**The original premise ("thread author through the workflow stream") does not match the code.** Workflow member deltas do **not** stream through the chat SSE proxy: `workflow_orchestrator.py::_run_step` dispatches each member via `POST /chat` (full output collected into child `AgentRun` rows) or `_dispatch_durable_member` (`POST /run` + poll) — never streamed; `CatalogChatPage` learns results by polling `getWorkflowRunTree`. `_proxy_agent_stream` (`registry-api/routers/chat.py`) is used **only** by single-agent chat (`stream_chat` / `stream_deployment_chat`). So attribution has two distinct sources, not one stream:
- **Single-agent chat (one speaker):** add `author` to the proxy's token frames (`{"type":"token","content":...,"author":"<agent_name>"}`) + an `{"type":"agent_start","author":...}` boundary. Meaningful only here. (`ChatPane` reads raw runner `text_delta` on the playground transport and derives author client-side from its `agentName` prop — a second transport; don't unify.)
- **Workflow (N speakers):** attribution rides on the **run-tree `children[]`** (live structural view, already carries per-member `agent_name`/`output`/`trace_id`/`cost`) + the **`scope=workflow_run` transcript reload** (`GET /memory?scope=workflow_run&thread_id=...`, rows carry `agent_name`+`message_kind`) — **no invented streaming path**.
- `AttributedBubble`/the reducer take an explicit `author`, so single-agent is the **degenerate one-speaker case of the same component**, not a fork (No-Bandaid).
- Grounded plan + task breakdown: `docs/plan/context-storage-poc-2/`.

### 3.2 Frontend — labeled bubbles across the three surfaces
- Add `author?: string` to the three local `Message` types: `ChatPane.tsx:8`, `AgentChatPage.tsx:17`, `CatalogChatPage.tsx:10`.
- New shared `AttributedBubble` component (`studio/src/components/chat/`) — agent name + deterministic per-agent color. Single-agent renders unlabeled (or subtle); multi-agent shows the name.
- SSE reducer (`AgentChatPage.tsx:318-324` + siblings): route each delta to the bubble matching its `author`; open a new bubble on `agent_start`.
- **`CatalogChatPage`** (priority): render per-member turns via `AttributedBubble`; reuse `WorkflowRunTree` `{parent, children}` (per-member `agent_name`/`output`/`trace_id`/`cost`, `WorkflowBuilderPage.tsx:785-871`) for the structural step view.
- **Eval transcript** (`pages/EvalResultsPage.tsx`): expandable per-item shared-thread transcript (reuse `AttributedBubble`) so multi-agent eval runs show per-agent turns, not just the final blob. `TraceDrawer` already has an `AGENT` span type.
- **Workflow "share context" toggle**: `WorkflowBuilderPage.tsx` first-save modal (after Orchestration ~L1010) — "Share context between agents" + per-session/per-run memory choice, persisted via `updateCompositeWorkflowApi` (the API type already carries `memory_enabled`).

### 3.3 Prove
- **Playwright** (`studio/e2e/`): run a multi-agent workflow → each agent's turn renders attributed (name + distinct bubble, `waitForResponse`); share-context toggle persists (save→reload); `CatalogChatPage` shows per-member turns, not one blob.
- **Vitest**: `AttributedBubble.test.tsx` (single vs multi-author, color); SSE reducer routing test.
- **Backend e2e** (extend suite-75): the workflow stream frames carry `author`; the reloaded `scope=workflow_run` transcript returns per-author rows.
- Image bumps: `registry-api` + `studio`. typecheck.

---

## 4. POC-3 — User-profile presets  *(after POC-2)*

Architecture doc §8. `user_profiles` table keyed by `user_id` (platform-level, deployment-independent). **Structured presets only** — response length / tone / format / language / expertise — enum values, so no free-text injection vector and safe to apply cross-tenant. Account **Preferences** page. Compiled into a bounded, platform-controlled **advisory** system directive with explicit precedence **governance > author instructions > workflow settings > user preference** (a preference never overrides a task/format/safety/governance requirement). `user_delegated` runs only (a daemon has no user). *Prove:* two users get different formatting from the same agent; profile survives reload. New: migration + `user_profiles` model/router, Preferences page, runner directive-composition. Image bumps: registry-api + studio + (runner if it composes the directive). **Gets its own `/plan`+`/tasks` at build time.**

---

## 5. POC-4 — Knowledge Base / RAG  *(after POC-3 — largest slice)*

Architecture doc §9. Team-scoped **Knowledge Base** + **Sources** (file upload) → **MinIO** blobs (via the `BlobStore` port) + chunk/embed/index → **pgvector** (via the `VectorStore` port; migration-0022 pattern). Retrieval exposed as a governed platform tool **`knowledge_search`** (OPA/HITL apply for free). New **Knowledge** page (Sources tab with ingestion status, chunk viewer, **Test retrieval**), attach-to-agent picker, **runtime citations**. **S5 tenant filter baked in** — mandatory `(team, kb_id)` predicate before any retrieval; S7 ingest content-scanning deferred to Tighten (synthetic Sources only in the POC). *Prove:* an agent answers from an uploaded Source **with a citation**; a query never returns another team's chunks; retrieval-test shows expected chunks. Biggest slice (full ingest pipeline + net-new upload UI + embedding call) — will need its own detailed `/plan`+`/tasks` and probably its own e2e suite. Image bumps: registry-api + studio + (embedding/ingest worker).

---

## 6. POC-5 — Conversation list + continue + memory viewer  *(BUILD LAST)*

Architecture doc §11 (line 339). The two-part conversation UX: **new** (fresh session, clean) vs **continue** (reopen a past conversation, keep talking with prior turns as context). Build **both** surfaces (team decision): an in-chat sidebar on `AgentChatPage` **and** the standalone `preview/ConversationsPage.tsx` wired to real data.

**Continue already works** — reusing a prior `session_id` on the next `/chat` POST makes the runner load that thread's earlier turns as context (`declarative-runner/main.py::_load_memory_context`), and `GET /memory?thread_id=...&scope=agent` rehydrates the UI. The **only new backend piece is a list-conversations query.**

- **Backend:** `memory.list_conversations(user_id, agent_name?, deployment_id?)` — aggregate over `agent_memory` grouped by `thread_id`: `title` = first user message `(array_agg(content ORDER BY message_index) FILTER (WHERE role='user'))[1]`, `message_count`, `last_activity = max(created_at)`, `session_id`, `agent_name`. Surface via the `ConversationStore` port. Two `require_user`, caller-scoped endpoints: `GET /agents/{name}/memory/conversations?deployment_id=` (sidebar) and `GET /me/conversations` (cross-agent, standalone). New `ConversationSummary` schema. **Title = first user message; Haiku titling deferred to POC-1b.**
- **Frontend:** `listConversations` / `listMyConversations` in `registryApi.ts`. `AgentChatPage`: make `sessionId` resettable (`AgentChatPage.tsx:68`), add a left `ConversationSidebar` ("New conversation" + list); New → new uuid + clear messages; Select → set sessionId to `thread_id` + seed `messages` from `listMemory(thread_id)`. Wire `preview/ConversationsPage.tsx` to real data; its Continue button opens `AgentChatPage` seeded on that `thread_id`.
- **Prove:** reload → conversation still listed → click → prior transcript rehydrates → follow-up continues with context (recall a turn-1 fact). Playwright + suite-75 T-S75-007 (per-thread summaries, ownership-scoped) + T-S75-008 (`/me/conversations`). Image bumps: registry-api + studio.
- **Deferred:** same sidebar on `CatalogChatPage` (identical pattern); admin `MemoryTab` per-user privacy → Tighten S9.

---

## 7. Not in scope (the Tighten line)

Per architecture doc §7 / §11, the POC gate is *functional*, not *hardened*. These stay after POC-5: cross-agent/indirect prompt-injection defenses (S1), raw-PII-through-safety-proxy on memory writes (S2), summarizer egress hardening (S3), `message_index` race (S4 — partial UNIQUE backstop already in migration 0064), vector tenant-filter beyond S5's baked-in predicate, fail-closed scope resolution audit (S6/S9), right-to-erasure spanning checkpoints (S8), at-rest/in-transit encryption (S10/S11), summarizer budget guard (S12). A green POC is a working product surface, not a hardened one — do not conflate.

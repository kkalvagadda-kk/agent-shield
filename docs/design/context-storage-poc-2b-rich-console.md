# POC-2b — Rich Multi-Agent Workflow Console

**Status**: Proposed (2026-07-16)
**Branch**: `worktree-ux-preview-context-storage` (commit only here; never merge to main — Kalyan merges manually)
**Companion**: [`context-storage-ux-roadmap.md`](./context-storage-ux-roadmap.md) (roadmap), [`context-storage-architecture.md`](./context-storage-architecture.md) (subsystem spec)
**Live baseline**: `registry-api:0.2.189`, `studio:0.1.142`, `declarative-runner:0.1.54`

---

## 1. Why this exists

POC-2 shipped the **attribution primitive** — `AttributedBubble` renders a colored dot + the agent's name above each bubble, wired to real SSE (single-agent) and to the polled run-tree (workflow). It's proven end-to-end.

But the design target the team aligned on is the **Multi-Agent Chat mock** (`studio/src/pages/preview/MultiAgentChatPage.tsx`, Image #6 in review). Held next to what actually renders in `CatalogChatPage`'s `WorkflowTurn` today, POC-2 delivers roughly a quarter of it. The mock is a **live** multi-speaker console — you watch each agent speak, call a tool, and hand off in real time. The current catalog workflow chat is the opposite: it POSTs `/workflows/{id}/runs`, shows a **"Running workflow…" spinner**, polls `getWorkflowRunTree` until the whole run is terminal, then dumps all members' finished bubbles at once (`CatalogChatPage.tsx:255-331`). No live members, no tool activity, no progressive reveal.

This is the gap POC-2b closes. **Reason from the running product**: everything below is measured against `CatalogChatPage.tsx::WorkflowTurn`/`pollWorkflowResult`, `AttributedBubble.tsx`, `routers/chat.py::_proxy_agent_stream`, `workflow_orchestrator.py::_dispatch`, and the `WorkflowRunTree` shape — not the roadmap prose.

### The mock, decomposed

| Mock element (Image #6) | In POC-2 today? | Source of truth needed |
|---|---|---|
| **Live progressive reveal** (members stream, not spinner-then-dump) | ❌ | **new multiplexed workflow SSE** (§3.0) |
| Agent avatar (Bot icon, per-agent color) | ❌ | frontend only |
| Name + colored dot | ✅ | `AttributedBubble` (done) |
| **Tool-call chip** ("called `web_search`") | ❌ | live SSE frame + run-tree projection (reload) |
| **Rationale box** (amber, distilled "why") | ❌ | **POC-1b** writer (`message_kind='rationale'`) |
| **Show-rationale toggle** | ❌ | frontend, gated on rationale existing |
| Content bubble | ✅ | `AttributedBubble` (done) |
| **Citation chips** ("security-policy.pdf") | ❌ | **POC-4 (RAG)** — deferred, slot only |
| Console shell (header "Workflow · N agents", subtitle, attribution info-bar) | ❌ | frontend only |

---

## 2. Scope decisions (READ FIRST)

Three calls, confirmed with the reviewer 2026-07-16:

1. **Make the catalog workflow chat stream members live** (§3.0) — the headline. Today workflows are spinner-then-dump; the mock is a live console. This adds a **new multiplexed workflow-run SSE endpoint** and refactors `workflow_orchestrator` into a **shared streaming generator** so the existing (non-streaming) `/runs` path and the new streaming path walk the *same* graph logic — no forked orchestrator (per the No-Bandaid rule). Reactive members stream tokens via their pod's `/chat/stream`; durable/HITL members emit start→(poll)→end around the existing durable path (no token stream, an accepted asymmetry — the mock's `poc-research-answer` uses reactive members and streams fully).

2. **Pull rationale into scope, but reuse the model's own reasoning — NOT a Haiku call** (confirmed by reviewer; supersedes POC-1b's Haiku summarizer). The code *already* injects a "state your one-sentence why before each tool call" prompt (`graph_builder.py:439`) and *already* extracts it (`_extract_reasoning`, used for HITL today). So 2b-ii captures that existing reasoning, persists it as `message_kind='rationale'`, emits a live `rationale` frame, and rehydrates on reload — **no second LLM hop**. Non-blocking (rationale null → no amber box). See §3.2.

3. **Citations stay deferred to POC-4.** Citation chips require a real knowledge base + retrieval (RAG), which doesn't exist yet. POC-2b builds the **citation slot** in the bubble (so POC-4 is pure data wiring) but ships it empty. In the ledger below, not hidden.

Everything except citations is buildable now against existing persistence + the member pods' existing `/chat/stream`.

---

## 3. Architecture — where each piece is sourced

**Two channels, one renderer.** POC-2b gives the workflow console a **live channel** (the new multiplexed SSE, §3.0) for the run-in-progress, and keeps the **persisted channel** (run-tree + shared transcript) as the source of truth for **reload/history** and for durable/HITL members that don't token-stream. The frontend uses the *same* attributed-bubble reducer for both: a live `token{author}` frame and a reloaded tree child both open/extend the same author-keyed bubble. So richness (tool chips, rationale) is emitted **both** as live frames and persisted rows — never one without the other.

Three surfaces reuse the same renderer and must all benefit:
- **`CatalogChatPage`** (consumer, production + sandbox catalog) — **live stream** in-flight (§3.0), tree on reload.
- **`WorkflowBuilderPage`** run panel (`WorkflowRunTree` component) — already shows per-member cards; upgrade in place (can adopt the same live stream).
- **`EvalResultsPage`** transcript — reuses `AttributedBubble` from the persisted transcript (POC-2).

### 3.0 Live member streaming (sub-phase 2b-0 — the headline)

**Server.** Refactor `workflow_orchestrator` so the graph walk (sequential / conditional / supervisor / handoff — see `ref_workflow_orchestration_routing`) is an **async generator** that yields typed frames as it executes, instead of returning only the final rolled-up result. The existing non-streaming `/runs` endpoint becomes a thin **drain** of that generator (collect frames, persist, return terminal state — behavior unchanged). A **new endpoint** streams the same generator to the client:

```
POST /api/v1/workflows/{id}/runs/stream   (SSE; body = { message, session_id? })
  → yields, per member, in graph order:
      {"type":"agent_start", "author":"poc-researcher"}
      {"type":"token",       "author":"poc-researcher", "content":"…"}   (reactive members)
      {"type":"tool_call",   "author":"poc-researcher", "tool":"web_search", "status":"ok"}
      {"type":"rationale",   "author":"poc-researcher", "content":"Searched for the record time…"}
      {"type":"agent_end",   "author":"poc-researcher"}
      … next member …
      {"type":"done", "run_id":"…"}
```

- **Per-member dispatch**: add `_dispatch_stream(agent, team, message, thread_id, conversation_id)` next to `_dispatch`. Reactive members → call the pod's existing `/chat/stream` and re-yield its frames tagged with `author=agent_name` (this is exactly what `chat.py::_proxy_agent_stream` already does for one agent — factor the per-pod proxy into a shared helper both call, so there is ONE pod-SSE reader). Durable members → keep `_dispatch_durable_member` (poll), bracket it with `agent_start`/`agent_end`, no token frames.
- **Shared graph logic, not forked**: the routing/edge-resolution/step-cap code stays in one place; only the leaf dispatch differs (await vs async-iterate). The non-streaming path drains the generator so both stay in lockstep — a routing change can't drift between them.
- **Persistence unchanged**: members still write their output + (2b-ii) rationale to the shared `workflow_run` transcript, and child `AgentRun` + `agent_run_steps` rows are still created. Streaming is observation, not a new write path — so reload (tree/transcript) always matches what streamed.

**Client.** In `CatalogChatPage`, replace the workflow branch of `handleSend` (`pollWorkflowResult`, lines 255-331) with an `EventSource` on `/workflows/{id}/runs/stream`, feeding the **same** `openAuthorBubble`/`routeToken` reducers the single-agent path already uses (lines 355-387) — a workflow member frame with `author=<member>` simply opens a new attributed bubble. `pollWorkflowResult`/`WorkflowTurn` stay for the reload/history render (seeded from the tree on page load).

### 3.1 Tool-call chips (sub-phase 2b-i)

- **Live source**: the member pod's `/chat/stream` already emits `tool_call_start`/`tool_call_end`; `chat.py:473` currently **drops** them ("informational — skip for consumer chat"). Stop dropping them — re-emit as `{"type":"tool_call","author":…,"tool":…,"status":…}` in the shared pod-SSE reader, so both single-agent chat and the workflow stream (§3.0) show the chip live.
- **Persisted source (reload)**: each member run already produces `agent_run_steps` (tool_name, status). Extend each child in `GET /workflows/{id}/runs/{runId}/tree` with a compact `tool_calls: [{ tool_name, status }]` projection (no new table). So a reloaded run shows the same chips.
- **Frontend**: a `ToolCallChip` in `AttributedBubble`'s existing `children` slot, fed by either the live `tool_call` frame or the tree child's `tool_calls[]`.

### 3.2 Rationale (sub-phase 2b-ii — reuse the model's own reasoning, NOT a Haiku call)

**Key finding (reason from running product):** the rationale the mock wants **already exists**. `graph_builder.py:439-444` already injects into every agent's prompt: *"Before calling any tool, first state in one short sentence why you need it and what specific information you are retrieving."* And `graph_builder.py:184 _extract_reasoning(state)` already pulls that one-sentence "why" out of the tool-calling `AIMessage` — it's used **today** to populate the HITL approval `reasoning` (line 290). So POC-1b's Haiku summarizer is **superseded**: we do not add a second LLM hop; we capture the reasoning the member already produced.

- **Capture**: at the member turn boundary (where output is saved), read `_extract_reasoning(graph_state)` — the same value HITL already uses — and pass it alongside the output.
- **Writer**: persist it as a row on the shared `workflow_run` transcript with `message_kind='rationale'`, `agent_name=<member>` (schema slot from migration 0064), co-located with the output write in `declarative-runner/main.py::_save_memory_turn` so rationale + output land atomically on the same thread.
- **Live**: the orchestrator emits a `rationale{author}` frame (§3.0) once the row is written, so it appears live under the streaming bubble.
- **Reload**: the run-tree read joins the rationale row by `(thread_id, agent_name)` → `rationale: string | null` per child; `GET /memory?scope=workflow_run&thread_id=` returns rows tagged by `message_kind` so the UI pairs each to its author's bubble.
- **Frontend**: amber rationale box above the content bubble (mock lines 62–67), shown only when `rationale` is present and the **Show-rationale toggle** is on.
- **Non-blocking + honest nulls**: `_extract_reasoning` is best-effort — empty for tool-forced calls, some models, and **members that call no tools** (e.g. `poc-answerer` has no tool-calling AIMessage, so its rationale may be empty → no amber box, by design; the mock shows rationale mainly on the tool-using members). Never gate or error on it.
- **Hardening drops out**: because there's no separate summarizer, the summarizer-egress/PII/budget Tighten items (S3/S12) largely evaporate — the "rationale" is the member's own governed output, already inside the safety boundary. *(Deferred alternative, not in scope: to also get a rationale on a tool-less final answer, broaden the prompt-injection to request a one-line rationale on the final turn — changes agent output, so out of scope for 2b-ii.)*

### 3.3 Console shell + avatars (sub-phase 2b-iii)

- Pure frontend. `WorkflowTurn` gains: per-agent avatar (Bot icon tinted via `agentColor`), a header ("`<workflow>` · N agents") + subtitle ("shared conversation thread — every agent reads the same transcript"), and the blue attribution info-bar with the toggle (mock lines 11–30).
- Extend `AttributedBubble` with an optional `avatar` + structured header, OR lift the mock's `Turn` layout into the real component. Keep it presentational; the single-agent degenerate case (no author) must render exactly as today (guard the Vitest).

### 3.4 Citation slot (deferred content, POC-4)

- Add a `citations?: {source, kb}[]` prop + chip row to the bubble (mock lines 73–81), always empty until POC-4 wires RAG. Documented as **deferred (intentional)**.

---

## 4. Data flow

**Live (in-flight) — new channel:**
```
POST /workflows/{id}/runs/stream  →  orchestrator async-generator walks the graph
  per member:  agent_start{author} → token{author}* → tool_call{author}* → rationale{author} → agent_end{author}
  end:         done{run_id}
CatalogChatPage EventSource → openAuthorBubble/routeToken (same reducer as single-agent) → progressive bubbles
```

**Persisted (reload / history / durable members) — unchanged writes:**
```
member run completes
  ├─ output    → agent_memory row (message_kind='agent_output', agent_name, workflow_run thread)  [POC-1, exists]
  ├─ rationale → agent_memory row (message_kind='rationale',   agent_name, same thread)           [2b-ii, NEW]
  └─ tool steps → agent_run_steps rows (tool_name, status)                                         [exists]

GET /workflows/{id}/runs/{runId}/tree
  → children[] each gains: tool_calls[]  (from agent_run_steps)   [2b-i]
                           rationale      (from agent_memory rationale row)  [2b-ii]

reload:  GET /memory?scope=workflow_run&thread_id=<runId>
  → all rows (output + rationale), grouped by agent_name → rehydrate attributed bubbles + amber boxes
```

**No new storage.** Streaming is observation over the same writes — the persisted channel always reproduces what streamed. Tool-calls project from `agent_run_steps`; rationale from `agent_memory` (slot already migrated).

---

## 5. Verification (Definition of Done gate)

- **Playwright** (`studio/e2e/`): run the `poc-research-answer` workflow in the catalog → assert (a) **progressive reveal** — `poc-researcher`'s bubble appears and grows *before* `poc-answerer`'s exists (`page.waitForResponse` on `/runs/stream`, assert the researcher bubble present while answerer absent), (b) two attributed bubbles each with an avatar + name, (c) `poc-researcher` shows a `web_search` tool chip, (d) toggle flips the amber rationale boxes, (e) **save→reload→bubbles+rationale survive** (reload from `/memory`/tree, not the store).
- **Vitest**: `AttributedBubble` renders avatar/chip/rationale/citation slots; single-agent degenerate case unchanged; `ToolCallChip`; rationale toggle logic; the workflow-stream reducer routes `agent_start`/`token`/`tool_call`/`rationale` frames to the right author bubble.
- **suite-75** extend: `T-S75-009` — run-tree children carry `tool_calls`; `T-S75-010` — rationale row written to the shared thread and returned per child; `T-S75-011` — `/workflows/{id}/runs/stream` emits `agent_start`+`token`+`agent_end` frames tagged with each member's author, and the non-streaming `/runs` drain still produces the same terminal tree (parity).
- **No orphan code**: grep every new symbol (`ToolCallChip`, `rationale` field, tree projection, `_dispatch_stream`, `/runs/stream`) for a live caller before done.
- **Image bumps**: `registry-api` (stream endpoint + orchestrator generator + tree projection + rationale read) + `declarative-runner` (capture `_extract_reasoning` → rationale row + tool-frame passthrough) + `studio` (frontend) — all three in `deploy-cpe2e.sh` + `deploy-eks.sh` + `charts/agentshield/values.yaml`.
- **Deploy is a separate, user-gated step** — build/push + Helm on the shared EKS cluster only after reviewer go.

## 6. Known gaps (ledger)

- **Durable/HITL members don't token-stream** — they emit `agent_start`→(poll)→`agent_end` with the final output, no live tokens (**by design** — the durable path runs via `/run`+poll, not `/chat/stream`). Reactive members stream fully. The mock's workflow is all-reactive.
- **Citations** — slot built, content **deferred (intentional)** to POC-4 (needs RAG).
- **Rationale on `CatalogChatPage` only vs all surfaces** — the eval transcript (`EvalResultsPage`) gets rationale for free via the shared renderer; the WorkflowBuilder run panel upgrade is in-scope.
- **Rationale on tool-less members** — a member that calls no tool (e.g. `poc-answerer`) may have empty reasoning → no amber box (by design; broadening the prompt to the final turn is a deferred alternative, §3.2).
- **Summarizer hardening** (S3/S12) — **mostly moot**: no separate summarizer exists in this design, so there's no summarizer egress to harden. The reasoning is the member's own governed output.
- **OPA bypass** (`graph_builder.py`, task #16) — still temporary; unrelated to this effort but reverts before the POC line ships.

---

## 7. Sub-phase summary (for /plan)

| Sub-phase | Deliverable | Backend | Frontend | In scope? |
|---|---|---|---|---|
| **2b-0** | **Live member streaming** | orchestrator → async generator; `_dispatch_stream`; new `POST /workflows/{id}/runs/stream`; shared pod-SSE reader; non-streaming `/runs` becomes a drain | `EventSource` on `/runs/stream`, reuse `openAuthorBubble`/`routeToken` | yes (headline) |
| **2b-i** | Tool-call chips | stop dropping SSE tool frames (shared reader); tree `tool_calls` projection (reload) | `ToolCallChip` in bubble children slot | yes (core) |
| **2b-ii** | Rationale (reuse model reasoning, no Haiku) | capture `_extract_reasoning` at turn boundary → persist `message_kind='rationale'` in `main.py::_save_memory_turn`; `rationale` frame; tree `rationale` join | amber box + Show-rationale toggle | yes (confirmed) |
| **2b-iii** | Console shell + avatars | none | header/subtitle/info-bar/avatar in the workflow console | yes (core) |
| **2b-iv** | Citation slot | none | empty chip row + prop | yes (trivial; POC-4 fills content) |

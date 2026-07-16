# Research & Grounding — POC-2b Rich Multi-Agent Workflow Console

All findings verified against the CURRENT committed code on `worktree-ux-preview-context-storage`
(registry-api 0.2.189 / studio 0.1.142 / declarative-runner 0.1.54). File:line citations are from
the read, not the design doc.

## R1 — The non-streaming workflow run path (drain target)

- **Router**: `services/registry-api/routers/composite_workflows.py`.
  - `POST /{workflow_id}/runs` → `start_workflow_run` (L389). Creates a parent `AgentRun` (context="playground", trigger_type=body.trigger_type, run_by=body.run_by), opens a Langfuse trace, then either `dispatch_to_orchestrator_pod` (if a prod orchestrator pod exists) or `asyncio.create_task(orchestrate(str(parent.id), wf.team, str(workflow_id), message, wf.orchestration))` (L494-497). **Fire-and-forget background task, not a generator.**
  - `GET /{workflow_id}/runs/{run_id}/tree` → `get_workflow_run_tree` (L531). Returns `WorkflowRunTreeResponse(parent, children)`; `children = AgentRun where parent_run_id == run_id order by started_at`. Uses `_with_trace_url(run)` (L544) to set the non-ORM `trace_url` — **the exact pattern for the new `tool_calls`/`rationale` fields.**
- **Orchestrator**: `services/registry-api/workflow_orchestrator.py`.
  - `orchestrate(parent_run_id, team, workflow_id, input_message, mode, shape="durable")` (L1039) routes to `orchestrate_conditional/supervisor/handoff/orchestrate_graph_sequential`, wrapped in try/except → `_mark_parent("failed")`.
  - The shared leaf is `_run_step` (L438): creates the child AgentRun, resolves member shape, dispatches (`_dispatch` reactive `/chat` OR `_dispatch_durable_member` `/run`), detects pending Approval, updates the child row, authors a parent-trace span (`trace_workflow_step`). `conversation_id = parent_run_id` hardcoded at L496.
  - Mode walkers `_run_sequential_from` (L664), `_run_conditional_from` (L828), `_run_handoff_from` (L874), `_run_supervisor_from` (L924) all call `_run_step` and route via `_conditional_next`/`_handoff_next`/`_parse_next_agent`; `_MAX_STEPS=50` cap (L45). `resume_orchestration` (L716) re-enters after a HITL approval.
- **Refactor**: convert the leaf to `_run_step_stream` (generator) and the walkers to generators that drain the leaf + re-yield frames; `orchestrate` becomes `async for _ in orchestrate_stream(...): pass`. All DB writes stay inside the walkers → draining reproduces today's behavior exactly (No-Bandaid: one graph walk).

## R2 — The member-pod SSE reader (factor out)

- `services/registry-api/routers/chat.py::_proxy_agent_stream` (L373-487) is the per-pod SSE reader: POSTs `{service_url}/chat/stream` with `{message, thread_id, conversation_id, scope}` + identity headers, translates named SSE events (`text_delta`→token, `done`, `error`, `approval_requested`) to data-only frames.
- **L473 is the drop**: `# tool_call_start / tool_call_end are informational — skip for consumer chat`. 2b-i removes this.
- **Inconsistency**: the resume path `resume_stream_chat` (L1136-1139) already forwards `tool_call_start`/`tool_call_end` as-is. The new shared reader normalizes BOTH to a single `{"type":"tool_call","author","tool","status"}` frame.
- `stream_chat` (L790) + `stream_deployment_chat` (L980) call `_proxy_agent_stream(..., author=name)` — signature must stay stable so these are untouched.

## R3 — Rationale: reuse the model's reasoning (NOT Haiku) — with a correction

- `sdk/agentshield_sdk/graph_builder.py:439` injects "state your one-sentence why before each tool call"; `_extract_reasoning(state)` (L184) pulls it and HITL uses it at L290 (the approval `reasoning`).
- **Correction (grounding surprise #3)**: `_extract_reasoning` reads `messages[-1]`. At the HITL *interrupt* the tool-calling AIMessage IS last — correct. At the *turn boundary* (where the member's output is saved) `messages[-1]` is the FINAL answer AIMessage — so `_extract_reasoning(final_state)` would capture the answer, not the reasoning. 2b-ii therefore adds `_extract_tool_rationale(state)` selecting the **last AIMessage with non-empty `tool_calls`** (same text-join logic). Tool-less members → `""` (no amber box, by design §3.2).
- **Capture surface**: the reactive member runs through the runner's `/chat` (sync) and `/chat/stream` (workflow members after the 2b-0 refactor go via `/chat/stream`). `workflow_executor.run()` (L704) returns `{"response","thread_id"}` from `result = await self.graph.ainvoke(...)` — so `run()` can add `rationale=_extract_tool_rationale(result)` cheaply. `run_streamed` (L780) does NOT return state, so the `/chat/stream` handler reads it back post-stream via `self.graph.aget_state(config)` (`extract_tool_rationale` method).
- **Persist**: `services/declarative-runner/main.py::_save_memory_turn` (L462) already POSTs user+agent_output messages with per-message `message_kind`. Add a `rationale` param → append a `message_kind='rationale'` message. **No migration** — migration 0064 already added `scope`/`message_kind` with `'rationale'` in the CHECK constraint, and `MemoryMessage.message_kind` (schemas.py L1800) already accepts `rationale`.
- **Live**: `/chat/stream` emits a new `event: rationale` SSE frame (workflow_run scope only) after the stream; the shared reader (R2) translates it; the orchestrator re-yields it.

## R4 — Tool-call persistence (grounding surprise #1 & #2)

- The step table is **`run_steps`** (ORM `RunStep`, models.py L1631), NOT `agent_run_steps`. Columns: `run_id` (polymorphic, no FK), `step_number`, `name`, `status` (CHECK in pending/running/completed/failed/awaiting_approval/cancelled), `output` (JSONB), UNIQUE(run_id, step_number).
- **`run_steps` rows are written ONLY by the durable `/run` path** (the step-update callback `/api/v1/internal/runs/{child_id}/step-update`). A REACTIVE member dispatched via `/chat` (or `/chat/stream`) writes NONE. The mock's `poc-research-answer` uses reactive members → run_steps would be empty → the design's "project tool_calls from agent_run_steps" yields nothing for the headline case.
- **Resolution (No-Bandaid, explicit marker)**: `_dispatch_stream` persists each observed `tool_call` frame as a `RunStep(run_id=child_id, name=<tool>, status="completed"|"failed", output={"kind":"tool_call","tool":...,"status":...})`. The tree projection reads run_steps where `output->>'kind' = 'tool_call'`. This makes "streaming is observation over the same writes" true for reactive members, and reload == stream. Durable members' native run_steps are a different shape and are gap-ledgered (no reactive-marker → no chip on reload; live start/end only).

## R5 — Run-tree response shape + frontend types

- `schemas.py`: `WorkflowRunTreeResponse{parent, children: AgentRunResponse[]}` (L548); `AgentRunResponse` (L1565) is `from_attributes` with a manually-set `trace_url` (L1593). Adding `tool_calls`/`rationale` as defaulted non-ORM fields follows the same pattern.
- `studio/src/api/registryApi.ts`: `WorkflowRunTree{parent, children: AgentRunItem[]}` (L597); `AgentRunItem` (L1369) — extend with `tool_calls?`/`rationale?`. `triggerWorkflowRun` (L738) + `getWorkflowRunTree` (L746) exist.

## R6 — Frontend surfaces

- `studio/src/pages/CatalogChatPage.tsx`: `WorkflowTurn` (L62) renders per-member `AttributedBubble` from the tree; `pollWorkflowResult` (L259) polls until terminal (spinner-then-dump); `sendWorkflowMessage` (L291) POSTs `/runs` + polls; `sendAgentMessage` (L333) uses `EventSource` + `openAuthorBubble`/`routeToken` (L363-366). Single `[sessionId]` (L165). The workflow branch is what 2b-0 replaces with a fetch-stream.
- `AttributedBubble.tsx` — presentational, `children` slot, degenerate single-agent case (no author) must stay byte-identical.
- `chatStream.ts` — pure `routeToken`/`openAuthorBubble`, generic over `M extends Attributed`, route by author (undefined = single-speaker).
- `agentColor.ts` — deterministic FNV-1a → 8-entry Tailwind palette; `agentColor(name)` returns `{bg,text,border,dot}` (static class literals — never interpolate).
- `MultiAgentChatPage.tsx` (preview mock) — the visual target: avatar (`Bot` tinted `turn.color`), name, tool chip (`Database`), amber rationale (`Lightbulb`), content, citations row, header "Workflow · 3 agents", subtitle, blue info-bar + Show-rationale checkbox.

## R7 — Test harnesses

- `scripts/e2e/suite-75-context-storage.sh` — in-pod python via `kubectl exec`, emits `RESULT <id> <PASS|FAIL|SKIP> <detail>` + `DIAG` lines tallied by bash; `agent_pod_breakage()` distinguishes capacity-SKIP from broken-pod-FAIL. Section A provisions `s75-wa`/`s75-wb` reactive agents + a sequential workflow. Registered in `run-all.sh` L124.
- `studio/e2e/context-attribution.spec.ts` — POC-2's Playwright: `beforeAll` builds a 2-member workflow via REST (ADMIN header `X-User-Sub: 75c7c8b3-...` platform-admin sub), publishes+admin-approves to the catalog, drives `/catalog/{id}/chat`, `waitForResponse` on `/tree`, asserts ≥2 attributed member bubbles. The template for T017.

## R8 — Fixture tool must be LOW-risk (grounding surprise #4)

- `scripts/seed-defaults.sh:67` seeds `web_search` as an **HTTP tool with `risk_level: "high"`**. A high-risk tool call trips the HITL gate (`decision.require_approval`), and a **reactive** workflow member fails-closed on an approval gate (`_park_or_fail` → `_fail_parent`, workflow_orchestrator.py L422). The OPA bypass (graph_builder.py L238) disables only the DENY, not `require_approval`.
- So the live-stream fixtures (T016 suite-75, T017 Playwright) create/attach a **low-risk** HTTP tool (`risk_level:"low"`) to the researcher so the reactive run completes AND shows a tool chip. The design doc says "web_search chip"; this is the faithful, runnable substitution (documented deviation).

## R9 — Versions & migration

- Current Alembic head is **0064** (`0064_agent_memory_shared_thread.py`, down_revision 0063). It already provides `scope`/`workflow_run_id`/`message_kind` (+`'rationale'` CHECK) + the `(thread_id, scope, message_index)` index + `UNIQUE(thread_id, message_index)`. **POC-2b needs NO new migration** — rationale rows and the tool-call projection reuse existing schema (`agent_memory` + `run_steps`).
- Image bumps: registry-api 0.2.189→0.2.190, declarative-runner 0.1.54→0.1.55, studio 0.1.142→0.1.143 in `deploy-cpe2e.sh` (L266/275/273), `deploy-eks.sh` (L67/69/70), `values.yaml` (L597/673/917).

## R10 — Fixed decisions (resolve ambiguities before coding)

1. **Transport**: `/runs/stream` is POST (needs a body). The frontend reads it with `fetch()` + `ReadableStream`, NOT `EventSource` (GET-only). Single-agent chat keeps `EventSource` (its start endpoint returns a GET stream_url).
2. **Rationale live-frame ordering**: the runner emits `event: rationale` AFTER the graph stream's internal `done`; the shared reader reads to EOF so it captures the trailing frame. The orchestrator re-yields it before its own run-level `done`.
3. **`conversation_id` threading**: `orchestrate`/`orchestrate_stream` take `conversation_id` (default = parent_run_id). The stream endpoint sets `parent.session_id = body.session_id` and uses `conversation_id = body.session_id or run_id`; the tree endpoint reads `parent.session_id or str(run_id)`. So rationale rehydration works whether or not a session is supplied.
4. **Durable members**: no token/tool_call/rationale live frames (agent_start→poll→agent_end); reload chips only for reactive-marker rows. Gap-ledgered.
5. **Stream endpoint is in-process only** (no production-orchestrator-pod dispatch). Gap-ledgered.
6. **Internal sentinel** `__member_end__` never leaves the server (filtered in the SSE serializer).

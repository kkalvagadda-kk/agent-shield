# Tasks — POC-2b Rich Multi-Agent Workflow Console

**Spec (authoritative)**: `docs/design/context-storage-poc-2b-rich-console.md`
**Plan**: `docs/plan/context-storage-poc-2b/plan.md` · **Research**: `research.md` · **Data model**: `data-model.md` · **Contracts**: `contracts/endpoints.md`, `contracts/sse-frames.md`
**Branch**: `worktree-ux-preview-context-storage` — commit ONLY here, never merge/PR to main (Karthik merges manually).
**Ships**: `registry-api:0.2.190` / `declarative-runner:0.1.55` / `studio:0.1.143` (baseline 0.2.189 / 0.1.54 / 0.1.142).

## How to read this file

- Tasks are dependency-topological; no task depends on a later-numbered one.
- `[P]` = this task's file set is disjoint from every other in-flight `[P]` task in the same band, so it may run in parallel.
- Each task lists exact **Files** (Create/Modify), a one-line **Acceptance**, **Deps**, and a copy-paste **Verify** command.
- **Checkpoints** (`[CP1a]`, `[CP1b]`, …) are phases, never `T###`. Each writes executable `scripts/` (a `bash scripts/deploy-eks.sh` deploy + a smoke script with real curl/kubectl/jq assertions) and is **user-gated** — deploy only after reviewer go (shared-cluster hazard; No-Merge-to-Main).
- **No new migration** — head is Alembic `0064`; `agent_memory` (scope/message_kind incl. `'rationale'`) and `run_steps` already exist (research R9).

## Grounding corrections baked into these tasks (research R1–R10)

1. Step table is **`run_steps`** (ORM `RunStep`), not `agent_run_steps`.
2. Reactive members write **no** `run_steps` rows on their own → the streaming orchestrator persists one `RunStep(output.kind='tool_call')` per observed `tool_call` frame so the tree projection has data and reload == stream.
3. Rationale uses **`_extract_tool_rationale`** (last AIMessage WITH tool_calls), **not** `_extract_reasoning` (`messages[-1]`, which is the final answer at the turn boundary).
4. Fixtures attach a **low-risk** HTTP tool (not the seeded high-risk `web_search`) so a reactive member calls it without tripping the HITL gate.
5. Frontend workflow stream uses **`fetch` + `ReadableStream`** (EventSource is GET-only); single-agent chat keeps `EventSource`.
6. Run-tree + stream endpoints live in **`routers/composite_workflows.py`**.

---

## Band 0 — Rationale capture (runner + SDK) [2b-ii backend]

### T001 [X] [P] — SDK: turn-boundary rationale extractor
- **Files**: Modify `sdk/agentshield_sdk/graph_builder.py`
- **Adds**: `def _extract_tool_rationale(state: Any) -> str` — selects the **last** `state["messages"]` entry whose `getattr(msg,"tool_calls",None)` is a non-empty list; joins text blocks exactly as `_extract_reasoning` does; `""` when none/malformed. Does NOT touch `_extract_reasoning` (HITL still uses it).
- **Acceptance**: for `[Human, AI(tool_calls=[…], content="Let me search…"), Tool, AI(content="The answer is…")]` returns `"Let me search…"`; tool-less state returns `""`. Imported by T002 (no orphan).
- **Deps**: none.
- **Verify**: `cd sdk && python3 -c "import ast; ast.parse(open('agentshield_sdk/graph_builder.py').read())" && grep -n "_extract_tool_rationale" agentshield_sdk/graph_builder.py`

### T002 [X] — Runner: expose rationale from the executor
- **Files**: Modify `services/declarative-runner/workflow_executor.py`
- **Adds**: `run()` returns `{"response","thread_id","rationale": _extract_tool_rationale(result)}` (import from `agentshield_sdk.graph_builder`); new `async def extract_tool_rationale(self, thread_id) -> str` reading `self.graph.aget_state(...)`, `""` on any error. `run_streamed` chunks unchanged.
- **Acceptance**: `run()` dict has `rationale`; `extract_tool_rationale` present, called by T003; module imports.
- **Deps**: T001.
- **Verify**: `cd services/declarative-runner && python3 -c "import ast; ast.parse(open('workflow_executor.py').read())" && grep -n "extract_tool_rationale\|\"rationale\"" workflow_executor.py`

### T003 [X] — Runner: persist + emit rationale on the member turn boundary
- **Files**: Modify `services/declarative-runner/main.py`
- **Adds**: `_save_memory_turn(..., rationale=None)` appends a third `{"role":"assistant","content":rationale,"message_kind":"rationale"}` message when `rationale` truthy AND `scope=="workflow_run"`. `/chat` reads `result.get("rationale")` → passes it. `/chat/stream` computes `rationale = await workflow_executor.extract_tool_rationale(...)` after the stream loop; if `scope=="workflow_run"` and rationale, yields `event: rationale\ndata: {"content":…}\n\n` before returning + passes `rationale` to `_save_memory_turn`. `scope=="agent"` neither emits nor persists.
- **Acceptance**: `grep` shows the param, the `event: rationale` emit, and both call sites; runner imports.
- **Deps**: T002.
- **Verify**: `cd services/declarative-runner && python3 -c "import ast; ast.parse(open('main.py').read())" && grep -n "rationale" main.py`

---

## Band 1 — Shared pod-SSE reader + single-agent tool chips [2b-0 / 2b-i]

### T004 [X] [P] — registry-api: the ONE pod-SSE reader
- **Files**: Create `services/registry-api/pod_stream.py`
- **Adds**: `async def stream_pod_chat_frames(...)` (signature in plan Key Interfaces). POSTs `{service_url}/chat/stream` with identity headers; yields `agent_start` before first token; translates `text_delta`→`token`, `tool_call_start`→`tool_call{status:ok}` (**do NOT drop — the 2b-i fix**), `tool_call_end`→`tool_call{status:error}` only on error else skip, `rationale`→`rationale`, `error`→`error`, `approval_requested`→passthrough; does NOT yield `done` (reads to EOF for a trailing `rationale`); ConnectError/non-200 → single `error` frame. Author-tags every frame.
- **Acceptance**: `def stream_pod_chat_frames` present; exactly one `tool_call` per invocation (start-only unless end errors). Imported by T005 + T006 (no orphan).
- **Deps**: none.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('pod_stream.py').read())" && grep -n "def stream_pod_chat_frames" pod_stream.py`

### T005 [X] — registry-api: single-agent chat uses the shared reader
- **Files**: Modify `services/registry-api/routers/chat.py`
- **Adds**: re-implement `_proxy_agent_stream` body over `stream_pod_chat_frames(..., scope="agent", author=author)`; serialize each dict via existing `_emit`; append `_emit({"type":"done","run_id":run_id})` after the loop; remove the duplicate `agent_start` emit and the L473 `tool_call_start / tool_call_end are informational` drop. Signature unchanged (`stream_chat`/`stream_deployment_chat` untouched). Net: single-agent chat now emits `tool_call` frames.
- **Acceptance**: L473 drop comment gone; `stream_pod_chat_frames` called; existing `T-S75-007` (author-tagged tokens) still passes.
- **Deps**: T004.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/chat.py').read())" && grep -n "stream_pod_chat_frames" routers/chat.py && ! grep -n "tool_call_start / tool_call_end are informational" routers/chat.py`

---

## Band 2 — Streaming orchestrator + tree projection [2b-0 / 2b-i backend]

### T006 [X] — registry-api: streaming member dispatch + reactive tool persistence
- **Files**: Modify `services/registry-api/workflow_orchestrator.py`
- **Adds**: `_dispatch_stream` (resolve env → `service_url`; iterate `stream_pod_chat_frames(author=agent_name)`; re-yield frames; accumulate tokens; persist each `tool_call` frame as `RunStep(run_id=child_id, name=tool, status=completed|failed, output={"kind":"tool_call","tool":…,"status":…})` via `AsyncSessionLocal`; final `__member_end__` sentinel) and `_run_step_stream` (reproduces `_run_step`'s child-row create / durable-vs-reactive branch / pending-Approval detection / child-row update / `trace_workflow_step`, as a generator emitting `agent_start`/frames/`agent_end` + sentinel; durable branch: `agent_start`→`await _dispatch_durable_member`→`agent_end`+sentinel, no token/tool frames).
- **Acceptance**: reactive member calling one tool leaves a `run_steps` row with `output->>'kind'='tool_call'` under the child id; `_dispatch_stream`/`_run_step_stream`/`__member_end__` present; wired by T007.
- **Deps**: T004.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('workflow_orchestrator.py').read())" && grep -n "_dispatch_stream\|_run_step_stream\|__member_end__" workflow_orchestrator.py`

### T007 [X] — registry-api: mode walkers → generators; `orchestrate` becomes a drain
- **Files**: Modify `services/registry-api/workflow_orchestrator.py`
- **Adds**: convert `_run_sequential_from`/`_run_conditional_from`/`_run_handoff_from`/`_run_supervisor_from` to async generators calling `_run_step_stream` (consume `__member_end__` for routing, re-yield other frames); keep every DB write / routing helper / `_MAX_STEPS` / shape fail-closed identical; thread `conversation_id`. Add `orchestrate_stream(...)` (mode→generator walkers, then `{"type":"done","run_id":parent_run_id}`). Rewrite `orchestrate(...)` to `conversation_id = conversation_id or parent_run_id` then `async for _ in orchestrate_stream(...): pass` inside the existing try/except → `_mark_parent(failed)`. `resume_orchestration` drains the generators locally (console-driven, not streamed).
- **Acceptance**: drain `/runs` produces the same terminal tree for a 2-member sequential workflow (`T-S75-004` regression); `orchestrate_stream` present; `orchestrate` body is the drain loop; ONE graph walk (no forked routing).
- **Deps**: T006.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('workflow_orchestrator.py').read())" && python3 -c "import workflow_orchestrator as w; assert hasattr(w,'orchestrate_stream') and hasattr(w,'orchestrate')"`

### T008 [X] [P] — registry-api: schemas for tree projection + stream request
- **Files**: Modify `services/registry-api/schemas.py`
- **Adds**: `class ToolCallProjection(tool_name:str, status:str)`; `class WorkflowRunStreamRequest(message:str, session_id:str|None=None)`; `AgentRunResponse` gains `tool_calls: list[ToolCallProjection] = Field(default_factory=list)` and `rationale: str | None = None` (non-ORM, defaulted like `trace_url`).
- **Acceptance**: `AgentRunResponse` validates from an ORM `AgentRun` with both fields defaulting empty; `configure_mappers()` unaffected.
- **Deps**: none.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('schemas.py').read())" && python3 -c "import schemas; schemas.WorkflowRunStreamRequest(message='x'); schemas.ToolCallProjection(tool_name='t',status='ok')"`

### T009 [X] — registry-api: stream endpoint + tree projection
- **Files**: Modify `services/registry-api/routers/composite_workflows.py`
- **Adds**: (a) `get_workflow_run_tree` projects per child `tool_calls` (from `RunStep` where `run_id==child.id AND output['kind'].astext=='tool_call'` ordered by `step_number`) and `rationale` (latest `AgentMemory` where `thread_id==conversation_id`, `scope=='workflow_run'`, `message_kind=='rationale'`, `agent_name==child.agent_name`), `conversation_id = parent.session_id or str(run_id)`, extending the existing `_with_trace_url` per-child builder. (b) `POST /{workflow_id}/runs/stream` (`stream_workflow_run`): require_user; load workflow (404/422 like `start_workflow_run`); create parent `AgentRun` (context="playground", trigger_type="api", run_by=caller, `session_id=body.session_id`); open trace; `conversation_id = body.session_id or str(parent.id)`; return `StreamingResponse(_sse(), media_type="text/event-stream", headers={Cache-Control:no-cache, X-Accel-Buffering:no})` draining `orchestrate_stream(...)` and **filtering out `__member_end__`**. In-process only (no orchestrator-pod dispatch — gap-ledgered).
- **Acceptance**: `runs/stream` route registered; tree children carry `tool_calls`/`rationale`; `orchestrate_stream` + both schemas consumed here; endpoint consumed by T014.
- **Deps**: T007, T008.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/composite_workflows.py').read())" && python3 -c "import routers.composite_workflows as c; assert any('runs/stream' in getattr(r,'path','') for r in c.router.routes)"`

### T020 [X] [P] — No-orphan gate: backend symbols
- **Files**: (verification only — no source edits)
- **Adds**: nothing; asserts every new backend symbol has a live caller. Greps: `stream_pod_chat_frames` called in BOTH `routers/chat.py` and `workflow_orchestrator.py`; `_dispatch_stream`/`_run_step_stream` called by the mode walkers; `orchestrate_stream` called by `orchestrate` AND `routers/composite_workflows.py::stream_workflow_run`; the `runs/stream` route present; `tool_calls`/`rationale` set in the tree endpoint; `_extract_tool_rationale` imported by `workflow_executor.py`; `extract_tool_rationale` called by `main.py`.
- **Acceptance**: every grep below returns ≥1 hit (no orphan). Fails the band if any is empty.
- **Deps**: T003, T005, T009.
- **Verify**: `cd services/registry-api && grep -rn "stream_pod_chat_frames" routers/chat.py workflow_orchestrator.py && grep -n "_dispatch_stream\|_run_step_stream" workflow_orchestrator.py && grep -n "orchestrate_stream" workflow_orchestrator.py routers/composite_workflows.py && grep -n "runs/stream\|tool_calls\|rationale" routers/composite_workflows.py && cd ../declarative-runner && grep -n "_extract_tool_rationale" workflow_executor.py && grep -n "extract_tool_rationale" main.py`

---

## Band 3 — Frontend reducers, components, API types [2b-i / 2b-iii / 2b-iv]

### T010 [X] [P] — frontend: pure stream reducers for chips + rationale
- **Files**: Modify `studio/src/lib/chatStream.ts`
- **Adds**: `interface AttributedRich extends Attributed { toolCalls?; rationale? }`; `attachToolCall(messages, author, toolCall, make)` and `attachRationale(messages, author, rationale, make)` — reuse `isOpenAssistantFor`: update the open assistant bubble for `author` immutably, else append `make(author)` seeded with the field. `routeToken`/`openAuthorBubble` untouched; no React import.
- **Acceptance**: `attachToolCall`/`attachRationale` present, pure; consumed by T014 + tested by T015.
- **Deps**: none.
- **Verify**: `cd studio && npx tsc --noEmit -p tsconfig.json 2>&1 | grep chatStream || echo "chatStream typecheck clean"`

### T011 [X] [P] — frontend: ToolCallChip component
- **Files**: Create `studio/src/components/chat/ToolCallChip.tsx`
- **Adds**: `export default function ToolCallChip({ tool, status? })` — mock chip (`Database` icon + `called <code>{tool}</code>`, `inline-flex bg-slate-100 rounded-md px-2 py-1 text-xs text-slate-500`); red tint (`text-red-600`) when `status==="error"`.
- **Acceptance**: renders the tool name; used by `AttributedBubble` (T012).
- **Deps**: none.
- **Verify**: `cd studio && npm run typecheck 2>&1 | tail -3`

### T012 [X] — frontend: AttributedBubble rich slots
- **Files**: Modify `studio/src/components/chat/AttributedBubble.tsx`
- **Adds**: props `avatar?`, `toolCalls?`, `rationale?`, `showRationale?` (default true), `citations?`. Render order: tinted `Bot` avatar next to name → `ToolCallChip` row above content → amber `rationale` box (`Lightbulb`, `text-amber-700 bg-amber-50 border-amber-100`) when truthy AND `showRationale!==false` → existing content box → `citations` chip row (empty in POC-2b). **Degenerate guard**: no author/`showLabel===false`/no rich props → DOM byte-identical to today. Keep `children`.
- **Acceptance**: single-agent render unchanged (Vitest degenerate case); rich slots render only when their prop is set.
- **Deps**: T011.
- **Verify**: `cd studio && npm run typecheck 2>&1 | tail -3`

### T013 [X] [P] — frontend: API types for stream + rich tree children
- **Files**: Modify `studio/src/api/registryApi.ts`
- **Adds**: `AgentRunItem` gains `tool_calls?: { tool_name:string; status:string }[]` and `rationale?: string | null`; `export interface WorkflowStreamFrame {...}`; `export const workflowRunStreamUrl = (workflowId) => \`/api/v1/workflows/${workflowId}/runs/stream\`` (FULL path — the page's `fetch` is not the axios instance). No `triggerWorkflowRun`-style helper.
- **Acceptance**: `WorkflowStreamFrame`/`tool_calls`/`rationale` present; consumed by T014; typecheck clean.
- **Deps**: none.
- **Verify**: `cd studio && npm run typecheck 2>&1 | tail -3`

### T014 [X] — frontend: CatalogChatPage live workflow console
- **Files**: Modify `studio/src/pages/CatalogChatPage.tsx`
- **Adds**: extend `Message` with `toolCalls?`/`rationale?`/`citations?`. **Replace `sendWorkflowMessage`** with a `fetch(POST /api/v1/workflows/{id}/runs/stream, {bearer, body:{message, session_id}})` + `response.body.getReader()` reader splitting on `\n\n`, parsing `data:` JSON to `WorkflowStreamFrame`, driving `agent_start`→`openAuthorBubble`, `token`→`routeToken`, `tool_call`→`attachToolCall`, `rationale`→`attachRationale`, `agent_end`→no-op, `done`→stop, `error`→append. Keep `pollWorkflowResult`/`WorkflowTurn` for reload only; upgrade `WorkflowTurn` to render each child's `tool_calls`/`rationale` via `AttributedBubble` gated on `showRationale`. Console shell (header `"{name} · {N} agents"`, subtitle, blue attribution info-bar + Show-rationale checkbox). Single-agent `sendAgentMessage` gains a `tool_call`→`attachToolCall` branch. Pass `avatar` to member bubbles.
- **Acceptance**: workflow chat opens `/runs/stream` (network), renders per-member bubbles progressively + chips + amber rationale toggled by the checkbox; all new symbols from T009/T010/T012/T013 consumed here; single-agent chat unchanged apart from live chips.
- **Deps**: T009, T010, T012, T013.
- **Verify**: `cd studio && npm run typecheck 2>&1 | tail -3 && grep -n "runs/stream\|attachToolCall\|attachRationale\|showRationale" src/pages/CatalogChatPage.tsx`

### T021 [X] [P] — No-orphan gate: frontend symbols
- **Files**: (verification only — no source edits)
- **Adds**: nothing; asserts each new frontend symbol has a live reader. Greps: `ToolCallChip` imported by `AttributedBubble.tsx` AND `CatalogChatPage.tsx`; `attachToolCall`/`attachRationale` called in `CatalogChatPage.tsx`; `WorkflowStreamFrame`/`workflowRunStreamUrl` referenced in `CatalogChatPage.tsx`; `AttributedBubble` rich slots (`avatar`/`rationale`/`citations`) referenced by the page.
- **Acceptance**: every grep returns ≥1 hit.
- **Deps**: T012, T014.
- **Verify**: `cd studio && grep -rn "ToolCallChip" src/components/chat/AttributedBubble.tsx src/pages/CatalogChatPage.tsx && grep -n "attachToolCall\|attachRationale\|WorkflowStreamFrame\|workflowRunStreamUrl" src/pages/CatalogChatPage.tsx`

---

## Band 4 — Tests (component + backend e2e + browser e2e)

### T015 [X] — frontend: Vitest for slots + reducers
- **Files**: Create `studio/src/components/chat/AttributedBubble.test.tsx`, Create `studio/src/components/chat/ToolCallChip.test.tsx`, Create (or extend) `studio/src/lib/chatStream.test.ts`
- **Adds**: `AttributedBubble.test.tsx` — (a) degenerate single-agent DOM parity, (b) author+avatar renders name+Bot, (c) `toolCalls` renders `ToolCallChip`, (d) `rationale`+`showRationale` toggles amber box, (e) `citations=[]` renders no chip row. `ToolCallChip.test.tsx` — renders tool name; error status tints red. `chatStream.test.ts` — `routeToken`/`openAuthorBubble` route by author (two authors → two bubbles); `attachToolCall`/`attachRationale` attach to the matching author's open bubble and open a new one when the author differs.
- **Acceptance**: `npm run test` green; no existing spec regressed.
- **Deps**: T010, T011, T012.
- **Verify**: `cd studio && npm run test`

### T016 [X] — backend e2e: suite-75 T-S75-009/010/011
- **Files**: Modify `scripts/e2e/suite-75-context-storage.sh`
- **Adds**: reuse Section A's `s75-wa`/`s75-wb` reactive agents + sequential workflow; create a **low-risk** HTTP tool (`risk_level:"low"`) `s75-tool-{suffix}` via `POST /tools` and attach to `s75-wa` via `POST /agents/{name}/tools`. `T-S75-011` (stream parity): `POST /runs/stream` collect frames, assert `agent_start`/`token`/`agent_end` each carry an `author` in `{s75-wa,s75-wb}` + final `done`; then drain `POST /runs` + poll tree, assert same child agent_names. `T-S75-009` (tree tool_calls): after drain, `GET .../tree`, assert `s75-wa` child's `tool_calls` non-empty `{tool_name,status}`. `T-S75-010` (rationale row): `GET /agents/s75-wa/memory?scope=workflow_run&thread_id={run_id}`, assert a `message_kind="rationale"` row for `s75-wa` and the tree child's `rationale` equals it; SKIP (DIAG) if the model produced no reasoning, but FAIL if the row/field plumbing is broken. Follow the `agent_pod_breakage` guard (justified SKIP, never launder broken pods).
- **Acceptance**: `bash -n` clean; the three IDs in the tally; PASS or justified capacity-SKIP against CP1.
- **Deps**: T009 (deployed at CP1).
- **Verify**: `bash -n scripts/e2e/suite-75-context-storage.sh && grep -n "T-S75-009\|T-S75-010\|T-S75-011" scripts/e2e/suite-75-context-storage.sh`

### T017 [X] — browser e2e: rich console Playwright spec
- **Files**: Create `studio/e2e/poc2b-rich-console.spec.ts`
- **Adds**: model on `context-attribution.spec.ts`. `beforeAll` (REST, ADMIN `X-User-Sub: 75c7c8b3-…`): create reactive memory-enabled `researcher`/`answerer`; create a **low-risk** HTTP tool, attach to `researcher`; compose a `sequential` reactive `memory_enabled` workflow, add both members, snapshot `eval_passed:true`, publish, admin-approve → `artifactId`. Test: (1) console shell — header `/· \d+ agents/` + shared-thread subtitle; (2) `page.waitForResponse('/workflows/*/runs/stream')` registered before send; (3) progressive reveal — `researcher` bubble visible while `answerer` absent, then both with avatar+name; (4) a `ToolCallChip` with the researcher's tool under its bubble; (5) rationale toggle — amber box visible, click off hides, on re-shows (SKIP-DIAG if model produced no rationale); (6) save→reload→survives — reload reads tree/`/memory` (`waitForResponse`), bubbles+rationale rehydrate from backend. Capacity boundary: `test.skip` if pods never warm; a completed-but-wrong render → FAIL.
- **Acceptance**: passes or capacity-skips against CP2 Studio; asserts wiring + persistence + `waitForResponse('/runs/stream')`.
- **Deps**: T014 (deployed at CP2).
- **Verify**: `cd studio && npx tsc --noEmit && ls e2e/poc2b-rich-console.spec.ts`

---

## Band 5 — Docs + image bumps

### T018 [P] — docs: experience + gap ledger + design status
- **Files**: Modify `docs/experience/playground.md`, Modify `docs/testing/manual-ui-e2e-test-plan.md`, Modify `docs/design/context-storage-poc-2b-rich-console.md`
- **Adds**: `playground.md` — new workflow-stream SSE frames (`agent_start`/`token`/`tool_call`/`rationale`/`agent_end`/`done`) + single-agent chat now surfaces `tool_call` chips (L473 drop removed). `manual-ui-e2e-test-plan.md` "Known gaps" — durable/HITL members don't token-stream *(deferred, intentional)*; citations slot empty *(deferred POC-4)*; durable-member reload tool-chips not projected *(not-yet-wired, debt)*; `/runs/stream` in-process only *(deferred, intentional)*. `context-storage-poc-2b-rich-console.md` — status → Implemented; fold the four grounding corrections.
- **Acceptance**: all three files updated; each gap tagged deferred-vs-debt.
- **Deps**: none (may run parallel with code).
- **Verify**: `grep -n "runs/stream\|rationale" docs/experience/playground.md`

### T019 — image bumps (all 3 services, 3 files)
- **Files**: Modify `scripts/deploy-cpe2e.sh`, Modify `scripts/deploy-eks.sh`, Modify `charts/agentshield/values.yaml`
- **Adds**: `REGISTRY_API_TAG=0.2.190`, `DECLARATIVE_RUNNER_TAG=0.1.55`, `STUDIO_TAG=0.1.143` in both `deploy-cpe2e.sh` (L266/275/273) and `deploy-eks.sh` (L67/69/70) with POC-2b comment headers; `values.yaml` registry-api `tag:"0.2.190"` (~L597), `declarativeRunnerTag:"0.1.55"` (~L673), studio `tag:"0.1.143"` (~L917). Never reuse a tag.
- **Acceptance**: all three tags identical across all three files.
- **Deps**: T003, T005, T007, T009, T014 (the code the images ship). Bump in the same commit as the code.
- **Verify**: `grep -rn "0.2.190\|0.1.55\|0.1.143" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml`

---

## Checkpoints

Checkpoints are gates, not code tasks. Each writes executable `scripts/`; deploy is **user-gated** (shared-cluster hazard). Order: complete CP1 (backend proven) before CP2 (frontend proven).

### [CP1a] — Backend deploy (after T001–T009, T019)
- **Writes/uses**: `bash scripts/deploy-eks.sh` (EKS test-cluster) — builds/pushes registry-api 0.2.190 + declarative-runner 0.1.55 + studio 0.1.143, applies Helm.
- **Gate to run**: reviewer go (No-Merge-to-Main; shared cluster). Do NOT deploy without it.
- **Exit**: three images rolled out; pods `Ready`; Alembic head still `0064` (no migration).

### [CP1b] — Backend smoke (after [CP1a], T016)
- **Writes**: `scripts/checkpoints/poc-2b-cp1-smoke.sh` — kubectl-exec into the registry-api pod; run `bash scripts/e2e/suite-75-context-storage.sh` and assert `T-S75-009/010/011` are 0 FAIL; additionally curl-assert (jq) that `POST /workflows/{id}/runs/stream` returns `content-type: text/event-stream` and ≥1 `data:` line carries `"type":"agent_start"` with an `author`; assert a drain `POST /runs` yields a matching terminal tree (parity).
- **Gate**: suite-75 exits 0 (PASS/justified-SKIP only); stream emits author-tagged frames; drain parity holds.
- **Verify**: `bash -n scripts/checkpoints/poc-2b-cp1-smoke.sh && bash scripts/checkpoints/poc-2b-cp1-smoke.sh`

### [CP2a] — Frontend deploy (after T010–T015, T019)
- **Writes/uses**: same `bash scripts/deploy-eks.sh` (studio 0.1.143 already built in CP1a; re-run the studio build step if the frontend lands after CP1).
- **Gate to run**: reviewer go.
- **Exit**: studio 0.1.143 serving the new CatalogChatPage.

### [CP2b] — Frontend browser smoke (after [CP2a], T017, T018)
- **Writes**: `scripts/checkpoints/poc-2b-cp2-smoke.sh` — `cd studio && npm run typecheck && npm run test` (Vitest green), then `bash scripts/studio-e2e.sh` filtered to `poc2b-rich-console.spec.ts` (Playwright green or capacity-skip).
- **Gate**: Vitest 100% green; Playwright proves progressive reveal + chip + rationale toggle + save→reload (or capacity-skips with no assertion failure).
- **Verify**: `bash -n scripts/checkpoints/poc-2b-cp2-smoke.sh && bash scripts/checkpoints/poc-2b-cp2-smoke.sh`

---

## Dependency graph (topological, no forward deps)

```
T001 → T002 → T003 ─────────────────────────────┐
T004 → T005                                       │
T004 → T006 → T007 → T009                         │
T008 ─────────────→ T009                          │
T003,T005,T009 → T020 (no-orphan: backend)        │
T010, T011 → T012 → T015                          │
T013 ─┐                                           │
T009,T010,T012,T013 → T014 → T017                 │
T012,T014 → T021 (no-orphan: frontend)            │
T016 (needs T009 deployed @ CP1)                  │
T018 [P]                                          │
T003,T005,T007,T009,T014 ───────────────────────→ T019

CP1 = deploy[CP1a] + smoke[CP1b]  over {T001..T009, T016, T019, T020}
CP2 = deploy[CP2a] + smoke[CP2b]  over {T010..T015, T017, T018, T021}
```

## Counts

- **Implementation tasks: 21** (T001–T021; T020/T021 are no-orphan gate tasks).
- **Checkpoint phases: 4** (`[CP1a]`, `[CP1b]`, `[CP2a]`, `[CP2b]`), grouped as CP1 (backend) + CP2 (frontend).
- **MVP critical path (headline 2b-0 live streaming)**: `T004 → T006 → T007 → T009 → [CP1] → T014 → T017 → [CP2]`, with feeders `T008 → T009` and `T010/T012/T013 → T014`. The rationale branch `T001 → T002 → T003` runs in parallel and merges at T009's tree projection / T019 image bump.

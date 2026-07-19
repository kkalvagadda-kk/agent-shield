# Implementation Plan — POC-2b Rich Multi-Agent Workflow Console

**Spec (authoritative)**: `docs/design/context-storage-poc-2b-rich-console.md`
**Companion**: `docs/design/context-storage-ux-roadmap.md` §3, `docs/design/context-storage-architecture.md`
**Branch**: `worktree-ux-preview-context-storage` — commit ONLY here, never merge to main (Karthik merges manually).
**Live baseline**: `registry-api:0.2.189` / `studio:0.1.142` / `declarative-runner:0.1.54`
**This change ships**: `registry-api:0.2.190` / `studio:0.1.143` / `declarative-runner:0.1.55`

---

## Goal

Turn the catalog workflow chat from spinner-then-dump into a **live multi-speaker console**, matching the Multi-Agent Chat mock (`studio/src/pages/preview/MultiAgentChatPage.tsx`). Five sub-phases:

- **2b-0 (headline)** — live member streaming. Refactor `workflow_orchestrator` so the graph walk is ONE async generator; the existing non-streaming `POST /workflows/{id}/runs` becomes a thin **drain** of that generator; a NEW `POST /workflows/{id}/runs/stream` streams the same frames as SSE. Reactive members stream tokens via the member pod's `/chat/stream` through ONE shared pod-SSE reader; durable members emit `agent_start`→(poll)→`agent_end` (no token frames — accepted asymmetry).
- **2b-i** — tool-call chips. Stop dropping the member pod's `tool_call_start/end` frames (shared reader → `tool_call` frame); persist observed tool calls so reload reproduces them; project `tool_calls[]` onto each run-tree child; render a `ToolCallChip`.
- **2b-ii** — rationale (REUSE the model's own reasoning; NO Haiku). Capture the last tool-calling `AIMessage`'s text at the member turn boundary, persist it as an `agent_memory` row with `message_kind='rationale'`, emit a live `rationale` frame, join it onto each run-tree child; render an amber box + Show-rationale toggle. Non-blocking; empty for tool-less members.
- **2b-iii** — console shell + avatars. Header `<workflow> · N agents`, subtitle, blue attribution info-bar, per-agent `Bot` avatar tinted via `agentColor`.
- **2b-iv** — citation slot. Frontend prop + empty chip row (content deferred to POC-4).

> **Alignment Check:** This serves the context-storage thesis (Agent and Workflow are one executable on a shared substrate) by making the SHARED workflow transcript *visible* — the user watches each governed member speak, call a tool, and hand off in real time, over the same persisted rows POC-1 already writes. The No-Bandaid rule is honored by keeping ONE orchestration graph walk (generator) that both the drain and the stream consume, and ONE pod-SSE reader that both single-agent chat and the workflow stream consume.

---

## Architecture

**Two channels, one renderer.** A **live channel** (new multiplexed SSE) drives the run-in-progress; the **persisted channel** (run-tree + shared transcript) is the source of truth for reload/history and for durable members. Both feed the *same* attributed-bubble reducers (`openAuthorBubble`/`routeToken` + new `attachToolCall`/`attachRationale`), so a live `token{author}` frame and a reloaded tree child both open/extend the same author-keyed bubble.

```
LIVE (in-flight):
  POST /workflows/{id}/runs/stream
    → orchestrate_stream(mode) async-generator walks the graph
        per member:  agent_start{author} → token{author}* → tool_call{author}* → rationale{author} → agent_end{author}
        end:         done{run_id}
    reactive member → _dispatch_stream → stream_pod_chat_frames (ONE pod-SSE reader) → member pod /chat/stream
    durable member  → _dispatch_durable_member (poll)  — agent_start/agent_end only
  CatalogChatPage EventSource → openAuthorBubble/routeToken/attachToolCall/attachRationale

DRAIN (non-stream, unchanged behavior):
  POST /workflows/{id}/runs → orchestrate(mode) → `async for _ in orchestrate_stream(...): pass`
    (all DB writes live INSIDE the generator, so draining == today's behavior)

PERSISTED (reload / history):
  member reactive tool_call frame → RunStep row (run_id=child_id, output.kind='tool_call')   [2b-i]
  member durable tool step        → RunStep row (existing step-update callback)               [exists]
  member rationale                → agent_memory row (scope='workflow_run', message_kind='rationale')  [2b-ii]
  GET /workflows/{id}/runs/{run_id}/tree
    → children[] each gains: tool_calls[] (from run_steps)  +  rationale (from agent_memory)
```

**Key grounding corrections (verified in code, differ from the design doc's prose):**
1. The step table is **`run_steps`** (ORM class `RunStep`), **not** `agent_run_steps`.
2. `run_steps` rows are written **only by the durable `/run` path** (step-update callback). Reactive members write NONE. So the tree `tool_calls` projection must have the **streaming orchestrator persist a `RunStep` per observed `tool_call` frame** (marker `output.kind='tool_call'`) — that is the "streaming is observation over the same writes" invariant made real for reactive members.
3. `_extract_reasoning(state)` reads `messages[-1]` — correct at the HITL *interrupt* (the tool-calling AIMessage is last) but **wrong at the turn boundary** (last message is the final answer). 2b-ii adds `_extract_tool_rationale(state)` that scans for the **last AIMessage that has tool_calls**.
4. `web_search` is a **high-risk** seeded tool → it trips the HITL gate and a *reactive* workflow fails-closed. The live-stream fixtures therefore attach a **low-risk** HTTP tool so the reactive run completes and still shows a chip.

---

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy async (registry-api); httpx SSE client; LangGraph/LangChain (declarative-runner); pytest-free bash+curl e2e (kubectl exec).
- **Frontend**: React 18 + Vite + TailwindCSS + React Query; `EventSource` for SSE; Vitest + React Testing Library (component); Playwright (browser e2e).
- **Infra**: Docker images built by `scripts/deploy-cpe2e.sh` (kind) / `scripts/deploy-eks.sh` (EKS test-cluster); Helm chart `charts/agentshield`.

---

## Constitution Check (CLAUDE.md gates)

| Gate | How this plan satisfies it |
|---|---|
| **DoD #1 — real user journey proven** | T017 Playwright drives the catalog workflow chat: progressive reveal (researcher bubble present while answerer absent, gated on `waitForResponse` of `/runs/stream`), two attributed bubbles w/ avatar+name, a tool chip, rationale toggle flips amber boxes. |
| **DoD #2 — save→reload→assert** | T017 reloads and asserts bubbles + rationale rehydrate from `GET /memory` + tree (not the store). T016 `T-S75-010`/`009` assert the rationale row + tool_calls survive a fresh tree read. |
| **DoD #3 — no orphan code** | Every new symbol has a wired caller in the same plan; each task's Acceptance criteria names the grep. Enumerated in "No-orphan ledger" below. |
| **DoD #4 — vertical slices** | Task order wires one thin path end-to-end (pod frame → orchestrator generator → SSE → CatalogChatPage bubble) before layering chips/rationale/shell. |
| **DoD #5 — honest gap ledger** | T018 updates `docs/testing/manual-ui-e2e-test-plan.md` "Known gaps" (durable members don't token-stream; citations deferred; durable-member reload chips; stream endpoint runs in-process only). |
| **DoD #6 — reason from running product** | Grounding corrections 1–4 above; every task cites verified file:line, not the design doc's line numbers. |
| **No-Bandaid** | ONE graph walk (generator) drained by the non-stream path; ONE pod-SSE reader (`stream_pod_chat_frames`) used by both chat.py and the orchestrator; explicit `scope`/`author` params (no type-sniffing); tool-call persistence uses an explicit `output.kind='tool_call'` marker (no name heuristics). |
| **Post-impl #2 — image bumps ×3 files** | T019 bumps registry-api/declarative-runner/studio in `deploy-cpe2e.sh` + `deploy-eks.sh` + `charts/agentshield/values.yaml`. |
| **Post-impl #3 — experience docs** | T018 updates `docs/experience/playground.md` (chat.py SSE frames changed). |
| **Post-impl #4 — frontend tests** | T015 Vitest; T017 Playwright. Both stay green. |

---

## File Structure

| File | Created/Modified | Responsibility (one line) |
|---|---|---|
| `services/registry-api/pod_stream.py` | **new** | ONE pod-SSE reader `stream_pod_chat_frames` — POSTs member pod `/chat/stream`, yields normalized frame dicts (agent_start/token/tool_call/rationale/approval_requested/error). |
| `services/registry-api/routers/chat.py` | modify | `_proxy_agent_stream` re-implemented over `stream_pod_chat_frames` (stops dropping tool_call at L473; now emits `tool_call`; passes `rationale` through). |
| `services/registry-api/workflow_orchestrator.py` | modify | Add `_dispatch_stream` + `_run_step_stream` + generator variants of the 4 mode walkers + `orchestrate_stream`; `orchestrate` becomes a drain; reactive `tool_call`→`RunStep` persistence; thread `conversation_id`. |
| `services/registry-api/routers/composite_workflows.py` | modify | New `POST /{id}/runs/stream` (SSE); tree endpoint projects `tool_calls` (run_steps) + `rationale` (agent_memory) per child. |
| `services/registry-api/schemas.py` | modify | `AgentRunResponse` +`tool_calls`/`rationale`; new `ToolCallProjection`, `WorkflowRunStreamRequest`. |
| `services/declarative-runner/workflow_executor.py` | modify | `run()` returns `rationale`; new `extract_tool_rationale(thread_id)` reads checkpoint state. |
| `services/declarative-runner/main.py` | modify | `/chat` + `/chat/stream` capture rationale; `_save_memory_turn` gains `rationale` param (writes `message_kind='rationale'` row); `/chat/stream` emits a `rationale` SSE frame (workflow_run scope only). |
| `sdk/agentshield_sdk/graph_builder.py` | modify | Add `_extract_tool_rationale(state)` (last AIMessage WITH tool_calls). |
| `studio/src/lib/chatStream.ts` | modify | Add `AttributedRich` + pure reducers `attachToolCall`, `attachRationale`. |
| `studio/src/components/chat/ToolCallChip.tsx` | **new** | Presentational "called `<tool>`" chip. |
| `studio/src/components/chat/AttributedBubble.tsx` | modify | Optional `avatar`, `toolCalls`, `rationale`+`showRationale`, `citations` slots; degenerate single-agent render unchanged. |
| `studio/src/api/registryApi.ts` | modify | `AgentRunItem` +`tool_calls?`/`rationale?`; `WorkflowStreamFrame` type; `workflowRunStreamUrl` helper. |
| `studio/src/pages/CatalogChatPage.tsx` | modify | Workflow branch → `EventSource` on `/runs/stream` reusing reducers; console shell (header/subtitle/info-bar) + Show-rationale toggle; single-agent path handles `tool_call`; `WorkflowTurn` renders chips/rationale from tree. |
| `studio/src/components/chat/AttributedBubble.test.tsx` | **new** | Vitest: avatar/chip/rationale(toggle)/citation slots + degenerate case. |
| `studio/src/components/chat/ToolCallChip.test.tsx` | **new** | Vitest: chip renders tool + status. |
| `studio/src/lib/chatStream.test.ts` | modify (or new if absent) | Vitest: `routeToken`/`openAuthorBubble`/`attachToolCall`/`attachRationale` route by author. |
| `studio/e2e/context-rich-console.spec.ts` | **new** | Playwright: progressive reveal + avatars + tool chip + rationale toggle + save→reload. |
| `scripts/e2e/suite-75-context-storage.sh` | modify | Add `T-S75-009` (tree tool_calls), `T-S75-010` (rationale row), `T-S75-011` (stream parity). |
| `scripts/checkpoints/poc-2b-cp1-smoke.sh` | **new** | CP1 backend smoke (curl/kubectl/jq): stream endpoint emits author-tagged frames; tree carries tool_calls+rationale. |
| `scripts/checkpoints/poc-2b-cp2-smoke.sh` | **new** | CP2 frontend smoke: Playwright spec + Vitest gate. |
| `scripts/deploy-cpe2e.sh` | modify | Bump all 3 image tags + comment headers. |
| `scripts/deploy-eks.sh` | modify | Bump all 3 image tags + comment headers. |
| `charts/agentshield/values.yaml` | modify | Bump registry-api/declarativeRunnerTag/studio tags to match. |
| `docs/experience/playground.md` | modify | Document new workflow-stream SSE frames + rich console. |
| `docs/testing/manual-ui-e2e-test-plan.md` | modify | Known-gaps ledger entries. |
| `docs/design/context-storage-poc-2b-rich-console.md` | modify | Mark status Implemented; fold grounding corrections. |

Every file above appears in a task below, and every task references only files above. (File Structure ⇄ Tasks bijection verified.)

---

## Key Interfaces (exact signatures)

### Backend — registry-api

```python
# services/registry-api/pod_stream.py  (NEW)
from typing import AsyncGenerator
import httpx

async def stream_pod_chat_frames(
    service_url: str,
    *,
    message: str,
    thread_id: str,
    conversation_id: str,
    scope: str,                 # "agent" | "workflow_run"
    author: str,                # tags every yielded frame
    trace_id: str | None = None,
    user_id: str = "",
    user_team: str = "",
    deployment_id: str = "",
    auto_approve: bool = False,
) -> AsyncGenerator[dict, None]:
    """POST {service_url}/chat/stream and yield NORMALIZED frame dicts, each carrying
    author=<author>. Frame types yielded (NO run-level 'done' — the caller owns that):
        {"type":"agent_start","author":author}
        {"type":"token","author":author,"content":str}
        {"type":"tool_call","author":author,"tool":str,"status":"ok"|"error"}   # from tool_call_start/tool_call_end
        {"type":"rationale","author":author,"content":str}                       # from the runner's new 'rationale' event
        {"type":"approval_requested","author":author, ...payload}
        {"type":"error","author":author,"message":str}
    Reads the pod stream to natural EOF (a trailing 'rationale' after the pod's internal
    'done' is captured). ConnectError/other → yields a single error frame."""
```

```python
# services/registry-api/workflow_orchestrator.py  (additions)
async def _dispatch_stream(
    agent_name: str, team: str, message: str, thread_id: str,
    conversation_id: str, scope: str, child_id: str,
) -> AsyncGenerator[dict, None]:
    """Reactive member: resolve env → service_url; iterate stream_pod_chat_frames(author=agent_name);
    re-yield each frame; persist each 'tool_call' frame as a RunStep (marker output.kind='tool_call');
    accumulate token content. Yields a FINAL sentinel dict:
        {"type":"__member_end__","author":agent_name,"status":"completed"|"failed"|"awaiting_approval",
         "output":str|None,"error":str|None}
    (the sentinel is consumed by the mode walker for routing; it is NOT sent to the client)."""

async def _run_step_stream(
    parent_run_id: str, team: str, agent_name: str, current_input: str, conversation_id: str,
) -> AsyncGenerator[dict, None]:
    """Create the child AgentRun (same as _run_step), then:
      durable member → yield {"type":"agent_start","author"}; await _dispatch_durable_member(...);
                       yield {"type":"agent_end","author"}; yield __member_end__ sentinel (no token frames).
      reactive member → yield {"type":"agent_start","author"}; async-for frame in _dispatch_stream(...):
                       re-yield member frames; on __member_end__ update the child row + author the parent
                       trace span (as _run_step does), then yield {"type":"agent_end","author"} + __member_end__.
    Pending-Approval detection + child-row writes + trace_workflow_step are preserved from _run_step."""

async def orchestrate_stream(
    parent_run_id: str, team: str, workflow_id: str, input_message: str, mode: str,
    conversation_id: str, shape: str = "durable",
) -> AsyncGenerator[dict, None]:
    """The ONE graph walk as a generator. Dispatches to the generator mode-walkers
    (_run_sequential_from / _run_conditional_from / _run_handoff_from / _run_supervisor_from —
    all converted to async generators). Yields member frames + a final {"type":"done","run_id"}.
    All DB writes (_mark_parent/_save_checkpoint/etc.) happen INSIDE the walkers."""

async def orchestrate(
    parent_run_id: str, team: str, workflow_id: str, input_message: str, mode: str,
    shape: str = "durable", conversation_id: str | None = None,
) -> None:
    """DRAIN. `conversation_id` defaults to parent_run_id. Behavior byte-for-byte as today:
        async for _ in orchestrate_stream(parent_run_id, team, workflow_id, input_message, mode,
                                           conversation_id or parent_run_id, shape): pass
    Wrapped in the existing try/except that fails the parent on crash."""
```

```python
# services/registry-api/schemas.py  (additions)
class ToolCallProjection(BaseModel):
    tool_name: str
    status: str            # "ok" | "error" (projected from run_steps)

class WorkflowRunStreamRequest(BaseModel):
    message: str
    session_id: str | None = None

# AgentRunResponse gains (non-ORM, set manually like trace_url):
#     tool_calls: list[ToolCallProjection] = Field(default_factory=list)
#     rationale: str | None = None
```

```python
# services/registry-api/routers/composite_workflows.py  (new endpoint)
@router.post("/{workflow_id}/runs/stream")   # returns StreamingResponse(media_type="text/event-stream")
async def stream_workflow_run(
    workflow_id: uuid.UUID, body: WorkflowRunStreamRequest,
    caller: dict = Depends(require_user), db: AsyncSession = Depends(get_db),
) -> StreamingResponse: ...
```

### Backend — declarative-runner / sdk

```python
# sdk/agentshield_sdk/graph_builder.py  (NEW function)
def _extract_tool_rationale(state: Any) -> str:
    """Turn-boundary rationale: the text content of the LAST AIMessage that HAS tool_calls
    (the one-sentence 'why' produced before the tool ran). Empty when no message had tool_calls
    (tool-less members) or the text block is empty. Reuses _extract_reasoning's text-join logic;
    differs ONLY in message selection (last-with-tool_calls vs messages[-1])."""

# services/declarative-runner/workflow_executor.py
#   run(...) return dict gains "rationale": _extract_tool_rationale(result)   # result = graph output state
async def extract_tool_rationale(self, thread_id: str) -> str:
    """Read the final checkpoint state for thread_id (self.graph.aget_state) and return
    _extract_tool_rationale(state.values). Best-effort → '' on any error (never raises)."""

# services/declarative-runner/main.py
async def _save_memory_turn(  # extended
    agent_name, conversation_id, user_msg, assistant_msg, user_id,
    scope="agent", workflow_run_id=None, deployment_id="",
    author_agent_name=None, message_kind="agent_output",
    rationale: str | None = None,   # NEW — when set AND scope=="workflow_run", append a
                                    # {"role":"assistant","content":rationale,"message_kind":"rationale"} message
) -> None: ...
```

### Frontend

```ts
// studio/src/lib/chatStream.ts  (additions)
export interface AttributedRich extends Attributed {
  toolCalls?: { tool_name: string; status: string }[];
  rationale?: string | null;
}
export function attachToolCall<M extends AttributedRich>(
  messages: M[], author: string | undefined,
  toolCall: { tool_name: string; status: string }, make: (author?: string) => M,
): M[];   // appends toolCall to the open assistant bubble for author (or opens one)
export function attachRationale<M extends AttributedRich>(
  messages: M[], author: string | undefined, rationale: string, make: (author?: string) => M,
): M[];   // sets rationale on the open assistant bubble for author (or opens one)

// studio/src/components/chat/AttributedBubble.tsx  (props added)
export interface AttributedBubbleProps {
  role: string; content: string; author?: string; showLabel?: boolean;
  streaming?: boolean; children?: ReactNode;
  avatar?: boolean;                                    // render a tinted Bot avatar
  toolCalls?: { tool_name: string; status: string }[]; // ToolCallChip row above content
  rationale?: string | null;                           // amber box above content
  showRationale?: boolean;                             // default true; gates the amber box
  citations?: { source: string; kb: string }[];        // empty in POC-2b (slot only)
}

// studio/src/components/chat/ToolCallChip.tsx  (NEW)
export default function ToolCallChip(props: { tool: string; status?: string }): JSX.Element;

// studio/src/api/registryApi.ts  (additions)
export interface WorkflowStreamFrame {
  type: "agent_start" | "token" | "tool_call" | "rationale" | "agent_end" | "done" | "error";
  author?: string; content?: string; tool?: string; status?: string;
  run_id?: string; message?: string;
}
export const workflowRunStreamUrl = (workflowId: string): string; // returns FULL "/api/v1/workflows/{id}/runs/stream" (fetch POST target; auth via Authorization header, not a token query param)
// AgentRunItem gains: tool_calls?: { tool_name: string; status: string }[]; rationale?: string | null;
```

> **Frame transport note (load-bearing):** `EventSource` is GET-only, but `/runs/stream` needs a POST body (`message`). CatalogChatPage opens the stream via `fetch(POST, {headers:{Accept:text/event-stream}})` + a `ReadableStream` reader that parses `data:` lines (the same shape EventSource would deliver), NOT `new EventSource`. The single-agent path keeps `EventSource` (its start endpoint is a separate POST that returns a GET stream_url). This is called out so the two surfaces don't accidentally share the wrong transport.

---

## No-orphan ledger (DoD #3 — every new symbol has a wired caller)

| New symbol | Wired caller (same plan) |
|---|---|
| `stream_pod_chat_frames` | `chat.py::_proxy_agent_stream` (T005) + `workflow_orchestrator._dispatch_stream` (T006) |
| `_extract_tool_rationale` | `workflow_executor.run()` + `extract_tool_rationale` (T002) |
| `extract_tool_rationale` (method) | `main.py::/chat/stream` handler (T003) |
| `_save_memory_turn(rationale=...)` | `main.py::/chat` + `/chat/stream` (T003) |
| runner `rationale` SSE event | `stream_pod_chat_frames` reads it (T004); `_dispatch_stream` re-yields (T006) |
| `_dispatch_stream` / `_run_step_stream` | mode-walker generators (T006/T007) |
| `orchestrate_stream` | `orchestrate` drain (T007) + `stream_workflow_run` endpoint (T009) |
| `AgentRunResponse.tool_calls`/`.rationale` | set in tree endpoint (T009); read by `WorkflowTurn` (T014) |
| `ToolCallProjection` / `WorkflowRunStreamRequest` | tree endpoint + stream endpoint (T008→T009) |
| `POST /{id}/runs/stream` | `CatalogChatPage` fetch-stream (T014) |
| `attachToolCall` / `attachRationale` | `CatalogChatPage` frame handlers (T014); Vitest (T015) |
| `ToolCallChip` | `AttributedBubble` toolCalls slot (T012) + `WorkflowTurn` (T014) |
| `AttributedBubble` avatar/rationale/citations | `CatalogChatPage`/`WorkflowTurn` (T014) |
| `WorkflowStreamFrame` / `workflowRunStreamUrl` / `AgentRunItem.tool_calls` | `CatalogChatPage` (T014) |

---

## Tasks

Ordering is dependency-topological; `[P]` marks tasks whose file sets are disjoint from other `[P]` tasks in the same band and may run in parallel. No task depends on a later-numbered task.

---

### T001 [P] — SDK: turn-boundary rationale extractor
- **Files**: `sdk/agentshield_sdk/graph_builder.py`
- **Interface contract**: add `def _extract_tool_rationale(state: Any) -> str` (signature in Key Interfaces). Select the **last** message in `state["messages"]` for which `getattr(msg, "tool_calls", None)` is a non-empty list; return its text content joined exactly as `_extract_reasoning` does (list-of-blocks → join `type=="text"` blocks; else `str(content)`), `.strip()`. Return `""` when no message has tool_calls or state is malformed. Do NOT modify `_extract_reasoning` (HITL still uses it).
- **Acceptance criteria**: `grep -n "_extract_tool_rationale" sdk/agentshield_sdk/graph_builder.py` shows the def; it is imported by `workflow_executor.py` in T002 (no orphan). For a state whose messages are `[Human, AI(tool_calls=[web_search], content="Let me search…"), Tool, AI(content="The answer is…")]`, returns `"Let me search…"` (NOT the final answer). For a tool-less state returns `""`.
- **Dependencies**: none.
- **Test cases**: unit-style asserted inside T002's runner + T016 `T-S75-010`. Also: `python3 -c "import ast; ast.parse(open('sdk/agentshield_sdk/graph_builder.py').read())"`.
- **Verification command**: `cd sdk && python3 -c "import ast; ast.parse(open('agentshield_sdk/graph_builder.py').read())" && grep -n "_extract_tool_rationale" agentshield_sdk/graph_builder.py`

### T002 — Runner: expose rationale from the executor
- **Files**: `services/declarative-runner/workflow_executor.py`
- **Interface contract**: In `run()`, after building `result = await self.graph.ainvoke(...)`, add `rationale = _extract_tool_rationale(result)` and return `{"response": out_scan.clean_text, "thread_id": thread_id, "rationale": rationale}` (import `_extract_tool_rationale` from `agentshield_sdk.graph_builder`). Add `async def extract_tool_rationale(self, thread_id: str) -> str` that does `state = await self.graph.aget_state({"configurable": {"thread_id": thread_id}})` then `return _extract_tool_rationale(getattr(state, "values", {}) or {})`, wrapped in try/except returning `""`. Do NOT change `run_streamed`'s yielded chunks.
- **Acceptance criteria**: `run()` return dict has `rationale`; `extract_tool_rationale` present and called by `main.py` in T003. Mappers/import: importing the module succeeds.
- **Dependencies**: T001.
- **Test cases**: exercised by T016 `T-S75-010` (rationale row appears for a tool-using member).
- **Verification command**: `cd services/declarative-runner && python3 -c "import ast; ast.parse(open('workflow_executor.py').read())" && grep -n "extract_tool_rationale\|\"rationale\"" workflow_executor.py`

### T003 — Runner: persist + emit rationale on the member turn boundary
- **Files**: `services/declarative-runner/main.py`
- **Interface contract**:
  - Extend `_save_memory_turn(..., rationale: str | None = None)`. When `rationale` is truthy AND `scope == "workflow_run"`, append a third message `{"role": "assistant", "content": rationale, "message_kind": "rationale"}` to the POSTed `messages` list (order: user, agent_output, rationale). Keep the existing fail-loud non-2xx logging.
  - `/chat`: read `rationale = result.get("rationale")` (when `result` is a dict) and pass `rationale=rationale` into the `_save_memory_turn` task.
  - `/chat/stream`: after the `async for chunk in run_streamed(...)` loop closes, compute `rationale = await workflow_executor.extract_tool_rationale(req.thread_id or conversation_id)`; if `req.scope == "workflow_run"` and rationale, `yield` a `rationale` SSE frame **before** the generator returns: `f"event: rationale\ndata: {json.dumps({'content': rationale})}\n\n"`; pass `rationale=rationale` into the `_save_memory_turn` task. (For `scope=='agent'` — single-agent chat — do NOT emit or persist rationale.)
- **Acceptance criteria**: `grep -n "rationale" services/declarative-runner/main.py` shows the param, the `event: rationale` emit, and both call sites. No orphan (rationale flows to `_save_memory_turn` + the SSE frame). Runner still passes syntax + import.
- **Dependencies**: T002.
- **Test cases**: T016 `T-S75-010`; T011 stream parity (a `rationale` frame appears for the tool-using member).
- **Verification command**: `cd services/declarative-runner && python3 -c "import ast; ast.parse(open('main.py').read())" && grep -n "rationale" main.py`

### T004 [P] — registry-api: the ONE pod-SSE reader
- **Files**: `services/registry-api/pod_stream.py` (new)
- **Interface contract**: implement `stream_pod_chat_frames(...)` (full signature in Key Interfaces). POST `{service_url}/chat/stream` with body `{"message","thread_id","conversation_id","scope"}` and headers (`X-AgentShield-Trace-ID`, `x-user-sub`, `x-agent-team`, `x-deployment-id`, and `x-agentshield-auto-approve: "true"` when `auto_approve`). Yield `{"type":"agent_start","author"}` before the first token. Parse named SSE events line-by-line (`event:`/`data:`/blank) exactly as `chat.py` does today; translate:
  - `text_delta` → `{"type":"token","author","content"}`
  - `tool_call_start` → `{"type":"tool_call","author","tool":payload.get("tool") or payload.get("tool_name",""),"status":"ok"}` **(do NOT drop — this is the 2b-i fix)**. `tool_call_end` → if it carries `error`/`status=="error"`, emit `{"type":"tool_call",...,"status":"error"}`; otherwise skip (start already emitted the chip; keep one chip per call).
  - `rationale` → `{"type":"rationale","author","content"}`
  - `error` → `{"type":"error","author","message"}`
  - `approval_requested` → `{"type":"approval_requested","author",**payload}`
  - `done` → do NOT yield (read continues to EOF so a trailing `rationale` is captured).
  On `httpx.ConnectError`/non-200/other → yield a single `{"type":"error","author","message":...}`.
- **Acceptance criteria**: `grep -n "def stream_pod_chat_frames" services/registry-api/pod_stream.py`; imported by chat.py (T005) and workflow_orchestrator.py (T006) — no orphan. Emits exactly one `tool_call` frame per tool invocation (start-only, unless end reports error).
- **Dependencies**: none (independent of T001–T003).
- **Test cases**: T011 stream parity; T016 `T-S75-009` relies on the persisted RunStep the orchestrator writes from this frame.
- **Verification command**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('pod_stream.py').read())"`

### T005 — registry-api: single-agent chat uses the shared reader (stop dropping tool frames)
- **Files**: `services/registry-api/routers/chat.py`
- **Interface contract**: Re-implement `_proxy_agent_stream` body to iterate `stream_pod_chat_frames(service_url, message=message, thread_id=conversation_id, conversation_id=conversation_id, scope="agent", author=author, trace_id=trace_id, user_id=user_id, user_team=user_team, deployment_id=deployment_id)` and serialize each dict via the existing `_emit`. After the async-for completes (or on the error/connect branches), append `_emit({"type":"done","run_id":run_id})`. The pre-existing `agent_start` emit is now produced by the reader — remove the duplicate. Keep the outer `httpx.ConnectError`/`CancelledError`/`Exception` guards (map to error+done). Net effect: single-agent chat now emits `tool_call` frames (the L473 drop is gone); `rationale` frames are not produced for `scope=="agent"` and simply never arrive.
- **Acceptance criteria**: `grep -n "tool_call_start / tool_call_end are informational" services/registry-api/routers/chat.py` returns nothing (comment/drop removed); `grep -n "stream_pod_chat_frames" routers/chat.py` shows the call. `_proxy_agent_stream` signature unchanged (callers `stream_chat`/`stream_deployment_chat` untouched). T-S75-007 (existing) still passes (author-tagged token frames).
- **Dependencies**: T004.
- **Test cases**: existing `T-S75-007`; T017 asserts a single-agent tool chip is possible (covered indirectly). Manual: existing playground/consumer chat still streams.
- **Verification command**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/chat.py').read())" && grep -n "stream_pod_chat_frames" routers/chat.py`

### T006 — registry-api: streaming member dispatch + reactive tool persistence
- **Files**: `services/registry-api/workflow_orchestrator.py`
- **Interface contract**: Add `_dispatch_stream` and `_run_step_stream` (signatures in Key Interfaces). `_dispatch_stream`:
  - Resolve `environment` via `_resolve_agent_environment`; build `service_url = f"http://{agent_name}-{environment}.{_team_namespace(team)}.svc.cluster.local:8080"`.
  - `async for frame in stream_pod_chat_frames(service_url, message=message, thread_id=thread_id, conversation_id=conversation_id, scope=scope, author=agent_name)`: re-`yield frame`; when `frame["type"]=="token"` append `frame["content"]` to an accumulator; when `frame["type"]=="tool_call"` persist a `RunStep(run_id=child_id, step_number=<next>, name=frame["tool"], status="completed" if frame["status"]=="ok" else "failed", output={"kind":"tool_call","tool":frame["tool"],"status":frame["status"]}, started_at=now, completed_at=now)` via `AsyncSessionLocal`.
  - After the loop, yield the `__member_end__` sentinel with `status="completed"` and `output="".join(accumulator)` (or `status="failed"`/`error` if an error frame was seen).
  - `_run_step_stream` reproduces `_run_step`'s child-row creation, durable vs reactive branch, pending-Approval detection, child-row update, and `trace_workflow_step` span — but as a generator emitting `agent_start`/member-frames/`agent_end` and the `__member_end__` sentinel. Durable branch: emit `agent_start`, `await _dispatch_durable_member(...)`, update child row, `trace_workflow_step`, emit `agent_end` + sentinel (no token/tool_call/rationale frames).
- **Acceptance criteria**: For a reactive member calling one tool, a `run_steps` row with `output->>'kind'='tool_call'` exists under the child id after the stream. `grep -n "_dispatch_stream\|_run_step_stream\|__member_end__" workflow_orchestrator.py`. Symbols wired by T007 (no orphan).
- **Dependencies**: T004.
- **Test cases**: T016 `T-S75-009` (tree children carry tool_calls), `T-S75-011` (author-tagged frames).
- **Verification command**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('workflow_orchestrator.py').read())" && grep -n "_dispatch_stream\|_run_step_stream" workflow_orchestrator.py`

### T007 — registry-api: mode walkers → generators; `orchestrate` becomes a drain
- **Files**: `services/registry-api/workflow_orchestrator.py`
- **Interface contract**: Convert `_run_sequential_from`, `_run_conditional_from`, `_run_handoff_from`, `_run_supervisor_from` into async generators that call `_run_step_stream(...)` instead of `_run_step(...)`: `async for frame in _run_step_stream(...): if frame["type"]=="__member_end__": status_val, output, err = frame["status"], frame["output"], frame["error"] else: yield frame`. All existing DB writes (`_mark_parent`, `_save_checkpoint`, `_park_or_fail`, `_fail_parent`), routing decisions (`_conditional_next`/`_handoff_next`/`_parse_next_agent`), the `_MAX_STEPS` cap, and the reactive/durable `shape` fail-closed logic stay identical — only the member-call becomes a generator drain that also re-yields frames. Thread `conversation_id` from the entry generators to `_run_step_stream`. Add `orchestrate_stream(...)` (routes mode → the generator walkers, then yields `{"type":"done","run_id":parent_run_id}`). Rewrite `orchestrate(...)` to derive `conversation_id = conversation_id or parent_run_id`, then `async for _ in orchestrate_stream(...): pass`, keeping the outer try/except → `_mark_parent(failed)`. `resume_orchestration` calls the generator walkers via a local drain helper (resume is console-driven, not streamed). Convert `orchestrate_graph_sequential`/`orchestrate_conditional`/`orchestrate_handoff`/`orchestrate_supervisor` to `async def ... (yield)` generators (they currently `_mark_parent("running")` then call `_run_*_from`) so `orchestrate_stream` can `async for ... yield` through them; `orchestrate_sequential` (legacy) may keep draining via `orchestrate`.
- **Acceptance criteria**: `POST /workflows/{id}/runs` (drain) still yields the same terminal tree as before for a 2-member sequential workflow (regression via existing `T-S75-004`). `grep -n "orchestrate_stream" workflow_orchestrator.py` shows the entry; `orchestrate` body is the drain loop. No forked graph logic (routing helpers referenced from ONE place).
- **Dependencies**: T006.
- **Test cases**: `T-S75-004` (unchanged behavior), `T-S75-011` (drain parity), `T-S75-005` (durable resume regression) all still green.
- **Verification command**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('workflow_orchestrator.py').read())" && python3 -c "import workflow_orchestrator as w; assert hasattr(w,'orchestrate_stream') and hasattr(w,'orchestrate')"`

### T008 [P] — registry-api: schemas for tree projection + stream request
- **Files**: `services/registry-api/schemas.py`
- **Interface contract**: Add `class ToolCallProjection(BaseModel)` (`tool_name: str`, `status: str`) and `class WorkflowRunStreamRequest(BaseModel)` (`message: str`, `session_id: str | None = None`). Add to `AgentRunResponse`: `tool_calls: list[ToolCallProjection] = Field(default_factory=list)` and `rationale: str | None = None` (non-ORM fields — `from_attributes` leaves them at default; the tree endpoint sets them explicitly).
- **Acceptance criteria**: `AgentRunResponse` validates from an ORM `AgentRun` with the two fields defaulting empty (existing `/runs` list endpoint keeps returning `tool_calls: []`); `configure_mappers()` unaffected.
- **Dependencies**: none.
- **Test cases**: covered by T009's tree tests.
- **Verification command**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('schemas.py').read())" && python3 -c "import schemas; schemas.WorkflowRunStreamRequest(message='x'); schemas.ToolCallProjection(tool_name='t',status='ok')"`

### T009 — registry-api: stream endpoint + tree projection
- **Files**: `services/registry-api/routers/composite_workflows.py`
- **Interface contract**:
  - **Tree endpoint** `get_workflow_run_tree`: compute `conversation_id = parent.session_id or str(run_id)`. For each child, project `tool_calls` by selecting `RunStep` rows where `RunStep.run_id == child.id` and `RunStep.output["kind"].astext == "tool_call"` ordered by `step_number` → `[ToolCallProjection(tool_name=s.name, status=(s.output or {}).get("status","ok"))]`; set `resp.tool_calls`. Project `rationale` by selecting the latest `AgentMemory` where `thread_id == conversation_id`, `scope == "workflow_run"`, `message_kind == "rationale"`, `agent_name == child.agent_name` (order by `message_index` desc, limit 1) → `resp.rationale = row.content or None`. Reuse the existing `_with_trace_url` per-child builder (extend it to set both new fields).
  - **New endpoint** `POST /{workflow_id}/runs/stream` (`stream_workflow_run`): require_user; load workflow (404/422 as `start_workflow_run` does); resolve `member_names`; create the parent `AgentRun` exactly as `start_workflow_run` (context="playground", trigger_type="api", run_by=caller sub) but ALSO set `parent.session_id = body.session_id` when provided; open the Langfuse trace; commit. Compute `conversation_id = body.session_id or str(parent.id)`. Return `StreamingResponse(_sse(), media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})` where `_sse()` does `async for frame in orchestrate_stream(str(parent.id), wf.team, str(workflow_id), body.message, wf.orchestration, conversation_id, wf.execution_shape): yield f"data: {json.dumps(frame)}\n\n"` filtering out the internal `__member_end__` sentinel (never leaks to the client). This endpoint runs the in-process orchestrator only (no production-orchestrator-pod dispatch — gap-ledgered).
- **Acceptance criteria**: `grep -n "runs/stream" routers/composite_workflows.py`; `grep -n "tool_calls\|rationale" routers/composite_workflows.py`. Endpoint registered (FastAPI picks up router). Tree children carry `tool_calls`/`rationale`. No orphan (`orchestrate_stream` + schemas consumed here; endpoint consumed by T014).
- **Dependencies**: T007, T008.
- **Test cases**: `T-S75-009`, `T-S75-010`, `T-S75-011`.
- **Verification command**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('routers/composite_workflows.py').read())" && python3 -c "import routers.composite_workflows as c; assert any('runs/stream' in getattr(r,'path','') for r in c.router.routes)"`

### T010 [P] — frontend: pure stream reducers for chips + rationale
- **Files**: `studio/src/lib/chatStream.ts`
- **Interface contract**: Add `AttributedRich` interface and `attachToolCall`/`attachRationale` (signatures in Key Interfaces). Both reuse `isOpenAssistantFor`: if the last bubble is an open assistant bubble for `author`, immutably update it (append to `toolCalls` / set `rationale`); else append `make(author)` seeded with the field. Keep existing `routeToken`/`openAuthorBubble` untouched.
- **Acceptance criteria**: pure (no React import); `grep -n "attachToolCall\|attachRationale" studio/src/lib/chatStream.ts`. Consumed by CatalogChatPage (T014) + tested (T015).
- **Dependencies**: none.
- **Test cases**: T015 `chatStream.test.ts`.
- **Verification command**: `cd studio && npx tsc --noEmit -p tsconfig.json 2>&1 | grep chatStream || echo "chatStream typecheck clean"`

### T011 [P] — frontend: ToolCallChip component
- **Files**: `studio/src/components/chat/ToolCallChip.tsx` (new)
- **Interface contract**: `export default function ToolCallChip({ tool, status }: { tool: string; status?: string })`. Renders the mock's chip (mock lines 56–60): `<Database size={11}/> called <code>{tool}</code>`, inline-flex, `bg-slate-100 rounded-md px-2 py-1 text-xs text-slate-500`. When `status === "error"`, tint red (`text-red-600`). Icon from `lucide-react` `Database`.
- **Acceptance criteria**: `grep -n "ToolCallChip" studio/src/components/chat/ToolCallChip.tsx`; used by AttributedBubble (T012). Renders the tool name.
- **Dependencies**: none.
- **Test cases**: T015 `ToolCallChip.test.tsx`.
- **Verification command**: `cd studio && npm run typecheck 2>&1 | tail -3`

### T012 — frontend: AttributedBubble rich slots
- **Files**: `studio/src/components/chat/AttributedBubble.tsx`
- **Interface contract**: Extend props (see Key Interfaces). Render order inside the bubble column: (1) if `avatar` and labelled, a tinted `Bot` avatar next to the name header (use `agentColor(author).text` for the icon color, matching mock lines 50–54); (2) `toolCalls?.map` → `<ToolCallChip>` row ABOVE the content box; (3) when `rationale` truthy AND `showRationale !== false`, the amber box (mock lines 62–67: `Lightbulb` icon, `text-amber-700 bg-amber-50 border-amber-100`); (4) the existing content box; (5) `citations?.length` → chip row (mock lines 73–81) — empty in POC-2b. **Degenerate guard**: when `author` is undefined / `showLabel===false` and no `avatar`/`toolCalls`/`rationale`/`citations`, the DOM is byte-identical to today (single-agent). Keep the `children` slot.
- **Acceptance criteria**: existing single-agent render unchanged (Vitest degenerate case green); new slots render only when their prop is set. `grep -n "ToolCallChip\|rationale\|avatar\|citations" studio/src/components/chat/AttributedBubble.tsx`.
- **Dependencies**: T011.
- **Test cases**: T015 `AttributedBubble.test.tsx`.
- **Verification command**: `cd studio && npm run typecheck 2>&1 | tail -3`

### T013 [P] — frontend: API types for stream + rich tree children
- **Files**: `studio/src/api/registryApi.ts`
- **Interface contract**: Add to `AgentRunItem`: `tool_calls?: { tool_name: string; status: string }[];` and `rationale?: string | null;`. Add `export interface WorkflowStreamFrame` (Key Interfaces). Add `export const workflowRunStreamUrl = (workflowId: string) => \`/api/v1/workflows/${workflowId}/runs/stream\`;` (FULL path — the page's `fetch` is not the axios instance, so it needs the `/api/v1` prefix). Do NOT add a `triggerWorkflowRun`-style helper (the stream is a fetch POST in the page).
- **Acceptance criteria**: `grep -n "WorkflowStreamFrame\|tool_calls\|rationale" studio/src/api/registryApi.ts`. Types consumed by CatalogChatPage (T014). Typecheck clean.
- **Dependencies**: none.
- **Test cases**: compile-time (typecheck).
- **Verification command**: `cd studio && npm run typecheck 2>&1 | tail -3`

### T014 — frontend: CatalogChatPage live workflow console
- **Files**: `studio/src/pages/CatalogChatPage.tsx`
- **Interface contract**:
  - Extend the `Message` interface with `toolCalls?: {tool_name;status}[]`, `rationale?: string | null`, `citations?: {source;kb}[]` (it already extends `Attributed` shape via role/content/author).
  - **Replace `sendWorkflowMessage`** (the `triggerWorkflowRun` + `pollWorkflowResult` body) with a fetch-based SSE reader: `POST /api/v1/workflows/{source_id}/runs/stream` (Keycloak bearer header; body `{message, session_id: sessionId}`), read `response.body` via `getReader()`, split on `\n\n`, parse `data:` JSON into `WorkflowStreamFrame`, and drive: `agent_start`→`openAuthorBubble(prev, f.author, mk)`; `token`→`routeToken`; `tool_call`→`attachToolCall`; `rationale`→`attachRationale`; `agent_end`→no-op; `done`→stop; `error`→append error text. Seed with `mk(author)` where `mk` mirrors `sendAgentMessage`'s but returns the rich `Message`.
  - Keep `pollWorkflowResult`/`WorkflowTurn` for **reload/history** only; upgrade `WorkflowTurn` to render each child's `tool_calls` (via `ToolCallChip`) and `rationale` (amber box) using `AttributedBubble`'s new props, gated on the page-level `showRationale` state.
  - **Console shell (2b-iii)**: when `isWorkflow`, render header `"{artifact.name} · {memberCount} agents"` (member count from `getCompositeWorkflow(source_id)` or the run tree's distinct child authors), subtitle `"{orchestration} orchestration · shared conversation thread — every agent reads the same transcript"`, and the blue attribution info-bar (mock lines 25–30) with a **Show rationale** checkbox bound to `showRationale` state (default true).
  - **Single-agent path**: in `sendAgentMessage`, add a `tool_call` branch → `attachToolCall` (chips now appear live for single agents too).
  - Pass `avatar` to member `AttributedBubble`s (workflow console) so each shows a tinted `Bot`.
- **Acceptance criteria**: workflow chat opens `/runs/stream` (network), renders per-member bubbles progressively, tool chips, and amber rationale boxes toggled by the checkbox. `grep -n "runs/stream\|attachToolCall\|attachRationale\|showRationale" studio/src/pages/CatalogChatPage.tsx`. No orphan (all new symbols from T009/T010/T012/T013 consumed here). Single-agent chat unchanged apart from live tool chips.
- **Dependencies**: T009, T010, T012, T013.
- **Test cases**: T017 Playwright.
- **Verification command**: `cd studio && npm run typecheck 2>&1 | tail -3`

### T015 — frontend: Vitest for slots + reducers
- **Files**: `studio/src/components/chat/AttributedBubble.test.tsx` (new), `studio/src/components/chat/ToolCallChip.test.tsx` (new), `studio/src/lib/chatStream.test.ts` (new or extend)
- **Interface contract**: `AttributedBubble.test.tsx`: (a) degenerate single-agent (no author/showLabel=false) renders no dot/label/chip/rationale — DOM parity assertion; (b) with `author` + `avatar` renders name + a Bot avatar; (c) `toolCalls` renders `ToolCallChip`(s); (d) `rationale` + `showRationale=true` renders amber box; `showRationale=false` hides it; (e) `citations=[]` renders no chip row. `ToolCallChip.test.tsx`: renders tool name; error status tints red. `chatStream.test.ts`: `routeToken`/`openAuthorBubble` route by author (two authors → two bubbles); `attachToolCall`/`attachRationale` attach to the matching author's open bubble and open a new bubble when the author differs.
- **Acceptance criteria**: `cd studio && npm run test` green (all new specs pass; no existing spec regressed).
- **Dependencies**: T010, T011, T012.
- **Test cases**: self.
- **Verification command**: `cd studio && npm run test`

### T016 — backend e2e: suite-75 T-S75-009/010/011
- **Files**: `scripts/e2e/suite-75-context-storage.sh`
- **Interface contract**: Add three test cases in the existing in-pod-python + RESULT/DIAG pattern (reuse Section A's provisioning: two reactive memory-enabled agents `s75-wa`/`s75-wb` + a sequential workflow; give `s75-wa` a **low-risk** HTTP tool so it emits a tool call without tripping HITL — create a `s75-tool-{suffix}` tool with `risk_level="low"` via `POST /tools` and attach via `POST /agents/{name}/tools`). 
  - `T-S75-011` (stream parity): `POST /workflows/{id}/runs/stream` with `{message}` (in-pod httpx `client.stream`), collect frames; assert frames include `agent_start`/`token`/`agent_end` each carrying `author` in `{s75-wa, s75-wb}` and a final `done`. Then `POST /workflows/{id}/runs` (drain) + poll the tree; assert the drain's terminal tree has the SAME child agent_names (parity). SKIP if members never reach running.
  - `T-S75-009` (tree tool_calls): after the drain run completes, `GET /workflows/{id}/runs/{run_id}/tree`; assert the `s75-wa` child's `tool_calls` is a non-empty list of `{tool_name,status}` (the low-risk tool). 
  - `T-S75-010` (rationale row): after a run, `GET /agents/s75-wa/memory?scope=workflow_run&thread_id={run_id}`; assert at least one row with `message_kind="rationale"` and `agent_name="s75-wa"`; and the tree child's `rationale` field is that string. SKIP gracefully if the model produced no tool-calling reasoning (DIAG the empty case — non-blocking per §3.2), but FAIL if the row/field plumbing is broken (e.g. tool_calls present but rationale key missing from the response schema).
  - Register nothing new in `run-all.sh` (suite-75 already registered).
- **Acceptance criteria**: `bash -n scripts/e2e/suite-75-context-storage.sh`; the three IDs appear in the tally; run against the deployed cluster (CP1) shows PASS or a justified SKIP (capacity), never a silent skip of broken pods (existing `agent_pod_breakage` guard).
- **Dependencies**: T009 deployed (CP1).
- **Test cases**: self.
- **Verification command**: `bash -n scripts/e2e/suite-75-context-storage.sh && grep -n "T-S75-009\|T-S75-010\|T-S75-011" scripts/e2e/suite-75-context-storage.sh`

### T017 — browser e2e: rich console Playwright spec
- **Files**: `studio/e2e/context-rich-console.spec.ts` (new)
- **Interface contract**: Model on `context-attribution.spec.ts`. `beforeAll` (REST, ADMIN headers): create two reactive memory-enabled agents (`researcher`, `answerer`); create a **low-risk** HTTP tool and attach it to `researcher`; compose a `sequential` reactive workflow (`memory_enabled: true`); add both members; snapshot `eval_passed:true` version; publish; admin-approve → `artifactId`. The test:
  1. Navigate `/catalog/{artifactId}/chat`; assert the console **shell**: header text matching `/· \d+ agents/` and the "shared conversation thread" subtitle.
  2. Register `page.waitForResponse` on `/workflows/*/runs/stream` (POST) BEFORE sending. Send a message.
  3. **Progressive reveal**: assert the `researcher` attributed bubble (name + color dot) is visible while the `answerer` bubble is NOT yet present (poll a short window after the stream response resolves). Then assert BOTH member bubbles appear, each with an avatar (`Bot` icon) + name.
  4. **Tool chip**: assert a `ToolCallChip` showing the researcher's tool name is visible under the researcher bubble.
  5. **Rationale toggle**: assert amber rationale box(es) are visible; click "Show rationale" off → they hide; on → they reappear. (SKIP the toggle assertion only if the model produced no rationale — DIAG, same non-blocking rule as T016.)
  6. **Save→reload→survives**: reload `/catalog/{artifactId}/chat` — the reload path reads the tree/`/memory`; assert the member bubbles + rationale rehydrate from the backend (not the store). Register `waitForResponse` on `/memory` or `/tree` to prove the read.
  - Capacity boundary: if the two agent pods never warm (no terminal multi-member run), `test.skip` (same rule as `context-attribution.spec`). A run that completes but renders wrong → FAIL.
- **Acceptance criteria**: `cd studio && npx playwright test e2e/context-rich-console.spec.ts` passes or capacity-skips against the deployed Studio (CP2). Asserts UI wiring + persistence + `waitForResponse` on `/runs/stream`.
- **Dependencies**: T014 deployed (CP2).
- **Test cases**: self.
- **Verification command**: `cd studio && npx tsc --noEmit && ls e2e/context-rich-console.spec.ts`

### T018 [P] — docs: experience + gap ledger + design status
- **Files**: `docs/experience/playground.md`, `docs/testing/manual-ui-e2e-test-plan.md`, `docs/design/context-storage-poc-2b-rich-console.md`
- **Interface contract**: `playground.md`: document the new workflow-stream SSE frames (`agent_start`/`token`/`tool_call`/`rationale`/`agent_end`/`done`) and that single-agent chat now surfaces `tool_call` chips (the L473 drop is removed). `manual-ui-e2e-test-plan.md` "Known gaps": add — durable/HITL members don't token-stream (by design); citations slot empty (deferred POC-4); durable-member reload tool-chips not projected (only reactive marker rows); `/runs/stream` runs in-process only (no production-orchestrator-pod path). Mark each `deferred (intentional)` vs `not-yet-wired (debt)`. `context-storage-poc-2b-rich-console.md`: set status → Implemented; fold the four grounding corrections.
- **Acceptance criteria**: all three files updated; gaps tagged.
- **Dependencies**: none (write once implementation is understood; may run in parallel with code).
- **Test cases**: n/a.
- **Verification command**: `grep -n "runs/stream\|rationale" docs/experience/playground.md`

### T019 — image bumps (all 3 services, 3 files)
- **Files**: `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`
- **Interface contract**: Set `REGISTRY_API_TAG=0.2.190`, `DECLARATIVE_RUNNER_TAG=0.1.55`, `STUDIO_TAG=0.1.143` in **both** `deploy-cpe2e.sh` (L266/275/273) and `deploy-eks.sh` (L67/69/70) with updated comment headers describing POC-2b. In `charts/agentshield/values.yaml`: registry-api `tag: "0.2.190"` (~L597), `declarativeRunnerTag: "0.1.55"` (~L673), studio `tag: "0.1.143"` (~L917). Never reuse a tag.
- **Acceptance criteria**: all three tags identical across the three files (`grep`). 
- **Dependencies**: T003, T005, T007, T009, T014 (code that ships in the images). Bump in the same commit as the code.
- **Test cases**: n/a.
- **Verification command**: `grep -rn "0.2.190\|0.1.55\|0.1.143" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml`

---

## Checkpoints

### CP1 — Backend deploy + smoke (after T001–T009, T016, T019)
- **Deploy**: `bash scripts/deploy-eks.sh` (EKS test-cluster; builds/pushes registry-api 0.2.190 + declarative-runner 0.1.55 + studio 0.1.143, applies Helm). Deploy is **user-gated** — proceed only after reviewer go (shared-cluster hazard; No-Merge-to-Main note).
- **Script**: `scripts/checkpoints/poc-2b-cp1-smoke.sh` — kubectl-exec into registry-api pod; run the three suite-75 additions (`T-S75-009/010/011`) via `bash scripts/e2e/suite-75-context-storage.sh` and assert 0 FAIL; additionally curl-assert (jq) that `POST /workflows/{id}/runs/stream` returns `content-type: text/event-stream` and at least one `data:` line carries `"type":"agent_start"` with an `author`.
- **Gate**: suite-75 exits 0 (PASS/justified-SKIP only); stream endpoint emits author-tagged frames; a drain `/runs` produces a matching terminal tree.

### CP2 — Frontend deploy + browser smoke (after T010–T015, T017, T018)
- **Deploy**: covered by the same `bash scripts/deploy-eks.sh` (studio 0.1.143 already built in CP1); if frontend lands after CP1, re-run the studio build step.
- **Script**: `scripts/checkpoints/poc-2b-cp2-smoke.sh` — `cd studio && npm run typecheck && npm run test` (Vitest green), then `bash scripts/studio-e2e.sh` filtered to `context-rich-console.spec.ts` (Playwright green or capacity-skip).
- **Gate**: Vitest 100% green; Playwright proves progressive reveal + chip + rationale toggle + save→reload (or capacity-skips with no assertion failure).

---

## Task dependency graph (topological, no forward deps)

```
T001 → T002 → T003
T004 → T005
T004 → T006 → T007 → T009
T008 → T009
T010, T011 → T012 → T015
T013 ─┐
T009,T010,T012,T013 → T014 → T017
T016 (needs T009 deployed @ CP1)
T018 [P]
T003,T005,T007,T009,T014 → T019
CP1 = {T001..T009, T016, T019} ; CP2 = {T010..T015, T017, T018}
```

Implementation-task count: **19** (T001–T019). Checkpoint count: **2** (CP1, CP2).

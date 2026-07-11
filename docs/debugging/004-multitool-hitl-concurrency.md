# 004 — Multi-tool HITL: concurrent interrupts, duplicate execution, orphan approvals

A worked example of a hard, multi-layer investigation (LangGraph internals +
registry + frontend). Read alongside the playbook (C-7, patterns P5/P6/P1).

## Symptom (as reported)

1. "Weather **and** activities" compound query: after approving the first
   `web_search`, the chat gets **stuck** and a toast says **"Stream connection
   lost."**
2. Later: the sandbox approval panel shows **multiple old approval requests**
   from previous turns, piling up.
3. Later: the Evaluate tab shows a **flood of "Calling web_search…" chips**.

## Investigation — step by step

### Step 1: reproduce at the API layer and COUNT events (playbook §1.3)
Streamed the compound query with a JWT and counted SSE events:
- INITIAL: `{tool_call_start: 2, approval_requested: 1}` — the model made **two**
  parallel tool calls, but only **one** `approval_requested` was surfaced.
- Queried session approvals: **2 pending**. So both tool calls created approval
  records, but the SSE surfaced only one.
- After approve #1 + resume: `{tool_call_start: 2, tool_call_end: 1, approval_requested: 1}` — the resume re-ran, executed one tool, and **re-interrupted** for the second; the resume stream ended without `done`.

**Reasoning:** two facts jumped out — (a) two approval records but one SSE event
(the frontend only ever saw one), and (b) the resume re-emitted `tool_call_start`
(the node re-runs). Both pointed at LangGraph interrupt/resume mechanics.

### Step 2: read the streaming + resume code
- `streaming.py` surfaces `pending[0]` only (`_extract_interrupts` returns a list
  but one is used) → explains "2 records, 1 SSE".
- `chat.py` `_proxy_resume` translated `token/done/error/tool_call_*` but **dropped
  `approval_requested`** → explains the hang (the re-interrupt was never forwarded;
  and `stream_events` emits *either* `approval_requested` or `done`, so no `done`).
- `AgentChatPage.connectResumeStream` handled only `token/done/error` → even if
  forwarded, the frontend wouldn't show it.

### Step 3: confirm the CORRECTNESS risk (re-execution) — the key finding
LangGraph `interrupt()` docstring: *"resumes from the start of the node,
re-executing all logic."* So on resume the tool node re-runs `governed_tool` in
full: OPA re-check, a **new** approval POST, and the **real tool HTTP call** — the
approved tool executes **again**. Verified by the resume event counts (extra
`tool_call_start`). This makes the "surface all + resume all" approach unsafe and
is the reason a compound query produced duplicate external searches.

### Step 4: evaluate the "just disable parallel tool calls" idea
Checked both providers' `bind_tools` in the pod's installed libs (playbook §1.5):
- `ChatAnthropic`: `parallel_tool_calls=False` works (→ `disable_parallel_tool_use`).
- `ChatBedrockConverse` (langchain_aws 0.2.35, used by serper-agent-4): **no such
  param**; passing it **TypeErrors** at runtime (`parallelToolCalls`). Dead end for
  the reproducing agent. → Pattern P6: don't depend on provider mechanics.

### Step 5: diagnose the "old approvals pile up" (complaint 2) via the DB
Queried recent approvals grouped by thread:
```
thread=26e45746 status=pending  q=weather forecast tomorrow San Francisco
thread=26e45746 status=approved q=weather forecast tomorrow San Francisco   <-- twin
```
Every turn had an **approved** row AND a **pending twin** with identical args.
**Root cause:** on resume the node re-runs and `governed_tool` re-POSTs the
approval; the first idempotency attempt matched only `pending`, but the original
was now `approved` → no match → a new **orphan pending** row. The session-scoped
panel showed every turn's orphan.

## Root causes (summary)

| # | Root cause | Owner |
|---|-----------|-------|
| 1 | Parallel high-risk tool calls interrupt in one super-step; shared interrupt id in langgraph 0.6.x → can't batch-resume | SDK / langgraph |
| 2 | `interrupt()` re-runs the whole tool node on resume → approved tools re-execute (duplicate API calls) + re-POST approvals (orphans) | SDK / langgraph (P5) |
| 3 | Resume proxy dropped `approval_requested`; frontend resume handlers ignored it | registry + studio |
| 4 | Sandbox panel showed the whole session's pending (surfaced the orphans) | studio |
| 5 | Provider flag can't enforce single-tool (Bedrock) | P6 |

## Fix

- **Provider-agnostic `post_model_hook` `_one_hitl_tool_per_turn`** (`graph_builder.py`):
  when 2+ tool calls are high-risk, trim to the first high-risk one (rewrite the
  AIMessage, same id, filter content tool_use blocks); no-op otherwise. One
  high-risk tool per super-step → no colliding interrupts, and the single-tool node
  re-runs its one tool exactly once (no duplicate execution).
- **Idempotent `create_approval`** per `(thread_id, tool_name, tool_args)` pending.
- **Resume chaining:** resume proxies (`chat.py`, `playground.py`) forward
  `approval_requested`; `AgentChatPage.connectResumeStream` handles it via a
  `reinterruptRef` (avoids a useCallback cycle); `PlaygroundPage` adds a resume
  nonce so the effect re-fires on the 2nd approval.
- **Panel shows only the current approval** (match the live event's `approval_id`),
  not the session list → benign orphan rows don't display.

## Verification
Re-streamed the compound query: INITIAL → **1** approval; approve → tool runs
**once** (one `tool_call_end`); the second tool then surfaces its own approval
(chained, not stuck); approve → completes with `done`. suite-45 10/0, Playwright green.

## Residual / follow-ups (honest)
- **Orphan pending rows still get created** by the node re-run (the pending-only
  idempotency can't catch the post-approval re-POST). They are **benign** (sandbox
  context, self-expire in 30 min, tool executed once) and no longer shown in the
  panel. The *correct* elimination is **tool_call_id-scoped idempotency**: inject
  `Annotated[str, InjectedToolCallId]` into `governed_tool`, store `tool_call_id`
  on the approval (migration), and match `(thread_id, tool_call_id)` regardless of
  status — so the re-run reuses the decided row instead of creating a new pending.
  Deferred (needs an SDK change + migration + agent redeploy).
- A low-risk tool sharing a super-step with the surviving high-risk one still
  re-executes on that node's resume (the trim is intentionally limited to 2+ HITL).

## Principles reinforced
- **P5**: assume interrupt-in-a-node re-runs everything before it on resume — make
  it idempotent or cap to one interrupt per node.
- **P6**: enforce cross-cutting behavior in our graph, not provider flags.
- **P1/§1.2**: the DB grouping (approved+pending twin per thread) is what turned a
  vague "old requests pile up" into a precise root cause — always query the data.

# Research — HITL multi-approval resume regression (code-truth)

**Companion to `plan.md`.** This file is grounded against the running code at the exact
`file:line` anchors below. The root cause is **settled** (see the design brief) — this
document states it, cites the evidence, explains *why the fix mirrors the forward path*,
and records the Ollama-determinism decision. Read this before touching code.

---

## 1. The settled root cause (do NOT re-derive)

A reactive workflow member's **forward dispatch** and **resume** are **asymmetric** in how they
detect a *second* approval gate:

### Forward path — parks correctly (authoritative pause detection)

`services/registry-api/workflow_orchestrator.py:616-624` (inside `_run_step_stream`):

```python
if status_val == "completed":
    async with AsyncSessionLocal() as s:
        pending = (await s.execute(
            select(Approval).where(Approval.thread_id == thread_id, Approval.status == "pending")
        )).scalar_one_or_none()
    if pending is not None:
        status_val = "awaiting_approval"
        logger.info("workflow %s: member '%s' paused for approval ...", ...)
```

After the member's `/chat/stream` returns, the orchestrator **re-checks the DB**: if a
`pending` Approval exists on the member's `thread_id`, the member actually *parked* — it is
NOT complete. This is why the **first** approval works.

### Resume path — completes on any HTTP 200 (the bug)

`services/registry-api/routers/approvals.py:158-199` (`_resume_and_advance`, reactive/chat
branch):

```python
async with httpx.AsyncClient(timeout=120.0) as client:
    resp = await client.post(f"{pod_url}/resume/{thread_id}", json=resume_body)
member_status = "completed" if resp.status_code == 200 else "failed"   # L160
...
# Workflow re-entry:
child.status = member_status                 # L186  ← always 'completed' on 200
child.output = member_output[:4000] ...      # L187  ← the echoed input
child.completed_at = datetime.now(...)       # L188
...
asyncio.create_task(resume_orchestration(parent_id, member_output, member_status))  # L197
```

This branch has **none** of the authoritative pause detection the forward path has. It marks
the member `completed` on *any* HTTP 200 and advances the parent — even when the resumed model
issued a **second** approval-gated tool call and the pod correctly re-parked.

### The pod compounds it

The pod's non-stream `/resume/{thread_id}` route
(`services/declarative-runner/main.py:688-712`) calls
`workflow_executor.resume()` (`services/declarative-runner/workflow_executor.py:876-910`),
which does a single `ainvoke(Command(resume=...))` and returns
`{"response": messages[-1].content, ...}` with **HTTP 200**, *ignoring*
`result.get("__interrupt__")`. So on a re-interrupt the pod returns 200 + the echoed input —
the registry cannot distinguish "done" from "re-parked" by the HTTP response alone.

### Observed live (design brief)

Run `e2a56353`, thread `ed039d9a…`: member `researcher-agent` under `research-summarize`
parked on `web_search` (query A, `approved`), resumed, issued a **second** `web_search`
(query B), the pod re-parked (new approval `41f3ca38`, `pending` forever), but the registry
marked the member `completed` with the echoed input, `summarization-agent` summarized the
non-answer, and the run was marked `completed`. DB end-state: **run `completed` AND an
approval left `pending`** — the invariant this fix guards.

### Why only reactive members hit this

The **durable** member resume path is already correct: `_resume_and_advance` L87-100 routes
durable members through `resume_durable_member`
(`workflow_orchestrator.py:255-304`), which **polls the child AgentRun** and returns
`awaiting_approval` if it lingers (L286-304); the caller then returns early (L92-94) without
advancing. Only the **reactive/chat** branch (approvals.py L142-199) lacks the equivalent
check. `execution-models-v2` routed reactive workflow-member resume onto this non-stream path;
the streaming forward path always parked, the resume path never did → **that asymmetry is the
regression.**

---

## 2. Why the fix mirrors the forward path (No-Bandaid)

The forward path already proved the correct discriminator: **the DB is the source of truth for
"is this member still parked?"** — a pending Approval on the thread means parked, regardless of
what the transport (stream frame or HTTP 200) says. The fix ports that *exact* check into the
resume re-entry block: after the pod `/resume` returns, before closing out the child, query for
a `pending` Approval on `thread_id`; if one exists, set the child `awaiting_approval` (leave
`output`/`completed_at` null) and do **not** call `resume_orchestration`.

This is architecturally correct rather than a bandaid because:

- It **fixes the class of problem** (transport-reported completion is not authoritative for a
  re-interrupting graph), not the one instance. Any Nth approval gate is handled by the same
  check — the inline decide re-fires `_resume_and_advance`, which loops until no `pending`
  remains, then completes + advances with the real answer.
- It makes the two paths **symmetric** — one discriminator (DB pending-approval), used
  identically forward and on resume. No `if getattr`, no HTTP-status sniffing, no priority
  fallthrough.
- The pod-side `resume()` `__interrupt__` fix (Task 4) is **optional defense-in-depth** — the
  DB check is authoritative and matches the forward path, so the transport does not need to
  carry the re-park signal. Task 4 is belt-and-suspenders, marked low-risk/optional.

The idempotency guard in `create_approval` (approvals.py L404-423) guarantees the check is
clean: on resume the pod replays the first interrupt node and re-POSTs `create_approval` with
the **same** `(thread_id, tool, args)`, which reuses the now-`approved` first approval (not
pending). Only a genuinely new second gate (different `tool_args`) mints a fresh `pending` row.
So `select(Approval).where(thread_id==…, status=='pending')` returns **only** the second gate,
never the just-decided first — the same reasoning the forward path relies on.

---

## 3. How the loop terminates (the inline decide re-fires the resume)

Both decide entrypoints schedule `_resume_and_advance` for a workflow member:

- Console decide: `approvals.decide_approval` L819-823.
- Inline/playground decide (what WorkflowChatPage uses): `playground.decide_playground_approval`
  L1560-1570 (matches `AgentRun.thread_id == approval.thread_id AND parent_run_id IS NOT NULL`).

So each time the user approves the *current* gate, `_resume_and_advance` runs again with the
**same** `thread_id`, re-posts `/resume`, and re-checks for pending. The recursion terminates
when the resumed model makes no further approval-gated call → no `pending` → the child is closed
`completed` with the real output → `resume_orchestration` advances the parent to the summarizer
→ `completed`. This exactly matches the durable member's existing re-park loop
(`resume_durable_member` returning `awaiting_approval` until terminal).

---

## 4. Frontend: the poll must surface a *new* pending approval

`studio/src/pages/WorkflowChatPage.tsx` `pollResumedResult` (L83-138) only breaks on
`parent.status === 'completed' | 'failed'` and never re-surfaces a fresh gate. When a member
re-parks, the parent stays `awaiting_approval` (the forward park set it via
`workflow_orchestrator.py:801`/`_park_or_fail`; the re-park does **not** clear it because the
fix does not call `resume_orchestration`), so the current poll spins for 60×4s then prints
"still running" — the second gate is invisible and orphaned in the UI too.

**Reference pattern already in the product:** `studio/src/pages/WorkflowBuilderPage.tsx`
L392-443 + L740-830 already does this for the builder Run panel — when
`tree.parent.status === 'awaiting_approval' && context !== 'production'` it calls
`refreshPendingApprovals()` (`listPendingApprovals(undefined, 'playground'/'sandbox')`) and
correlates by `child.thread_id` to render inline cards, and keeps polling because
`awaiting_approval` is **not terminal**. WorkflowChatPage's `pollResumedResult` must mirror
that: on `awaiting_approval`, fetch playground pending approvals, match the parked child's
`thread_id`, and re-render the inline `ConversationApprovalPanel`. Because the just-decided
approval is already `approved` (not `pending`), a `status=pending` match on the thread is
inherently the **new** gate — no id-tracking needed.

`ApprovalInboxItem` (registryApi.ts L1466-1491) carries `id`, `tool_name`, `tool_args`,
`risk_level`, `thread_id`, `team`, `context`, `created_at`, `thread_context_snippet` — enough
to build the `SessionApproval` shape (L1554-1567) the panel needs (`reasoning`←
`thread_context_snippet`, `requested_by`←null; both nullable in the panel).

---

## 5. The Ollama non-determinism decision

The live double-park depends on gemma issuing **two** `web_search` calls, which is
non-deterministic. Decision (matches the design brief + the suite-79 header convention):

1. **Best-effort live case (T-S79-004a):** engineer a two-search prompt (e.g. ask for two
   distinct facts that each need a search) + a bounded retry. If it genuinely won't double-park,
   emit a **loud SKIP** with a diagnostic (`SKIP T-S79-004a no-double-park …`) — **never a false
   PASS**.
2. **Deterministic backstop (T-S79-004b) — the real gate:** after the member parks on the first
   gate, **seed a second `pending` Approval on the same `thread_id`** (different `tool_args`) via
   `POST /api/v1/approvals/`, then approve the first inline. The registry MUST re-park (child +
   parent stay `awaiting_approval`) instead of completing — this exercises the exact fixed line
   independent of what the model does, and **fails against current code** (which marks
   `completed` + advances while the seeded approval is `pending` → invariant violated). This is
   what guarantees the reproduce-first requirement is met deterministically.

The invariant asserted by both: **never (parent run `completed` AND an approval left
`pending`)**, plus (live case) final run `completed` with both members `completed`, the
researcher output a real answer (not the echoed input), and zero `pending` approvals on the
thread.

---

## 6. Blast radius (what this change touches / shares infra with)

| Surface | Shared with | Regression suite that guards it |
|---|---|---|
| `_resume_and_advance` reactive branch | console decide (`decide_approval`) **and** inline decide (`decide_playground_approval`) — both call it | suite-79 (workflow inline HITL), suite-45 (agent HITL sandbox+prod) |
| Durable member resume (`resume_durable_member`) | same function, different early-return branch (L87-100) — **untouched** | suite-45 / durable workflow suites |
| Single-agent chat resume (STREAM path `resume_stream`→`stream_events`) | different endpoint (`/resume/{id}/stream`) — **untouched**; verify it already handles re-interrupt (Task 5) | suite-45 |
| WorkflowChatPage poll | `getWorkflowRunTree`, `listPendingApprovals`, `ConversationApprovalPanel` (shared with WorkflowBuilderPage + sandbox chat) | Vitest `WorkflowChatPage.test.tsx`; Playwright `workflow-inline-approval-live` (builder) + new chat spec/manual step |

Nothing in the durable path, the single-agent stream path, or the OPA/authority gates changes.
The fix is additive (an early re-park branch) — the existing single-approval flow is byte-for-byte
unchanged when no second `pending` approval exists.

---

## 7. Multi-context routing — which surfaces reach the fixed code (VERIFIED, not assumed)

The bug lives in the SHARED `_resume_and_advance` reactive workflow-**member** re-entry block
(approvals.py L170-199). It is reached for a **workflow member** (a child AgentRun with
`parent_run_id`) from all three contexts, via **two** decide entrypoints — both confirmed by
reading the code:

| Decide entrypoint | Code (verified) | Schedules `_resume_and_advance`? | Reaches the member re-entry fix? |
|---|---|---|---|
| `decide_approval` — console / production, PATCH `/approvals/{id}` | approvals.py **L819-823** | YES — for any approval with `thread_id`+`agent_name`+`team` | YES for a reactive workflow member (has `parent_run_id`). A **top-level agent** returns early at L178 (no parent to advance); its re-park instead surfaces as a NEW `pending` row in the console queue. |
| `decide_playground_approval` — inline sandbox + eval, POST `/playground/approvals/{id}/decide` | playground.py **L1560-1570** | YES — ONLY when `member_child` exists (AgentRun `thread_id==approval.thread_id AND parent_run_id IS NOT NULL`) | YES for a reactive workflow member. A **single agent** is NOT scheduled here — the client (chat) or eval poll drives `/resume-stream` (the STREAM path) instead. |

**Eval HITL specifically** (`services/eval-runner/main.py`): `_poll_durable` (L303-338) polls a
run's steps; on an `awaiting_approval` step with an `approval_id` it calls `_self_approve`
(L272-300), which decides via `POST /playground/approvals/{id}/decide` and then drives
`GET /playground/runs/{run_id}/resume-stream`. So:
- **Workflow-member eval** → `decide_playground_approval` finds `member_child` → schedules
  `_resume_and_advance` → the fix applies.
- **Single-agent eval** → no `member_child` → resume is stream-driven; `_poll_durable`'s
  `approved` set only dedups ids already handled, so a NEW re-park `approval_id` is re-approved on
  the next poll — the re-park is handled by re-approval, not by `_resume_and_advance`.

**Consequence for coverage.** The SAME workflow-member re-park bug is reachable from **sandbox
(inline)**, **eval (inline)**, and **production (console)** because all three funnel a
workflow-member decision into the one `_resume_and_advance`. Single-agent surfaces use a different
(stream/queue) path and are verified independently (expected to already re-park; fix only if a test
proves otherwise — root-cause first, don't weaken a working control).

**Why the deterministic backstop covers every context.** The re-park query in the fix
(`Approval.thread_id==thread_id AND status=='pending'`) is **context-agnostic** — it never reads
`context`. So seeding a second pending approval on a parked workflow member's thread and asserting
the registry re-parks proves the shared core for sandbox, eval, **and** production in one
deterministic test, independent of which decide endpoint fired. The per-context bash/browser cases
then prove only the **independent wiring** (which decide endpoint fired; which UI re-render
happens), and may SKIP-loud on local capacity (production needs published artifacts + warm prod
pods; evals need the eval-runner + a dataset) — never a false pass, and the backstop still fully
guards the shared code path they exercise.
</content>
</invoke>

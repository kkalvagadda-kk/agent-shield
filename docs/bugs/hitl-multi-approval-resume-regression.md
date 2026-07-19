# Reactive workflow-member resume drops the SECOND HITL approval → run "completes" with an orphaned gate

**Found:** 2026-07-18, in a live browser run of the `research-summarize` workflow (user reported: approved the inline HITL, but the run finished with an echoed non-answer). **Fixed:** registry-api `0.2.206`.

**Regression test:** `scripts/e2e/suite-79-workflow-hitl.sh` **T-S79-004b** (deterministic re-park backstop). **Investigation log:** [`docs/debugging/012-hitl-second-approval-orphaned.md`](../debugging/012-hitl-second-approval-orphaned.md).

## Symptom
A reactive workflow member (`researcher-agent`) parks for HITL on its first `web_search`. The user approves inline. The workflow then reports **`completed`**, but the researcher's answer is just the **echoed input** (no search result), `summarization-agent` summarizes the non-answer, and — in the DB — a **second approval is left `pending` forever**.

Evidence (run `e2a56353-…`, thread `ed039d9a…`):
- `approvals`: two rows on the thread — query A `approved`, query B **`pending`** (orphaned).
- `run_steps`: **one** `web_search` step.
- Pod logs: after `POST /resume 200`, the model issued a **second** `web_search`, the pod re-parked (`on_interrupt`, new approval `41f3ca38`) — but the registry had already advanced.

## Root cause
The **forward** and **resume** paths for a reactive member were asymmetric.

- **Forward dispatch** (`workflow_orchestrator.py` ~L616-624) does *authoritative pause detection*: after a member's `/chat/stream` returns, if a `pending` Approval exists on the thread, it sets `awaiting_approval` and does **not** advance. That's why the *first* approval works.
- **Resume** (`routers/approvals.py:_resume_and_advance`) posts to the pod's non-stream `/resume` and set `member_status = "completed" if resp.status_code == 200 else "failed"`, then closed the child and called `resume_orchestration` — with **no** re-check for a new pending approval. So when the resumed model made a **second** approval-gated call and the pod correctly re-parked (its `/resume` handler, `workflow_executor.resume()`, ignores `result["__interrupt__"]` and echoes `messages[-1].content`, returning HTTP 200), the registry read that 200 as "done", marked the member `completed` with the echoed output, advanced the workflow, and orphaned the second approval.

A pod HTTP 200 does **not** prove completion; the authoritative signal is "no pending approval remains on the thread" — exactly what the forward path already used, and what the resume path lacked. This is the same class as the [side-effecting-lost](side-effecting-lost-on-declarative-runner-path.md) trap: two paths that should share one invariant, but only one enforced it.

Not caused by the knowledge/conversations work; the reactive-member resume path shipped with the 0.2.203 HITL fix, which handled a *single* approval cycle and was never built to survive a second interrupt within one member. The trigger (model making two tool calls in a turn) made the latent asymmetry visible.

## Fix
Mirror the forward path's authoritative pause detection into the resume re-entry block of `_resume_and_advance` (`routers/approvals.py`): after resolving the parked child + parent, **before** closing the child, if a `pending` Approval still exists on the thread, set `child.status = "awaiting_approval"`, commit, and `return` — do **not** advance the parent. The next inline (`decide_playground_approval`) or console (`decide_approval`) decision re-fires `_resume_and_advance`, which re-checks and loops until no pending remains, then closes the child `completed` and advances with the real answer.

```python
still_pending = (await s.execute(
    select(Approval.id)
    .where(Approval.thread_id == thread_id, Approval.status == "pending")
    .limit(1)
)).first()
if still_pending is not None:
    child.status = "awaiting_approval"
    await s.commit()
    return   # re-parked — do NOT advance
```

`.first()` (not `scalar_one`) so a genuine double-interrupt with >1 pending never raises. Purely additive: with no second pending approval every existing single-approval flow is byte-for-byte unchanged. The check is context-agnostic (`thread_id` + `status='pending'`), so it covers both the inline/sandbox+eval decide and the console/production decide — the single shared `_resume_and_advance` both endpoints schedule.

**Invariant enforced:** *never (a run is `completed` AND an approval on its thread is left `pending`).*

## Verification
`scripts/e2e/suite-79-workflow-hitl.sh` T-S79-004b seeds a second `pending` approval on the parked member's thread, approves the first inline, and asserts the invariant. **RED** against `0.2.205** (`FAIL … run completed while a 2nd approval was still pending — gate orphaned`), **GREEN** against `0.2.206` (`PASS … re-parked (parent=awaiting_approval, held)`), with T-S79-001/002/003 still passing. Frontend re-surfacing of the second inline gate (`WorkflowChatPage`) + the console/eval/production surface proofs are tracked as follow-on tasks in `docs/plan/hitl-multi-approval-fix/tasks.md`.

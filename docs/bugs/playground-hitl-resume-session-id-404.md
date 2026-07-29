# Playground HITL resume-after-approve 404s ("Stream connection lost")

**Found:** 2026-07-28 (Claude-in-Chrome lifecycle journey, leg 12 — sandbox HITL inline self-approve).
**Fixed:** registry-api `0.2.239` (commit on branch `lifecycle-journey-suite`).

## Symptom
In the Playground, a high-risk tool call parks for inline self-approval (correct). After
clicking **Approve**, streaming does **not** resume: a red **"Stream connection lost"** toast
appears, no final answer bubble renders, and the run never completes. The Event Trace shows
`tool_call_start → approval_requested → approval_decided (approved)` and then nothing.

Reproduced on studio `0.1.170` / registry-api `0.2.238` for every high-risk tool call in the
Playground.

## Root cause (the design flaw, not the surface error)
The F-F fix (`0.2.235`, Issue 1) threaded a persisted **`session_id`** so a chat's turns share
one reloadable thread. It made the run's checkpoint/HITL **`thread_id = run.session_id`**
(reactive chat), which **diverges from `PlaygroundRun.id`** (the per-turn PK).

The frontend does the right thing: `PlaygroundPage.handleHitlDecided(decision, threadId)` opens
`GET /api/v1/playground/runs/{threadId}/resume-stream`, passing the **thread id (= session_id)**
— it needs that to find the Approval (`Approval.thread_id == session_id`).

But the backend `resume_stream_playground_run` (`services/registry-api/routers/playground.py`)
resolved the run by **`PlaygroundRun.id == run_id` only**, then set `thread_id = run_id`. Since
no `PlaygroundRun` has `id == session_id`, the lookup failed:

```
GET /api/v1/playground/runs/3dc6bde0-…/resume-stream  →  404 {"detail":"Playground run not found"}
```

So the approval was persisted (`decide_playground_approval … decision=approved` → 200) but the
resume stream 404'd, the runner never re-entered the graph (agent-pod logs show the HITL record
created, then only `/health` probes — **no** psycopg/checkpointer error), and the client reported
the dropped stream as "Stream connection lost". The endpoint carried the pre-F-F invariant
`thread_id == run_id == PlaygroundRun.id`, which F-F broke without updating this reader.

Evidence (DB): the parked thread `3dc6bde0…` matched **0 rows by `id`**, **1 row by
`session_id`** (run `id=5893f807…`, `session_id=3dc6bde0…`).

## Fix (class-fix)
Resolve the run by **either** its PK **or** its `session_id`, and key the approval lookup on the
same thread the graph checkpointed under — matching how the dispatch path already computes it
(`thread_id = run.session_id or run_id`, playground.py ~L788):

```python
result = await db.execute(
    select(PlaygroundRun)
    .where(or_(PlaygroundRun.id == parsed_id, PlaygroundRun.session_id == run_id))
    .order_by(PlaygroundRun.started_at.desc())
)
run = result.scalars().first()
...
thread_id = run.session_id or str(run.id)
```

This makes the endpoint accept whichever id the caller holds — the per-turn `run_id`
(durable/non-chat, `session_id` NULL → matches PK) or the session-scoped thread id (reactive
chat → matches `session_id`). The invariant is no longer assumed; both readers derive `thread_id`
the same way.

## Files changed
- `services/registry-api/routers/playground.py` — `or_` import; run resolution in
  `resume_stream_playground_run` matches PK OR `session_id`; `thread_id = run.session_id or str(run.id)`.
- `scripts/e2e/suite-92-playground-hitl-resume-session-id.sh` — regression guard (registered in `scripts/test-manifest.txt`, group `hitl,chat`).
- Image bump `registry-api 0.2.238 → 0.2.239` in `scripts/deploy-eks.sh`, `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`.

## Regression test (failing-first)
`suite-92` seeds a `playground_run` whose `session_id != id`, then hits
`/playground/runs/{session_id}/resume-stream`:
- **Pre-fix** → `404 "Playground run not found"` (T-S92-001 FAIL — bug reproduced).
- **Post-fix** → run resolves; reaches the approval lookup (`"No decided approval found for this run"`).
- T-S92-002 (PK path still resolves) and T-S92-003 (unknown id still 404s) guard against over-broadening.

## Lessons
- A stored invariant (`thread_id == run_id`) that a later feature breaks must be re-checked at
  **every reader**, not just the writer. F-F updated the dispatch/thread writer but not this
  resume reader.
- API-only e2e can't catch this: the resume is opened by the browser after an inline click. Only
  a real UI journey (or a test that hits the exact `resume-stream` path) surfaces it — which is
  why the Claude-in-Chrome journey found it and `suite-8-playground` did not.

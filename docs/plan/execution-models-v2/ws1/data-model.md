# WS-1 Data Model — checkpoint-of-record consolidation (no DDL)

WS-1 adds **no migration**. Its data-model work is deciding **which existing store is authoritative** so a
resumed run re-enters real state instead of "lost state" (B3). Today there are two competing checkpoints:

| Store | Where | Keyed by | Today's problem |
|---|---|---|---|
| LangGraph `PostgresSaver` | SDK `checkpointer` (imported by declarative-runner) | `thread_id` | Holds full graph state incl. `interrupt()` position — **already correct**, but not consulted on production resume. |
| `declarative-runner/checkpoint.py` (`agent_runs.*` JSON) | `save_checkpoint(run_id, last_completed_step, state)` `:30` | `run_id` | **Orphan — no caller.** `_resume_interrupted_runs` reads it and finds nothing → "lost state". |

## Decision (No-Bandaid: one source of truth, not two guarded)

- **`PostgresSaver` (thread_id) = the single checkpoint of record.** Graph position, messages, and the
  `interrupt()` pause all live there; `resume_durable(thread_id, decision)` re-enters from it.
- **`checkpoint.py` is reduced to a step-index bookmark** — `last_completed_step` only, used for
  **callback idempotency** (don't re-POST a `run_steps` row already written after a mid-run pod restart).
  It is advisory, never the resume source. Its redundant graph-state `save` is deleted.
- **`_resume_interrupted_runs`** changes from "load JSON checkpoint" to "find `agent_runs` in
  `running`/`awaiting_approval` with a `thread_id` → call `resume_durable(thread_id)`".

## Existing columns WS-1 relies on (all present — verified 2026-07-12)

| Column | Table | Line | Use in WS-1 |
|---|---|---|---|
| `thread_id` | `agent_runs` | `models.py:1508` | PostgresSaver key for resume. |
| `parent_run_id` | `agent_runs` | `models.py:1509` | D4 — durable member child runs nest under the parent for the run tree. |
| `orchestrator_state` (JSONB) | `agent_runs` | migration 0032 | D3 — carries the checkpointed traversal cursor (node / supervisor accumulator). |
| `approval_id` (FK) | `run_steps` | migration 0018 | HITL park — links the `awaiting_approval` step to its `Approval`. |
| `status` | `agent_runs` | — | `awaiting_approval` park state; `running`→`completed`/`failed`. |

## `orchestrator_state` cursor shape (D3 — the only structural addition, JSONB, no DDL)

```jsonc
// sequential (today): { "mode":"sequential", "team":"...", "workflow_id":"...", "next_index": 2 }
// conditional/handoff (NEW — Markovian, tiny): { ..., "current_node": "agentB", "visited_count": 3 }
// supervisor (NEW — accumulator): { ..., "workers_done": ["a","b"], "outputs": {...}, "iteration": 2 }
```

`_halt_for_approval` (`workflow_orchestrator.py:249`) today writes only `{mode,team,workflow_id}`. WS-1 adds
the cursor per mode; `resume_orchestration` (`:415`) reads it and calls the matching `_run_*_from`.

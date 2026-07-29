# Playground conversations lost on leaving the screen (single-turn, no rehydration)

**Found/Fixed:** 2026-07-28 — branch `lifecycle-journey-suite` (registry-api + studio;
image tags bumped at Phase-1 exit). Issue 1 of the three reported Playground defects.

## Symptom

On the Playground (`/playground`), a single-agent chat's history was lost the moment the
user left the screen. Each turn appeared to start a brand-new conversation; the agent did
not remember prior turns; returning to the Playground showed a blank pane.

## Root cause (the design flaw, not the surface error)

The Playground run door was **single-turn by construction**:

1. `PlaygroundRunCreate` (`schemas.py`) had **no `session_id`** field, and the shared
   builder `_create_and_dispatch_playground_run` (`routers/playground.py`) never stamped one
   on the `PlaygroundRun` row. The reactive stream keys the checkpoint + transcript on
   `thread_id = run.session_id or run_id` (`playground.py`), so with `session_id` always NULL
   **every turn got `thread_id = run_id`** — a fresh one-turn thread per message. The agent
   never saw prior turns (different thread → different checkpoint), and no multi-turn
   conversation ever existed to reload.
2. `PlaygroundPage.tsx` had **no mount/return rehydration** — the only path that reloaded a
   past thread was a manual `ConversationSidebar` click. On navigate/reload all chat state
   reset to empty.

The `PlaygroundRun.session_id` **column already existed** (`models.py`, `String(256)`); only
the write path and the client were missing — so no migration was needed.

This is the same "two builders / a missing field never threaded through" class the shared
`_create_and_dispatch_playground_run` was created to prevent — here the field simply never
existed on the door at all.

## Fix (the class-fix)

Thread a stable per-chat `session_id` end-to-end and rehydrate on return — bringing the
Playground to parity with `AgentChatPage` (the working reference):

- **Backend:** add `session_id` to `PlaygroundRunCreate`; thread it through the shared
  builder → `PlaygroundRun(session_id=...)`. Non-chat doors (`test-event`, durable) pass
  `None` → unchanged `thread_id = run_id`. One builder, both doors, by construction.
- **Frontend:** `ChatPane` forwards a parent-owned `sessionId` on every `startPlaygroundRun`
  (only when set → the no-session path is byte-identical to before). `PlaygroundPage` uses its
  `chatKey` as that session id (uuid for a new chat, the thread id when seeding), and an effect
  **auto-rehydrates the agent's most recent thread on select** (reads the transcript back from
  the backend via `listConversations` → `seedFromThread`/`listMemory`, never client state).

Turns now thread into ONE reloadable conversation, the agent remembers prior turns (same
checkpoint), the History sidebar shows real multi-turn threads, and returning to an agent
resumes the last conversation.

## F-A investigated and NOT changed (recorded so it isn't silently dropped)

The pre-investigation plan also suspected **workflow** conversations were unattributed
(`start_workflow_run`'s `AgentRun` at `composite_workflows.py:505` has no `user_id`/`session_id`).
Re-verification on this base proved that path is **not** what backs workflow chat: the chat
surface (`WorkflowChatPage`) posts to `/runs/stream` → `stream_workflow_run`, whose parent run
**does** stamp `user_id=caller_sub` + `session_id` (`composite_workflows.py:616,619`), and the
conversation semi-join keys off exactly those on the parent (`memory.py:413-418,486-489`). So
workflow chat conversations already persist + rehydrate. Line 505 backs `triggerWorkflowRun`
(one-shot triggered runs), not chat — stamping it would be an unrequested behavior change.
**Left untouched.**

## Tests

- `studio/src/components/playground/ChatPane.test.tsx` — "threads turns: forwards sessionId as
  session_id …" (failing-first: pre-fix `startPlaygroundRun` sent no `session_id`).
- `studio/src/pages/PlaygroundPage.test.tsx` — "F-F: auto-rehydrates the agent's latest thread
  on select, with no manual click" (pre-fix had no mount effect → no `listMemory` on mount).
- Real save→reload→survived round-trip (deployed agent) is proven by the Playwright journey
  suite (Phase 2, leg 5) — recorded in the gap ledger until it runs on the cluster.

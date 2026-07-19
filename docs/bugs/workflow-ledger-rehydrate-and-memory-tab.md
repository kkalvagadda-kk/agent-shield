# Workflow run ledger: chat opened empty on a past session (G1) + the Memory tab was empty (G2)

**Found:** 2026-07-18 (follow-on gaps after the POC-5 Conversations-tab fix). **Fixed:** registry-api
`0.2.207` + studio `0.1.156`.

**Regression tests:** `scripts/e2e/suite-78-conversations.sh` **T-S78-006** (backend read-back),
`studio/src/pages/WorkflowChatPage.test.tsx` (G1), `studio/src/components/agent-detail/WorkflowMemoryTab.test.tsx`
(G2), `studio/e2e/workflow-memory.spec.ts` (both journeys). **Related:** the POC-5
`GET /workflows/{id}/conversations` fix (`memory.list_workflow_conversations`).

## Symptom
Two surfaces of the workflow deployment run-ledger were broken, both for the same underlying reason:

- **G1 — WorkflowChat opened empty on a past session.** Clicking a past Conversations row routed to
  `/workflows/{id}/d/{depId}/chat?session=<thread>` and *continued the correct session*, but the transcript
  panel showed the empty **"Send a message to run this workflow."** composer instead of replaying the prior
  turns. (The single-agent `AgentChatPage` already rehydrated; the workflow chat never did.)
- **G2 — the deployment Memory tab was empty.** It rendered `<MemoryTab agentName={workflow.name} …>`, which
  reads `GET /agents/{workflow_name}/memory` → `memory.list_recent(agent_name=workflow_name)`.

## Root cause
A workflow's transcript rows are authored by its **members**: `agent_memory.agent_name = <member>`,
`user_id = NULL`, `scope = 'workflow_run'`. So any read keyed by the *workflow's* name (or by a `user_id` on
the member rows) matches **zero** rows — exactly the trap POC-5 already fixed for the Conversations *list*.
Only the Conversations tab got the parent-run-scoped read; **the Memory tab and the chat rehydrate were left
on the per-agent path** (Memory tab) or had **no rehydrate at all** (chat). This is the same class as the
Conversations bug, not a new one — the fix simply had not been extended to these two surfaces.

## Fix (the class-fix, one shared backend read for both)
Added a workflow-scoped memory **entries** read that mirrors `list_workflow_conversations`' ownership
semi-join (the thread set is the workflow's parent runs' `session_id`s for the caller; ownership comes from
the parent run's `user_id`, since the member rows are NULL), but returns individual `AgentMemory` rows with a
dual ordering matching the per-agent `GET /agents/{name}/memory` contract:

- `services/registry-api/memory.py` — `list_workflow_memory(...)` (ORM select; `thread_id` given →
  `message_index ASC` for replay, absent → `created_at DESC` for the tab).
- `services/registry-api/conversation_store.py` — `list_workflow_memory` on the `ConversationStore` Protocol
  + Postgres adapter.
- `services/registry-api/routers/composite_workflows.py` — `GET /workflows/{id}/memory` (`require_user`,
  `_get_workflow` 404), returning `list[AgentMemoryResponse]`.
- `studio/src/api/registryApi.ts` — `listWorkflowMemory(workflowId, {thread_id?, limit?, offset?})`.
- **G1:** `studio/src/pages/WorkflowChatPage.tsx` — `seedFromThread` + a mount effect that replays the
  `?session` thread (mirror of `AgentChatPage`).
- **G2:** `studio/src/components/agent-detail/WorkflowMemoryTab.tsx` (new, read-only — member rows aren't
  agent-owned, so no Clear-All/Delete) wired into `WorkflowDeploymentOverviewPage` in place of `MemoryTab`.

**One endpoint serves both** (dual `thread_id` behavior copies the per-agent contract): the Memory tab lists
recent entries, WorkflowChat replays one thread — same read, same ownership rule.

## Deferred (intentional) — G3 per-deployment scoping
Playground/builder workflow runs write `workflow_deployment_id = NULL`, so the lists are scoped by
`workflow_id` + owner, not per-deployment. True per-deployment scoping first needs *deployed* runs to
populate that column at run time (a deployed-runtime change) — recorded in the gap ledger, not built here.

## Verification
- **Backend (save→reload→assert):** `suite-78 T-S78-006` seeds a `workflow_run` member row, then reads it
  back through `GET /workflows/{id}/memory` — asserts entries carry `agent_name=<member>` / `scope=workflow_run`,
  `?thread_id=` returns the thread oldest-first, and a non-owner (USER_B) gets `[]`. **6/6 green** on `0.2.207`.
- **Reproduce-first (frontend):** `WorkflowChatPage.test.tsx` + `WorkflowMemoryTab.test.tsx` both FAILED
  pre-fix (no fetch / component absent), pass post-fix. Full Vitest **343 green**, `tsc` clean.
- **End-user (real browser):** `studio/e2e/workflow-memory.spec.ts` asserts the Memory tab fires
  `GET /workflows/{id}/memory` + renders an entry, and a past session replays via `?thread_id=` (empty
  composer gone). Plus Claude-in-Chrome exploratory.

*(No numbered `docs/debugging/NNN` log — the diagnosis was a known class, a direct mirror of the shipped
Conversations fix, with no multi-layer dead-ends; per CLAUDE.md rule 8 the numbered log is required only when
the diagnosis was non-obvious. This postmortem + the gap-ledger note cover it.)*

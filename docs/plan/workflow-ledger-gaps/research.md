# Research & Decisions — Workflow ledger gaps

Grounded from the running product on 2026-07-18 (studio `0.1.154` / registry-api `0.2.206`). Every claim below cites a real `file:line` that was read, per CLAUDE.md rule 6.

---

## D1 — How `AgentChatPage` rehydration works, and the exact transcript read G1 reuses

`AgentChatPage` rehydrates a past session with two pieces (`studio/src/pages/AgentChatPage.tsx`):

1. **`seedFromThread(agentName, threadId, deploymentId)`** (L123-142) calls
   `listMemory(agentName, { thread_id, deployment_id, limit: 200 })`
   (`registryApi.ts:1608-1620` → `GET /api/v1/agents/{name}/memory?thread_id=…`), then
   `setSessionId(threadId)` and `setMessages(rows.filter(role∈{user,assistant}).map(...))`.
2. **A mount effect** (L146-153) reads `?session` from `useSearchParams` and calls `seedFromThread` once on mount (deep-link). History-row clicks call `seedFromThread` directly (L477).

The transcript endpoint is `GET /agents/{name}/memory` with `thread_id` (`routers/memory.py:120-185`). With `thread_id` present it delegates to `store.load(conversation_id=thread_id, …)` and returns turns oldest-first by `message_index`; with `thread_id` absent it delegates to `store.list_recent` and returns recent rows created_at DESC. **This dual behavior is the template the workflow endpoint copies.**

**Why G1 cannot reuse `GET /agents/{name}/memory` directly.** The transcript for a workflow lives under `scope='workflow_run'` and the per-agent endpoint first calls `_get_agent_or_404(name)`. A workflow's *name* is not an `agents` row, so any attempt to pass `workflow.name` 404s. (`memory.load_context`'s `workflow_run` branch, L153-179, already drops the `agent_name` predicate and would return the right rows — but the endpoint's 404 guard blocks reaching it.) Hence G1 needs a **workflow-scoped** endpoint, and the natural one is the same endpoint G2 introduces.

**Decision:** `WorkflowChatPage.seedFromThread(workflowId, threadId)` calls `listWorkflowMemory(workflowId, { thread_id: threadId, limit: 200 })` (the `thread_id` branch of the new endpoint), mirroring `AgentChatPage` line-for-line. `WorkflowChatPage` already initializes `sessionId` from `?session` (L60-62) and already renders member bubbles via `AttributedBubble` on the live stream (L302-321) — so seeding `messages` is the only missing piece.

**Author labeling nuance.** `AgentChatPage.seedFromThread` sets `author: r.agent_name` on every row. For a workflow, user-turn rows also carry a member `agent_name`, but the user branch (`WorkflowChatPage.tsx:303-308`) ignores `author`. To avoid ever mislabeling a user bubble, the workflow seed sets `author` only on assistant rows (`author: r.role === "assistant" ? r.agent_name : undefined`). Functionally identical to the reference; slightly cleaner.

---

## D2 — Why `list_workflow_memory` mirrors the Conversations join (member-name / NULL-user)

A workflow's transcript rows are authored by its **members**: `agent_memory.agent_name` = the member (e.g. `researcher-agent`), `scope='workflow_run'`, and `user_id = NULL` (the reactive member dispatch never threads the initiating user through). This is documented in the shipped Conversations fix (`docs/testing/manual-ui-e2e-test-plan.md:15+`) and visible in `memory._LIST_WORKFLOW_CONVERSATIONS_SQL` (`memory.py:396-423`).

Because of that, both the per-agent conversation list and the per-agent memory list fail for workflows:
- **Conversations** filtered `user_id = caller AND agent_name = workflow_name` → matched nothing. **Fixed** in POC-5 by resolving the thread set through the workflow's **parent runs** (`agent_runs.workflow_id` / `.user_id` / `.session_id == thread_id`) via a DISTINCT semi-join, taking ownership from the parent run (`list_workflow_conversations`, `memory.py:426-453`).
- **Memory** (`WorkflowDeploymentOverviewPage.tsx:198`) renders `<MemoryTab agentName={workflow.name}>`, which calls `listMemory(workflow.name)` → `GET /agents/{workflow_name}/memory` → `store.list_recent(agent_name=workflow_name)` (`memory.py:235-265`). `list_recent` filters `agent_name = workflow_name` — but member rows carry `agent_name = member`, so it matches **zero** rows → empty tab. **Same class**, still open.

**Decision:** `list_workflow_memory` reuses the identical ownership semi-join as `list_workflow_conversations` (thread set = the workflow's parent runs' `session_id`s for `user_id = caller`, `parent_run_id IS NULL`), but returns individual `AgentMemory` **entries** instead of aggregated summaries. Ownership is still derived from the parent run (member rows' `user_id` is NULL). This is the exact class-fix the Conversations tab received, applied to the Memory lens — not a patch.

**Difference from `list_workflow_conversations`:** it takes **no `workflow_name`**. The conversations aggregate substitutes the workflow name as the display `agent_name` (summaries have one name per thread). Entries must keep their author (`agent_name` = member) so the tab and the chat replay can show *which member* produced each row. Dropping `workflow_name` from the signature is correct, not an omission.

---

## D3 — New endpoint vs reusing an existing memory endpoint

Considered reusing `GET /agents/{name}/memory?scope=workflow_run` (its `load_context` workflow_run branch already reads by `thread_id + scope`, ignoring `agent_name`). **Rejected:** the endpoint's `_get_agent_or_404` guard (`routers/memory.py:141`) blocks it for a workflow name, and — critically — that path has **no ownership scoping** (any caller could read any workflow_run thread by id). The Conversations fix deliberately put workflow reads on `/workflows/{id}/…` with `require_user` + parent-run ownership. The Memory read must follow the same governance boundary.

**Decision:** add `GET /workflows/{id}/memory` (require_user, `_get_workflow` 404, ownership via parent run) — a sibling of the already-shipped `GET /workflows/{id}/conversations` (`composite_workflows.py:269-296`). One workflow-scoped surface, one ownership rule, reused by both the Memory tab (no `thread_id`) and the chat replay (`thread_id`).

**ORM select vs text SQL.** `_LIST_WORKFLOW_CONVERSATIONS_SQL` is raw `text()` because it aggregates (`GROUP BY thread_id`, `array_agg … FILTER`, `bool_or` for derived environment) — things the ORM expresses awkwardly. `list_workflow_memory` does **not** aggregate; it returns rows. Returning ORM `AgentMemory` rows lets the router call `AgentMemoryResponse.model_validate(r)` losslessly (id / message_index / created_at / member agent_name intact) — the exact contract `list_recent` already relies on for the per-agent Memory tab (`routers/memory.py:150-154`). And the dual ordering (`message_index ASC` for a thread vs `created_at DESC` for the list) is a one-line branch in ORM but painful to parameterize in `text()`. So the query is an ORM `select(AgentMemory)` with an `IN (select(AgentRun.session_id)…distinct())` semi-join — mirroring the Conversations *scoping semantics* while matching the *return contract* of `list_recent`.

---

## D4 — Endpoint response shape and client type

**Decision:** `GET /workflows/{id}/memory` → `list[AgentMemoryResponse]` (existing schema, `schemas.py:1857-1875`) — the same DTO the per-agent memory list returns, so the client reuses the existing `MemoryMessage` interface (`registryApi.ts:1594-1606`) with no new type. Each item carries the member author (`agent_name`), `scope="workflow_run"`, `user_id=null`, and `deployment_id=null` (playground/builder runs). `AgentMemoryResponse` is currently NOT imported in `composite_workflows.py` (its `schemas` import block is L39-59) — the import is added in T2.

`limit` defaults to `200` (covers the chat replay's 200-turn seed and the tab's 100), `le=500`.

---

## D5 — Frontend: separate `WorkflowMemoryTab` vs a mode param on `MemoryTab`

`MemoryTab` (`studio/src/components/agent-detail/MemoryTab.tsx`) is agent-scoped: it owns **Clear All** (`clearAgentMemory`, L32-38) and **Delete thread** (`deleteMemoryThread`, L24-30) mutations keyed by agent name. Those don't apply to workflow member rows (there is no workflow member clear/delete endpoint, and erasing member transcripts by agent name would be wrong). Overloading `MemoryTab` with a `kind: 'agent' | 'workflow'` param would force type-sniffing conditionals around every mutation — the anti-pattern CLAUDE.md's "no bandaid" section forbids.

**Decision:** new read-only `WorkflowMemoryTab.tsx` that calls `listWorkflowMemory` — mirroring the existing `WorkflowConversationsTab.tsx` precedent (a separate workflow component, not a mode flag on the agent one). It renders entries (role, author = member `agent_name`, content, created_at) with thread chips, no destructive actions.

---

## D6 — G3 (per-deployment scoping) deferral rationale

Playground and builder workflow runs write `agent_memory` rows with `workflow_deployment_id = NULL` (the reactive member dispatch does not tag a workflow deployment id). Confirmed by the existing gap note (`docs/testing/manual-ui-e2e-test-plan.md:51-54`) and by the fact that both the Conversations and the new Memory reads scope by `workflow_id` + owner (never by a workflow deployment id).

Therefore true per-deployment scoping is **not implementable from the read side today** — there is no populated column to filter on. It first requires *deployed* workflow runs to populate `workflow_deployment_id` at run time, which is a deployed-runtime (declarative-runner / orchestrator) change, out of scope for this read-only ledger work.

**Decision:** G3 stays **deferred (intentional)**, recorded in the gap ledger (T9). The current `workflow_id` + owner scoping is correct and safe for the playground/builder runs that exist; nothing is silently degraded.

---

## D7 — Test strategy (reproduce-first + save→reload→assert)

- **G2 backend reproduce + save→reload:** extend `suite-78` (already carries a workflow_run fixture: seed L154-177, `T-S78-005` L270-290) with `T-S78-006` that **seeds a member memory row via ORM then reads it back** through `GET /workflows/{id}/memory`. Fails today (404) → passes at CP1. This is the canonical persist→reload→assert for the new read surface.
- **G1 frontend reproduce:** `WorkflowChatPage.test.tsx` mirrors the `AgentChatPage.test.tsx:192-259` rehydration block (mock `listWorkflowMemory`, assert bubbles replay). Fails today (empty state, no fetch).
- **G2 frontend guard:** `WorkflowMemoryTab.test.tsx` mirrors `WorkflowConversationsTab.test.tsx` (asserts the workflow endpoint is used, not `listMemory`).
- **End-user proof:** `workflow-memory.spec.ts` (Playwright, real Keycloak) asserts both journeys with `waitForResponse` on `GET /workflows/{id}/memory` (+ `?thread_id`), plus Claude-in-Chrome exploratory at CP4.
- **Debugging log:** not written — the diagnosis is a known class (mirror of the shipped Conversations fix), so rule 8's numbered `docs/debugging/NNN` (next free `013`) does not apply; the `docs/bugs/…` postmortem covers it. Decision recorded in the gap ledger.

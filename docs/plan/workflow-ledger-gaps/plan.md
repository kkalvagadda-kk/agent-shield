# Implementation Plan — Workflow ledger gaps (G1 rehydrate chat · G2 Memory tab · G3 deferred)

**Source brief:** `docs/plan/workflow-ledger-gaps/design-brief.md`
**Grounded against:** studio `0.1.154` / registry-api `0.2.206` (running product, code read 2026-07-18).
**Target images:** registry-api `0.2.207` (G2 backend) · studio `0.1.155` (G1 + G2 wiring).
**Companion docs:** `research.md` (decisions), `quickstart.md` (copy-paste build/deploy/test).
No `data-model.md` — **no schema change**: this reuses `agent_memory` + `agent_runs` exactly as POC-5's Conversations fix did.

---

## Goal

Close the two remaining, already-diagnosed workflow run-ledger gaps and record the third as an intentional deferral:

- **G1** — `WorkflowChatPage` opens a selected past session on the empty *"Send a message to run this workflow."* state instead of replaying the past turns. It already continues the *correct* session (`?session=<thread_id>` routing works — `WorkflowChatPage.tsx:60-62`); it just never fetches + seeds the transcript. Mirror the WORKING rehydration in `AgentChatPage` (`seedFromThread` + mount effect, `AgentChatPage.tsx:123-153`).
- **G2** — The workflow deployment **Memory** tab is empty for the same member-name / NULL-user reason Conversations had. `WorkflowDeploymentOverviewPage.tsx:198` renders `<MemoryTab agentName={workflow.name} …>`, which reads `GET /agents/{workflow_name}/memory` → `list_recent(agent_name=workflow_name)` — but workflow-member `agent_memory` rows carry `agent_name`=member, `user_id`=NULL, `scope='workflow_run'`, so that read matches **zero** rows. Add a workflow-scoped read (`memory.list_workflow_memory` + store method + `GET /workflows/{id}/memory`) that resolves the thread set through the workflow's PARENT runs (exactly like `list_workflow_conversations`) and returns memory ENTRIES; wire a `WorkflowMemoryTab`.
- **G3** — Per-deployment scoping. **DEFERRED (intentional).** Playground/builder workflow runs carry `workflow_deployment_id = NULL`, so lists are scoped by `workflow_id` + owner, not per-deployment. True per-deployment scoping first needs *deployed* runs to populate that column at run time (a deployed-runtime change, out of scope). Recorded in the gap ledger, not implemented.

**One coherent feature area** (workflow run-ledger surfacing) → one plan. G1 and G2 share the exact same new backend read (`GET /workflows/{id}/memory`, dual `thread_id` behavior), so they ship together in studio `0.1.155`.

---

## Architecture

The workflow memory read mirrors the POC-5 Conversations fix precisely — the difference is that it returns **entries** (individual `agent_memory` rows), not per-thread **summaries**.

```
G2 (Memory tab, all entries)         G1 (chat replay, one thread)
        │                                     │
        ▼                                     ▼
WorkflowMemoryTab.tsx                 WorkflowChatPage.seedFromThread
  listWorkflowMemory(wfId,{limit})      listWorkflowMemory(wfId,{thread_id})
        └───────────────┬─────────────────────┘
                        ▼
      registryApi.listWorkflowMemory  →  GET /api/v1/workflows/{id}/memory?thread_id=
                        ▼
      composite_workflows.list_workflow_memory  (require_user; _get_workflow → 404)
                        ▼
      ConversationStore.list_workflow_memory  (port + Postgres adapter)
                        ▼
      memory.list_workflow_memory  (ORM select)
        scope='workflow_run'
        AND thread_id IN (SELECT DISTINCT session_id FROM agent_runs
                          WHERE workflow_id=:id AND parent_run_id IS NULL
                            AND user_id=:sub AND session_id IS NOT NULL)   ← ownership via PARENT run
        thread_id given  → one thread, ORDER BY message_index ASC   (replay order → G1)
        thread_id absent → recent entries,   ORDER BY created_at DESC (Memory tab → G2)
```

**Why one endpoint serves both G1 and G2:** the existing per-agent `GET /agents/{name}/memory` already has this exact dual shape — with `thread_id` it returns a transcript (`store.load`, message_index order); without it, recent rows (`store.list_recent`, created_at DESC) (`routers/memory.py:120-185`). The workflow endpoint copies that contract. G1 cannot reuse the per-agent endpoint because `_get_agent_or_404(workflow.name)` 404s (a workflow name is not an agent), so the workflow-scoped endpoint is required — and once it exists, its `thread_id` branch is exactly the transcript G1 needs.

**Ownership:** the workflow_run transcript rows have `user_id = NULL`, so ownership comes from the parent run's `user_id` (the same semi-join `_LIST_WORKFLOW_CONVERSATIONS_SQL` uses, `memory.py:396-423`). A caller only ever sees threads from workflow parent runs they own.

---

## Tech Stack

- **Backend:** FastAPI + SQLAlchemy async (`services/registry-api`). New read reuses `AgentMemory` + `AgentRun` ORM models — no migration.
- **Frontend:** React + Vite + React Query + TailwindCSS (`studio/`).
- **Tests:** bash+curl in-pod suites (`scripts/e2e/suite-78`), Vitest + React Testing Library (`studio/src/**/*.test.tsx`), Playwright real-Keycloak specs (`studio/e2e/*.spec.ts`), Claude-in-Chrome exploratory.
- **Deploy:** local docker-desktop k8s — `docker build` → `helm upgrade --install` (see `quickstart.md`).

---

## Constitution Check (CLAUDE.md Definition of Done, rules 1-8)

| Rule | Status | How satisfied |
|---|---|---|
| 1 — Real user journey proven (Playwright/manual) | PASS | `studio/e2e/workflow-memory.spec.ts` (T8) drives both journeys in a real browser: open the deployment **Memory** tab (asserts `GET /workflows/{id}/memory` via `waitForResponse`, a row renders) and open a past session in **WorkflowChat** (asserts a prior member bubble replays). Plus Claude-in-Chrome exploratory at CP4. |
| 2 — Save → reload → assert survived | PASS | Backend: `suite-78 T-S78-006` seeds a `workflow_run` member row via ORM, then `GET /workflows/{id}/memory` **reads it back** (persist → reload → assert). Frontend: `WorkflowMemoryTab.test.tsx` renders the reloaded entry; Playwright re-opens the tab and asserts the network read + rendered row. |
| 3 — No orphan code | PASS | Every new symbol has a caller in the same change: `memory.list_workflow_memory` ← store adapter; `ConversationStore.list_workflow_memory` ← router; `GET /workflows/{id}/memory` ← `registryApi.listWorkflowMemory` ← `WorkflowMemoryTab` + `WorkflowChatPage.seedFromThread`; `listWorkflowMemory` ← both consumers. Grep verification listed per task. |
| 4 — Vertical slices | PASS | G2 slice wired UI→API→DB→read-back (T1→T2→CP1→T4/T7→CP4) before/independent of G1's UI. Each checkpoint proves one thin path. |
| 5 — Honest gap ledger | PASS | T9 updates `docs/testing/manual-ui-e2e-test-plan.md`: flips G1+G2 (currently listed as *not-yet-wired debt* at L43-50) to **shipped**, keeps **G3 deferred (intentional)** (L51-54), and records the "no numbered debugging log — known class" decision. |
| 6 — Reason from running product | PASS | Every anchor in this plan is a real file:line read on 2026-07-18 (not the design doc). The brief's root cause was re-confirmed against `WorkflowDeploymentOverviewPage.tsx:198` + `memory.py:235-265`. |
| 7 — Bug fixes reproduce-first | PASS | G2 → **T1** (`suite-78 T-S78-006`, FAILS: endpoint 404) before **T2** fix. G1 → **T3** (`WorkflowChatPage.test.tsx`, FAILS: empty state, `listWorkflowMemory` never called) before **T6** fix. CP2 confirms both RED before any fix lands. No existing control is weakened. |
| 8 — Document the bug | PASS | T9 writes `docs/bugs/workflow-ledger-rehydrate-and-memory-tab.md` (Found/Fixed + Symptom + Root cause + Fix) cross-linking T-S78-006, the two Vitests, and `workflow-memory.spec.ts`. A numbered `docs/debugging/NNN` log is **intentionally not** written — the diagnosis is a known class (mirror of the already-documented Conversations fix; the brief settles it, no multi-layer dead-ends); this decision is recorded in the gap ledger + Execution Notes. |

---

## File Structure

| File | Created/Modified | Responsibility |
|---|---|---|
| `services/registry-api/memory.py` | Modified | Add `from models import AgentRun` to the L21 import; add `list_workflow_memory(...)` — ORM select, parent-run ownership semi-join, dual `thread_id` ordering, returns `list[AgentMemory]`. Mirrors `list_recent` (L235-265) return + the `_LIST_WORKFLOW_CONVERSATIONS_SQL` (L396-423) scoping. |
| `services/registry-api/conversation_store.py` | Modified | Add `list_workflow_memory(...)` to the `ConversationStore` Protocol (after L130) and the `PostgresConversationStore` adapter (after L276), delegating to `memory.list_workflow_memory`. Mirrors the `list_workflow_conversations` port pair (L115-130 / L259-276). |
| `services/registry-api/routers/composite_workflows.py` | Modified | Add `AgentMemoryResponse` to the `schemas` import (L39-59); add `GET /{workflow_id}/memory` endpoint (after the conversations endpoint, ~L297) → `AgentMemoryResponse` list, `require_user`, `_get_workflow` 404. Mirrors `GET /{workflow_id}/conversations` (L269-296). |
| `scripts/e2e/suite-78-conversations.sh` | Modified | Add `T-S78-006` reusing the existing workflow fixture (`wf_id`/`WF_MEMBER`/`T_WF`/`WF_FIRST`, seed L154-177): assert `GET /workflows/{id}/memory` returns the member entry for USER_A, `?thread_id=T_WF` returns that thread oldest-first, USER_B excluded. Append `"T-S78-006"` to `IDS` (L83). |
| `studio/src/api/registryApi.ts` | Modified | Add `listWorkflowMemory(workflowId, {thread_id?,limit?,offset?}) → MemoryMessage[]` → `GET /workflows/{id}/memory`, alongside `listWorkflowConversations` (L1685-1694). Reuses the existing `MemoryMessage` type (L1594-1606). |
| `studio/src/pages/WorkflowChatPage.tsx` | Modified | **G1 fix.** Import `useCallback` + `listWorkflowMemory`; add `seedFromThread(workflowId, threadId)` (fetch `listWorkflowMemory(id,{thread_id,limit:200})`, filter role∈{user,assistant}, map to `Message` with `author` on assistant rows) + a mount effect that seeds when `?session` is present. Mirrors `AgentChatPage.tsx:123-153`. |
| `studio/src/components/agent-detail/WorkflowMemoryTab.tsx` | **Created** | **G2 fix.** Read-only workflow memory lens: `useQuery(["workflow-memory", workflowId], () => listWorkflowMemory(workflowId,{limit:100}))`; renders entries (role, author=member `agent_name`, content, created_at) with thread chips (selecting a thread re-queries `{thread_id}`). No Clear/Delete (member rows aren't agent-owned). Separate component, mirroring `WorkflowConversationsTab.tsx`. |
| `studio/src/pages/WorkflowDeploymentOverviewPage.tsx` | Modified | Replace `import MemoryTab` (L19) + `<MemoryTab agentName={workflow?.name ?? id!} deploymentId={depId} />` (L198) with `WorkflowMemoryTab workflowId={id!} deploymentId={depId!}`; remove the now-unused `MemoryTab` import. |
| `studio/src/pages/WorkflowChatPage.test.tsx` | **Created** | **G1 reproduce + guard (Vitest).** Mirror `AgentChatPage.test.tsx:192-259`: render at `/workflows/wf-1/chat?session=thread-42`, mock `listWorkflowMemory`, assert it is called with `{thread_id:"thread-42"}` and prior bubbles render. FAILS pre-fix. |
| `studio/src/components/agent-detail/WorkflowMemoryTab.test.tsx` | **Created** | **G2 reproduce + save→reload guard (Vitest).** Mirror `WorkflowConversationsTab.test.tsx`: mock `listWorkflowMemory` → one entry; assert the tab renders it and calls `listWorkflowMemory(workflowId)` and does NOT call `listMemory`. FAILS pre-fix (component absent). |
| `studio/e2e/workflow-memory.spec.ts` | **Created** | **Playwright journeys.** Mirror `workflow-conversations.spec.ts`: (a) Memory tab → assert `waitForResponse` `GET /workflows/{id}/memory` + a row renders; (b) open a past session in WorkflowChat → assert `GET /workflows/{id}/memory?thread_id=` + a prior member bubble replays. Warm-fixture annotate-skip when the browser user has no runs; the network guard always runs. |
| `scripts/deploy-cpe2e.sh` | Modified | `REGISTRY_API_TAG` `0.2.206`→`0.2.207` (L295) + `STUDIO_TAG` `0.1.154`→`0.1.155` (L320) + comment headers. |
| `charts/agentshield/values.yaml` | Modified | registry-api tag `0.2.206`→`0.2.207` (L623) + studio tag `0.1.154`→`0.1.155` (L954) — mirror `deploy-cpe2e.sh` (helm uses baked values, no `--set`). |
| `docs/bugs/workflow-ledger-rehydrate-and-memory-tab.md` | **Created** | Per-bug postmortem for G1+G2 (rule 8). |
| `docs/testing/manual-ui-e2e-test-plan.md` | Modified | Flip G1+G2 to shipped; keep G3 deferred (intentional); record the debugging-log skip decision. |

---

## Key Interfaces (exact signatures — identical across all tasks)

### `memory.list_workflow_memory` (service, `memory.py`)
```python
async def list_workflow_memory(
    db: AsyncSession,
    *,
    workflow_id: str,
    user_id: str,
    thread_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[AgentMemory]:
    """Workflow memory ENTRIES scoped through the workflow's parent runs.

    Mirrors list_workflow_conversations' ownership semi-join (the thread set is the
    workflow's parent runs' session_ids for this owner — the workflow_run transcript
    rows carry user_id=NULL), but returns individual AgentMemory rows (entries), not
    per-thread summaries. Two orderings, matching the per-agent GET /agents/{name}/memory
    dual behavior (routers/memory.py:120-185):
      thread_id given  → one thread's transcript, ORDER BY message_index ASC  (replay → G1)
      thread_id absent → recent entries across the workflow's threads, created_at DESC (tab → G2)
    Returns [] for a workflow the caller has never run. No workflow_name needed —
    entries keep their author agent_name (the member), which the tab/replay want to show.
    """
```
Implementation (ORM, no text SQL — returns rows so `AgentMemoryResponse.model_validate` stays lossless, like `list_recent`):
```python
q = select(AgentMemory).where(
    AgentMemory.scope == "workflow_run",
    AgentMemory.thread_id.in_(
        select(AgentRun.session_id).where(
            AgentRun.workflow_id == _uuid.UUID(workflow_id),
            AgentRun.parent_run_id.is_(None),
            AgentRun.user_id == user_id,
            AgentRun.session_id.isnot(None),
        ).distinct()
    ),
)
if thread_id:
    q = q.where(AgentMemory.thread_id == thread_id).order_by(AgentMemory.message_index.asc())
else:
    q = q.order_by(AgentMemory.created_at.desc())
q = q.limit(limit).offset(offset)
return list((await db.execute(q)).scalars().all())
```

### `ConversationStore.list_workflow_memory` (port + Postgres adapter, `conversation_store.py`)
```python
async def list_workflow_memory(
    self,
    db: AsyncSession,
    *,
    workflow_id: str,
    user_id: str,
    thread_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[AgentMemory]: ...
```
Adapter body delegates verbatim to `memory.list_workflow_memory(db, workflow_id=…, user_id=…, thread_id=…, limit=…, offset=…)`.

### `GET /workflows/{workflow_id}/memory` (router, `composite_workflows.py`)
```python
@router.get(
    "/{workflow_id}/memory",
    response_model=list[AgentMemoryResponse],
    summary="List this workflow's memory entries for the caller (workflow ledger)",
)
async def list_workflow_memory(
    workflow_id: uuid.UUID,
    thread_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentMemoryResponse]:
    wf = await _get_workflow(workflow_id, db)  # 404 if the workflow is unknown
    store = get_conversation_store()
    rows = await store.list_workflow_memory(
        db, workflow_id=str(workflow_id), user_id=claims["sub"],
        thread_id=thread_id, limit=limit, offset=offset,
    )
    return [AgentMemoryResponse.model_validate(r) for r in rows]
```
**Response shape** — `list[AgentMemoryResponse]` (existing schema, `schemas.py:1857-1875`), each item:
```
{ agent_name: <member author>, thread_id, role, content, message_kind, scope: "workflow_run",
  id, message_index, created_at, user_id: null, session_id, deployment_id: null, workflow_run_id }
```

### `registryApi.listWorkflowMemory` (client, `registryApi.ts`)
```typescript
export const listWorkflowMemory = async (
  workflowId: string,
  params?: { thread_id?: string; limit?: number; offset?: number }
): Promise<MemoryMessage[]> => {
  const resp = await http.get(`/workflows/${workflowId}/memory`, { params });
  return resp.data;
};
```

### `WorkflowChatPage.seedFromThread` (G1, `WorkflowChatPage.tsx`)
```typescript
const seedFromThread = useCallback(async (workflowId: string, threadId: string) => {
  const rows = await listWorkflowMemory(workflowId, { thread_id: threadId, limit: 200 });
  setSessionId(threadId);
  setMessages(
    rows
      .filter((r) => r.role === "user" || r.role === "assistant")
      .map((r) => ({
        role: r.role as "user" | "assistant",
        content: r.content,
        author: r.role === "assistant" ? r.agent_name : undefined, // label member bubbles only
      })),
  );
}, []);
```

---

## Tasks (reproduce-first ordered)

> Legend: **[R]** reproduce (must FAIL first) · **[F]** fix/impl · **[CPn]** checkpoint (build+deploy+run).

### T1 — [R] Reproduce G2 backend: `suite-78 T-S78-006` (FAILS today)
- **Files:** `scripts/e2e/suite-78-conversations.sh`
- **Interface contract:** exercises `GET /api/v1/workflows/{wf_id}/memory` (+ `?thread_id=`). Reuses the existing seed (L154-177): workflow `wf_id`, parent `AgentRun` owned by USER_A with `session_id=T_WF`, two `workflow_run` member rows (`agent_name=WF_MEMBER`, `user_id=NULL`).
- **Acceptance:** New `T-S78-006` asserts, for USER_A's bearer token: `GET /workflows/{wf_id}/memory` returns ≥2 rows all with `thread_id==T_WF`, `agent_name==WF_MEMBER`, `scope=="workflow_run"`; the first user row's content `==WF_FIRST`; `?thread_id=T_WF` returns the thread oldest-first (`message_index` ascending); USER_B's call returns `[]` (ownership via parent run). Append `"T-S78-006"` to `IDS` (L83). Ordering/env-skip discipline identical to T-S78-005.
- **Dependencies:** none.
- **Test cases:** happy path (USER_A sees entries), ownership (USER_B excluded), thread filter (single-thread, ordered).
- **Verification:** `bash scripts/e2e/suite-78-conversations.sh` → `T-S78-006 FAIL http=404` (endpoint absent). Records the reproduce.

### T2 — [F] G2 backend: query + store method + endpoint (+ registry-api tag bump)
- **Files:** `services/registry-api/memory.py`, `services/registry-api/conversation_store.py`, `services/registry-api/routers/composite_workflows.py`, `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
- **Interface contract:** the four signatures in **Key Interfaces**, byte-consistent.
- **Acceptance:** `memory.list_workflow_memory` added (with `from models import AgentRun`); Protocol + adapter method added; `GET /{workflow_id}/memory` added with `AgentMemoryResponse` imported; `REGISTRY_API_TAG`→`0.2.207` in `deploy-cpe2e.sh` (L295) + values.yaml (L623) with comment headers.
- **Dependencies:** T1.
- **Test cases:** covered by T-S78-006 (turns green at CP1).
- **Verification:** `python3 -c "import ast; ast.parse(open('services/registry-api/memory.py').read())"` (repeat for the 2 other .py); mapper check — `cd services/registry-api && python3 -c "import conversation_store, memory; from routers import composite_workflows; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('ok')"`. No orphan: `grep -rn "list_workflow_memory" services/registry-api` shows service←adapter←router chain.

### CP1 — Build+deploy registry-api `0.2.207`; run suite-78
- Build + `helm upgrade` registry-api (quickstart §2). Run `bash scripts/e2e/suite-78-conversations.sh`.
- **Gate:** `T-S78-006 PASS` **and** `T-S78-001..005` stay green (regression: same suite, shared fixture/teardown). If red, fix before proceeding.

### T3 — [R] Reproduce G1: `WorkflowChatPage.test.tsx` (FAILS today)
- **Files:** `studio/src/pages/WorkflowChatPage.test.tsx` (new)
- **Interface contract:** `vi.mock('../api/registryApi')` providing `getCompositeWorkflow`, `getWorkflowRunTree`, `workflowRunStreamUrl`, `listWorkflowMemory`; mock keycloak; `MockEventSource`/`scrollIntoView` shims (copy from `AgentChatPage.test.tsx:36-52`). Route `/workflows/:id/chat`.
- **Acceptance:** Test "rehydrates prior member turns when opened with `?session=<tid>`" mocks `listWorkflowMemory` → `[user "summarize the Q3 report", assistant(author "summarizer") "Here is the summary."]`, renders `/workflows/wf-1/chat?session=thread-42`, asserts `listWorkflowMemory` called with `("wf-1", objectContaining({thread_id:"thread-42"}))` and both bubbles render; and asserts the empty state text `Send a message to run this workflow.` is NOT present.
- **Dependencies:** none (API mocked).
- **Test cases:** deep-link seed replays; (optional) no `?session` → nothing seeded.
- **Verification:** `cd studio && npx vitest run src/pages/WorkflowChatPage.test.tsx` → FAIL (`listWorkflowMemory` never called; empty state shown).

### T4 — [R] Reproduce G2 frontend + save→reload guard: `WorkflowMemoryTab.test.tsx` (FAILS today)
- **Files:** `studio/src/components/agent-detail/WorkflowMemoryTab.test.tsx` (new)
- **Interface contract:** `vi.mock('../../api/registryApi')` with `listWorkflowMemory` (+ `listMemory` as a spy to assert-not-called); `renderWithProviders`. Mirror `WorkflowConversationsTab.test.tsx`.
- **Acceptance:** mock `listWorkflowMemory` → one entry `{agent_name:"summarizer", thread_id:"wf-t1", role:"assistant", content:"Here is the Q3 summary.", …}`; assert the content renders, `listWorkflowMemory` called with `("wf-1", …)`, and `listMemory` NOT called (the bug was querying the per-agent list).
- **Dependencies:** none.
- **Test cases:** renders reloaded entry; does not fall back to `listMemory`.
- **Verification:** `cd studio && npx vitest run src/components/agent-detail/WorkflowMemoryTab.test.tsx` → FAIL (component file/import absent — the failing observation for the empty tab).

### CP2 — Reproduce-first gate (confirm RED)
- `cd studio && npx vitest run src/pages/WorkflowChatPage.test.tsx src/components/agent-detail/WorkflowMemoryTab.test.tsx`.
- **Gate:** both **FAIL** (G1 empty-state / no fetch; G2 missing component). This is the recorded reproduce evidence for rule 7 before any fix.

### T5 — [F] `registryApi.listWorkflowMemory`
- **Files:** `studio/src/api/registryApi.ts`
- **Interface contract:** the `listWorkflowMemory` signature above; placed next to `listWorkflowConversations` (L1685-1694); returns `MemoryMessage[]`.
- **Acceptance:** function exported; `GET /workflows/{id}/memory` with `params` passthrough.
- **Dependencies:** T3, T4 (their mocks reference it).
- **Test cases:** consumed by T6/T7.
- **Verification:** `cd studio && npm run typecheck`. No orphan: `grep -rn "listWorkflowMemory" studio/src` (added here; wired by T6/T7).

### T6 — [F] G1 fix: WorkflowChatPage rehydration
- **Files:** `studio/src/pages/WorkflowChatPage.tsx`
- **Interface contract:** `seedFromThread` above + a mount `useEffect` mirroring `AgentChatPage.tsx:146-153` (seed once from `?session` when `id` present; `// eslint-disable-next-line react-hooks/exhaustive-deps`). Import `useCallback` (extend L1) + `listWorkflowMemory` (extend L5-11 import).
- **Acceptance:** opening `?session=<tid>` fetches + replays past turns as attributed member bubbles; the New-conversation reset (L73-78) still clears + re-keys the session.
- **Dependencies:** T5.
- **Test cases:** T3 flips to PASS.
- **Verification:** `cd studio && npx vitest run src/pages/WorkflowChatPage.test.tsx` → PASS. No orphan: `grep -n "seedFromThread\|listWorkflowMemory" studio/src/pages/WorkflowChatPage.tsx`.

### T7 — [F] G2 frontend fix: `WorkflowMemoryTab` + page wiring
- **Files:** `studio/src/components/agent-detail/WorkflowMemoryTab.tsx` (new), `studio/src/pages/WorkflowDeploymentOverviewPage.tsx`
- **Interface contract:** `WorkflowMemoryTab({ workflowId, deploymentId }: { workflowId: string; deploymentId: string })`; `useQuery(["workflow-memory", workflowId, selectedThread], () => listWorkflowMemory(workflowId, { thread_id: selectedThread ?? undefined, limit: 100 }))`. Read-only rendering (role, author=`agent_name`, content, created_at) with thread chips (mirror `MemoryTab.tsx:40-124` minus Clear/Delete). Page: import `WorkflowMemoryTab`, render `{activeTab === "memory" && <WorkflowMemoryTab workflowId={id!} deploymentId={depId!} />}` (replaces L198), remove the `MemoryTab` import (L19).
- **Acceptance:** the workflow deployment Memory tab lists entries via the workflow endpoint; `MemoryTab` import removed (no unused import → typecheck clean).
- **Dependencies:** T5.
- **Test cases:** T4 flips to PASS.
- **Verification:** `cd studio && npx vitest run src/components/agent-detail/WorkflowMemoryTab.test.tsx` → PASS. No orphan: `grep -rn "WorkflowMemoryTab" studio/src` (component ← page).

### CP3 — Typecheck + full Vitest green
- `cd studio && npm run typecheck && npm run test`.
- **Gate:** typecheck clean; all Vitest green including the two new files + the untouched `AgentChatPage.test.tsx` / `WorkflowConversationsTab.test.tsx` (regression: shared `ConversationSidebar` / registryApi surface).

### T8 — [F] Playwright journeys: `workflow-memory.spec.ts`
- **Files:** `studio/e2e/workflow-memory.spec.ts` (new)
- **Interface contract:** mirror `workflow-conversations.spec.ts` (real Keycloak via `global-setup`, `ADMIN` X-User headers for API prep, `PLAYWRIGHT_BASE_URL`). Find a workflow deployment; prefer one with memory entries for the browser user.
- **Acceptance:** (a) `page.goto(/workflows/{id}/d/{depId})` → click **memory** tab → `waitForResponse` on `GET /api/v1/workflows/{id}/memory` (status 200) → if rows, assert an entry renders; (b) click **conversations** tab → click a row → URL `…/chat?session=<thread>` → `waitForResponse` on `GET /api/v1/workflows/{id}/memory?thread_id=…` → assert a prior member bubble is visible in `[data-testid=workflow-chat-transcript]`. Warm-fixture annotate-skip when the user has no runs (network guards still run).
- **Dependencies:** T6, T7 (and deployed studio at CP4).
- **Test cases:** Memory-tab network+render; chat replay network+bubble; empty-fixture skip.
- **Verification:** runs at CP4 (`bash scripts/studio-e2e.sh workflow-memory.spec.ts`).

### T9 — [F] Docs, gap ledger, studio tag bump
- **Files:** `docs/bugs/workflow-ledger-rehydrate-and-memory-tab.md` (new), `docs/testing/manual-ui-e2e-test-plan.md`, `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
- **Interface contract:** bug doc sections per rule 8 — one-line title; **Found/Fixed** (2026-07-18, registry-api `0.2.207` + studio `0.1.155`); **Symptom** (G1 empty replay; G2 empty tab); **Root cause** (member-name/NULL-user; `WorkflowChatPage` never seeds; `MemoryTab` keyed by `workflow.name` → `list_recent` matches nothing); **Fix** (workflow memory read via parent-run semi-join + `seedFromThread` + `WorkflowMemoryTab`) — the class-fix, cross-linking `T-S78-006`, `WorkflowChatPage.test.tsx`, `WorkflowMemoryTab.test.tsx`, `workflow-memory.spec.ts`.
- **Acceptance:** gap ledger L43-50 flipped to a **shipped** section (mirror the existing Conversations-tab section at L15+); L51-54 **G3** kept as **deferred (intentional)**, reworded to reference this plan + the `workflow_deployment_id` NULL reason; the "no numbered debugging log (known class)" decision recorded. `STUDIO_TAG`→`0.1.155` (`deploy-cpe2e.sh` L320 + values.yaml L954) with comment headers.
- **Dependencies:** T6, T7.
- **Verification:** headers present; `grep -n "0.1.155\|0.2.207" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` shows both bumped in both files.

### CP4 — Build+deploy studio `0.1.155`; end-user verification + regression sweep
- Build + `helm upgrade` studio (quickstart §2). Then:
  - **Playwright:** `bash scripts/studio-e2e.sh workflow-memory.spec.ts` **and** `bash scripts/studio-e2e.sh workflow-conversations.spec.ts` (regression — shared route/sidebar) green.
  - **Claude-in-Chrome exploratory (real user):** log in, open a reactive workflow deployment → **Memory** tab renders entries (not empty); open **Conversations** → click a past session → WorkflowChat **replays** the prior member bubbles (not the empty composer).
  - **Regression sweep:** re-run `bash scripts/e2e/suite-78-conversations.sh` (all green), `cd studio && npm run typecheck && npm run test`.
- **Gate:** all green; both end-user journeys visibly correct.

---

## Complexity Tracking

| Concern | Decision | Rationale |
|---|---|---|
| One endpoint (dual `thread_id`) vs two | **One** `GET /workflows/{id}/memory` | Copies the proven per-agent `GET /agents/{name}/memory` dual contract (`routers/memory.py:120-185`); G1 (thread) + G2 (list) are the same read at two scopes — fewer surfaces, one ownership rule. |
| ORM select vs text SQL for the query | **ORM select** (unlike `_LIST_WORKFLOW_CONVERSATIONS_SQL` text) | No `GROUP BY` (entries, not summaries) → returning ORM rows keeps `AgentMemoryResponse.model_validate` lossless, exactly like `list_recent`; dual ordering is trivial in ORM, awkward in parameterized text. Ownership semi-join semantics still mirror the Conversations SQL. (See research.md.) |
| New `WorkflowMemoryTab` vs a mode-param on `MemoryTab` | **New component** | `MemoryTab` owns agent-scoped Clear-All / Delete-thread mutations that don't apply to workflow member rows; splitting avoids a type-sniff/conditional (CLAUDE.md "no bandaid"), and matches the existing `WorkflowConversationsTab` precedent. |
| G3 per-deployment scoping | **Deferred (intentional)** | Playground/builder runs write `workflow_deployment_id=NULL`; real per-deployment scoping needs deployed runs to populate it first — a runtime change out of scope. Recorded in the gap ledger, not built. |

---

## Execution Notes

- **Shared studio build (coordination).** studio `0.1.155` also carries an **unrelated** `EditAgentModal` `knowledge_search` class-fix, tracked separately as task **#16** — NOT part of this plan's task list. The studio deploy at CP4 is therefore shared/coordinated: build once, verify both changes. Do not re-bump the studio tag for #16.
- **Reproduce-first is enforced by ordering:** T1 (backend RED) → CP1 (green after T2); T3+T4 (frontend RED) → CP2 confirms RED → fixes T5-T7 → CP3 green. Never write a fix before its failing test/observation exists.
- **Tag bumps travel with their service change:** registry-api tag in T2, studio tag in T9 — both mirrored into `deploy-cpe2e.sh` AND `values.yaml` (helm uses baked values, no `--set`).
- **No migration.** Confirm `alembic` is untouched; the read reuses `agent_memory` + `agent_runs`.
- **Debugging log intentionally skipped.** The G2 diagnosis is a known class (mirror of the shipped Conversations fix; brief settles root cause) — no multi-layer dead-ends — so `docs/debugging/NNN` (next free `013`) is not written; the postmortem `docs/bugs/…` + this note in the gap ledger cover it (rule 8 requires the numbered log only when the diagnosis was non-obvious).
- **Experience docs:** `WorkflowChatPage` / `WorkflowDeploymentOverviewPage` are NOT in CLAUDE.md's `docs/experience/playground.md` trigger list; the canonical record for this workflow surface is `docs/testing/manual-ui-e2e-test-plan.md` (updated in T9). No `playground.md` change required.

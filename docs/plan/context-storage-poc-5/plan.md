# POC-5 — Conversations & Memory in the Product · Implementation Plan

**Branch**: `worktree-ux-preview-context-storage` — commit here **only**; never merge/PR
to main (Karthik merges manually).
**Spec (authoritative)**: `docs/design/context-storage-poc-5-conversations.md`.
**Companions**: `context-storage-ux-roadmap.md` §6, `context-storage-architecture.md` §11.
**Baseline (verified)**: `registry-api:0.2.191`, `studio:0.1.144`, `declarative-runner:0.1.56`,
Alembic head `0065`.
**Targets**: `registry-api:0.2.193`, `studio:0.1.146`, declarative-runner unchanged.

Read `research.md` + `data-model.md` + `contracts/list-conversations.md` before coding.
Every ground-truth claim there was read from code, not the design doc.

---

## 1. Goal

Make stored conversations **visible and resumable** everywhere a chat is exposed, in
sandbox and production. POC-0/1 already persist every turn (`agent_memory`) and
continue-with-context already works (re-POST with the same `session_id` reloads prior
turns). The **only** missing piece is a **list** read + the surfaces that render it:

1. **Backend**: one new read query `memory.list_conversations` (grouped by `thread_id`)
   behind the `ConversationStore` port, exposed by **two** `require_user` endpoints —
   scoped (`GET /agents/{name}/memory/conversations?deployment_id=`) and cross-agent
   (`GET /me/conversations`).
2. **Frontend**: **one** shared `ConversationSidebar` mounted at **three** surfaces —
   standalone `Conversations` page (promoted to real nav), docked History in
   `AgentChatPage` (sandbox) + `CatalogChatPage` (production), and a new `Conversations`
   tab on `DeploymentOverviewPage` (deployment-scoped). `New conversation` → fresh uuid +
   clear; `Select` → set `sessionId = thread_id` + seed messages from `listMemory` → the
   chat rehydrates and the reply box continues on that thread.

**Done = a real user journey proven** (Playwright, all three surfaces: reload → listed →
click → transcript rehydrates → follow-up recalls a turn-1 fact), a save→reload→assert
round-trip, and no orphaned symbol.

> **Alignment Check:** the ultimate goal is *user-facing, resumable conversation history*.
> The plan resists the easy-but-wrong shortcut of stamping an `environment` column on
> writes (a schema change + a runner rebuild + a backfill) — research §R2 shows environment
> is a **pure join** on the already-stamped `deployment_id`. The read query owns the
> derivation; nothing upstream changes. That keeps the slice a read + UI slice, exactly
> as scoped.

## 2. Architecture

```
                               ┌─────────────────────────────────────────────┐
                               │ Postgres: agent_memory  (⨯ no new columns)   │
                               │   group by thread_id, LEFT JOIN              │
                               │   production_deployments → environment       │
                               └───────────────▲─────────────────────────────┘
                                               │ raw SQL aggregate
                        memory.list_conversations(user_id, agent_name?, deployment_id?)
                                               │
                        ConversationStore.list_conversations  (port + PG adapter)
                                               │
                 ┌─────────────────────────────┴──────────────────────────────┐
   GET /agents/{name}/memory/conversations?deployment_id=      GET /me/conversations
        (routers/memory.py, require_user, scoped)          (routers/me.py, require_user)
                 │                                                    │
                 │  ConversationSummary[]                             │
   ┌─────────────┴───────────────┐                    ┌──────────────┴───────────────┐
   │ listConversations(name,..)  │                    │ listMyConversations(..)       │  registryApi.ts
   └─────────────┬───────────────┘                    └──────────────┬───────────────┘
                 │                                                    │
        ┌────────┴─────────────────────  ConversationSidebar  ───────┴────────┐
        │            (one component, three mounts; env-filter helper)          │
        ├── AgentChatPage (docked, sandbox)   ── onSelect → seed via listMemory │
        ├── CatalogChatPage (docked, prod)    ── onSelect → seed via listMemory │
        ├── DeploymentOverviewPage → ConversationsTab (scoped, nav to chat)     │
        └── ConversationsPage (standalone, /me, All/Sandbox/Production filter)  │
```

**Continue** needs no new backend: selecting a row sets `sessionId = thread_id` and seeds
the chat pane from `listMemory(thread_id)`; the next POST reuses that `session_id` and the
runner reloads earlier turns as context.

## 3. Tech Stack

- **Backend**: Python 3.11, FastAPI, SQLAlchemy async (`AsyncSession`), raw `text()` SQL
  for the aggregate (`array_agg … FILTER`, `bool_or`), Pydantic v2 DTO. Auth via
  `require_user` (`auth_middleware.py`). Transcript seam = `ConversationStore` +
  `store_factory.get_conversation_store()`.
- **Frontend**: React + TypeScript, Vite, React Query, TailwindCSS, `react-router-dom`,
  `lucide-react`. Axios client (`http`, `baseURL:/api/v1`).
- **Tests**: Vitest + React Testing Library (`renderWithProviders`, `vi.mock`), Playwright
  (real Keycloak, `scripts/studio-e2e.sh`), bash+httpx e2e (`kubectl exec` into
  `registry-api`, `X-User-Sub` headers) → **suite-78**.
- **Deploy**: `scripts/deploy-cpe2e.sh` (kind) / `scripts/deploy-eks.sh` (EKS), Helm
  `charts/agentshield/values.yaml`. **User-gated** shared-cluster step.

## 4. Constitution Check (CLAUDE.md)

| Gate | How POC-5 satisfies it |
|---|---|
| **DoD 1 — real journey, not endpoint** | Playwright drives all three surfaces incl. the deployment Conversations tab: reload → click → rehydrate → follow-up recalls turn-1 (T14). |
| **DoD 2 — save→reload→assert** | Every surface's Playwright reloads the page and asserts the conversation is still listed + its transcript re-reads from `/memory` (backend), not client state. suite-78 asserts summaries survive a fresh query. |
| **DoD 3 — no orphan code** | T-grep step per new symbol: `list_conversations`, `ConversationSummary`, `listConversations`, `listMyConversations`, `ConversationSidebar`, `ConversationsTab`, the `conversations` Tab literal — each has a live caller in the same change. |
| **DoD 4 — vertical slice** | Slice A wires backend end-to-end (query→port→endpoints→suite-78) and deploys+smokes (CP-A) before any UI. Slice B wires one surface (docked AgentChatPage) fully before the others. |
| **DoD 5 — honest gap ledger** | §8 lists deferred items (Haiku titling, production agent-deployment-overview page, per-user MemoryTab privacy) tagged deferred vs debt; mirrored into `docs/testing/manual-ui-e2e-test-plan.md` header. |
| **DoD 6 — reason from running product** | Plan is grounded in code (research.md), not the design doc; `environment`-not-stored and the sandbox-only deployment-overview route were found by reading, and corrected here. |
| **No-bandaid** | Environment is derived by an explicit `production_deployments` join (class-correct), not `getattr`/`isinstance` sniffing or a per-write column hack. New endpoints take an **explicit** `require_user` identity, never a query-param user id. |
| **Post-impl: e2e / image bumps / experience docs / FE tests / verify** | suite-78 + run-all registration (T6); image bumps in all three files (T15); `docs/experience/playground.md` updated (T13); Vitest + Playwright (T8–T14); typecheck + `ast.parse` + `configure_mappers` (Verify rows). |

## 5. File Structure

| # | File | New/Edit | Task |
|---|---|---|---|
| 1 | `services/registry-api/memory.py` | Edit — add `list_conversations` | T1 |
| 2 | `services/registry-api/conversation_store.py` | Edit — port method + PG adapter | T2 |
| 3 | `services/registry-api/schemas.py` | Edit — `ConversationSummary` | T3 |
| 4 | `services/registry-api/routers/memory.py` | Edit — scoped endpoint + `require_user` | T4 |
| 5 | `services/registry-api/routers/me.py` | Edit — `/me/conversations` | T5 |
| 6 | `scripts/e2e/suite-78-conversations.sh` | New — bash e2e | T6 |
| 6 | `scripts/e2e/run-all.sh` | Edit — register suite-78 | T6 |
| 7 | `studio/src/api/registryApi.ts` | Edit — type + 2 client fns | T7 |
| 8 | `studio/src/components/conversations/ConversationSidebar.tsx` | New — shared component + `filterConversationsByEnv` | T8 |
| 8 | `studio/src/components/conversations/ConversationSidebar.test.tsx` | New — Vitest | T8 |
| 9 | `studio/src/pages/AgentChatPage.tsx` | Edit — resettable session + `?session` seed + docked sidebar | T9 |
| 9 | `studio/src/pages/AgentChatPage.test.tsx` | Edit — update | T9 |
| 10 | `studio/src/pages/CatalogChatPage.tsx` | Edit — resettable session + docked sidebar | T10 |
| 10 | `studio/src/pages/CatalogChatPage.test.tsx` | Edit — update | T10 |
| 11 | `studio/src/pages/ConversationsPage.tsx` | New — real standalone page | T11 |
| 11 | `studio/src/pages/ConversationsPage.test.tsx` | New — Vitest (env filter) | T11 |
| 12 | `studio/src/components/agent-detail/ConversationsTab.tsx` | New — deployment-scoped tab | T12 |
| 12 | `studio/src/pages/DeploymentOverviewPage.tsx` | Edit — add `conversations` Tab | T12 |
| 12 | `studio/src/components/agent-detail/ConversationsTab.test.tsx` | New — Vitest | T12 |
| 13 | `studio/src/components/Sidebar.tsx` | Edit — retire PREVIEW_ITEMS, add real nav | T13 |
| 13 | `studio/src/App.tsx` | Edit — `/conversations` route | T13 |
| 13 | `docs/experience/playground.md` | Edit — document the surfaces | T13 |
| 14 | `studio/e2e/conversations-sidebar.spec.ts` | New — standalone + docked | T14 |
| 14 | `studio/e2e/deployment-conversations.spec.ts` | New — deployment tab, both envs | T14 |
| 15 | `scripts/deploy-cpe2e.sh` | Edit — tags 0.2.193 / 0.1.146 | T15 |
| 15 | `scripts/deploy-eks.sh` | Edit — tags | T15 |
| 15 | `charts/agentshield/values.yaml` | Edit — tags | T15 |

Preview mock `studio/src/pages/preview/ConversationsPage.tsx` is **not deleted** — it stays
demo-only until `DEMO` is retired; the real page is a new sibling at `pages/ConversationsPage.tsx`.

## 6. Key Interfaces (exact signatures)

### Backend

```python
# memory.py  (new)
async def list_conversations(
    db: AsyncSession,
    *,
    user_id: str,
    agent_name: str | None = None,
    deployment_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Grouped-by-thread_id conversation summaries owned by user_id, newest-first.
    Keys per row: thread_id, session_id, agent_name, title, message_count,
    last_activity (datetime), environment ('sandbox'|'production'), deployment_id (str|None).
    Environment via LEFT JOIN production_deployments (bool_or). See data-model §3."""
```

```python
# conversation_store.py  — add to BOTH the Protocol and PostgresConversationStore
async def list_conversations(
    self,
    db: AsyncSession,
    *,
    user_id: str,
    agent_name: str | None = None,
    deployment_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    ...
# adapter body: return await memory.list_conversations(db, user_id=user_id,
#     agent_name=agent_name, deployment_id=deployment_id, limit=limit, offset=offset)
```

```python
# schemas.py  (new)
class ConversationSummary(BaseModel):
    thread_id: str
    session_id: str | None = None
    agent_name: str
    title: str | None = None
    message_count: int
    last_activity: datetime
    environment: str                 # 'sandbox' | 'production'
    deployment_id: uuid.UUID | None = None
```

```python
# routers/memory.py  (new endpoint; require_user added to imports)
@router.get("/{name}/memory/conversations", response_model=list[ConversationSummary],
            summary="List the caller's conversations with this agent")
async def list_agent_conversations(
    name: str,
    deployment_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]: ...
```

```python
# routers/me.py  (new endpoint)
@router.get("/conversations", response_model=list[ConversationSummary],
            summary="List the caller's conversations across all agents")
async def list_my_conversations(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]: ...
```

### Frontend

```ts
// registryApi.ts  (new)
export interface ConversationSummary {
  thread_id: string;
  session_id: string | null;
  agent_name: string;
  title: string | null;
  message_count: number;
  last_activity: string;
  environment: "sandbox" | "production";
  deployment_id: string | null;
}

export const listConversations = async (
  agentName: string,
  params?: { deployment_id?: string; limit?: number; offset?: number },
): Promise<ConversationSummary[]> => {
  const resp = await http.get(`/agents/${agentName}/memory/conversations`, { params });
  return resp.data;
};

export const listMyConversations = async (
  params?: { limit?: number; offset?: number },
): Promise<ConversationSummary[]> => {
  const resp = await http.get(`/me/conversations`, { params });
  return resp.data;
};
```

```ts
// components/conversations/ConversationSidebar.tsx  (new)
export type EnvFilter = "all" | "sandbox" | "production";

export function filterConversationsByEnv(
  list: ConversationSummary[],
  env: EnvFilter,
): ConversationSummary[]; // env==="all" ? list : list.filter(c => c.environment === env)

export type ConversationScope =
  | { kind: "agent"; agentName: string; deploymentId?: string }
  | { kind: "me" };

export interface ConversationSidebarProps {
  scope: ConversationScope;
  activeThreadId: string | null;
  onSelect: (summary: ConversationSummary) => void;   // consumer seeds/navigates
  onNew: () => void;
  showEnvFilter?: boolean;                             // standalone page only
  className?: string;
}
export default function ConversationSidebar(props: ConversationSidebarProps): JSX.Element;
```

`ConversationSidebar` is **pure list + filter**: it fetches via React Query
(`listConversations` for `agent` scope, `listMyConversations` for `me`), renders rows
(title / agent / env badge / count / relative time), a `New conversation` button, and an
optional All/Sandbox/Production filter. It does **not** fetch transcripts — each consumer
seeds on `onSelect`.

```ts
// AgentChatPage.tsx + CatalogChatPage.tsx  — resettable session
const [sessionId, setSessionId] = useState(() =>
  searchParams.get("session") ?? crypto.randomUUID());
// helper reused by both mounts + the ?session seed:
async function seedFromThread(agentName: string, threadId: string, deploymentId?: string) {
  const rows = await listMemory(agentName, { thread_id: threadId, deployment_id: deploymentId, limit: 200 });
  setSessionId(threadId);
  setMessages(rows
    .filter(r => r.role === "user" || r.role === "assistant")
    .map(r => ({ role: r.role as "user" | "assistant", content: r.content, author: r.agent_name })));
}
```

## 7. Tasks

Legend — **Deps** are task IDs; **Verify** commands are copy-pasteable.

---

### Slice A — Backend (vertical: query → port → DTO → endpoints → e2e → deploy)

#### T1 — `memory.list_conversations`
- **Files**: `services/registry-api/memory.py`
- **Interface**: `list_conversations(db, *, user_id, agent_name=None, deployment_id=None, limit=100, offset=0) -> list[dict]` (§6).
- **Do**: add the raw-SQL aggregate from data-model §3 (`array_agg … FILTER`, `bool_or`,
  `LEFT JOIN production_deployments`, casted optional binds). Return
  `[dict(r._mapping) for r in result]`. Import nothing new beyond `text` (already imported).
- **Acceptance**: with two owned threads (one w/ a `production_deployments` deployment_id,
  one sandbox), returns 2 summaries, correct `title`/`message_count`/`environment`,
  newest-first; another user's rows never appear.
- **Deps**: none.
- **Tests**: exercised by suite-78 (T6).
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('memory.py').read())"`

#### T2 — `ConversationStore.list_conversations` (port + adapter)
- **Files**: `services/registry-api/conversation_store.py`
- **Interface**: §6 — identical signature on the `ConversationStore` Protocol and
  `PostgresConversationStore`; adapter delegates to `memory.list_conversations`.
- **Do**: add `Any` to the `typing` import if needed; keep the seam intact (router never
  calls `memory.*` directly for this read).
- **Acceptance**: `get_conversation_store().list_conversations(...)` returns the same dicts
  as T1.
- **Deps**: T1.
- **Tests**: suite-78 (T6).
- **Verify**: `python3 -c "import ast; ast.parse(open('conversation_store.py').read())"`

#### T3 — `ConversationSummary` schema
- **Files**: `services/registry-api/schemas.py`
- **Interface**: §6 Pydantic model. Place next to `AgentMemoryResponse` (~L1863).
- **Acceptance**: `ConversationSummary(**row)` validates a T1 dict (datetime + uuid coerce).
- **Deps**: none.
- **Tests**: suite-78 (T6).
- **Verify**: `python3 -c "import ast; ast.parse(open('schemas.py').read())"`

#### T4 — scoped endpoint `GET /agents/{name}/memory/conversations`
- **Files**: `services/registry-api/routers/memory.py`
- **Interface**: §6 `list_agent_conversations`. Add `from auth_middleware import require_user`
  and import `ConversationSummary` from `schemas`.
- **Do**: `_get_agent_or_404`; `store.list_conversations(db, user_id=claims["sub"],
  agent_name=name, deployment_id=deployment_id, limit=limit, offset=offset)`;
  `return [ConversationSummary(**r) for r in rows]`. Declare it above the existing
  `DELETE /{name}/memory/{thread_id}` block for readability (no method conflict, but keep
  static paths grouped).
- **Acceptance**: `200` + `ConversationSummary[]`; `404` unknown agent; a second
  `X-User-Sub` gets its own (disjoint) list; `deployment_id` narrows to that deployment.
- **Deps**: T2, T3.
- **Tests**: suite-78 T-S78-001/002/003.
- **Verify**: mapper import below.

#### T5 — cross-agent endpoint `GET /me/conversations`
- **Files**: `services/registry-api/routers/me.py`
- **Interface**: §6 `list_my_conversations`. Add imports: `Query` (from fastapi),
  `get_conversation_store` (from `store_factory`), `ConversationSummary` (from `schemas`).
- **Do**: `store.list_conversations(db, user_id=claims["sub"], limit=limit, offset=offset)`
  → `[ConversationSummary(**r) ...]`. No agent/deployment filter.
- **Acceptance**: returns every owned thread across agents, each with `environment`;
  ownership-scoped.
- **Deps**: T2, T3.
- **Tests**: suite-78 T-S78-004.
- **Verify**:
  `python3 -c "import routers.memory, routers.me, schemas; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('ok')"`

#### T6 — suite-78 e2e + registration
- **Files**: `scripts/e2e/suite-78-conversations.sh` (new), `scripts/e2e/run-all.sh` (edit)
- **Do**: mirror `suite-76-preferences.sh` (kubectl exec `registry-api`, `httpx`,
  `X-User-Sub`/`X-User-Team` headers). Seed via `POST /agents/{name}/memory` with a real
  agent, two threads for USER_A (one with a real `production_deployments.id` as
  `deployment_id`, one sandbox/no deployment_id) and one thread for USER_B. `chmod +x`.
  Register in `run-all.sh` after suite-76/77:
  `run_suite "Suite 78: Conversations (POC-5 list)" "suite-78-conversations.sh"`.
- **Cases**:
  - `T-S78-001` — `GET /agents/{name}/memory/conversations` for USER_A returns per-thread
    summaries: `title` = first user message, correct `message_count`, `last_activity`
    present, newest-first.
  - `T-S78-002` — ownership: USER_B's list excludes USER_A's threads (and vice-versa).
  - `T-S78-003` — `?deployment_id=<prod id>` returns only the production thread, tagged
    `environment="production"`; `?deployment_id=<sandbox id>` returns only the sandbox
    thread tagged `"sandbox"`.
  - `T-S78-004` — `GET /me/conversations` for USER_A returns **both** threads (cross-agent),
    each carrying its `environment`.
- **Acceptance**: `bash scripts/e2e/suite-78-conversations.sh` prints all `RESULT … PASS`,
  exit 0.
- **Deps**: T4, T5 (**deployed** — run after CP-A).
- **Verify**: `bash -n scripts/e2e/suite-78-conversations.sh && test -x scripts/e2e/suite-78-conversations.sh`

---

### ✅ CHECKPOINT CP-A — Backend deployed + smoked (see `quickstart.md §CP-A`)
Bump tags (T15 registry-api portion), build+deploy registry-api, run suite-78 green.
**Gate: do not start Slice B until CP-A passes.** Executable: `quickstart.md` CP-A block.

---

### Slice B — Frontend (shared component first, then the three mounts)

#### T7 — API client: type + two functions
- **Files**: `studio/src/api/registryApi.ts`
- **Interface**: §6 `ConversationSummary`, `listConversations`, `listMyConversations`.
  Place in the Memory section (~L1557).
- **Acceptance**: `import { listConversations, listMyConversations, ConversationSummary }`
  resolves; typecheck clean.
- **Deps**: none (parallel with Slice A).
- **Tests**: consumed by T8–T12 tests.
- **Verify**: `cd studio && npm run typecheck`

#### T8 — `ConversationSidebar` (shared) + `filterConversationsByEnv`
- **Files**: `studio/src/components/conversations/ConversationSidebar.tsx` (new),
  `…/ConversationSidebar.test.tsx` (new)
- **Interface**: §6 (`ConversationSidebarProps`, `filterConversationsByEnv`, `EnvFilter`,
  `ConversationScope`).
- **Do**: React Query keyed `["conversations", scope]`; `agent` scope →
  `listConversations(agentName, {deployment_id})`, `me` scope → `listMyConversations()`.
  Render: `New conversation` button (calls `onNew`), optional env-filter pills
  (`showEnvFilter`), rows via `filterConversationsByEnv` (title | `"Untitled conversation"`,
  agent name, env badge sbx/prod, `message_count` turns, relative `last_activity`), active
  row highlighted by `activeThreadId`. Empty state: "No conversations yet." Loading state.
  `onSelect(summary)` on row click.
- **Acceptance (Vitest)**: renders a mocked list; empty state when `[]`; clicking a row
  calls `onSelect` with that summary; `New conversation` calls `onNew`; `filterConversationsByEnv`
  returns all for `"all"` and only-matching for `"sandbox"`/`"production"`.
- **Deps**: T7.
- **Tests**: `ConversationSidebar.test.tsx` (list / empty / select / new / filter predicate).
- **Verify**: `cd studio && npm run test -- ConversationSidebar && npm run typecheck`

#### T9 — AgentChatPage: resettable session + `?session` seed + docked History
- **Files**: `studio/src/pages/AgentChatPage.tsx`, `studio/src/pages/AgentChatPage.test.tsx`
- **Interface**: `const [sessionId, setSessionId] = useState(...)`; `seedFromThread` (§6);
  `useSearchParams` for `?session`.
- **Do**: (a) change L76 to `useState(() => searchParams.get("session") ?? crypto.randomUUID())`;
  (b) on mount, if `?session` present, `seedFromThread(name, session, depId)`; (c) add a
  header `History` toggle button that mounts `<ConversationSidebar scope={{kind:"agent",
  agentName:name, deploymentId:depId}} activeThreadId={sessionId} onSelect={s =>
  seedFromThread(name, s.thread_id, depId)} onNew={() => { setSessionId(crypto.randomUUID());
  setMessages([]); }} />` in a left/right dock (reuse the existing flex shell). Guard: block
  select/new while `isStreaming || awaitingApproval`.
- **Acceptance**: opening `?session=<tid>` rehydrates prior turns from `/memory`; selecting a
  row in the dock swaps the transcript and re-keys `sessionId`; New clears + fresh uuid;
  sending after a select reuses the thread's `session_id`.
- **Deps**: T7, T8.
- **Tests**: extend `AgentChatPage.test.tsx` — mock `listMemory`/`listConversations`; assert
  seed-on-`?session` and select-swaps-transcript.
- **Verify**: `cd studio && npm run test -- AgentChatPage && npm run typecheck`

#### T10 — CatalogChatPage: resettable session + docked History (production)
- **Files**: `studio/src/pages/CatalogChatPage.tsx`, `studio/src/pages/CatalogChatPage.test.tsx`
- **Do**: same pattern as T9 with `scope={{kind:"agent", agentName, deploymentId:
  activeDeployment?.id}}`. Add `import { listMemory, listConversations } from ...`. Reuse
  the `mk` bubble factory; only seed user/assistant rows (workflow rich slots aren't
  reconstructed on seed — plain bubbles, matching the reload path's non-workflow branch).
  `sessionId` → `[sessionId, setSessionId]`.
- **Acceptance**: docked History lists this production deployment's threads; select rehydrates;
  New resets; follow-up continues the thread.
- **Deps**: T7, T8.
- **Tests**: extend `CatalogChatPage.test.tsx` (mock catalog detail + listConversations +
  listMemory; assert dock render + select swap).
- **Verify**: `cd studio && npm run test -- CatalogChatPage && npm run typecheck`

#### T11 — standalone `ConversationsPage` (real, promoted)
- **Files**: `studio/src/pages/ConversationsPage.tsx` (new),
  `studio/src/pages/ConversationsPage.test.tsx` (new)
- **Do**: two-pane. Left = `<ConversationSidebar scope={{kind:"me"}} showEnvFilter
  activeThreadId={selected?.thread_id ?? null} onSelect={setSelected} onNew={...} />`. Right
  = read-only transcript preview of `selected` via `listMemory(selected.agent_name,
  {thread_id, deployment_id})` + a **Continue** button → `navigate(\`/agents/${selected.agent_name}/chat?session=${selected.thread_id}\`)`
  (sandbox resume path; production standalone-continue is a known gap §8). Header copy
  matches the mock's intent (no `ConsoleContextBar`, no amber preview banner — this is real).
- **Acceptance**: lists the caller's conversations across agents; env pills filter client-side;
  selecting shows the transcript; Continue navigates to the seeded chat.
- **Deps**: T7, T8.
- **Tests (Vitest)**: mock `listMyConversations` with mixed envs → assert All shows both,
  Sandbox shows only sandbox, Production shows only production (the env-filter predicate on
  the page); empty state.
- **Verify**: `cd studio && npm run test -- ConversationsPage && npm run typecheck`

#### T12 — Deployment `Conversations` tab
- **Files**: `studio/src/components/agent-detail/ConversationsTab.tsx` (new),
  `studio/src/pages/DeploymentOverviewPage.tsx` (edit),
  `studio/src/components/agent-detail/ConversationsTab.test.tsx` (new)
- **Do**: `ConversationsTab({ agentName, deploymentId })` = `<ConversationSidebar
  scope={{kind:"agent", agentName, deploymentId}} activeThreadId={null} onSelect={s =>
  navigate(\`/agents/${agentName}/d/${deploymentId}/chat?session=${s.thread_id}\`)}
  onNew={() => navigate(\`/agents/${agentName}/d/${deploymentId}/chat\`)} />` (nav reuses the
  full AgentChatPage deployment-chat machinery + T9's `?session` seed — no chat-logic
  duplication). In `DeploymentOverviewPage.tsx`: extend `type Tab = "overview" | "runs" |
  "memory" | "conversations";`; add `"conversations"` to the tab-map array (label
  auto-capitalizes); add `{activeTab === "conversations" && <ConversationsTab agentName={name!}
  deploymentId={depId} />}`. Keep the existing `memory` tab (MemoryTab) untouched —
  Conversations sits **beside** it (operator-inspect vs user-resume).
- **Acceptance**: the tab bar shows Overview / Runs / Memory / Conversations; Conversations
  lists this deployment's threads; clicking navigates to the deployment chat seeded with that
  session; the Memory tab still works.
- **Deps**: T8, T9 (nav relies on T9's `?session` seed).
- **Tests (Vitest)**: `DeploymentOverviewPage.test.tsx` (or new) renders the 4th tab + mounts
  `ConversationsTab`; `ConversationsTab.test.tsx` renders the sidebar with the deployment scope.
- **Verify**: `cd studio && npm run test -- DeploymentOverview ConversationsTab && npm run typecheck`

#### T13 — Nav promotion + route + experience doc
- **Files**: `studio/src/components/Sidebar.tsx`, `studio/src/App.tsx`,
  `docs/experience/playground.md`
- **Do**: (a) Sidebar — remove the `DEMO`-gated `Context Preview` section (`PREVIEW_ITEMS`
  block, L236–246) and add a real top-level `Conversations` item (`History` icon) →
  `/conversations`; drop the now-unused `PREVIEW_ITEMS` const (grep first — it's only used
  there). Put the item near the top (its own single-item group, or head of Build). (b) App —
  add `<Route path="/conversations" element={<ConversationsPage />} />` importing the **new**
  `./pages/ConversationsPage` (leave `/preview/conversations` pointing at the mock while
  `DEMO` remains). (c) `docs/experience/playground.md` — add a "Conversations & History"
  section covering the three surfaces + the two endpoints + env filter + continue behaviour.
- **Acceptance**: `Conversations` appears in real nav (non-DEMO); `/conversations` renders the
  real page; no dead `PREVIEW_ITEMS` reference; experience doc updated.
- **Deps**: T11.
- **Tests**: covered by T14 Playwright (nav → page).
- **Verify**: `cd studio && npm run typecheck && grep -rn "PREVIEW_ITEMS" src` (expect: no hits)

#### T14 — Playwright (all three surfaces, both envs where applicable)
- **Files**: `studio/e2e/conversations-sidebar.spec.ts` (new),
  `studio/e2e/deployment-conversations.spec.ts` (new)
- **Do**: real Keycloak (global-setup); target the https gateway. Per the DoD, each spec:
  send a turn-1 message carrying a memorable fact → reload the page → assert the conversation
  is **listed** (from backend) → click it → assert the prior transcript **rehydrates**
  (`page.waitForResponse` on `/memory`) → send a follow-up → assert the reply **recalls the
  turn-1 fact** (or, where few agent pods are deployed, assert the request fired +
  `session_id` reused — same boundary the bash suites accept).
  - `conversations-sidebar.spec.ts`: (1) standalone `/conversations` — list + env filter +
    Continue → seeded chat; (2) docked History in `AgentChatPage` (sandbox).
  - `deployment-conversations.spec.ts`: `/agents/:name/d/:depId` → Conversations tab → list →
    click → nav to `?session` chat → rehydrate → follow-up. Assert the scoped list only
    shows this deployment's threads. (Production deployment-scoping is proven at the endpoint
    by suite-78 T-S78-003; the page-level run uses the reachable sandbox route — §8 gap.)
- **Acceptance**: `bash scripts/studio-e2e.sh` green for both specs.
- **Deps**: T9, T11, T12, T13, **CP-C deploy**.
- **Verify**: `bash scripts/studio-e2e.sh` (after CP-C).

---

### T15 — Image bumps (all three files, same commit)
- **Files**: `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`
- **Do**: `REGISTRY_API_TAG 0.2.191 → 0.2.193`, `STUDIO_TAG 0.1.144 → 0.1.146` in all three
  (deploy-cpe2e.sh L266/273, deploy-eks.sh L67/70, values.yaml L597/917). Update the comment
  headers ("POC-5 conversations list + sidebar"). Leave `DECLARATIVE_RUNNER_TAG 0.1.56`.
- **Acceptance**: `grep -R "0.2.193" scripts/deploy-cpe2e.sh scripts/deploy-eks.sh charts/agentshield/values.yaml`
  → 3 hits; same for `0.1.146`; no residual `0.2.191`/`0.1.144` for these two services.
- **Deps**: none (values consumed at deploy). Do the registry-api bump before CP-A, the
  studio bump before CP-C (or all at once with T15 — either is fine as long as CP-A ships
  0.2.193 and CP-C ships 0.1.146).
- **Verify**: `grep -c "0.2.193" charts/agentshield/values.yaml && grep -c "0.1.146" charts/agentshield/values.yaml`

---

## 8. Known gaps (ledger — mirror into `docs/testing/manual-ui-e2e-test-plan.md` header)

- **Haiku conversation titles** — *deferred (intentional)* → POC-1b. Title = first user
  message until then.
- **Production agent-deployment-overview page** — *deferred (intentional)*. No dedicated
  production route for `DeploymentOverviewPage` exists today (research §R6); the Conversations
  **tab** is reachable on the sandbox route only. Production deployment-scoping is proven at
  the **endpoint** by suite-78 T-S78-003.
- **Standalone Continue for production rows** — *not-yet-wired (debt)*. `ConversationsPage`
  Continue routes to the sandbox agent chat (`/agents/:name/chat?session=`); production rows
  need artifact-id resolution to route to `CatalogChatPage`. Rows still list + preview; only
  the Continue target is sandbox. (Docked History in `CatalogChatPage` covers production
  resume.)
- **`user_id IS NULL` rows invisible** — *by design*. Daemon/legacy turns have no owner.
- **Admin `MemoryTab` per-user privacy** — *deferred* → Tighten S9 (unchanged by POC-5).
- **Aggregate index** — *deferred (intentional)*. No `(user_id, created_at)` index yet;
  add if suite-78 shows slowness at scale.

## 9. MVP critical path

`T3 → T1 → T2 → T4 → T5 → T15(registry-api) → CP-A → T7 → T8 → T9 → T11 → T12 → T13 →
T15(studio) → CP-C → T14`.
(T6 lands with Slice A but runs after CP-A; T10 parallels T9–T12; CP-B — typecheck + Vitest —
gates before CP-C.)

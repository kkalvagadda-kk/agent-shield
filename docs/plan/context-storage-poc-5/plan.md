# POC-5 — Conversations & Memory in the Product · Implementation Plan

**Branch**: `worktree-ux-preview-context-storage` — commit here **only**; never merge/PR
to main (Karthik merges manually).
**Spec (authoritative)**: `docs/design/context-storage-poc-5-conversations.md`.
**Companions**: `context-storage-ux-roadmap.md` §6, `context-storage-architecture.md` §11.

**Baseline (verified against code 2026-07-17, on top of the uncommitted POC-4 working tree)**:
`registry-api:0.2.195`, `studio:0.1.146` (deploy-cpe2e.sh; `values.yaml` lags at `0.1.145`),
`declarative-runner:0.1.57`, Alembic head **`0067`** (POC-4).
**Targets**: `registry-api:0.2.195` (**NO bump — backend already shipped**), `studio:0.1.147`,
declarative-runner unchanged. **No migration.**

> Read `research.md` + `data-model.md` + `contracts/list-conversations.md` before coding.
> Every ground-truth claim there was read from the running code on 2026-07-17, not the
> design doc. **The entire backend slice is already implemented, committed (`83199f5`),
> and live in `registry-api:0.2.195` — it is marked ✅ DONE below and MUST NOT be
> re-specified as work.** The remaining work is the frontend slice + `suite-78`.

---

## 1. Goal

Make stored conversations **visible and resumable** everywhere a chat is exposed, in
sandbox and production. POC-0/1 already persist every turn (`agent_memory`) and
continue-with-context already works (re-POST with the same `session_id` reloads prior
turns via `declarative-runner/main.py::_load_memory_context`). The backend list read and
its two endpoints are **already shipped**. What remains is the **surface layer**:

1. **Backend (✅ DONE)**: `memory.list_conversations` (grouped by `thread_id`) behind the
   `ConversationStore` port, exposed by two `require_user` endpoints — scoped
   (`GET /agents/{name}/memory/conversations?deployment_id=`) and cross-agent
   (`GET /me/conversations`). Verified live in `0.2.195`.
2. **Frontend (the work)**: one shared `ConversationSidebar` mounted at **three** surfaces —
   standalone `Conversations` page (promoted to real nav), docked History in
   `AgentChatPage` (sandbox) + `CatalogChatPage` (production), and a new `Conversations`
   tab on `DeploymentOverviewPage` (deployment-scoped). `New conversation` → fresh uuid +
   clear; `Select` → set `sessionId = thread_id` + seed messages from `listMemory` → the
   chat rehydrates and the reply box continues on that thread.
3. **Test/deploy (the work)**: `suite-78` backend e2e (runs against the already-live pod),
   Vitest for the new components, Playwright for the three surfaces, `studio:0.1.147` bump.

**Done = a real user journey proven** (Playwright, all three surfaces: reload → listed →
click → transcript rehydrates → follow-up recalls a turn-1 fact), a save→reload→assert
round-trip, and no orphaned symbol.

> **Alignment Check:** the ultimate goal is *user-facing, resumable conversation history*.
> The shipped backend resisted the easy-but-wrong shortcut of stamping an `environment`
> column on writes (a schema change + a runner rebuild + a backfill) — research §R2 shows
> environment is a **pure join** on the already-stamped `deployment_id`. The read query owns
> the derivation; nothing upstream changes. This plan keeps that discipline on the frontend:
> the sidebar is **one** component reused at three mounts (no per-surface fork), each consumer
> passing an explicit `scope`, never sniffing an implicit env.

## 2. Architecture

```
                               ┌─────────────────────────────────────────────┐
                               │ Postgres: agent_memory  (⨯ no new columns)   │
                               │   group by thread_id, LEFT JOIN              │
                               │   production_deployments → environment       │
                               └───────────────▲─────────────────────────────┘
                                               │ raw SQL aggregate  ✅ SHIPPED
                        memory.list_conversations(user_id, agent_name?, deployment_id?)
                                               │
                        ConversationStore.list_conversations  (port + PG adapter)  ✅ SHIPPED
                                               │
                 ┌─────────────────────────────┴──────────────────────────────┐
   GET /agents/{name}/memory/conversations?deployment_id=      GET /me/conversations
        (routers/memory.py, require_user, scoped) ✅          (routers/me.py, require_user) ✅
                 │                                                    │
                 │  ConversationSummary[]  ✅ SHIPPED                 │
   ┌─────────────┴───────────────┐                    ┌──────────────┴───────────────┐
   │ listConversations(name,..)  │  ← NEW (T2)        │ listMyConversations(..)       │  ← NEW (T2)
   └─────────────┬───────────────┘                    └──────────────┬───────────────┘
                 │                                                    │
        ┌────────┴─────────────────────  ConversationSidebar  ───────┴────────┐  ← NEW (T3)
        │            (one component, three mounts; env-filter helper)          │
        ├── AgentChatPage (docked, sandbox)   ── onSelect → seed via listMemory │  ← T4
        ├── CatalogChatPage (docked, prod)    ── onSelect → seed via listMemory │  ← T5
        ├── ConversationsPage (standalone, /me, All/Sandbox/Production filter)  │  ← T6
        └── DeploymentOverviewPage → ConversationsTab (scoped, nav to chat)     │  ← T7
```

**Continue** needs no new backend: selecting a row sets `sessionId = thread_id` and seeds
the chat pane from `listMemory(thread_id)`; the next POST reuses that `session_id` and the
runner reloads earlier turns as context.

## 3. Tech Stack

- **Backend (already shipped)**: Python 3.11, FastAPI, SQLAlchemy async (`AsyncSession`),
  raw `text()` SQL for the aggregate (`array_agg … FILTER`, `bool_or`), Pydantic v2 DTO.
  Auth via `require_user` (`auth_middleware.py`). Transcript seam = `ConversationStore` +
  `store_factory.get_conversation_store()`.
- **Frontend**: React + TypeScript, Vite, React Query, TailwindCSS, `react-router-dom`,
  `lucide-react`. Axios client (`http`, `baseURL:/api/v1`).
- **Tests**: Vitest + React Testing Library (`renderWithProviders`, `vi.mock`), Playwright
  (real Keycloak, `scripts/studio-e2e.sh`), bash+httpx e2e (`kubectl exec` into
  `registry-api`, `X-User-Sub` headers) → **suite-78**.
- **Deploy**: **LOCAL docker-desktop** via `scripts/deploy-cpe2e.sh` + Helm
  (`charts/agentshield/values.yaml`). Checkpoints run locally. **Do NOT** use
  `scripts/deploy-eks.sh` for this work (that is the shared EKS path).

## 4. Constitution Check (CLAUDE.md)

| Gate | How POC-5 satisfies it |
|---|---|
| **DoD 1 — real journey, not endpoint** | Playwright drives all three surfaces incl. the deployment Conversations tab: reload → click → rehydrate → follow-up recalls turn-1 (T10). |
| **DoD 2 — save→reload→assert** | Every surface's Playwright reloads the page and asserts the conversation is still listed + its transcript re-reads from `/memory` (backend), not client state. suite-78 asserts summaries survive a fresh query. |
| **DoD 3 — no orphan code** | Grep step per new symbol: `listConversations`, `listMyConversations`, `ConversationSummary` (TS), `ConversationSidebar`, `filterConversationsByEnv`, `ConversationsTab`, the `conversations` Tab literal — each has a live caller in the same change (quickstart no-orphan block). Backend symbols already have live callers (shipped). |
| **DoD 4 — vertical slice** | Backend is a proven, deployed slice (query→port→endpoints, live in 0.2.195). Frontend wires one surface (docked AgentChatPage, T4) fully before the others. |
| **DoD 5 — honest gap ledger** | §8 lists deferred items (Haiku titling, production agent-deployment-overview route, standalone Continue for production rows, per-user MemoryTab privacy) tagged deferred vs debt; mirrored into `docs/testing/manual-ui-e2e-test-plan.md` header. |
| **DoD 6 — reason from running product** | Plan is grounded in code read 2026-07-17 (research.md): the shipped SQL, the shipped schema shape (`agent_name`/`last_activity`/`deployment_id` all Optional in Pydantic), the POC-4 uncommitted citation wiring, and the sandbox-only deployment-overview route were all found by reading, not the design doc. |
| **No-bandaid** | Environment is derived by an explicit `production_deployments` join (class-correct), not `getattr`/`isinstance` sniffing. `ConversationSidebar` takes an **explicit** `scope` discriminated union — no per-surface fork, no implicit env sniffing. |
| **Post-impl: e2e / image bumps / experience docs / FE tests / verify** | suite-78 + run-all registration after suite-77 (T1); studio image bump in deploy-cpe2e.sh + values.yaml (T11); `docs/experience/playground.md` new section without clobbering POC-4 (T9); Vitest + Playwright (T3–T7, T10); frontend types validated via the **studio Docker build** (`tsc && vite build`) since node/npm is not on this host (see §Verify note). |

## 5. File Structure

Legend: ✅ = already shipped (do NOT touch as work); **New/Edit** = the remaining work.
Preserve the **uncommitted POC-4 working-tree changes** in every file marked (POC-4) —
those files carry citation wiring that is not yet committed.

| # | File | New/Edit | Task |
|---|---|---|---|
| — | `services/registry-api/memory.py` | ✅ DONE — `list_conversations` (array_agg[1] uuid fix) | — |
| — | `services/registry-api/conversation_store.py` | ✅ DONE — port method + PG adapter | — |
| — | `services/registry-api/schemas.py` | ✅ DONE — `ConversationSummary` (~L1874) | — |
| — | `services/registry-api/routers/memory.py` | ✅ DONE — scoped endpoint + `require_user` | — |
| — | `services/registry-api/routers/me.py` | ✅ DONE — `/me/conversations` | — |
| 1 | `scripts/e2e/suite-78-conversations.sh` | New — bash e2e | T1 |
| 1 | `scripts/e2e/run-all.sh` | Edit — register suite-78 after suite-77 | T1 |
| 2 | `studio/src/api/registryApi.ts` | Edit — TS type + 2 client fns | T2 |
| 3 | `studio/src/components/conversations/ConversationSidebar.tsx` | New — shared component + `filterConversationsByEnv` | T3 |
| 3 | `studio/src/components/conversations/ConversationSidebar.test.tsx` | New — Vitest | T3 |
| 4 | `studio/src/pages/AgentChatPage.tsx` (POC-4 citation wiring) | Edit — resettable session + `?session` seed + docked History | T4 |
| 4 | `studio/src/pages/AgentChatPage.test.tsx` | Edit — update | T4 |
| 5 | `studio/src/pages/CatalogChatPage.tsx` (POC-2b + POC-4 rich wiring) | Edit — resettable session + docked History | T5 |
| 5 | `studio/src/pages/CatalogChatPage.test.tsx` | Edit — update | T5 |
| 6 | `studio/src/pages/ConversationsPage.tsx` | New — real standalone page | T6 |
| 6 | `studio/src/pages/ConversationsPage.test.tsx` | New — Vitest (env filter) | T6 |
| 7 | `studio/src/components/agent-detail/ConversationsTab.tsx` | New — deployment-scoped tab | T7 |
| 7 | `studio/src/pages/DeploymentOverviewPage.tsx` | Edit — add `conversations` Tab | T7 |
| 7 | `studio/src/components/agent-detail/ConversationsTab.test.tsx` | New — Vitest | T7 |
| 8 | `studio/src/components/Sidebar.tsx` | Edit — retire PREVIEW_ITEMS, add real nav | T8 |
| 8 | `studio/src/App.tsx` (POC-4 knowledge routes) | Edit — `/conversations` route | T8 |
| 9 | `docs/experience/playground.md` (POC-4 section at L557) | Edit — append Conversations & History section | T9 |
| 10 | `studio/e2e/conversations-sidebar.spec.ts` | New — standalone + docked | T10 |
| 10 | `studio/e2e/deployment-conversations.spec.ts` | New — deployment tab | T10 |
| 11 | `scripts/deploy-cpe2e.sh` | Edit — `STUDIO_TAG 0.1.146 → 0.1.147` | T11 |
| 11 | `charts/agentshield/values.yaml` | Edit — studio tag `0.1.145 → 0.1.147` | T11 |

Preview mock `studio/src/pages/preview/ConversationsPage.tsx` is **not deleted** — it stays
demo-only until `DEMO` is retired; the real page is a new sibling at `pages/ConversationsPage.tsx`.
`scripts/deploy-eks.sh` is **intentionally not** in this table — POC-5 deploys locally
(docker-desktop). registry-api stays `0.2.195` (no bump — backend already shipped).

## 6. Key Interfaces

### Backend — ✅ already shipped (reference only; do NOT re-implement)

```python
# memory.py  (SHIPPED) — raw-SQL aggregate; deployment_id uses array_agg[1] (no min(uuid))
async def list_conversations(
    db: AsyncSession, *, user_id: str, agent_name: str | None = None,
    deployment_id: str | None = None, limit: int = 100, offset: int = 0,
) -> list[dict[str, Any]]: ...

# conversation_store.py  (SHIPPED) — on the Protocol AND PostgresConversationStore
async def list_conversations(self, db, *, user_id, agent_name=None,
    deployment_id=None, limit=100, offset=0) -> list[dict]: ...

# schemas.py  (SHIPPED) — note the Optional fields; see data-model §4
class ConversationSummary(BaseModel):
    thread_id: str
    session_id: str | None = None
    agent_name: str | None = None       # aggregate min() over a NOT-NULL column → never null in practice
    title: str | None = None
    message_count: int
    last_activity: datetime | None = None
    deployment_id: str | None = None
    environment: str                     # 'sandbox' | 'production'

# routers/memory.py  (SHIPPED) — GET /{name}/memory/conversations, require_user, deployment_id/limit/offset
# routers/me.py       (SHIPPED) — GET /conversations, require_user, limit/offset
```

### Frontend — the work

```ts
// registryApi.ts  (NEW — T2). Place in the Memory section next to listMemory (~L1600).
export interface ConversationSummary {
  thread_id: string;
  session_id: string | null;
  agent_name: string;                    // query guarantees non-null (agent_memory.agent_name NOT NULL)
  title: string | null;                  // first user message; null if no user turn
  message_count: number;
  last_activity: string;                 // ISO-8601; max(created_at), always present
  deployment_id: string | null;
  environment: "sandbox" | "production";
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

> The TS `agent_name`/`last_activity` are typed non-null (unlike the defensively-Optional
> Pydantic) because the aggregate guarantees them: `agent_name` is `min()` over a NOT-NULL
> column and `last_activity` is `max(created_at)`. This avoids null-guards in every renderer.

```ts
// components/conversations/ConversationSidebar.tsx  (NEW — T3)
export type EnvFilter = "all" | "sandbox" | "production";

// env==="all" ? list : list.filter(c => c.environment === env)
export function filterConversationsByEnv(
  list: ConversationSummary[], env: EnvFilter,
): ConversationSummary[];

export type ConversationScope =
  | { kind: "agent"; agentName: string; deploymentId?: string }
  | { kind: "me" };

export interface ConversationSidebarProps {
  scope: ConversationScope;
  activeThreadId: string | null;
  onSelect: (summary: ConversationSummary) => void;   // consumer seeds/navigates
  onNew: () => void;
  showEnvFilter?: boolean;                             // standalone page only
  disabled?: boolean;                                  // block select/new while streaming/awaiting approval
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
// AgentChatPage.tsx + CatalogChatPage.tsx  — resettable session (T4/T5)
const [searchParams] = useSearchParams();                       // AgentChatPage: add useSearchParams import
const [sessionId, setSessionId] = useState(() =>
  searchParams.get("session") ?? crypto.randomUUID());
// shared seed helper (per page; maps listMemory rows → the page's Message type):
async function seedFromThread(agentName: string, threadId: string, deploymentId?: string) {
  const rows = await listMemory(agentName, { thread_id: threadId, deployment_id: deploymentId, limit: 200 });
  setSessionId(threadId);
  setMessages(rows
    .filter(r => r.role === "user" || r.role === "assistant")
    .map(r => ({ role: r.role as "user" | "assistant", content: r.content, author: r.agent_name })));
  // NOTE: rich slots (citations/toolCalls/rationale/tree) are NOT reconstructed on seed —
  // plain bubbles, matching each page's existing non-live reload branch. Do NOT drop the
  // POC-4 citation wiring on the live path.
}
```

## 7. Tasks

Legend — **Deps** are task IDs; **Verify** commands are copy-pasteable. Because node/npm is
**not installed on this host**, frontend typecheck/Vitest cannot run locally — types are
validated by the **studio Docker build** (`tsc && vite build`) during `deploy-cpe2e.sh`
(CP-C), and Vitest is authored but executed in CI/build. Where a task lists
`npm run test`/`npm run typecheck`, treat it as "authored + green in CI/build", not a
local gate. Static checks that DO run locally (`bash -n`, grep for orphans/anchors) are the
local gate for frontend tasks.

---

### ✅ T0 — Backend (DONE, do not re-implement)

Query → port → DTO → both endpoints are **committed (`83199f5`) and live in
`registry-api:0.2.195`**. Verified: `memory.list_conversations` (memory.py L388, array_agg[1]
uuid fix), `ConversationStore.list_conversations` (conversation_store.py L97/L223),
`ConversationSummary` (schemas.py L1874), `GET /agents/{name}/memory/conversations`
(routers/memory.py L94), `GET /me/conversations` (routers/me.py L95). **No registry-api bump.
No migration.** `suite-78` (T1) exercises this live.

---

### Slice A — Backend e2e (only remaining backend work)

#### T1 — suite-78 e2e + registration
- **Files**: `scripts/e2e/suite-78-conversations.sh` (new), `scripts/e2e/run-all.sh` (edit)
- **Do**: mirror `suite-76-preferences.sh` / `suite-77-knowledge-rag.sh` (kubectl exec
  `registry-api`, `httpx`, `X-User-Sub`/`X-User-Team` headers). Seed via
  `POST /agents/{name}/memory` with a real memory-enabled agent: two threads for USER_A
  (one whose `deployment_id` is a real `production_deployments.id`, one sandbox / no
  `deployment_id`) and one thread for USER_B. `chmod +x`. Register in `run-all.sh` **after
  suite-77** (POC-4 is registered at L126):
  `run_suite "Suite 78: Conversations (POC-5 list)" "suite-78-conversations.sh"`.
- **Cases**:
  - `T-S78-001` — `GET /agents/{name}/memory/conversations` (USER_A) returns per-thread
    summaries: `title` = first user message, correct `message_count`, `last_activity`
    present, newest-first.
  - `T-S78-002` — ownership: USER_B's list excludes USER_A's threads (and vice-versa).
  - `T-S78-003` — `?deployment_id=<prod id>` returns only the production thread, tagged
    `environment="production"`; `?deployment_id=<sandbox id>` (or the sandbox thread's
    deployment) returns only the sandbox thread tagged `"sandbox"`.
  - `T-S78-004` — `GET /me/conversations` (USER_A) returns **both** threads (cross-agent),
    each carrying its `environment`.
- **Acceptance**: `bash scripts/e2e/suite-78-conversations.sh` prints all `RESULT … PASS`,
  exit 0 — against the **already-live** `0.2.195` pod (no rebuild needed).
- **Deps**: T0 (already deployed).
- **Verify**: `bash -n scripts/e2e/suite-78-conversations.sh && test -x scripts/e2e/suite-78-conversations.sh`

---

### ✅ CHECKPOINT CP-A — suite-78 green against live 0.2.195 (see `quickstart.md §CP-A`)
Backend is already deployed; no bump. Confirm the pod is on `0.2.195`, smoke both endpoints,
run suite-78 green. **Gate: do not start UI work until CP-A passes** (proves the contract the
UI depends on). If the pod predates `0.2.195`, run `bash scripts/deploy-cpe2e.sh` first (it
builds registry-api at the unchanged `0.2.195` tag).

---

### Slice B — Frontend (shared client + component first, then the mounts)

#### T2 — API client: type + two functions
- **Files**: `studio/src/api/registryApi.ts` (POC-4 knowledge additions are already in this
  file — preserve them)
- **Interface**: §6 `ConversationSummary`, `listConversations`, `listMyConversations`.
  Place in the Memory section, next to `listMemory` (~L1600).
- **Acceptance**: `import { listConversations, listMyConversations, ConversationSummary }`
  resolves; consumed by T3–T7.
- **Deps**: none (parallel with Slice A).
- **Verify**: `grep -n "listConversations\|listMyConversations\|interface ConversationSummary" studio/src/api/registryApi.ts` (3 hits); types green in the CP-C Docker build.

#### T3 — `ConversationSidebar` (shared) + `filterConversationsByEnv`
- **Files**: `studio/src/components/conversations/ConversationSidebar.tsx` (new),
  `…/ConversationSidebar.test.tsx` (new)
- **Interface**: §6 (`ConversationSidebarProps`, `filterConversationsByEnv`, `EnvFilter`,
  `ConversationScope`).
- **Do**: React Query keyed on the scope (e.g. `["conversations", scope]`); `agent` scope →
  `listConversations(agentName, {deployment_id})`, `me` scope → `listMyConversations()`.
  Render: `New conversation` button (calls `onNew`), optional env-filter pills
  (`showEnvFilter`), rows via `filterConversationsByEnv` (title | `"Untitled conversation"`,
  agent name, env badge sbx/prod, `message_count` turns, relative `last_activity`), active
  row highlighted by `activeThreadId`. Empty state: "No conversations yet." Loading state.
  `onSelect(summary)` on row click. When `disabled`, suppress select/new.
- **Acceptance (Vitest)**: renders a mocked list; empty state when `[]`; clicking a row
  calls `onSelect` with that summary; `New conversation` calls `onNew`; `filterConversationsByEnv`
  returns all for `"all"` and only-matching for `"sandbox"`/`"production"`.
- **Deps**: T2.
- **Verify**: `test -f studio/src/components/conversations/ConversationSidebar.tsx`; Vitest authored (green in CI/build).

#### T4 — AgentChatPage: resettable session + `?session` seed + docked History
- **Files**: `studio/src/pages/AgentChatPage.tsx`, `studio/src/pages/AgentChatPage.test.tsx`
- **PRESERVE (POC-4, uncommitted)**: the citation wiring already in this file — the
  `citations?: Citation[]` field on `Message` (L29-30), the `Citation`/`routeToken`/
  `openAuthorBubble`/`attachCitations`/`parseKnowledgeCitations` imports from `../lib/chatStream`
  (L17-23), `maybeAttachCitations` (L121), and `citations={m.citations}` on `AttributedBubble`
  (L486). Do NOT remove or regress any of these.
- **Do**: (a) add `useSearchParams` to the `react-router-dom` import (L2); (b) change L84
  `const [sessionId] = useState(() => crypto.randomUUID());` →
  `const [searchParams] = useSearchParams(); const [sessionId, setSessionId] = useState(() => searchParams.get("session") ?? crypto.randomUUID());`;
  (c) add `listMemory`, `listConversations` to the registryApi import (L5-13); (d) add a
  `seedFromThread` helper (§6) that maps `listMemory` rows → the local `Message` type
  (user/assistant plain bubbles only); (e) on mount, if `?session` present, `seedFromThread(name, session, depId)`;
  (f) add a header `History` toggle that mounts
  `<ConversationSidebar scope={{kind:"agent", agentName:name!, deploymentId:depId}} activeThreadId={sessionId} onSelect={s => seedFromThread(name!, s.thread_id, depId)} onNew={() => { setSessionId(crypto.randomUUID()); setMessages([]); }} disabled={isStreaming || awaitingApproval} />`
  in a dock within the existing `flex h-screen` shell (the page is already horizontal-flex;
  the right side already conditionally mounts `ConversationApprovalPanel`, so add the dock on
  the left or right without breaking that).
- **Acceptance**: opening `?session=<tid>` rehydrates prior turns from `/memory`; selecting a
  row in the dock swaps the transcript and re-keys `sessionId`; New clears + fresh uuid;
  sending after a select reuses the thread's `session_id`; citation chips still render on live
  knowledge answers.
- **Deps**: T2, T3.
- **Verify**: `grep -n "setSessionId\|seedFromThread\|ConversationSidebar\|attachCitations" studio/src/pages/AgentChatPage.tsx` (all present); Vitest updated.

#### T5 — CatalogChatPage: resettable session + docked History (production)
- **Files**: `studio/src/pages/CatalogChatPage.tsx`, `studio/src/pages/CatalogChatPage.test.tsx`
- **PRESERVE (POC-2b + POC-4, uncommitted)**: the rich `Message` slots (`toolCalls`,
  `rationale`, `citations`, `tree`, `runId`, L34-46), `WorkflowTurn`, the workflow SSE reducers
  (`openAuthorBubble`/`routeToken`/`attachToolCall`/`attachRationale`), and the
  `sessionStorage wf-lastrun` reload path. Do NOT regress these.
- **Do**: (a) `useSearchParams` is already imported (L153); (b) change L195
  `const [sessionId] = useState(...)` → `const [sessionId, setSessionId] = useState(() => searchParams.get("session") ?? crypto.randomUUID());`;
  (c) add `listMemory`, `listConversations` to the registryApi import (L6-14); (d) add a
  `seedFromThread` helper that maps `listMemory` rows → plain user/assistant `Message`s;
  (e) mount `<ConversationSidebar scope={{kind:"agent", agentName:agentName!, deploymentId:activeDeployment?.id}} activeThreadId={sessionId} onSelect={s => seedFromThread(agentName!, s.thread_id, activeDeployment?.id)} onNew={() => { setSessionId(crypto.randomUUID()); setMessages([]); }} disabled={isStreaming || !!pendingApproval} />`
  in a docked panel. **Layout note**: the page is `flex flex-col h-screen` (vertical); wrap the
  existing column in a horizontal flex so the sidebar docks beside it (or use a slide-over
  drawer) — do not break the header/console-shell/messages/input stack.
- **Acceptance**: docked History lists this production deployment's threads; select rehydrates;
  New resets; follow-up continues the thread; the workflow reload path + attribution still work.
- **Deps**: T2, T3.
- **Verify**: `grep -n "setSessionId\|seedFromThread\|ConversationSidebar\|WorkflowTurn" studio/src/pages/CatalogChatPage.tsx`; Vitest updated.

#### T6 — standalone `ConversationsPage` (real, promoted)
- **Files**: `studio/src/pages/ConversationsPage.tsx` (new — a **sibling** to the preview mock
  at `pages/preview/ConversationsPage.tsx`, which stays), `studio/src/pages/ConversationsPage.test.tsx` (new)
- **Do**: two-pane. Left = `<ConversationSidebar scope={{kind:"me"}} showEnvFilter activeThreadId={selected?.thread_id ?? null} onSelect={setSelected} onNew={...} />`.
  Right = read-only transcript preview of `selected` via
  `listMemory(selected.agent_name, {thread_id: selected.thread_id, deployment_id: selected.deployment_id ?? undefined})`
  + a **Continue** button → `navigate(\`/agents/${selected.agent_name}/chat?session=${selected.thread_id}\`)`
  (sandbox resume path; production standalone-continue is a known gap §8). Real header copy —
  no `ConsoleContextBar`, no amber "preview" banner (that's the mock).
- **Acceptance**: lists the caller's conversations across agents; env pills filter client-side;
  selecting shows the transcript; Continue navigates to the seeded chat.
- **Deps**: T2, T3.
- **Verify (Vitest)**: mock `listMyConversations` with mixed envs → All shows both, Sandbox
  only sandbox, Production only production; empty state. `test -f studio/src/pages/ConversationsPage.tsx`.

#### T7 — Deployment `Conversations` tab
- **Files**: `studio/src/components/agent-detail/ConversationsTab.tsx` (new),
  `studio/src/pages/DeploymentOverviewPage.tsx` (edit),
  `studio/src/components/agent-detail/ConversationsTab.test.tsx` (new)
- **Do**: `ConversationsTab({ agentName, deploymentId })` =
  `<ConversationSidebar scope={{kind:"agent", agentName, deploymentId}} activeThreadId={null} onSelect={s => navigate(\`/agents/${agentName}/d/${deploymentId}/chat?session=${s.thread_id}\`)} onNew={() => navigate(\`/agents/${agentName}/d/${deploymentId}/chat\`)} />`
  (nav reuses the full AgentChatPage deployment-chat machinery + T4's `?session` seed — no
  chat-logic duplication; route `/agents/:name/d/:depId/chat` already exists, App.tsx L75). In
  `DeploymentOverviewPage.tsx`: extend `type Tab = "overview" | "runs" | "memory";` (L30) →
  `… | "conversations";`; add `"conversations"` to the tab-map array (L139 — the `capitalize`
  class auto-labels it); add `{activeTab === "conversations" && <ConversationsTab agentName={name!} deploymentId={depId} />}`
  after the memory conditional (L167). Keep the existing `memory` tab (`MemoryTab`) untouched —
  Conversations sits **beside** it (operator-inspect vs user-resume).
- **Acceptance**: the tab bar shows Overview / Runs / Memory / Conversations; Conversations
  lists this deployment's threads; clicking navigates to the deployment chat seeded with that
  session; the Memory tab still works.
- **Deps**: T3, T4 (nav relies on T4's `?session` seed).
- **Verify**: `grep -n "conversations\|ConversationsTab" studio/src/pages/DeploymentOverviewPage.tsx`; Vitest.

#### T8 — Nav promotion + route
- **Files**: `studio/src/components/Sidebar.tsx`, `studio/src/App.tsx` (POC-4 knowledge routes
  already in this file — preserve them)
- **Do**: (a) **Sidebar** — delete the `DEMO`-gated *Context Preview* block (L236-246) and the
  `PREVIEW_ITEMS` const (L43-47); the `Home` and `MessagesSquare` imports (L25) become unused —
  **remove them** (keep `History`, `SlidersHorizontal`; `DEMO` is still used by `BUILD_ITEMS`
  L50, keep it). Add a real top-level `Conversations` item (`History` icon) → `/conversations`
  (its own single-item group near the top, above Build). (b) **App** — add
  `<Route path="/conversations" element={<ConversationsPage />} />` importing the **new**
  `./pages/ConversationsPage`; the existing `import ConversationsPage from "./pages/preview/ConversationsPage"`
  (L47) collides — rename that import to `PreviewConversationsPage` and update its
  `/preview/conversations` route (L70) to use it, leaving the preview reachable while `DEMO` remains.
- **Acceptance**: `Conversations` appears in real nav (non-DEMO); `/conversations` renders the
  real page; no dead `PREVIEW_ITEMS`/`Home`/`MessagesSquare` reference in Sidebar; `/preview/conversations`
  still resolves.
- **Deps**: T6.
- **Verify**: `grep -n "PREVIEW_ITEMS" studio/src/components/Sidebar.tsx` (no hits); `grep -n "MessagesSquare\|\bHome\b" studio/src/components/Sidebar.tsx` (no hits); `grep -n "/conversations" studio/src/App.tsx` (route present).

#### T9 — Experience doc
- **Files**: `docs/experience/playground.md` (POC-4 "Team Knowledge Base / RAG & citation
  chips" section is at L557 — **do not clobber it**)
- **Do**: **append** a new `## Conversations & History (context-storage POC-5)` section at the
  end of the file covering: the three surfaces (standalone `/conversations`, docked History in
  `AgentChatPage`/`CatalogChatPage`, deployment `Conversations` tab), the two endpoints
  (`GET /agents/{name}/memory/conversations`, `GET /me/conversations`), the All/Sandbox/Production
  filter, and continue-with-context behaviour (reuse `session_id` → runner reloads prior turns).
- **Acceptance**: new section present; the POC-4 section (L557) is untouched.
- **Deps**: T6, T7.
- **Verify**: `grep -n "Conversations & History (context-storage POC-5)\|Team Knowledge Base / RAG & citation chips" docs/experience/playground.md` (both hits).

#### T10 — Playwright (three surfaces)
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
    click → nav to `?session` chat → rehydrate → follow-up. Assert the scoped list only shows
    this deployment's threads. (Production deployment-scoping is proven at the endpoint by
    suite-78 T-S78-003; the page-level run uses the reachable sandbox route — §8 gap.)
- **Acceptance**: `bash scripts/studio-e2e.sh` green for both specs.
- **Deps**: T4, T6, T7, T8, **CP-C deploy**.
- **Verify**: `bash scripts/studio-e2e.sh` (after CP-C).

---

### T11 — Image bump (studio only, both files, same commit)
- **Files**: `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
- **Do**: `STUDIO_TAG 0.1.146 → 0.1.147` in `deploy-cpe2e.sh` (L291) and set the studio image
  tag `0.1.145 → 0.1.147` in `values.yaml` (L936 — currently lagging behind deploy-cpe2e.sh).
  Update both comment headers ("0.1.147: POC-5 Conversations & History — sidebar + 3 surfaces +
  real nav"). Leave `REGISTRY_API_TAG 0.2.195` and `DECLARATIVE_RUNNER_TAG 0.1.57` unchanged.
- **Acceptance**: `grep -R "0.1.147" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` →
  2 hits; no residual `0.1.146`/`0.1.145` for studio in these two files.
- **Deps**: none (values consumed at deploy; do this before CP-C).
- **Verify**: `grep -c "0.1.147" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml`

---

## 8. Known gaps (ledger — mirror into `docs/testing/manual-ui-e2e-test-plan.md` header)

- **Haiku conversation titles** — *deferred (intentional)* → POC-1b. Title = first user
  message until then.
- **Production agent-deployment-overview route** — *deferred (intentional)*. No dedicated
  production route for `DeploymentOverviewPage` exists today (research §R6); the Conversations
  **tab** is reachable on the sandbox route only. Production deployment-scoping is proven at
  the **endpoint** by suite-78 T-S78-003.
- **Standalone Continue for production rows** — *not-yet-wired (debt)*. `ConversationsPage`
  Continue routes to the sandbox agent chat (`/agents/:name/chat?session=`); production rows
  need artifact-id resolution to route to `CatalogChatPage`. Rows still list + preview; only
  the Continue target is sandbox. (Docked History in `CatalogChatPage` covers production resume.)
- **Seed drops rich slots** — *by design*. Selecting a past thread seeds plain user/assistant
  bubbles (no citation chips / tool chips / rationale / run-tree reconstructed). The live path
  still renders them; only the rehydrated history is plain — matching each page's existing
  non-live reload branch.
- **`user_id IS NULL` rows invisible** — *by design*. Daemon/legacy turns have no owner.
- **Admin `MemoryTab` per-user privacy** — *deferred* → Tighten S9 (unchanged by POC-5).
- **Aggregate index** — *deferred (intentional)*. No `(user_id, created_at)` index yet;
  add if suite-78 shows slowness at scale.

## 9. MVP critical path

`T1 → CP-A → T2 → T3 → T4 → T6 → T7 → T8 → T9 → T11 → CP-C → T10`.
(T5 parallels T4–T7; CP-B — the studio Docker build type-gate — is folded into CP-C since
node/npm is not on this host. suite-78 (T1) runs after CP-A against the live 0.2.195 pod.)

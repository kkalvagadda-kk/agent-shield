# POC-5 — Research & Grounding

Ground truth verified against the worktree `worktree-ux-preview-context-storage` on
**2026-07-17**, on top of the **uncommitted POC-4 working tree**. Live baseline:
`registry-api:0.2.195`, `studio:0.1.146` (deploy-cpe2e.sh) / `0.1.145` (values.yaml lags),
`declarative-runner:0.1.57`, Alembic head **`0067`**. Every claim below was read from code,
not the design doc.

---

## R0. The backend slice is already implemented, committed, and live

The whole POC-5 backend landed in commit **`83199f5`** ("feat(poc-5): backend —
list_conversations query + two endpoints") and is baked into `registry-api:0.2.195`:

- `memory.list_conversations` — `services/registry-api/memory.py` **L388** (SQL const
  `_LIST_CONVERSATIONS_SQL` at L362). Uses the **array_agg[1] uuid fix**: `deployment_id`
  is `(array_agg(am.deployment_id ORDER BY am.message_index) FILTER (WHERE deployment_id IS NOT NULL))[1]::text`,
  **not** `min(deployment_id)` (Postgres has no `min(uuid)` aggregate — the design's first
  cut would have errored). Returns `[dict(row) for row in result.mappings().all()]`.
- `ConversationStore.list_conversations` — `conversation_store.py` **L97** (Protocol) + **L223**
  (`PostgresConversationStore` adapter, delegates to `memory.list_conversations`).
- `ConversationSummary` — `schemas.py` **L1874** (see R7 for the exact shipped shape).
- `GET /agents/{name}/memory/conversations` — `routers/memory.py` **L94** (`require_user`,
  `_get_agent_or_404`, `deployment_id`/`limit`/`offset`, `ConversationSummary.model_validate`).
  Declared **before** `GET /{name}/memory` so there is no route-order ambiguity.
- `GET /me/conversations` — `routers/me.py` **L95** (`require_user`, `limit`/`offset`,
  `get_conversation_store()`, `ConversationSummary.model_validate`). Imports already added
  (`ConversationSummary`, `get_conversation_store`).

**Decision: POC-5 adds NO backend code and NO registry-api bump.** The only backend artifact
still to write is `suite-78` (the e2e proof), which runs against the live `0.2.195` pod.

## R1. Alembic head — **0067**, no migration needed

`services/registry-api/alembic/versions/` head is `0067_knowledge_base_rag.py` (POC-4;
`0066_drop_llm_provider_check.py` and `0065_user_profiles.py` precede it). POC-5 is a
**read-side aggregate** over the existing `agent_memory` table — it adds **no column, no
index, no migration**. Existing indexes (`ix_agent_memory_thread_msg`,
`idx_agent_memory_thread_scope`) cover `thread_id`/`message_index` grouping; `created_at`
ordering is over the grouped result. **Decision: no Alembic file. If suite-78 shows the
aggregate is slow at scale, a `(user_id, created_at)` index is a Tighten follow-up —
flagged, not built.**

## R2. `environment` is NOT stored on `agent_memory` — it is DERIVED (already implemented)

`models.py::AgentMemory` has no `environment` column. The only environment signal is
`deployment_id` (uuid, nullable). The shipped query derives environment by the two-table
split — a memory row is **production iff its `deployment_id` is a `production_deployments.id`**,
otherwise **sandbox** (a `deployments.id`, or `NULL` for a bare playground turn):

```sql
LEFT JOIN production_deployments pd ON agent_memory.deployment_id = pd.id
-- environment := CASE WHEN bool_or(pd.id IS NOT NULL) THEN 'production' ELSE 'sandbox' END
```

Do **not** read `deployments.environment` — that column defaults to `'production'` and the
sandbox `AgentChatPage` already treats `environment !== 'production'` as sandbox, so it is an
unreliable discriminator. `production_deployments` membership is the reliable one. The write
path stamps `deployment_id` for both sandbox and production runs (`routers/chat.py` →
`declarative-runner/main.py` → `POST /agents/{name}/memory`); environment is a pure read-side
join. **This design §5 risk ("confirm the write path stamps environment") is resolved: it
does not stamp environment, but `deployment_id` is stamped and environment is a pure join. The
POC-5 query owns the derivation; no write-path change was required.**

## R3. `user_id` ownership is populated on the chat write path

`routers/chat.py` resolves `run.user_id` = caller `sub` and forwards it to the pod;
`declarative-runner/main.py` writes `user_id=user_id`. So `agent_memory.user_id` carries the
owning caller's `sub` for interactive chat turns. Ownership scoping
(`WHERE user_id = :caller_sub`) is sound. **Caveat (known gap):** a turn written with a NULL
`user_id` (daemon/legacy) can't be attributed and will not appear in `/me/conversations` —
correct by design.

## R4. The transcript seam — `ConversationStore` port (POC-0) already carries the new method

`conversation_store.py` defines the `ConversationStore` Protocol + `PostgresConversationStore`
adapter, constructed via `store_factory.get_conversation_store()`. It already has
`append / load / list_recent / list_conversations / erase`. `list_recent` (cross-thread
newest-first ORM rows) backs the admin Memory tab; `list_conversations` (grouped-by-thread
summaries, POC-5) backs the resume lens. The seam stays the only place callers touch the
transcript — both endpoints go through `get_conversation_store().list_conversations(...)`.

## R5. Endpoint homes & auth (as shipped)

- `routers/memory.py` — prefix `/api/v1/agents`, `tags=["memory"]`. `GET /{name}/memory/conversations`
  (L94) uses `require_user` (the only route here that does) and is declared **before**
  `GET /{name}/memory` (L120) and `DELETE /{name}/memory/{thread_id}` (L231), so `/conversations`
  never binds to a `{thread_id}` param.
- `routers/me.py` — prefix `/api/v1/me`, already uses `require_user`. `GET /conversations` (L95)
  sits next to the POC-3 `/me/preferences` routes.
- Both routers are already registered in `main.py`. No `main.py` change.

## R6. DeploymentOverviewPage tab shape (exact, 2026-07-17)

`studio/src/pages/DeploymentOverviewPage.tsx`:
- `type Tab = "overview" | "runs" | "memory";` (**L30**).
- `context: DeploymentContext = "playground";` — **hardcoded sandbox** (L39). Route
  `/agents/:name/d/:depId`. `getDeployments(name)` returns the **sandbox** `deployments` rows.
- Tab bar renders `(["overview", "runs", "memory"] as Tab[]).map(...)` with a `capitalize`
  class (**L139**) — a new tab's label is auto-derived from the string.
- Content conditionals at **L156-167**: `overview` / `runs` / `memory`; `memory` mounts
  `<MemoryTab agentName={name!} deploymentId={depId} />` (L167).

**Grounding surprise (unchanged):** there is **no dedicated production
agent-deployment-overview route**. Production deployments surface via `DeploymentsPage`
(`/deployments`) and `CatalogChatPage` (`/catalog/:artifactId/chat`). So the deployment
**Conversations tab** is reachable on the **sandbox** page only. The design's "sandbox AND
production" for this tab is satisfied at two levels: (a) the tab is env-agnostic — it passes
`?deployment_id=<depId>` and each returned row carries its own derived `environment`; (b) the
**production** deployment-scoping is proven at the endpoint layer by **suite-78** T-S78-003.
A production agent-deployment-overview page is a **known gap** (plan §8).

## R7. `ConversationSummary` — the SHIPPED shape (Optional-heavy)

The live Pydantic model (`schemas.py` L1874) is **more permissive** than the design doc's
draft — several fields are declared Optional:

```python
class ConversationSummary(BaseModel):
    thread_id: str
    session_id: str | None = None
    agent_name: str | None = None       # ← Optional in Pydantic
    title: str | None = None
    message_count: int
    last_activity: datetime | None = None  # ← Optional in Pydantic
    deployment_id: str | None = None     # ← str, not uuid.UUID
    environment: str
```

**Frontend consequence:** the TS interface (T2) types `agent_name` and `last_activity` as
**non-null** (`string`), because the aggregate guarantees them — `agent_name` is `min()` over
a NOT-NULL column and `last_activity` is `max(created_at)` — even though Pydantic declares
them Optional defensively. `session_id`, `title`, `deployment_id` stay nullable. This keeps
renderers free of spurious null-guards while matching the wire reality. `deployment_id` is a
plain `str` (the SQL casts `::text`), not a uuid.

## R8. Chat pages — `sessionId` and layout (exact, 2026-07-17)

- `AgentChatPage.tsx` **L84** — `const [sessionId] = useState(() => crypto.randomUUID());`
  (single-column-ish; the shell is `flex h-screen` with a `flex-1 min-w-0` chat column and a
  right-side `ConversationApprovalPanel` mounted only in sandbox-approval flow). `sendMessage`
  posts `session_id: sessionId` (L330-331). Reusing a prior `session_id` reloads earlier turns
  server-side — **continue already works**. Imports `useParams, Link` from react-router-dom
  (L2) — **`useSearchParams` must be added** (T4). **POC-4 citation wiring is present and
  uncommitted** — see plan §7 T4 PRESERVE list.
- `CatalogChatPage.tsx` **L195** — same `const [sessionId] = useState(...)`. Already imports
  `useSearchParams` (L153, reading `?dep`). Shell is `flex flex-col h-screen` (**vertical** —
  docking a sidebar needs a horizontal wrapper or a drawer). Carries rich `Message` slots
  (`toolCalls`/`rationale`/`citations`/`tree`/`runId`, L34-46) + `WorkflowTurn` + workflow SSE
  reducers + `sessionStorage wf-lastrun` reload — all POC-2b/POC-4, uncommitted, **must be
  preserved** (plan §7 T5).
- **Both must make `sessionId` resettable** (add the setter) so selecting a conversation or
  starting a new one re-keys the thread. Both must seed via `listMemory` on select and on
  `?session`.

## R9. Sidebar / routes / preview scaffolding to retire (exact, 2026-07-17)

- `Sidebar.tsx` — `PREVIEW_ITEMS` const at **L43-47** (Preview Home / Multi-agent Chat /
  Conversations → `/preview/*`), rendered only under `DEMO` at **L236-246**. Imports at L25:
  `Home, MessagesSquare, SlidersHorizontal, History`. **`Home` and `MessagesSquare` are used
  ONLY by `PREVIEW_ITEMS`** (grep-confirmed) — retiring the block makes them unused imports,
  so they must be removed to keep the Docker build's `tsc` clean. **`History` is reused** for
  the real Conversations nav item. `DEMO` stays (used by `BUILD_ITEMS` L50). `SlidersHorizontal`
  stays (Response Preferences footer link).
- `App.tsx` — **L47** `import ConversationsPage from "./pages/preview/ConversationsPage";`,
  **L70** route `/preview/conversations`. The real page is a **new sibling**
  `pages/ConversationsPage.tsx`; adding `import ConversationsPage from "./pages/ConversationsPage"`
  **collides** with L47 — rename the preview import to `PreviewConversationsPage` (T8). POC-4
  knowledge routes (`/knowledge`, `/knowledge/:id`, L66-67) are in this file and uncommitted —
  preserve them.

## R10. API client & types (exact, 2026-07-17)

`registryApi.ts`: `http = axios.create({ baseURL: "/api/v1" })`. `MemoryMessage` interface
(**L1574**) + `listMemory(agentName, {thread_id?, scope?, deployment_id?, limit?, offset?})`
(**L1588**) already exist — **reused to seed a selected thread's transcript**. `DeploymentContext`
= `"playground" | "production"` (L358). **`ConversationSummary` / `listConversations` /
`listMyConversations` are NOT yet in this file** (grep-confirmed) — added in T2. This file is
in the uncommitted POC-4 working tree (knowledge additions) — preserve them.

## R11. Test infra

- **Vitest**: `renderWithProviders` from `src/test/utils.tsx`, `vi.mock('../api/registryApi')`;
  colocated `*.test.tsx`. Analogs: `AgentChatPage.test.tsx`, `CatalogChatPage.test.tsx`,
  `agent-detail/*.test.tsx`. **node/npm is NOT installed on this host** — Vitest is authored
  but executed in CI/the studio Docker build, not locally.
- **Playwright**: `studio/e2e/*.spec.ts`, real Keycloak via `e2e/global-setup.ts`, run by
  `bash scripts/studio-e2e.sh`. Analogs: `deployment-overview.spec.ts`, `knowledge.spec.ts`.
- **Bash e2e**: `scripts/e2e/suite-NN-*.sh`, `kubectl exec` into `registry-api`, `httpx`
  assertions with `X-User-Sub`/`X-User-Team` headers (see `suite-76-preferences.sh` /
  `suite-77-knowledge-rag.sh`). **suite-77 (POC-4) is already registered in `run-all.sh`
  (L126)**; POC-5 is **suite-78** (`T-S78-00x`), registered after it.

## R12. Image tags (current → target) & deploy path

| Service | File / line | Current | Target |
|---|---|---|---|
| registry-api | `deploy-cpe2e.sh:278`, `values.yaml:613` | `0.2.195` | **`0.2.195` (unchanged — backend shipped)** |
| studio | `deploy-cpe2e.sh:291` | `0.1.146` | **`0.1.147`** |
| studio | `values.yaml:936` | `0.1.145` (lags) | **`0.1.147`** |
| declarative-runner | `deploy-cpe2e.sh:293` | `0.1.57` | **unchanged** |

**Note the studio mismatch:** `deploy-cpe2e.sh` is at `0.1.146` but `values.yaml` lags at
`0.1.145` (a POC-4 working-tree inconsistency). Setting **both** to `0.1.147` in T11 reconciles
it. **Deploy is LOCAL docker-desktop** via `scripts/deploy-cpe2e.sh` + Helm (values baked in,
no `--set`). `scripts/deploy-eks.sh` is the shared-EKS path and is **not** used for POC-5 —
the CLAUDE.md canonical bump is `deploy-cpe2e.sh` + `values.yaml` only. `studio/package.json`
`build` = `tsc && vite build`, so the Docker build (`npm run build`) is the type gate.

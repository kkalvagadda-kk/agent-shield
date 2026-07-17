# POC-5 — Research & Grounding

Ground truth verified against the worktree `worktree-ux-preview-context-storage` on
2026-07-16, **after POC-2b/POC-3 landed**. Live baseline: `registry-api:0.2.191`,
`studio:0.1.144`, `declarative-runner:0.1.56`. Every claim below was read from code,
not the design doc.

---

## R1. Alembic head — **0065**, no migration needed

`services/registry-api/alembic/versions/` head is `0065_user_profiles.py` (POC-3).
POC-5 is a **read-side aggregate** over the existing `agent_memory` table — it adds
**no column, no index, no migration**. The `ix_agent_memory_thread_msg` +
`idx_agent_memory_thread_scope` indexes already cover `thread_id`/`message_index`
grouping; `created_at` ordering is over the grouped result, not a table scan hot path
at POC scale. **Decision: no Alembic file. If suite-78 shows the aggregate is slow at
scale, a `(user_id, created_at)` index is a Tighten follow-up — flagged, not built.**

## R2. `environment` is NOT stored on `agent_memory` — it must be DERIVED

`models.py::AgentMemory` (L1779) columns: `agent_name, team, thread_id, user_id, role,
content, message_index, session_id, deployment_id (uuid, nullable), workflow_run_id,
scope, message_kind, created_at, expires_at`. **There is no `environment` column.**

The only environment signal is `deployment_id`. The write path proves how it is set:
- `routers/chat.py:513` passes `deployment_id=str(deployment.id)` to the runner for
  **both** sandbox and production runs.
- For a **production** run `deployment` is a `ProductionDeployment` (its `id` lands in
  `agent_memory.deployment_id`). For a **sandbox** run it is a `Deployment` row.
- `declarative-runner/main.py:547/628` forwards that `deployment_id` into
  `POST /agents/{name}/memory`, which `save_turn` stamps onto the row.

So environment is derivable by the **two-table split** (see MEMORY ref
`parity_architecture`): a memory row is **production iff its `deployment_id` is a
`production_deployments.id`**, otherwise **sandbox** (a `deployments.id`, or `NULL` for
a bare playground turn). Concretely:

```sql
LEFT JOIN production_deployments pd ON agent_memory.deployment_id = pd.id
-- environment := CASE WHEN bool_or(pd.id IS NOT NULL) THEN 'production' ELSE 'sandbox' END
```

Do **not** read `deployments.environment` — that column defaults to `'production'`
(`models.py:657`) and the sandbox `AgentChatPage` already treats
`environment !== 'production'` as sandbox, so it is an unreliable discriminator.
`production_deployments` membership is the reliable one. **This is the single grounding
risk the design §5 flagged ("confirm the write path stamps environment"): it does not
stamp environment, but `deployment_id` is stamped and environment is a pure join. The
POC-5 query owns the derivation; no write-path change is required.**

## R3. `user_id` ownership is populated on the chat write path

`routers/chat.py` resolves `run.user_id` = caller `sub` and forwards `x-user-id` to the
pod; `declarative-runner/main.py:547` writes `user_id=user_id`. So `agent_memory.user_id`
carries the owning caller's `sub` for interactive chat turns. Ownership scoping
(`WHERE user_id = :caller_sub`) is therefore sound. **Caveat (known gap):** a turn
written with a NULL `user_id` (daemon/legacy) can't be attributed and will not appear in
`/me/conversations` — correct by design (you can't own an unattributed row).

## R4. The transcript seam — `ConversationStore` port already exists (POC-0)

`conversation_store.py` defines `ConversationStore` Protocol + `PostgresConversationStore`
adapter, constructed via `store_factory.get_conversation_store()` (env `CONVERSATION_STORE`,
default `postgres`). It already has `append / load / list_recent / erase`, each delegating
to `memory.py` service functions. **`list_recent` (cross-thread newest-first ORM rows,
backs the Memory tab) is NOT what POC-5 needs** — POC-5 needs a **grouped-by-thread
summary**. POC-5 adds a **new** `list_conversations` method to the port + adapter,
delegating to a new `memory.list_conversations`. The seam stays the only place callers
touch the transcript.

## R5. Endpoint homes & auth

- `routers/memory.py` — prefix `/api/v1/agents`, `tags=["memory"]`. Existing routes:
  `POST /{name}/memory`, `GET /{name}/memory`, `POST /{name}/memory/search`,
  `DELETE /{name}/memory/clear`, `DELETE /{name}/memory/{thread_id}`. **None use
  `require_user`.** POC-5's `GET /{name}/memory/conversations` **adds `require_user`**
  (ownership scoping by caller `sub`). No route-ordering conflict: there is no
  `GET /{name}/memory/{param}`, so `/conversations` is unambiguous.
- `routers/me.py` — prefix `/api/v1/me`, already uses `require_user` and is where POC-3
  put `/me/preferences`. POC-5's `GET /me/conversations` goes here (cross-agent).
- Both routers are already registered in `main.py` (L85–86). No `main.py` change.

## R6. DeploymentOverviewPage tab shape (exact)

`studio/src/pages/DeploymentOverviewPage.tsx`:
- `type Tab = "overview" | "runs" | "memory";` (L30).
- `context: DeploymentContext = "playground";` — **hardcoded sandbox** (L39). Route
  `/agents/:name/d/:depId`. `getDeployments(name)` returns the **sandbox** `deployments`
  table rows.
- Tab bar renders `(["overview", "runs", "memory"] as Tab[]).map(...)` with a
  `capitalize` class (L139) — so a new tab's label is auto-derived from the string.
- Content is `{activeTab === "overview" && ...}` / `runs` / `memory` conditionals
  (L156–167). `memory` mounts `<MemoryTab agentName={name!} deploymentId={depId} />`.

**Grounding surprise:** there is **no dedicated production agent-deployment-overview
route**. Production deployments surface via `DeploymentsPage` (`/deployments`) and
`CatalogChatPage` (`/catalog/:artifactId/chat`). So the deployment **Conversations tab**
is reachable on the **sandbox** page only. The design's "sandbox AND production" for this
tab is satisfied at **two levels**: (a) the tab is env-agnostic — it passes
`?deployment_id=<depId>` and each returned row carries its own derived `environment`; (b)
the **production** deployment-scoping is proven at the endpoint layer by **suite-78**
(pass a `production_deployments.id` as `deployment_id`, assert only that deployment's
threads return, tagged `environment='production'`). The page-level Playwright runs against
the reachable sandbox route. A production agent-deployment-overview page is a **known
gap** (see plan §8).

## R7. Chat pages — `sessionId` and layout

- `AgentChatPage.tsx:76` — `const [sessionId] = useState(() => crypto.randomUUID());`
  (design said L68; POC-2b shifted it to **L76**). Single-column chat; `sendMessage`
  posts `session_id: sessionId`. Reusing a prior `session_id` reloads earlier turns
  server-side (`declarative-runner/main.py::_load_memory_context`) — **continue already
  works**. Right side already conditionally mounts `ConversationApprovalPanel`.
- `CatalogChatPage.tsx:195` — same `const [sessionId] = useState(...)`, single column,
  production (`context: "production"`, `deployment_id: activeDeployment?.id`).
- **Both must make `sessionId` resettable** (add the setter) so selecting a conversation
  or starting a new one re-keys the thread.

## R8. Sidebar / routes / preview scaffolding to retire

- `Sidebar.tsx:43` `PREVIEW_ITEMS` (Preview Home / Multi-agent Chat / Conversations →
  `/preview/*`) rendered only when `DEMO` (L237). POC-5 **retires the Context Preview
  section** and adds a real top-level `Conversations` nav item (`History` icon already
  imported, L25) → `/conversations`.
- `App.tsx:70` route `/preview/conversations` → `preview/ConversationsPage` (a **mock**
  using `MOCK_CONVERSATIONS`/`MOCK_MEMORY`). POC-5 promotes a real `/conversations` route
  and repoints. Keep the `/preview/*` routes only as long as `DEMO` uses them; the real
  page does not depend on `DEMO`.

## R9. API client & types

`registryApi.ts`: `http = axios.create({ baseURL: "/api/v1" })`. `MemoryMessage`
interface + `listMemory(agentName, {thread_id?, scope?, deployment_id?, limit?, offset?})`
already exist (L1560/L1574) — **reused to seed a selected thread's transcript**.
`deleteMemoryThread`/`clearAgentMemory` exist. POC-5 adds `ConversationSummary` +
`listConversations` + `listMyConversations`.

## R10. Test infra

- Vitest: `renderWithProviders` from `src/test/utils.tsx`, `vi.mock('../api/registryApi')`;
  colocated `*.test.tsx`. Analogs exist (`AgentChatPage.test.tsx`, `CatalogChatPage.test.tsx`,
  `agent-detail/*.test.tsx`).
- Playwright: `studio/e2e/*.spec.ts`, real Keycloak via `e2e/global-setup.ts`, run by
  `bash scripts/studio-e2e.sh`. Analog: `deployment-overview.spec.ts`,
  `hitl-deployment-chat.spec.ts`.
- Bash e2e: `scripts/e2e/suite-NN-*.sh`, `kubectl exec` into `registry-api`, `httpx`
  assertions with `X-User-Sub`/`X-User-Team` headers (see `suite-76-preferences.sh`).
  Last suite is **76**; **77 is reserved for POC-2b/POC-4** (land before POC-5), so
  POC-5 is **suite-78** (`T-S78-00x`), per the task's hard requirement.

## R11. Image tags (current → target)

| Service | File | Current | Target |
|---|---|---|---|
| registry-api | `deploy-cpe2e.sh:266`, `deploy-eks.sh:67`, `values.yaml:597` | `0.2.191` | **`0.2.193`** |
| studio | `deploy-cpe2e.sh:273`, `deploy-eks.sh:70`, `values.yaml:917` | `0.1.144` | **`0.1.146`** |
| declarative-runner | `deploy-cpe2e.sh:275` | `0.1.56` | **unchanged** |

`.192`/`.145` are reserved for POC-4 (lands before POC-5). declarative-runner is **not**
rebuilt: POC-5 is a read query + frontend slice; no runner code changes (verified — the
runner only *writes* memory + *loads* context, both already correct for continue).

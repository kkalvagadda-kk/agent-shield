# POC-5 — Conversations & Memory in the Product · Tasks

**Branch**: `worktree-ux-preview-context-storage` — commit here **only**; never merge/PR to main.
**Spec (authoritative)**: `docs/design/context-storage-poc-5-conversations.md`.
**Inputs**: `plan.md` · `research.md` · `data-model.md` · `contracts/list-conversations.md`.
**Baseline (verified)**: `registry-api:0.2.191`, `studio:0.1.144`, `declarative-runner:0.1.56`, **Alembic head `0065`**.
**Targets**: `registry-api:0.2.193`, `studio:0.1.146`, declarative-runner **unchanged** (read + frontend only).

> **NO MIGRATION.** Alembic head is `0065`; POC-5 is a read-side aggregate over the existing
> `agent_memory` table — no column, no index, no Alembic file (research §R1, data-model §1).
> **`environment` is NOT a stored column** — it is DERIVED in the read query via
> `LEFT JOIN production_deployments` + `bool_or(pd.id IS NOT NULL)` (research §R2, data-model §2).
> Nothing on the write path changes; `declarative-runner` is **not** rebuilt.

Legend — **Deps** are task IDs. **[P]** = parallelizable (files disjoint from siblings).
**Verify** commands are copy-pasteable. Every task ≤ 3 files.

---

## Slice A — Backend (vertical: query → port → DTO → endpoints → e2e → deploy)

### T1 — `memory.list_conversations` (read query, environment DERIVED)
- **Files**: `services/registry-api/memory.py`
- **Do**: add `async def list_conversations(db, *, user_id, agent_name=None, deployment_id=None, limit=100, offset=0) -> list[dict[str, Any]]` running the raw-SQL aggregate from **data-model §3** verbatim: `array_agg(content ORDER BY message_index) FILTER (WHERE role='user'))[1]` as `title`, `count(*)` as `message_count`, `max(created_at)` as `last_activity`, `min(deployment_id)::text`, and `CASE WHEN bool_or(pd.id IS NOT NULL) THEN 'production' ELSE 'sandbox' END` as `environment` via `LEFT JOIN production_deployments pd ON am.deployment_id = pd.id`. Casted optional binds (`CAST(:agent_name AS text)`, `CAST(:deployment_id AS uuid)`), `WHERE am.user_id = :user_id`, `GROUP BY am.thread_id`, `ORDER BY max(am.created_at) DESC`. Return `[dict(r._mapping) for r in result]`. No new imports beyond `text` (already imported). **No migration — this owns the environment derivation.**
- **Acceptance**: two owned threads (one prod deployment_id, one sandbox) → 2 summaries, correct `title`/`message_count`/`environment`, newest-first; another user's rows never appear.
- **Deps**: none.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('memory.py').read())"`

### T2 — `ConversationStore.list_conversations` (port + PG adapter)
- **Files**: `services/registry-api/conversation_store.py`
- **Do**: add the identical-signature method to **both** the `ConversationStore` Protocol and `PostgresConversationStore`; adapter body delegates: `return await memory.list_conversations(db, user_id=user_id, agent_name=agent_name, deployment_id=deployment_id, limit=limit, offset=offset)`. Add `Any` to the `typing` import if needed. Router must reach this read only through the port (seam intact).
- **Acceptance**: `get_conversation_store().list_conversations(...)` returns the same dicts as T1.
- **Deps**: T1.
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('conversation_store.py').read())"`

### T3 — `ConversationSummary` DTO [P]
- **Files**: `services/registry-api/schemas.py`
- **Do**: add the Pydantic v2 model from **data-model §4** next to `AgentMemoryResponse` (~L1863): `thread_id:str`, `session_id:str|None=None`, `agent_name:str`, `title:str|None=None`, `message_count:int`, `last_activity:datetime`, `environment:str`, `deployment_id:uuid.UUID|None=None`. Ensure `datetime`/`uuid` imported.
- **Acceptance**: `ConversationSummary(**row)` validates a T1 dict (datetime + uuid coerce).
- **Deps**: none (parallel with T1/T2).
- **Verify**: `cd services/registry-api && python3 -c "import ast; ast.parse(open('schemas.py').read())"`

### T4 — scoped endpoint `GET /agents/{name}/memory/conversations`
- **Files**: `services/registry-api/routers/memory.py`
- **Do**: add `list_agent_conversations` (§6): `from auth_middleware import require_user`, import `ConversationSummary` from `schemas`, `Query`/`Optional`. `_get_agent_or_404(name)`; `store = get_conversation_store()`; `rows = await store.list_conversations(db, user_id=claims["sub"], agent_name=name, deployment_id=deployment_id, limit=limit, offset=offset)`; `return [ConversationSummary(**r) for r in rows]`. `response_model=list[ConversationSummary]`. Place above the `DELETE /{name}/memory/{thread_id}` block (no route-ordering conflict — no `GET /{name}/memory/{param}` exists, research §R5).
- **Acceptance**: `200` + `ConversationSummary[]`; `404` unknown agent; a second `X-User-Sub` gets a disjoint list; `deployment_id` narrows to that deployment.
- **Deps**: T2, T3.
- **Verify**: mapper import in T5.

### T5 — cross-agent endpoint `GET /me/conversations` (+ mapper gate)
- **Files**: `services/registry-api/routers/me.py`
- **Do**: add `list_my_conversations` (§6). Imports: `Query` (fastapi), `get_conversation_store` (store_factory), `ConversationSummary` (schemas). `rows = await store.list_conversations(db, user_id=claims["sub"], limit=limit, offset=offset)` (no agent/deployment filter) → `[ConversationSummary(**r) for r in rows]`. `response_model=list[ConversationSummary]`.
- **Acceptance**: returns every owned thread across agents, each with `environment`; ownership-scoped.
- **Deps**: T2, T3.
- **Verify**: `cd services/registry-api && python3 -c "import routers.memory, routers.me, schemas; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('ok')"`

### T6 — suite-78 e2e + registration
- **Files**: `scripts/e2e/suite-78-conversations.sh` (new), `scripts/e2e/run-all.sh` (edit)
- **Do**: mirror `suite-76-preferences.sh` (kubectl exec `registry-api`, `httpx`, `X-User-Sub`/`X-User-Team`). Seed via `POST /agents/{name}/memory` with a real agent: USER_A gets two threads (one carrying a real `production_deployments.id` as `deployment_id`, one sandbox/no deployment_id), USER_B one thread. `chmod +x`. Register after suite-76: `run_suite "Suite 78: Conversations (POC-5 list)" "suite-78-conversations.sh"`.
- **Cases**:
  - `T-S78-001` — scoped list for USER_A: per-thread summaries, `title`=first user message, correct `message_count`, `last_activity` present, newest-first.
  - `T-S78-002` — ownership: USER_B's list excludes USER_A's threads (and vice-versa).
  - `T-S78-003` — deployment filter: `?deployment_id=<prod id>` → only the production thread, `environment="production"`; `?deployment_id=<sandbox id>` → only the sandbox thread, `environment="sandbox"` (environment-derivation correctness).
  - `T-S78-004` — `GET /me/conversations` for USER_A → **both** threads (cross-agent), each carrying its `environment`.
- **Acceptance**: `bash scripts/e2e/suite-78-conversations.sh` prints all `RESULT … PASS`, exit 0 (**run after CP-A deploy**).
- **Deps**: T4, T5.
- **Verify**: `bash -n scripts/e2e/suite-78-conversations.sh && test -x scripts/e2e/suite-78-conversations.sh && grep -q suite-78 scripts/e2e/run-all.sh`

### T15a — registry-api image bump (0.2.193) [P]
- **Files**: `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`
- **Do**: `REGISTRY_API_TAG 0.2.191 → 0.2.193` in all three (deploy-cpe2e.sh ~L266, deploy-eks.sh ~L67, values.yaml ~L597). Update comment headers ("POC-5 conversations list + sidebar"). **Do NOT touch STUDIO_TAG yet** (T15b) or `DECLARATIVE_RUNNER_TAG 0.1.56` (unchanged).
- **Acceptance**: `grep -R "0.2.193"` → 3 hits across the three files; no residual `0.2.191` for registry-api.
- **Deps**: none (must land before CP-A).
- **Verify**: `grep -c "0.2.193" charts/agentshield/values.yaml scripts/deploy-cpe2e.sh scripts/deploy-eks.sh`

### No-orphan grep — Slice A
- `grep -rn "list_conversations" services/registry-api/routers` → callers in `memory.py` **and** `me.py`.
- `grep -rn "ConversationSummary" services/registry-api/routers services/registry-api/schemas.py` → defined once, used in both routers.

---

## ✅ CHECKPOINT CP-A — Backend deployed + smoked

**Gate: do not start Slice B until CP-A passes.** (Executable: `quickstart.md §CP-A`.)

```bash
# 1. registry-api built + deployed at 0.2.193 (user-gated shared-cluster step)
bash scripts/deploy-eks.sh                 # or deploy-cpe2e.sh for kind
# 2. suite-78 green against the deployed pod
bash scripts/e2e/suite-78-conversations.sh # all T-S78-00x RESULT … PASS, exit 0
```
Requires: T1–T6, T15a. Proves the query + port + DTO + both endpoints + environment
derivation end-to-end on a real pod before any UI is built (DoD 4 — vertical slice).

---

## Slice B — Frontend (shared component first, then the three mounts)

### T7 — API client: `ConversationSummary` type + `listConversations` + `listMyConversations` [P]
- **Files**: `studio/src/api/registryApi.ts`
- **Do**: add the TS `ConversationSummary` interface (data-model §4) + `listConversations(agentName, params?)` → `GET /agents/{name}/memory/conversations` and `listMyConversations(params?)` → `GET /me/conversations` (§6), in the Memory section (~L1557). Reuse existing `http` axios client.
- **Acceptance**: `import { listConversations, listMyConversations, ConversationSummary }` resolves; typecheck clean.
- **Deps**: none (parallel with Slice A).
- **Verify**: `cd studio && npm run typecheck`

### T8 — shared `ConversationSidebar` + `filterConversationsByEnv` (+ Vitest)
- **Files**: `studio/src/components/conversations/ConversationSidebar.tsx` (new), `studio/src/components/conversations/ConversationSidebar.test.tsx` (new)
- **Do**: implement `ConversationSidebarProps`, `EnvFilter`, `ConversationScope`, `filterConversationsByEnv` (§6). React Query keyed `["conversations", scope]`; `agent` scope → `listConversations(agentName,{deployment_id})`, `me` scope → `listMyConversations()`. Render `New conversation` button (`onNew`), optional env-filter pills (`showEnvFilter`), rows (title | `"Untitled conversation"`, agent name, env badge sbx/prod, `message_count` turns, relative `last_activity`), active row by `activeThreadId`, empty state "No conversations yet.", loading state. Row click → `onSelect(summary)`. **Pure list + filter — no transcript fetch.**
- **Acceptance (Vitest)**: renders mocked list; empty state on `[]`; row click calls `onSelect` with that summary; `New conversation` calls `onNew`; `filterConversationsByEnv` returns all for `"all"`, only-matching for `"sandbox"`/`"production"`.
- **Deps**: T7.
- **Verify**: `cd studio && npm run test -- ConversationSidebar && npm run typecheck`

### T9 — AgentChatPage: resettable session + `?session` seed + docked History (+ test)
- **Files**: `studio/src/pages/AgentChatPage.tsx`, `studio/src/pages/AgentChatPage.test.tsx`
- **Do**: (a) L76 → `const [sessionId, setSessionId] = useState(() => searchParams.get("session") ?? crypto.randomUUID())` (add `useSearchParams`); (b) on mount, if `?session` present → `seedFromThread(name, session, depId)` (§6 helper: `listMemory` → `setSessionId(threadId)` + `setMessages(user/assistant rows)`); (c) header `History` toggle mounting `<ConversationSidebar scope={{kind:"agent",agentName:name,deploymentId:depId}} activeThreadId={sessionId} onSelect={s => seedFromThread(name,s.thread_id,depId)} onNew={() => {setSessionId(crypto.randomUUID()); setMessages([]);}} />` in the existing flex shell. Guard: block select/new while `isStreaming || awaitingApproval`.
- **Acceptance**: `?session=<tid>` rehydrates prior turns from `/memory`; dock select swaps transcript + re-keys `sessionId`; New clears + fresh uuid; send-after-select reuses the thread's `session_id`.
- **Deps**: T7, T8.
- **Verify**: `cd studio && npm run test -- AgentChatPage && npm run typecheck`

### T10 — CatalogChatPage: resettable session + docked History (production) (+ test) [P]
- **Files**: `studio/src/pages/CatalogChatPage.tsx`, `studio/src/pages/CatalogChatPage.test.tsx`
- **Do**: same pattern as T9 with `scope={{kind:"agent", agentName, deploymentId: activeDeployment?.id}}`. `import { listMemory, listConversations }`. `const [sessionId, setSessionId] = useState(...)` at L195. Reuse the `mk` bubble factory; seed only user/assistant rows (plain bubbles — workflow rich slots not reconstructed, matching the reload path's non-workflow branch).
- **Acceptance**: docked History lists this production deployment's threads; select rehydrates; New resets; follow-up continues the thread.
- **Deps**: T7, T8. (Files disjoint from T9 → parallel.)
- **Verify**: `cd studio && npm run test -- CatalogChatPage && npm run typecheck`

### T11 — standalone `ConversationsPage` (real, /me, env filter) (+ Vitest)
- **Files**: `studio/src/pages/ConversationsPage.tsx` (new), `studio/src/pages/ConversationsPage.test.tsx` (new)
- **Do**: two-pane. Left = `<ConversationSidebar scope={{kind:"me"}} showEnvFilter activeThreadId={selected?.thread_id ?? null} onSelect={setSelected} onNew={...} />`. Right = read-only transcript preview of `selected` via `listMemory(selected.agent_name,{thread_id,deployment_id})` + a **Continue** button → `navigate(\`/agents/${selected.agent_name}/chat?session=${selected.thread_id}\`)` (sandbox resume; production standalone-continue is a **known gap §8**). No `ConsoleContextBar`, no preview banner — this is real. **New sibling to** `pages/preview/ConversationsPage.tsx` (mock kept until DEMO retired).
- **Acceptance**: lists caller's conversations cross-agent; env pills filter client-side; select shows transcript; Continue navigates to seeded chat.
- **Deps**: T7, T8.
- **Verify**: `cd studio && npm run test -- ConversationsPage && npm run typecheck`

### T12 — Deployment `Conversations` tab (new tab on DeploymentOverviewPage) (+ Vitest)
- **Files**: `studio/src/components/agent-detail/ConversationsTab.tsx` (new), `studio/src/pages/DeploymentOverviewPage.tsx` (edit), `studio/src/components/agent-detail/ConversationsTab.test.tsx` (new)
- **Do**: `ConversationsTab({agentName, deploymentId})` = `<ConversationSidebar scope={{kind:"agent",agentName,deploymentId}} activeThreadId={null} onSelect={s => navigate(\`/agents/${agentName}/d/${deploymentId}/chat?session=${s.thread_id}\`)} onNew={() => navigate(\`/agents/${agentName}/d/${deploymentId}/chat\`)} />` (nav reuses AgentChatPage + T9's `?session` seed — no chat-logic duplication). In `DeploymentOverviewPage.tsx`: extend `type Tab = "overview"|"runs"|"memory"|"conversations"` (research §R6, L30); add `"conversations"` to the tab-map array (label auto-capitalizes, L139); add `{activeTab === "conversations" && <ConversationsTab agentName={name!} deploymentId={depId} />}`. **Leave the existing `memory` tab (MemoryTab) untouched** — Conversations sits beside it.
- **Acceptance**: tab bar shows Overview / Runs / Memory / Conversations; Conversations lists this deployment's threads; click navigates to deployment chat seeded with that session; Memory tab still works.
- **Deps**: T8, T9 (nav relies on T9's `?session` seed).
- **Verify**: `cd studio && npm run test -- DeploymentOverview ConversationsTab && npm run typecheck`

### T13 — Nav promotion (retire PREVIEW_ITEMS) + route + experience doc
- **Files**: `studio/src/components/Sidebar.tsx`, `studio/src/App.tsx`, `docs/experience/playground.md`
- **Do**: (a) Sidebar — remove the `DEMO`-gated `Context Preview` section (`PREVIEW_ITEMS` block, L236–246) + drop the now-unused `PREVIEW_ITEMS` const (grep-verify it's used only there); add a real top-level `Conversations` item (`History` icon, already imported) → `/conversations`. (b) App — add `<Route path="/conversations" element={<ConversationsPage />} />` importing the **new** `./pages/ConversationsPage` (leave `/preview/conversations` → mock while `DEMO` remains). (c) `docs/experience/playground.md` — add a "Conversations & History" section: three surfaces + two endpoints + env filter + continue behaviour.
- **Acceptance**: `Conversations` in real nav (non-DEMO); `/conversations` renders the real page; no dead `PREVIEW_ITEMS`; experience doc updated.
- **Deps**: T11.
- **Verify**: `cd studio && npm run typecheck && grep -rn "PREVIEW_ITEMS" studio/src` (expect: no hits)

### T15b — studio image bump (0.1.146) [P]
- **Files**: `scripts/deploy-cpe2e.sh`, `scripts/deploy-eks.sh`, `charts/agentshield/values.yaml`
- **Do**: `STUDIO_TAG 0.1.144 → 0.1.146` in all three (deploy-cpe2e.sh ~L273, deploy-eks.sh ~L70, values.yaml ~L917). Update comment headers. Leave `DECLARATIVE_RUNNER_TAG 0.1.56` unchanged.
- **Acceptance**: `grep -R "0.1.146"` → 3 hits; no residual `0.1.144` for studio.
- **Deps**: none (must land before CP-C).
- **Verify**: `grep -c "0.1.146" charts/agentshield/values.yaml scripts/deploy-cpe2e.sh scripts/deploy-eks.sh`

### No-orphan grep — Slice B
- `grep -rn "listConversations\|listMyConversations" studio/src` → callers in `ConversationSidebar.tsx` (+ page usages).
- `grep -rn "ConversationSidebar" studio/src` → mounted in AgentChatPage, CatalogChatPage, ConversationsPage, ConversationsTab.
- `grep -rn "ConversationsTab" studio/src` → mounted in DeploymentOverviewPage.
- `grep -rn '"conversations"' studio/src/pages/DeploymentOverviewPage.tsx` → Tab literal in type, tab-map, and content conditional.

---

## ✅ CHECKPOINT CP-B — Frontend build gate

**Gate: must pass before CP-C deploy.**
```bash
cd studio && npm run typecheck && npm run test
```
Requires: T7–T13, T15b. TypeScript clean + all Vitest (ConversationSidebar list/empty/select/new/filter-predicate, AgentChatPage, CatalogChatPage, ConversationsPage env-filter, DeploymentOverviewPage new tab, ConversationsTab) green.

---

## Slice C — Playwright (real journeys, all three surfaces)

### T14 — Playwright specs (standalone + docked + deployment tab)
- **Files**: `studio/e2e/conversations-sidebar.spec.ts` (new), `studio/e2e/deployment-conversations.spec.ts` (new)
- **Do**: real Keycloak (global-setup), target the https gateway. Each spec: send a turn-1 message with a memorable fact → **reload** → assert the conversation is **listed** (from backend) → click → assert transcript **rehydrates** (`page.waitForResponse` on `/memory`) → follow-up → assert reply **recalls the turn-1 fact** (or, where few agent pods are deployed, assert the request fired + `session_id` reused — same boundary the bash suites accept).
  - `conversations-sidebar.spec.ts`: (1) standalone `/conversations` — list + env filter + Continue → seeded chat; (2) docked History in `AgentChatPage` (sandbox).
  - `deployment-conversations.spec.ts`: `/agents/:name/d/:depId` → **Conversations tab** → list → click → nav to `?session` chat → **rehydrate → follow-up recalls turn-1**. Assert the scoped list shows only this deployment's threads. (Production deployment-scoping proven at the endpoint by suite-78 T-S78-003; page-level run uses the reachable sandbox route — §8 gap.)
- **Acceptance**: `bash scripts/studio-e2e.sh` green for both specs (**after CP-C deploy**).
- **Deps**: T9, T11, T12, T13, CP-C.
- **Verify**: `bash scripts/studio-e2e.sh`

---

## ✅ CHECKPOINT CP-C — Studio deployed + Playwright proven

**Gate: final DoD gate.** (Executable: `quickstart.md §CP-C`.)
```bash
# 1. studio built + deployed at 0.1.146 (user-gated shared-cluster step)
bash scripts/deploy-eks.sh                 # or deploy-cpe2e.sh for kind
# 2. all three surfaces proven in the browser
bash scripts/studio-e2e.sh                 # conversations-sidebar + deployment-conversations green
```
Requires: T14, T15b, CP-B. Proves the real user journey on all three surfaces — especially
the **DeploymentOverviewPage conversations tab**: reload → listed → click → rehydrate →
continue-recalls-turn-1 (DoD 1 + 2).

---

## Dependency graph / MVP critical path

**MVP critical path**:
`T3 → T1 → T2 → T4 → T5 → T15a → CP-A → T7 → T8 → T9 → T11 → T12 → T13 → T15b → CP-B → CP-C → T14`

- T6 (suite-78) lands with Slice A but **runs after CP-A**.
- T10 (CatalogChatPage) parallels T9–T12 (disjoint files).
- Parallel-eligible **[P]**: T3 ∥ T1/T2; T7 ∥ all of Slice A; T10 ∥ T9/T11/T12; T15a ∥ Slice A; T15b ∥ Slice B.

## Task ↔ plan mapping

| Plan task | Tasks.md |
|---|---|
| T1 query | T1 | T2 port | T2 | T3 schema | T3 | T4 scoped endpoint | T4 | T5 /me endpoint | T5 |
| T6 suite-78 | T6 | T7 api client | T7 | T8 sidebar | T8 | T9 AgentChatPage | T9 | T10 CatalogChatPage | T10 |
| T11 ConversationsPage | T11 | T12 deployment tab | T12 | T13 nav/route/doc | T13 | T14 Playwright | T14 | T15 image bumps | T15a (registry-api) + T15b (studio) |

Every plan task maps. **No migration task** (Alembic head 0065 — read-only POC).
**Environment derivation** owned by the T1 query (`LEFT JOIN production_deployments` + `bool_or`),
verified by suite-78 T-S78-003 and the Vitest env-filter predicate — never a stored column.

## Known gaps (ledger — mirror into `docs/testing/manual-ui-e2e-test-plan.md` header)
- **Haiku titles** — deferred (intentional) → POC-1b. Title = first user message.
- **Production agent-deployment-overview page** — deferred (intentional). Conversations tab reachable on sandbox route only; production deployment-scoping proven at the endpoint (suite-78 T-S78-003).
- **Standalone Continue for production rows** — not-yet-wired (debt). Continue routes to sandbox chat; production resume covered by docked History in `CatalogChatPage`.
- **`user_id IS NULL` rows invisible** — by design (unattributed daemon/legacy turns).
- **Admin `MemoryTab` per-user privacy** — deferred → Tighten S9.
- **Aggregate `(user_id, created_at)` index** — deferred (intentional); add if suite-78 shows slowness.

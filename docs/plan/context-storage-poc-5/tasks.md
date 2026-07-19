# POC-5 — Conversations & Memory in the Product — Executable Tasks

**Branch:** `worktree-ux-preview-context-storage` (commit here ONLY — never merge/PR to main; Karthik merges manually).
**Spec:** `docs/design/context-storage-poc-5-conversations.md` · **Plan:** `./plan.md` · **Research:** `./research.md`
**Data model:** `./data-model.md` · **Contract:** `./contracts/list-conversations.md` · **Quickstart:** `./quickstart.md`
**Constitution:** `/Users/kalyankalvagadda/code/agent-shield/CLAUDE.md` (Definition of Done + Post-Implementation Checklist)

**Totals:** **11 implementation tasks (T001–T011)** + **3 checkpoints (CP1a / CP1b / CP1c)** = **14 items**.
**Targets:** `studio:0.1.147`. `registry-api:0.2.195` **UNCHANGED** (backend already shipped). `declarative-runner:0.1.57` unchanged. **No migration** (Alembic head stays `0067`).

> **Alignment Check:** the goal is *user-facing, resumable conversation history* at every chat surface, sandbox and production. The shipped backend derives `environment` as a pure read-side join on the already-stamped `deployment_id` (no schema change, no runner rebuild, no backfill). Every task below preserves that discipline: **one** `ConversationSidebar` reused at three mounts, each consumer passing an **explicit** `scope` — never sniffing an implicit env. No task degrades that to make something compile.

---

## ✅ DONE — Slice A backend (live in `registry-api:0.2.195`, committed `83199f5`)

**Do NOT emit or re-create these — they are shipped and load-bearing for the frontend.** Verified by reading the running code 2026-07-17:

| Symbol | Location (shipped) |
|---|---|
| `memory.list_conversations` (raw-SQL aggregate; `array_agg[1]::text` uuid fix — no `min(uuid)`) | `services/registry-api/memory.py` L388 (SQL const L362) |
| `ConversationStore.list_conversations` (Protocol + `PostgresConversationStore` adapter) | `services/registry-api/conversation_store.py` L97 / L223 |
| `ConversationSummary` DTO (Optional-heavy Pydantic; see data-model §4) | `services/registry-api/schemas.py` L1874 |
| `GET /agents/{name}/memory/conversations?deployment_id=` (`require_user`, declared before `/{name}/memory`) | `services/registry-api/routers/memory.py` L94 |
| `GET /me/conversations` (`require_user`) | `services/registry-api/routers/me.py` L95 |

**registry-api needs NO bump. No Alembic migration.** The only remaining backend artifact is `suite-78` (T001), which exercises this **already-live** pod. Everything else below is the frontend surface layer + test/deploy.

---

## Conventions

- **Task line:** `- [ ] [T00N] [P] <description> — \`primary path(s)\``, followed by **Do / Preserve / Acceptance / Deps / Verify** sub-bullets. Checkpoints: `- [ ] [CP1x] <description> — \`scripts/plan-poc5/…\``.
- **[P]** = parallel-safe: its files are disjoint from its sibling tasks AND its deps are already met. No shared file with another [P] task.
- **Granularity:** each task touches **1–3 closely related files**. A component and its colocated `*.test.tsx` count as one closely-related unit; unrelated files are never combined.
- **Checkpoints** `CP1a / CP1b / CP1c` are **mandatory gates** with executable scripts under `scripts/plan-poc5/`. Do not proceed past a red checkpoint.
- **Deploy is LOCAL docker-desktop** — `scripts/deploy-cpe2e.sh` builds images and runs `helm upgrade --install` (tags baked into `charts/agentshield/values.yaml`, no `--set`). **Do NOT use `scripts/deploy-eks.sh`** (that is the shared-EKS path). **The MAIN SESSION runs the actual build+deploy** — checkpoint scripts stop at the pre-deploy static gate and then assert rollout/verify.
- **Host note — no node/npm.** `node`/`npm` are **not installed on this host**, so `npm run typecheck` / `npm run test` cannot run locally. TypeScript is validated by the **studio Docker build** (`tsc && vite build` inside `studio/Dockerfile`, run at CP1c) — a type error **fails the build**. Vitest is **authored** but **CI/build-run, not host-run**. The local frontend gate is static only: `bash -n`, file presence, and no-orphan / dead-import / POC-4-preservation greps (CP1b).

---

## Phase summary

| Phase | Tasks | Purpose | Gate |
|---|---|---|---|
| **0 — Backend proof** | T001 | `suite-78` e2e against the already-live `0.2.195` pod (only remaining backend work) | **CP1a** |
| **1 — Foundational frontend** | T002, T003 | Shared API client (type + 2 fns) + the one shared `ConversationSidebar` component | — |
| **2 — Chat console mounts** | T004, T005 | Docked History in `AgentChatPage` (sandbox) + `CatalogChatPage` (production); resettable session | — |
| **3 — Standalone / deployment / nav** | T006, T007, T008 | Real `/conversations` page, deployment `Conversations` tab, nav promotion + route | **CP1b** |
| **4 — Polish: docs + tests + bump** | T009, T010, T011 | Experience doc, Playwright specs, studio `0.1.147` bump (both files) | **CP1c** |

**Checkpoint placement:** CP1a after Phase 0 (backend contract gate before any UI); CP1b after Phase 3 (frontend static gate over all authored TS — no cluster, no npm); CP1c after Phase 4 (studio Docker build = type gate → local `helm upgrade` deploy → Playwright green). The plan's CP-B is folded into the CP1b static gate + the CP1c build gate because node/npm is not on this host.

---

## Phase 0 — Backend proof (Slice A — the only remaining backend work)

- [ ] [T001] [P] suite-78 backend e2e (list + ownership + env-scope + cross-agent) against the already-live `0.2.195` pod, registered after suite-77 — `scripts/e2e/suite-78-conversations.sh` (new), `scripts/e2e/run-all.sh` (edit)
  - **Do:** mirror `suite-76-preferences.sh` / `suite-77-knowledge-rag.sh` (`kubectl exec` into `registry-api`, `httpx`, `X-User-Sub` / `X-User-Team` headers). Seed via `POST /agents/{name}/memory` on a real memory-enabled agent: two threads for USER_A (one whose `deployment_id` is a real `production_deployments.id`; one sandbox / no `deployment_id`) and one thread for USER_B. `chmod +x`. Register **after suite-77 (run-all.sh L126)**: `run_suite "Suite 78: Conversations (POC-5 list)" "suite-78-conversations.sh"`.
  - **Cases:** `T-S78-001` — `GET /agents/{name}/memory/conversations` (USER_A): per-thread summaries, `title` = first user message, correct `message_count`, `last_activity` present, **newest-first**. `T-S78-002` — ownership: USER_B's list excludes USER_A's threads (and vice-versa). `T-S78-003` — `?deployment_id=<prod id>` returns only the production thread tagged `environment="production"`; the sandbox thread's deployment returns only it, tagged `"sandbox"`. `T-S78-004` — `GET /me/conversations` (USER_A) returns **both** threads (cross-agent), each carrying its `environment`.
  - **Acceptance:** `bash scripts/e2e/suite-78-conversations.sh` prints all `RESULT … PASS`, exit 0 — against the **already-live** `0.2.195` pod (no rebuild).
  - **Deps:** none (backend shipped).
  - **Verify:** `bash -n scripts/e2e/suite-78-conversations.sh && test -x scripts/e2e/suite-78-conversations.sh && grep -n "suite-78-conversations" scripts/e2e/run-all.sh`

- [ ] [CP1a] Backend contract gate — suite-78 green against the live `0.2.195` pod — `scripts/plan-poc5/checkpoint-a.sh` (new, executable)
  - **Do:** author `scripts/plan-poc5/checkpoint-a.sh` (mirrors quickstart §CP-A): (1) assert the running pod image is `…/registry-api:0.2.195` (`kubectl -n agentshield-platform get deploy/agentshield-registry-api -o jsonpath=…`); if it predates it, the **main session** runs `bash scripts/deploy-cpe2e.sh` first (builds registry-api at the **unchanged** `0.2.195` tag — no bump); (2) in-pod smoke both endpoints with real auth headers (`/me/conversations` → 200 + JSON list); (3) `bash scripts/e2e/suite-78-conversations.sh`.
  - **Acceptance / Exit:** pod on `0.2.195`, both endpoints `200`, suite-78 all-PASS, exit 0.
  - **GATE:** **do not start UI work (T003+) until CP1a passes** — it proves the contract every frontend surface binds to. (T002 API-client typing may proceed in parallel; it is pure types.)
  - **Deps:** T001.
  - **Verify:** `bash scripts/plan-poc5/checkpoint-a.sh`

---

## Phase 1 — Foundational frontend (shared client + shared component)

- [ ] [T002] [P] API client: `ConversationSummary` TS interface + `listConversations(agentName, params?)` + `listMyConversations(params?)` — `studio/src/api/registryApi.ts`
  - **Do:** add the exact shapes from plan §6 / data-model §4 in the Memory section next to `listMemory` (~L1588–1600; `MemoryMessage` is L1574). `listConversations(agentName, {deployment_id?, limit?, offset?})` → `GET /agents/${agentName}/memory/conversations`; `listMyConversations({limit?, offset?})` → `GET /me/conversations`. Type `agent_name` and `last_activity` as **non-null** `string` (the aggregate guarantees them — `min()` over a NOT-NULL column / `max(created_at)`), while `session_id`/`title`/`deployment_id` stay nullable; `environment: "sandbox" | "production"`.
  - **Preserve:** the uncommitted **POC-4 knowledge additions** already in this file — do not remove/reorder them.
  - **Acceptance:** `import { listConversations, listMyConversations, ConversationSummary }` resolves; consumed by T003–T007.
  - **Deps:** none (parallel with Phase 0).
  - **Verify:** `grep -n "listConversations\|listMyConversations\|interface ConversationSummary" studio/src/api/registryApi.ts` (3 hits); types green in the CP1c Docker build.

- [ ] [T003] Shared `ConversationSidebar` (pure list + filter, React-Query-fed) + `filterConversationsByEnv` + Vitest — `studio/src/components/conversations/ConversationSidebar.tsx` (new), `studio/src/components/conversations/ConversationSidebar.test.tsx` (new)
  - **Do:** implement the plan §6 interface (`ConversationSidebarProps`, `ConversationScope` discriminated union `{kind:"agent",agentName,deploymentId?} | {kind:"me"}`, `EnvFilter`, `filterConversationsByEnv`). React Query keyed on the scope (`["conversations", scope]`): `agent` scope → `listConversations(agentName,{deployment_id})`, `me` scope → `listMyConversations()`. Render: a `New conversation` button (calls `onNew`), optional env-filter pills (`showEnvFilter` — All / Sandbox / Production), rows via `filterConversationsByEnv` (title | `"Untitled conversation"`, agent name, env badge sbx/prod, `message_count` turns, relative `last_activity`), active row highlighted by `activeThreadId`, empty state `"No conversations yet."`, loading state. `onSelect(summary)` on row click. When `disabled`, suppress select/new (block while streaming / awaiting approval). It does **NOT** fetch transcripts — each consumer seeds on `onSelect`.
  - **Acceptance (Vitest, CI/build-run):** renders a mocked list; empty state on `[]`; row click → `onSelect(summary)`; `New conversation` → `onNew`; `filterConversationsByEnv` returns all for `"all"`, only-matching for `"sandbox"`/`"production"`.
  - **Deps:** T002, CP1a.
  - **Verify:** `test -f studio/src/components/conversations/ConversationSidebar.tsx && grep -n "filterConversationsByEnv\|ConversationScope" studio/src/components/conversations/ConversationSidebar.tsx`; Vitest authored (green in CI/build).

---

## Phase 2 — Chat console mounts (docked History; resettable session)

- [ ] [T004] `AgentChatPage`: resettable `sessionId` + `?session` seed + docked History — **preserving POC-4 citation wiring already on disk** — `studio/src/pages/AgentChatPage.tsx`, `studio/src/pages/AgentChatPage.test.tsx`
  - **Preserve (POC-4, uncommitted — do NOT remove/regress):** `citations?: Citation[]` on `Message` (**L29–30**); the `Citation`/`routeToken`/`openAuthorBubble`/`attachCitations`/`parseKnowledgeCitations` imports from `../lib/chatStream` (**L17–23**); `maybeAttachCitations` (**L121**); `citations={m.citations}` on `AttributedBubble` (**L486**).
  - **Do:** (a) add `useSearchParams` to the `react-router-dom` import (**L2**); (b) change **L84** `const [sessionId] = useState(() => crypto.randomUUID());` → `const [searchParams] = useSearchParams(); const [sessionId, setSessionId] = useState(() => searchParams.get("session") ?? crypto.randomUUID());`; (c) add `listMemory`, `listConversations` to the registryApi import (**L5–13**); (d) add a `seedFromThread(agentName, threadId, deploymentId?)` helper (plan §6) mapping `listMemory` rows → the local `Message` type (**plain user/assistant bubbles only** — rich slots NOT reconstructed); (e) on mount, if `?session` present → `seedFromThread(name, session, depId)`; (f) add a header **History** toggle mounting `<ConversationSidebar scope={{kind:"agent", agentName:name!, deploymentId:depId}} activeThreadId={sessionId} onSelect={s => seedFromThread(name!, s.thread_id, depId)} onNew={() => { setSessionId(crypto.randomUUID()); setMessages([]); }} disabled={isStreaming || awaitingApproval} />` in a dock within the existing `flex h-screen` shell (the right side already conditionally mounts `ConversationApprovalPanel` — add the dock without breaking that).
  - **Acceptance:** `?session=<tid>` rehydrates prior turns from `/memory`; selecting a row swaps the transcript + re-keys `sessionId`; New clears + fresh uuid; sending after a select reuses the thread's `session_id`; **citation chips still render on live knowledge answers**.
  - **Deps:** T002, T003.
  - **Verify:** `grep -n "setSessionId\|seedFromThread\|ConversationSidebar\|attachCitations" studio/src/pages/AgentChatPage.tsx` (all present); Vitest updated.

- [ ] [T005] [P] `CatalogChatPage`: resettable `sessionId` + docked History (production) — **preserving POC-2b rich Message slots + POC-4 wiring** — `studio/src/pages/CatalogChatPage.tsx`, `studio/src/pages/CatalogChatPage.test.tsx`
  - **Preserve (POC-2b + POC-4, uncommitted — do NOT regress):** the rich `Message` slots (`toolCalls`, `rationale`, `citations`, `tree`, `runId`, **L34–46**), `WorkflowTurn`, the workflow SSE reducers (`openAuthorBubble`/`routeToken`/`attachToolCall`/`attachRationale`), and the `sessionStorage wf-lastrun` reload path.
  - **Do:** (a) `useSearchParams` is already imported (**L153**, reads `?dep`); (b) change **L195** `const [sessionId] = useState(...)` → `const [sessionId, setSessionId] = useState(() => searchParams.get("session") ?? crypto.randomUUID());`; (c) add `listMemory`, `listConversations` to the registryApi import (**L6–14**); (d) add a `seedFromThread` helper mapping `listMemory` rows → plain user/assistant `Message`s; (e) mount `<ConversationSidebar scope={{kind:"agent", agentName:agentName!, deploymentId:activeDeployment?.id}} activeThreadId={sessionId} onSelect={s => seedFromThread(agentName!, s.thread_id, activeDeployment?.id)} onNew={() => { setSessionId(crypto.randomUUID()); setMessages([]); }} disabled={isStreaming || !!pendingApproval} />` in a docked panel. **Layout:** the page is `flex flex-col h-screen` (**vertical**) — wrap the existing column in a horizontal flex (or use a slide-over drawer) so the sidebar docks beside it; do not break the header / console-shell / messages / input stack.
  - **Acceptance:** docked History lists this production deployment's threads; select rehydrates; New resets; follow-up continues the thread; the workflow reload path + attribution still work.
  - **Deps:** T002, T003 (disjoint files from T004 → parallel-safe with it).
  - **Verify:** `grep -n "setSessionId\|seedFromThread\|ConversationSidebar\|WorkflowTurn" studio/src/pages/CatalogChatPage.tsx` (all present); Vitest updated.

---

## Phase 3 — Standalone page + deployment tab + nav promotion

- [ ] [T006] [P] Real standalone `ConversationsPage` (cross-agent `/me`, env filter, transcript preview + Continue) + Vitest — `studio/src/pages/ConversationsPage.tsx` (new — a **sibling** to the preview mock at `pages/preview/ConversationsPage.tsx`, which stays), `studio/src/pages/ConversationsPage.test.tsx` (new)
  - **Do:** two-pane. Left = `<ConversationSidebar scope={{kind:"me"}} showEnvFilter activeThreadId={selected?.thread_id ?? null} onSelect={setSelected} onNew={...} />`. Right = read-only transcript preview of `selected` via `listMemory(selected.agent_name, {thread_id: selected.thread_id, deployment_id: selected.deployment_id ?? undefined})` + a **Continue** button → `navigate(\`/agents/${selected.agent_name}/chat?session=${selected.thread_id}\`)` (sandbox resume path; production standalone-Continue is a known gap — see ledger). Real header copy — **no** `ConsoleContextBar`, **no** amber "preview" banner (those belong to the mock).
  - **Acceptance:** lists the caller's conversations across agents; env pills filter **client-side**; selecting shows the transcript; Continue navigates to the seeded chat.
  - **Deps:** T002, T003.
  - **Verify (Vitest):** mock `listMyConversations` with mixed envs → All shows both, Sandbox only sandbox, Production only production; empty state. `test -f studio/src/pages/ConversationsPage.tsx`.

- [ ] [T007] [P] Deployment `Conversations` tab (scoped, nav-to-chat) + Vitest — `studio/src/components/agent-detail/ConversationsTab.tsx` (new), `studio/src/pages/DeploymentOverviewPage.tsx` (edit), `studio/src/components/agent-detail/ConversationsTab.test.tsx` (new)
  - **Do:** `ConversationsTab({ agentName, deploymentId })` = `<ConversationSidebar scope={{kind:"agent", agentName, deploymentId}} activeThreadId={null} onSelect={s => navigate(\`/agents/${agentName}/d/${deploymentId}/chat?session=${s.thread_id}\`)} onNew={() => navigate(\`/agents/${agentName}/d/${deploymentId}/chat\`)} />` (nav reuses the full `AgentChatPage` deployment-chat machinery + T004's `?session` seed — no chat-logic duplication; route `/agents/:name/d/:depId/chat` already exists, App.tsx L75). In `DeploymentOverviewPage.tsx`: extend `type Tab = "overview" | "runs" | "memory";` (**L30**) → `… | "conversations";`; add `"conversations"` to the tab-map array (**L139** — the `capitalize` class auto-labels it); add `{activeTab === "conversations" && <ConversationsTab agentName={name!} deploymentId={depId} />}` after the memory conditional (**L167**). Keep the existing `memory` tab (`MemoryTab`) untouched — Conversations sits **beside** it (operator-inspect vs user-resume).
  - **Acceptance:** the tab bar shows Overview / Runs / Memory / Conversations; Conversations lists this deployment's threads; clicking navigates to the deployment chat seeded with that session; the Memory tab still works.
  - **Deps:** T003, T004 (nav relies on T004's `?session` seed). Disjoint files from T006 → parallel-safe with it.
  - **Verify:** `grep -n "conversations\|ConversationsTab" studio/src/pages/DeploymentOverviewPage.tsx`; Vitest.

- [ ] [T008] Nav promotion (retire DEMO `PREVIEW_ITEMS`, add real `Conversations` item w/ History icon) + `/conversations` route → real page — `studio/src/components/Sidebar.tsx`, `studio/src/App.tsx`
  - **Do:** (a) **Sidebar** — delete the `DEMO`-gated *Context Preview* render block (**L236–246**) and the `PREVIEW_ITEMS` const (**L43–47**); the `Home` and `MessagesSquare` imports (**L25**) become unused — **remove them** (keep `History`, `SlidersHorizontal`; keep `DEMO` — still used by `BUILD_ITEMS` L50). Add a real top-level `Conversations` item (`History` icon) → `/conversations` (its own single-item group near the top, above Build). (b) **App** — add `<Route path="/conversations" element={<ConversationsPage />} />` importing the **new** `./pages/ConversationsPage`; the existing `import ConversationsPage from "./pages/preview/ConversationsPage"` (**L47**) collides — **rename that import to `PreviewConversationsPage`** and update its `/preview/conversations` route (**L70**) to use it, leaving the preview reachable while `DEMO` remains.
  - **Preserve:** the uncommitted **POC-4 knowledge routes** (`/knowledge`, `/knowledge/:id`, L66–67) in `App.tsx`.
  - **Acceptance:** `Conversations` appears in real (non-DEMO) nav; `/conversations` renders the real page; no dead `PREVIEW_ITEMS`/`Home`/`MessagesSquare` reference in Sidebar; `/preview/conversations` still resolves.
  - **Deps:** T006.
  - **Verify:** `grep -n "PREVIEW_ITEMS" studio/src/components/Sidebar.tsx` (**no hits**); `grep -n "MessagesSquare\|\bHome\b" studio/src/components/Sidebar.tsx` (**no hits**); `grep -n "/conversations" studio/src/App.tsx` (route present).

- [ ] [CP1b] Frontend static gate — no cluster, no npm — `scripts/plan-poc5/checkpoint-b.sh` (new, executable)
  - **Do:** author `scripts/plan-poc5/checkpoint-b.sh` (mirrors quickstart §CP-B). Assert: (1) **no-orphan** — each new symbol has a live caller: `listConversations`/`listMyConversations` (client + sidebar), `interface ConversationSummary` (registryApi), `ConversationSidebar` (T004/T005/T006/T007 mounts), `filterConversationsByEnv` (sidebar + page filter), `ConversationsTab` (DeploymentOverviewPage), `"conversations"` (4th tab); (2) **retired** — `grep -rn "PREVIEW_ITEMS" studio/src` and `grep -rn "\bHome\b\|MessagesSquare" studio/src/components/Sidebar.tsx` → **no hits**; (3) **files present** — `registryApi.ts`, `ConversationSidebar.tsx`, `ConversationsPage.tsx`, `ConversationsTab.tsx`; (4) **POC-4 preserved** — `grep -n "attachCitations\|parseKnowledgeCitations\|citations" studio/src/pages/AgentChatPage.tsx` still matches; (5) `bash -n scripts/e2e/suite-78-conversations.sh`.
  - **Acceptance / Exit:** all new files present, every no-orphan grep returns a caller, retired greps return nothing, POC-4 citation wiring still referenced, exit 0. (Type/Vitest gates run inside the studio Docker build at CP1c — node/npm absent here.)
  - **Deps:** T002–T008.
  - **Verify:** `bash scripts/plan-poc5/checkpoint-b.sh`

---

## Phase 4 — Polish: experience doc + Playwright + image bump

- [ ] [T009] [P] Experience doc — **append** a Conversations & History section (do NOT clobber POC-4's Knowledge section) — `docs/experience/playground.md`
  - **Do:** **append** a new `## Conversations & History (context-storage POC-5)` section at the **end** of the file (POC-4's "Team Knowledge Base / RAG & citation chips" section at **L557** stays untouched). Cover: the three surfaces (standalone `/conversations`, docked History in `AgentChatPage`/`CatalogChatPage`, deployment `Conversations` tab), the two endpoints (`GET /agents/{name}/memory/conversations`, `GET /me/conversations`), the All/Sandbox/Production filter, and continue-with-context behaviour (reuse `session_id` → runner reloads prior turns via `declarative-runner/main.py::_load_memory_context`).
  - **Acceptance:** new section present; the POC-4 section (L557) is byte-for-byte untouched.
  - **Deps:** T006, T007.
  - **Verify:** `grep -n "Conversations & History (context-storage POC-5)\|Team Knowledge Base / RAG & citation chips" docs/experience/playground.md` (both hits).

- [ ] [T010] Playwright — three surfaces, reload→listed→rehydrate→recall — `studio/e2e/conversations-sidebar.spec.ts` (new), `studio/e2e/deployment-conversations.spec.ts` (new)
  - **Do:** real Keycloak (global-setup), target the https gateway. Per DoD each spec: send a turn-1 message carrying a memorable fact → **reload** the page → assert the conversation is **listed** (from backend) → click it → assert the prior transcript **rehydrates** (`page.waitForResponse` on `/memory`) → send a follow-up → assert the reply **recalls the turn-1 fact** (or, where few agent pods are deployed, assert the request fired + `session_id` reused — the same boundary the bash suites accept).
    - `conversations-sidebar.spec.ts`: (1) standalone `/conversations` — list + env filter + Continue → seeded chat; (2) docked History in `AgentChatPage` (sandbox).
    - `deployment-conversations.spec.ts`: `/agents/:name/d/:depId` → Conversations tab → list → click → nav to `?session` chat → rehydrate → follow-up. Assert the scoped list only shows this deployment's threads. (Production deployment-scoping is proven at the endpoint by suite-78 `T-S78-003`; the page-level run uses the reachable **sandbox** route — see ledger.)
  - **Acceptance:** `bash scripts/studio-e2e.sh` green for both specs (run at CP1c, after deploy).
  - **Deps:** T004, T006, T007, T008; **runs at CP1c (post-deploy)**.
  - **Verify:** `test -f studio/e2e/conversations-sidebar.spec.ts && test -f studio/e2e/deployment-conversations.spec.ts`; executed by `bash scripts/studio-e2e.sh` after CP1c deploy.

- [ ] [T011] [P] Studio image bump `0.1.146 → 0.1.147` (both files; `values.yaml` is LAGGING at `0.1.145` — reconcile both to `0.1.147`) — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - **Do:** set `STUDIO_TAG 0.1.146 → 0.1.147` in `deploy-cpe2e.sh` (**L291**) and the studio image `tag 0.1.145 → 0.1.147` in `values.yaml` (**L936** — currently lagging). Update both comment headers ("0.1.147: POC-5 Conversations & History — sidebar + 3 surfaces + real nav"). Leave `REGISTRY_API_TAG 0.2.195` (L278) and `DECLARATIVE_RUNNER_TAG 0.1.57` (L293) **unchanged**.
  - **Acceptance:** `grep -R "0.1.147" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` → 2 hits; no residual `0.1.146`/`0.1.145` studio tag in these two files.
  - **Deps:** none (values consumed at deploy; do before CP1c).
  - **Verify:** `grep -c "0.1.147" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml`

- [ ] [CP1c] Studio built (type gate) + deployed (local `helm upgrade`) + Playwright green — `scripts/plan-poc5/checkpoint-c.sh` (new, executable)
  - **Do:** author `scripts/plan-poc5/checkpoint-c.sh` reflecting the **LOCAL docker-desktop** flow: (1) confirm the bump — `grep -R "0.1.147" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` (2 hits); (2) **the MAIN SESSION** runs `bash scripts/deploy-cpe2e.sh` (builds `studio:0.1.147` via `tsc && vite build` — **this is the TypeScript gate; a type error fails the build** — then `helm upgrade --install`, tags baked in, no `--set`); the script then asserts `kubectl -n agentshield-platform rollout status deploy/agentshield-studio --timeout=180s`; (3) `bash scripts/studio-e2e.sh` (real Keycloak, https gateway) runs both new specs. **NOT** `scripts/deploy-eks.sh`.
  - **Acceptance / Exit (DoD 1+2):** studio Docker build clean (no type errors), rollout healthy, and for each surface — reload → conversation listed → click → transcript rehydrates (a `/memory` response is awaited) → follow-up reuses the thread's `session_id` (recalls turn-1 where an agent pod is live). Any step that can't run (no live pod) is recorded in `docs/testing/manual-ui-e2e-test-plan.md` with the reason.
  - **Deps:** T009, T010, T011, CP1b.
  - **Verify:** `bash scripts/plan-poc5/checkpoint-c.sh` (main session runs the deploy step).

---

## Dependency graph (no forward refs)

```
T001 ─► CP1a ─► T003
T002 ─────────► T003
T003 ─► T004 ;  T003 ─► T005 [P]
T004, T003 ─► T006 [P]
T004, T003 ─► T007 [P]
T006 ─► T008
T002…T008 ─► CP1b
T006, T007 ─► T009 [P]
T004, T006, T007, T008 ─► T010
(no deps) ─► T011 [P]
T009, T010, T011, CP1b ─► CP1c
```

## MVP critical path (suggested scope)

**Full path:** `T001 → CP1a → T002 → T003 → T004 → T006 → T007 → T008 → T009 → T011 → CP1c → T010`.

- **Thinnest shippable slice (prove one vertical path first, per DoD 4):** `T001 → CP1a → T002 → T003 → T004 → T011 → CP1c` — backend proven + the shared component + the **sandbox docked-History** mount, deployed and Playwright-checked. That alone demonstrates list → select → rehydrate → continue end-to-end at one surface.
- **T005** (production docked History) parallels T004; **T006/T007** parallel each other; **T009/T011** are disjoint polish — add them after the sandbox slice is green.
- The plan's CP-B is folded into **CP1b** (static) + the **CP1c** studio Docker build, since node/npm is not on this host.

## Parallel batches ([P] = disjoint files, deps met)

- **Batch A (kickoff):** T001 (backend e2e) ‖ T002 (API client types) — no shared files.
- **Batch B (after T003):** T004 ‖ T005 — disjoint chat pages.
- **Batch C (after T004):** T006 ‖ T007 — standalone page vs deployment tab, disjoint files.
- **Batch D (polish):** T009 ‖ T011 — experience doc vs deploy/values, disjoint.

---

## Known gaps (ledger — carried from plan §8; mirror into `docs/testing/manual-ui-e2e-test-plan.md` header)

- **Haiku conversation titles** — *deferred (intentional)* → POC-1b. Title = first user message until then.
- **Production agent-deployment-overview route** — *deferred (intentional)*. No dedicated production route for `DeploymentOverviewPage` exists today (research §R6); the Conversations **tab** is reachable on the **sandbox** route only. Production deployment-scoping is proven at the **endpoint** by suite-78 `T-S78-003`.
- **Standalone Continue for production rows** — *not-yet-wired (debt)*. `ConversationsPage` Continue routes to the sandbox agent chat (`/agents/:name/chat?session=`); production rows need artifact-id resolution to route to `CatalogChatPage`. Rows still list + preview; only the Continue target is sandbox. (Docked History in `CatalogChatPage` covers production resume.)
- **Seed drops rich slots** — *by design*. Selecting a past thread seeds plain user/assistant bubbles (no citation chips / tool chips / rationale / run-tree reconstructed). The live path still renders them; only the rehydrated history is plain — matching each page's existing non-live reload branch.
- **`user_id IS NULL` rows invisible** — *by design*. Daemon/legacy turns have no owner to scope to.
- **Admin `MemoryTab` per-user privacy** — *deferred* → Tighten S9 (unchanged by POC-5).
- **Aggregate index** — *deferred (intentional)*. No `(user_id, created_at)` index yet; add if suite-78 shows slowness at scale.

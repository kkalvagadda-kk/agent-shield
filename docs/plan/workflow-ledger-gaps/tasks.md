# Tasks — Workflow ledger gaps (G1 rehydrate chat · G2 Memory tab · G3 deferred)

**Plan:** `docs/plan/workflow-ledger-gaps/plan.md` · **Brief:** `docs/plan/workflow-ledger-gaps/design-brief.md`
**Grounded against:** studio `0.1.155` (task #16 already deployed) / registry-api `0.2.206` (code read 2026-07-18).
**Target images:** registry-api `0.2.206`→`0.2.207` (G2 backend) · studio **`0.1.156`** (G1 + G2 wiring).
**Companion:** `research.md` (decisions), `quickstart.md` (copy-paste build/deploy/test).
**No migration** — reuses `agent_memory` + `agent_runs` exactly as POC-5's Conversations fix did.

> **STUDIO TAG = 0.1.156 (NOT 0.1.155).** studio `0.1.155` is already built/deployed for the unrelated
> EditAgentModal `knowledge_search` class-fix (task #16). The current file value is `0.1.155`
> (`deploy-cpe2e.sh` L325, `values.yaml` L954), so the physical edit in T9b is **`0.1.155`→`0.1.156`**
> in BOTH files. Do NOT double-bump. See **Execution Notes → Shared studio build**.

---

## Overview

| | Count |
|---|---|
| **Implementation tasks** | 11 (T1, T2a, T2b, T3, T4, T5, T6, T7, T8, T9a, T9b) |
| **Checkpoint scripts** | 7 (CP1a, CP1b, CP2a, CP3a, CP4a, CP4b, CP4c) |
| **Total tasks** | **18** |
| **Phases** | 5 impl phases + 4 checkpoint phases |
| **Parallel opportunities** | T3∥T4 · T6∥T7 · T8∥T9a∥T9b |

### Phase → Checkpoint layout

| # | Phase | Tasks | Gate / Output | Parallel |
|---|---|---|---|---|
| P0 | Reproduce G2 backend (RED) | T1 | `T-S78-006` FAILS http=404 | — |
| P1 | Fix G2 backend + registry-api tag | T2a, T2b | query→store→endpoint added | — |
| **CP1** | **Build+deploy registry-api `0.2.207`; suite-78** | CP1a, CP1b | `T-S78-006` PASS · 001–005 green | — |
| P2 | Reproduce G1+G2 frontend (RED) | T3, T4 | two Vitests written | **T3∥T4** |
| **CP2** | **Reproduce-first gate — confirm BOTH RED** | CP2a | both Vitests FAIL | — |
| P3 | Fix frontend (client + G1 + G2) | T5, T6, T7 | rehydrate + tab wired | **T6∥T7** (after T5) |
| **CP3** | **Typecheck + full Vitest green** | CP3a | tsc clean · all Vitest green | — |
| P4 | Playwright + docs + studio tag | T8, T9a, T9b | spec + bug doc + tag `0.1.156` | **T8∥T9a∥T9b** |
| **CP4** | **Build+deploy studio `0.1.156`; end-user verify + regression sweep** | CP4a, CP4b, CP4c | both journeys visibly correct · all suites green | — |

**Legend:** `[R]` reproduce (must FAIL first) · `[F]` fix/impl · `[P]` parallelizable (different files, no incomplete-sibling dep).
Checkpoint scripts live under `scripts/checkpoints/`, start `#!/usr/bin/env bash` + `set -euo pipefail`, echo `=== Checkpoint N: … ===`, use real kubectl/curl/jq, assert HTTP codes + JSON fields, exit non-zero on first failure, end `echo "PASS"`. The MAIN session runs each checkpoint (it does the actual build/deploy/suite execution).

---

## Phase 0 — Reproduce G2 backend (RED)

- [ ] [T1] [R] Add `T-S78-006` reproduce case (exercises `GET /workflows/{id}/memory`, FAILS http=404 pre-fix) — `scripts/e2e/suite-78-conversations.sh`
  - **Reuse the existing workflow fixture** (seed L154-177): workflow `wf_id`, parent `AgentRun` owned by USER_A with `session_id=T_WF`, two `workflow_run` member rows (`agent_name=WF_MEMBER`, `user_id=NULL`, first user content `WF_FIRST`).
  - **Assert (USER_A bearer):** `GET /api/v1/workflows/{wf_id}/memory` → ≥2 rows, all `thread_id==T_WF` · `agent_name==WF_MEMBER` · `scope=="workflow_run"`; the first user row's content `==WF_FIRST`. `?thread_id=T_WF` → same thread oldest-first (`message_index` ascending). **USER_B** call → `[]` (ownership via parent run).
  - **Append** `"T-S78-006"` to `IDS` (L83). Ordering/env-skip discipline identical to `T-S78-005` (L270-290).
  - **Verify (RED):** `bash scripts/e2e/suite-78-conversations.sh` → `T-S78-006 FAIL http=404` (endpoint absent). Records the reproduce; `T-S78-001..005` unaffected.

---

## Phase 1 — Fix G2 backend + registry-api tag

- [ ] [T2a] [F] Workflow memory read: service query + ConversationStore port/adapter — `services/registry-api/memory.py`, `services/registry-api/conversation_store.py`
  - **`memory.py`:** add `from models import AgentRun` to the L21 import; add `async def list_workflow_memory(db, *, workflow_id, user_id, thread_id=None, limit=200, offset=0) -> list[AgentMemory]` — ORM `select(AgentMemory)` where `scope=="workflow_run"` AND `thread_id IN (select(AgentRun.session_id) where workflow_id==UUID, parent_run_id IS NULL, user_id==user_id, session_id IS NOT NULL).distinct()`; `thread_id` given → `.where(thread_id==)` + `order_by(message_index.asc())` (replay/G1); absent → `order_by(created_at.desc())` (tab/G2); then `.limit().offset()`. Mirrors `list_recent` return (L235-265) + `_LIST_WORKFLOW_CONVERSATIONS_SQL` scoping (L396-423). Exact body in plan **Key Interfaces**.
  - **`conversation_store.py`:** add matching `list_workflow_memory(...)` to the `ConversationStore` Protocol (after L130) AND the `PostgresConversationStore` adapter (after L276) — adapter delegates verbatim to `memory.list_workflow_memory(...)`. Mirrors the `list_workflow_conversations` port pair (L115-130 / L259-276).
  - **Dependencies:** T1.
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/memory.py').read())"` (+ `conversation_store.py`); mapper check `cd services/registry-api && python3 -c "import conversation_store, memory; from routers import composite_workflows; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('ok')"`. No orphan yet — adapter references the service method; router added in T2b.

- [ ] [T2b] [F] `GET /workflows/{id}/memory` endpoint + registry-api tag bump `0.2.206`→`0.2.207` — `services/registry-api/routers/composite_workflows.py`, `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - **`composite_workflows.py`:** add `AgentMemoryResponse` to the `schemas` import block (L39-59); add `GET /{workflow_id}/memory` after the conversations endpoint (~L297): `response_model=list[AgentMemoryResponse]`, `Depends(require_user)`, `wf = await _get_workflow(workflow_id, db)` (404), `store.list_workflow_memory(db, workflow_id=str(workflow_id), user_id=claims["sub"], thread_id=…, limit=…, offset=…)`, return `[AgentMemoryResponse.model_validate(r) for r in rows]`. `thread_id: str|None=Query(None)`, `limit=Query(200, ge=1, le=500)`, `offset=Query(0, ge=0)`. Mirrors `GET /{workflow_id}/conversations` (L269-296). Exact body in plan **Key Interfaces**.
  - **`deploy-cpe2e.sh`:** `REGISTRY_API_TAG` `0.2.206`→`0.2.207` (L295) + update the studio/registry comment header describing G2 backend.
  - **`values.yaml`:** registry-api `tag: "0.2.206"`→`"0.2.207"` (L623) + a comment line (mirror the L621-622 style). helm uses baked values (no `--set`) — both files MUST match.
  - **Dependencies:** T2a.
  - **Verify:** `python3 -c "import ast; ast.parse(open('services/registry-api/routers/composite_workflows.py').read())"`; re-run the mapper check from T2a. No orphan: `grep -rn "list_workflow_memory" services/registry-api` shows service←adapter←router chain. `grep -n "0.2.207" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` shows both bumped.

---

## Checkpoint 1 — Build+deploy registry-api `0.2.207`; run suite-78

Gate: `T-S78-006` **PASS** and `T-S78-001..005` stay green (regression — same suite, shared fixture/teardown). If red, fix before proceeding.

- [ ] [CP1a] Build registry-api `0.2.207` + `helm upgrade` + wait for rollout — `scripts/checkpoints/cp1a-deploy-registry-api.sh`
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  echo "=== Checkpoint 1a: build+deploy registry-api 0.2.207 ==="
  cd "$(git rev-parse --show-toplevel)"
  NS="${NAMESPACE:-agentshield-platform}"
  [ "$(kubectl config current-context)" = "docker-desktop" ] || { echo "FAIL: not docker-desktop context"; exit 1; }
  docker build -t registry.internal/agentshield/registry-api:0.2.207 services/registry-api/
  helm upgrade --install agentshield charts/agentshield -n "$NS" --reset-values --force-conflicts --timeout 20m
  kubectl -n "$NS" rollout status deploy/agentshield-registry-api --timeout=300s
  # Assert the live pod is actually serving 0.2.207
  IMG=$(kubectl -n "$NS" get deploy/agentshield-registry-api -o jsonpath='{.spec.template.spec.containers[?(@.name=="registry-api")].image}')
  echo "$IMG" | grep -q '0.2.207' || { echo "FAIL: deployed image is $IMG, expected :0.2.207"; exit 1; }
  echo "PASS"
  ```

- [ ] [CP1b] Run suite-78; assert `T-S78-006` PASS + 001–005 green (in-pod token+curl+jq read-back of the seeded `workflow_run` row) — `scripts/checkpoints/cp1b-smoke-suite78.sh`
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  echo "=== Checkpoint 1b: suite-78 (GET /workflows/{id}/memory read-back) ==="
  cd "$(git rev-parse --show-toplevel)"
  # suite-78 execs into the registry-api pod, mints a real Keycloak token, curls the
  # endpoint and asserts the JSON fields (agent_name/thread_id/scope/message_index/[]).
  OUT=$(bash scripts/e2e/suite-78-conversations.sh 2>&1) || { echo "$OUT"; echo "FAIL: suite-78 exited non-zero"; exit 1; }
  echo "$OUT"
  echo "$OUT" | grep -q 'T-S78-006.*PASS' || { echo "FAIL: T-S78-006 not PASS"; exit 1; }
  echo "$OUT" | grep -qE '0 failed|all green'   || { echo "FAIL: suite-78 reported failures"; exit 1; }
  for id in T-S78-001 T-S78-002 T-S78-003 T-S78-004 T-S78-005; do
    echo "$OUT" | grep -q "$id.*FAIL" && { echo "FAIL: regression in $id"; exit 1; } || true
  done
  echo "PASS"
  ```

---

## Phase 2 — Reproduce G1 + G2 frontend (RED)

- [ ] [T3] [R] [P] G1 reproduce (FAILS today): `WorkflowChatPage.test.tsx` — `studio/src/pages/WorkflowChatPage.test.tsx` *(new)*
  - `vi.mock('../api/registryApi')` providing `getCompositeWorkflow`, `getWorkflowRunTree`, `workflowRunStreamUrl`, `listWorkflowMemory`; mock keycloak; `MockEventSource`/`scrollIntoView` shims (copy `AgentChatPage.test.tsx:36-52`). Route `/workflows/:id/chat`.
  - **Test "rehydrates prior member turns when opened with `?session=<tid>`":** mock `listWorkflowMemory` → `[user "summarize the Q3 report", assistant(author "summarizer") "Here is the summary."]`; render `/workflows/wf-1/chat?session=thread-42`; assert `listWorkflowMemory` called with `("wf-1", objectContaining({thread_id:"thread-42"}))`, both bubbles render, and the empty state `Send a message to run this workflow.` is **NOT** present.
  - **Dependencies:** none (API mocked). **[P]** with T4.
  - **Verify (RED):** `cd studio && npx vitest run src/pages/WorkflowChatPage.test.tsx` → FAIL (`listWorkflowMemory` never called; empty state shown).

- [ ] [T4] [R] [P] G2 reproduce + save→reload guard (FAILS today): `WorkflowMemoryTab.test.tsx` — `studio/src/components/agent-detail/WorkflowMemoryTab.test.tsx` *(new)*
  - `vi.mock('../../api/registryApi')` with `listWorkflowMemory` (+ `listMemory` as a spy to assert-NOT-called); `renderWithProviders`. Mirror `WorkflowConversationsTab.test.tsx`.
  - **Assert:** mock `listWorkflowMemory` → one entry `{agent_name:"summarizer", thread_id:"wf-t1", role:"assistant", content:"Here is the Q3 summary.", …}`; the content renders; `listWorkflowMemory` called with `("wf-1", …)`; `listMemory` **NOT** called (the bug queried the per-agent list).
  - **Dependencies:** none. **[P]** with T3.
  - **Verify (RED):** `cd studio && npx vitest run src/components/agent-detail/WorkflowMemoryTab.test.tsx` → FAIL (component file/import absent — the failing observation for the empty tab).

---

## Checkpoint 2 — Reproduce-first gate (confirm BOTH RED)

Gate: both Vitests **FAIL** (G1 empty-state / no fetch; G2 missing component). This is the recorded reproduce evidence for CLAUDE.md rule 7 — no fix lands before this gate is red.

- [ ] [CP2a] Run both reproduce Vitests; assert BOTH FAIL (RED gate) — `scripts/checkpoints/cp2a-reproduce-red.sh`
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  echo "=== Checkpoint 2: reproduce-first RED gate (G1 + G2 frontend) ==="
  cd "$(git rev-parse --show-toplevel)/studio"
  # Each MUST fail pre-fix. `! ...` inverts: script passes only when the test fails.
  ! npx vitest run src/pages/WorkflowChatPage.test.tsx                    > /tmp/wlg-cp2-g1.log 2>&1 \
    && { echo "G1 reproduce is RED (expected)"; } \
    || { echo "FAIL: WorkflowChatPage.test.tsx unexpectedly GREEN — reproduce is not reproducing"; cat /tmp/wlg-cp2-g1.log; exit 1; }
  ! npx vitest run src/components/agent-detail/WorkflowMemoryTab.test.tsx > /tmp/wlg-cp2-g2.log 2>&1 \
    && { echo "G2 reproduce is RED (expected)"; } \
    || { echo "FAIL: WorkflowMemoryTab.test.tsx unexpectedly GREEN — reproduce is not reproducing"; cat /tmp/wlg-cp2-g2.log; exit 1; }
  echo "PASS"
  ```

---

## Phase 3 — Fix frontend (client → G1 → G2)

- [ ] [T5] [F] API client `listWorkflowMemory` — `studio/src/api/registryApi.ts`
  - Add `export const listWorkflowMemory = async (workflowId, params?: { thread_id?; limit?; offset? }): Promise<MemoryMessage[]>` → `GET /workflows/${workflowId}/memory` with `{ params }` passthrough; place next to `listWorkflowConversations` (L1685-1694). Reuse the existing `MemoryMessage` type (L1594-1606) — no new type.
  - **Dependencies:** T3, T4 (their mocks reference it). Blocks T6, T7.
  - **Verify:** `cd studio && npm run typecheck`. `grep -rn "listWorkflowMemory" studio/src` (added here; wired by T6/T7).

- [ ] [T6] [F] [P] G1 fix — WorkflowChatPage rehydration (`seedFromThread` + mount effect) — `studio/src/pages/WorkflowChatPage.tsx`
  - Import `useCallback` (extend L1) + `listWorkflowMemory` (extend L5-11 import). Add `seedFromThread(workflowId, threadId)` = `await listWorkflowMemory(workflowId, { thread_id: threadId, limit: 200 })` → `setSessionId(threadId)` → `setMessages(rows.filter(role∈{user,assistant}).map(r => ({ role, content, author: r.role==="assistant" ? r.agent_name : undefined })))`. Add a mount `useEffect` mirroring `AgentChatPage.tsx:146-153` (seed once from `?session` when `id` present; `// eslint-disable-next-line react-hooks/exhaustive-deps`). Exact body in plan **Key Interfaces**.
  - Preserve the New-conversation reset (L73-78) — it must still clear + re-key the session.
  - **Dependencies:** T5. **[P]** with T7 (different file).
  - **Verify:** `cd studio && npx vitest run src/pages/WorkflowChatPage.test.tsx` → PASS (T3 flips green). `grep -n "seedFromThread\|listWorkflowMemory" studio/src/pages/WorkflowChatPage.tsx`.

- [ ] [T7] [F] [P] G2 fix — new `WorkflowMemoryTab` + deployment-page wiring — `studio/src/components/agent-detail/WorkflowMemoryTab.tsx` *(new)*, `studio/src/pages/WorkflowDeploymentOverviewPage.tsx`
  - **`WorkflowMemoryTab.tsx`:** `WorkflowMemoryTab({ workflowId, deploymentId }: { workflowId: string; deploymentId: string })`; `useQuery(["workflow-memory", workflowId, selectedThread], () => listWorkflowMemory(workflowId, { thread_id: selectedThread ?? undefined, limit: 100 }))`; read-only render (role, author = `agent_name`, content, created_at) with thread chips (selecting a thread re-queries `{thread_id}`). Mirror `MemoryTab.tsx:40-124` **minus** Clear/Delete (member rows aren't agent-owned). Precedent: `WorkflowConversationsTab.tsx`.
  - **`WorkflowDeploymentOverviewPage.tsx`:** remove `import MemoryTab` (L19); replace `<MemoryTab agentName={workflow?.name ?? id!} deploymentId={depId} />` (L198) with `{activeTab === "memory" && <WorkflowMemoryTab workflowId={id!} deploymentId={depId!} />}`; import `WorkflowMemoryTab`. No unused import left (typecheck clean).
  - **Dependencies:** T5. **[P]** with T6 (different files).
  - **Verify:** `cd studio && npx vitest run src/components/agent-detail/WorkflowMemoryTab.test.tsx` → PASS (T4 flips green). `grep -rn "WorkflowMemoryTab" studio/src` (component ← page).

---

## Checkpoint 3 — Typecheck + full Vitest green

Gate: `tsc` clean; ALL Vitest green including the two new files + the untouched `AgentChatPage.test.tsx` / `WorkflowConversationsTab.test.tsx` (regression — shared `ConversationSidebar` / registryApi surface).

- [ ] [CP3a] `npm run typecheck` + full `npm run test` (regression sweep of the Vitest suite) — `scripts/checkpoints/cp3a-typecheck-vitest.sh`
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  echo "=== Checkpoint 3: typecheck + full Vitest ==="
  cd "$(git rev-parse --show-toplevel)/studio"
  npm run typecheck
  npm run test
  # Re-assert the two target specs are individually green (belt + suspenders)
  npx vitest run src/pages/WorkflowChatPage.test.tsx src/components/agent-detail/WorkflowMemoryTab.test.tsx
  echo "PASS"
  ```

---

## Phase 4 — Playwright journeys + docs + studio tag bump

- [ ] [T8] [F] [P] Playwright end-user journeys: `workflow-memory.spec.ts` — `studio/e2e/workflow-memory.spec.ts` *(new)*
  - Mirror `workflow-conversations.spec.ts` (real Keycloak via `global-setup`, `ADMIN` X-User headers for API prep, `PLAYWRIGHT_BASE_URL`). Find a workflow deployment; prefer one with memory entries for the browser user.
  - **(a) Memory tab:** `page.goto(/workflows/{id}/d/{depId})` → click **memory** tab → `waitForResponse` `GET /api/v1/workflows/{id}/memory` (200) → if rows, assert an entry renders.
  - **(b) Chat replay:** click **conversations** tab → click a row → URL `…/chat?session=<thread>` → `waitForResponse` `GET /api/v1/workflows/{id}/memory?thread_id=…` → assert a prior member bubble is visible in `[data-testid="workflow-chat-transcript"]`.
  - Warm-fixture **annotate-skip** when the browser user has no runs (the network guards always run).
  - **Dependencies:** T6, T7 (runs against deployed studio at CP4). **[P]** with T9a, T9b (different files).
  - **Verify:** executed at CP4b (`bash scripts/studio-e2e.sh e2e/workflow-memory.spec.ts`).

- [ ] [T9a] [F] [P] Bug postmortem + gap ledger flip (G1/G2 shipped, G3 deferred, debugging-log skip recorded) — `docs/bugs/workflow-ledger-rehydrate-and-memory-tab.md` *(new)*, `docs/testing/manual-ui-e2e-test-plan.md`
  - **Bug doc (rule 8):** one-line title; **Found/Fixed** (2026-07-18, registry-api `0.2.207` + studio `0.1.156`); **Symptom** (G1 empty replay; G2 empty tab); **Root cause** (member-name/NULL-user; `WorkflowChatPage` never seeds; `MemoryTab` keyed by `workflow.name` → `list_recent` matches nothing); **Fix** (workflow memory read via parent-run semi-join + `seedFromThread` + `WorkflowMemoryTab`) — the class-fix, cross-linking `T-S78-006`, `WorkflowChatPage.test.tsx`, `WorkflowMemoryTab.test.tsx`, `workflow-memory.spec.ts`.
  - **Gap ledger:** flip the G1+G2 *not-yet-wired debt* entries (L43-50) to a **shipped** section (mirror the existing Conversations-tab section at L15+); keep **G3** (L51-54) as **deferred (intentional)**, reworded to reference this plan + the `workflow_deployment_id = NULL` reason; record the **"no numbered `docs/debugging/NNN` log — known class (mirror of the shipped Conversations fix), rule 8 requires the numbered log only when the diagnosis was non-obvious"** decision.
  - **Dependencies:** T6, T7. **[P]** with T8, T9b.
  - **Verify:** bug doc has all required sections; gap ledger shows G1/G2 shipped + G3 deferred + the debugging-log-skip note.

- [ ] [T9b] [F] [P] Studio tag bump `0.1.155`→**`0.1.156`** (both files) — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
  - **`deploy-cpe2e.sh`:** `STUDIO_TAG` `0.1.155`→`0.1.156` (**L325** — note the plan text said L320; the real anchor is L325) + append a comment header for G1/G2 workflow ledger wiring.
  - **`values.yaml`:** studio `tag: "0.1.155"`→`"0.1.156"` (**L954**) + a comment line (mirror the L949-954 style).
  - **Do NOT double-bump** — one studio `0.1.156` build is shared with HITL task T003 + EditAgentModal #16 (see Execution Notes). The physical edit is `0.1.155`→`0.1.156` because #16 already advanced the file to `0.1.155`.
  - **Dependencies:** T6, T7. **[P]** with T8, T9a.
  - **Verify:** `grep -n "0.1.156" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` shows both bumped; no stray `0.1.155` left as the active tag in either file.

---

## Checkpoint 4 — Build+deploy studio `0.1.156`; end-user verification + regression sweep

Gate: all green; **both end-user journeys visibly correct** (Memory tab lists entries; a past session replays prior member bubbles, not the empty composer). The studio deploy is **shared/coordinated** — the main session sequences the WorkflowChatPage edits with HITL T003 and builds studio `0.1.156` ONCE.

- [ ] [CP4a] Build studio `0.1.156` + `helm upgrade` + wait for rollout (assert live image) — `scripts/checkpoints/cp4a-deploy-studio.sh`
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  echo "=== Checkpoint 4a: build+deploy studio 0.1.156 (shared build) ==="
  cd "$(git rev-parse --show-toplevel)"
  NS="${NAMESPACE:-agentshield-platform}"
  [ "$(kubectl config current-context)" = "docker-desktop" ] || { echo "FAIL: not docker-desktop context"; exit 1; }
  docker build -t registry.internal/agentshield/studio:0.1.156 studio/
  helm upgrade --install agentshield charts/agentshield -n "$NS" --reset-values --force-conflicts --timeout 20m
  kubectl -n "$NS" rollout status deploy/agentshield-studio --timeout=300s
  IMG=$(kubectl -n "$NS" get deploy/agentshield-studio -o jsonpath='{.spec.template.spec.containers[0].image}')
  echo "$IMG" | grep -q '0.1.156' || { echo "FAIL: deployed studio image is $IMG, expected :0.1.156"; exit 1; }
  echo "PASS"
  ```

- [ ] [CP4b] Playwright: `workflow-memory.spec.ts` (this plan) + `workflow-conversations.spec.ts` (regression — shared route/sidebar) — `scripts/checkpoints/cp4b-playwright.sh`
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  echo "=== Checkpoint 4b: Playwright end-user journeys ==="
  cd "$(git rev-parse --show-toplevel)"
  bash scripts/studio-e2e.sh e2e/workflow-memory.spec.ts        # G1 replay + G2 tab (asserts GET /workflows/{id}/memory via waitForResponse)
  bash scripts/studio-e2e.sh e2e/workflow-conversations.spec.ts # regression (shared route/sidebar)
  echo "PASS"
  # MANUAL (not scripted) — Claude-in-Chrome exploratory, real user (quickstart §5):
  #   1. Log in to Studio (platform-admin / PlatformAdmin2024).
  #   2. Open a reactive workflow deployment → Memory tab → confirm it LISTS member entries (was empty).
  #   3. Conversations tab → click a past session → confirm WorkflowChat REPLAYS prior member bubbles
  #      (not the empty "Send a message to run this workflow." composer).
  ```

- [ ] [CP4c] Regression sweep — suite-78 + typecheck + full Vitest (blast radius: `agent_memory` read path, `ConversationStore` port, deployment overview page, `ConversationSidebar` route) — `scripts/checkpoints/cp4c-regression.sh`
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  echo "=== Checkpoint 4c: regression sweep ==="
  cd "$(git rev-parse --show-toplevel)"
  OUT=$(bash scripts/e2e/suite-78-conversations.sh 2>&1) || { echo "$OUT"; echo "FAIL: suite-78 non-zero"; exit 1; }
  echo "$OUT" | grep -qE '0 failed|all green' || { echo "$OUT"; echo "FAIL: suite-78 reported failures"; exit 1; }
  cd studio && npm run typecheck && npm run test
  echo "PASS"
  ```

---

## Complexity Tracking

| Concern | Decision | Rationale |
|---|---|---|
| One endpoint (dual `thread_id`) vs two | **One** `GET /workflows/{id}/memory` | Copies the proven per-agent `GET /agents/{name}/memory` dual contract (`routers/memory.py:120-185`); G1 (thread) + G2 (list) are the same read at two scopes — one ownership rule. |
| ORM select vs text SQL | **ORM select** (unlike `_LIST_WORKFLOW_CONVERSATIONS_SQL` text) | No `GROUP BY` (entries, not summaries) → returning ORM rows keeps `AgentMemoryResponse.model_validate` lossless like `list_recent`; dual ordering is a one-line ORM branch. Ownership semi-join still mirrors the Conversations SQL. |
| New `WorkflowMemoryTab` vs mode-param on `MemoryTab` | **New component** | `MemoryTab` owns agent-scoped Clear-All/Delete mutations that don't apply to workflow member rows; splitting avoids a type-sniff conditional (CLAUDE.md "no bandaid") and matches `WorkflowConversationsTab` precedent. |
| G3 per-deployment scoping | **Deferred (intentional) — NO impl task** | Playground/builder runs write `workflow_deployment_id=NULL`; real per-deployment scoping first needs deployed runs to populate it (a deployed-runtime change, out of scope). Recorded in the gap ledger (T9a), not built. |

---

## Execution Notes

- **Shared studio build (coordination) — studio `0.1.156`.** studio `0.1.155` is ALREADY built/deployed for the unrelated EditAgentModal `knowledge_search` class-fix (task #16), so the current file value is `0.1.155` and this plan's physical bump is `0.1.155`→`0.1.156` (T9b). **`WorkflowChatPage.tsx` (T6) is ALSO edited by the pending HITL task T003** (re-surface the 2nd inline approval gate). The MAIN session sequences those `WorkflowChatPage` edits together and does **ONE** studio `0.1.156` build at CP4 — the CP4a deploy is shared/coordinated. **Do NOT double-bump the studio tag.**
- **Reproduce-first is enforced by ordering:** T1 (backend RED) → CP1 (green after T2a/T2b); T3+T4 (frontend RED) → **CP2 confirms BOTH RED** → fixes T5-T7 → CP3 green. Never write a fix before its failing test/observation exists.
- **Tag bumps travel with their service change:** registry-api tag in T2b, studio tag in T9b — both mirrored into `deploy-cpe2e.sh` AND `values.yaml` (helm uses baked values, no `--set`). Real anchors: `deploy-cpe2e.sh` REGISTRY_API_TAG **L295**, STUDIO_TAG **L325**; `values.yaml` registry **L623**, studio **L954**.
- **No migration.** `alembic` is untouched; the read reuses `agent_memory` + `agent_runs`.
- **Debugging log intentionally skipped.** The G2 diagnosis is a known class (mirror of the shipped Conversations fix; the brief settles root cause) — no multi-layer dead-ends — so the numbered `docs/debugging/NNN` (next free `013`) is NOT written. The postmortem `docs/bugs/workflow-ledger-rehydrate-and-memory-tab.md` (T9a) + the gap-ledger note cover rule 8, which requires the numbered log only when the diagnosis was non-obvious.
- **Experience docs:** `WorkflowChatPage` / `WorkflowDeploymentOverviewPage` are NOT in CLAUDE.md's `docs/experience/playground.md` trigger list; the canonical record for this workflow surface is `docs/testing/manual-ui-e2e-test-plan.md` (updated in T9a). No `playground.md` change required.
- **Node is on this host** (`/opt/homebrew/bin`): Vitest (`cd studio && npx vitest run <file>`) and Playwright (`bash scripts/studio-e2e.sh <spec>`) run locally. Deploy is local docker-desktop (`docker build … → helm upgrade --install agentshield charts/agentshield -n agentshield-platform --reset-values --force-conflicts --timeout 20m`). The main session runs the actual build/deploy/suites.

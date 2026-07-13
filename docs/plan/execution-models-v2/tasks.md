# WS-0 Tasks — Foundation: `agent_class` authoring + shape-aware dispatch

**Source plan:** `docs/plan/execution-models-v2/plan.md` (+ `research.md`, `data-model.md`,
`contracts/create-patch-api.md`, `contracts/shared-dispatch-helper.md`, `quickstart.md`).
**Scope:** WS-0 ONLY. **This is the executable task list — run top-to-bottom, respecting the dependency graph.**

## Conventions
- **`[P]`** = parallelizable with other `[P]` tasks in the same phase (different files, no shared state).
- Task IDs map 1:1 to plan §7 (`T1`…`T9`); some are split into `Ta/Tb` where independent files allow parallelism.
- Every task ends with a **Verify** command that must pass before checking the box.
- **Do NOT deploy with bare `helm upgrade`** — only `scripts/deploy-cpe2e.sh` (T9).

> ⚠️ **PREFLIGHT — migration number.** The plan says `0058` / `down_revision="0057"` (head was `0057` on
> 2026-07-12; the doc's `0055`/`0056` is stale). **Confirm before writing the migration:**
> `ls services/registry-api/alembic/versions/ | sort | tail -3` — if head advanced past `0057`, use head+1
> and set `down_revision` to the real head. Everything downstream references `0058`; update consistently if it moved.

---

## Phase A — Authoring vertical (UI → API → DB → read back). Prove save→reload BEFORE Phase B.

### [ ] T1 — Migration `0058` + ORM `agent_class` NOT NULL on both executables
- **Files:** `services/registry-api/alembic/versions/0058_agent_class_not_null_and_workflows_agent_class.py` (**C**);
  `services/registry-api/models.py` (M).
- **Do:** migration `revision="0058"`, `down_revision="0057"`, upgrade/downgrade exactly per `data-model.md`
  (backfill NULL `agents.agent_class`→`user_delegated`, NOT NULL, CHECK; add `workflows.agent_class` NOT NULL +
  CHECK). `Agent.agent_class` → NOT NULL + `server_default 'user_delegated'` + CHECK; new
  `CompositeWorkflow.agent_class` same; reword `models.py:155` reactive/durable comment (R1).
- **Accept:** `alembic upgrade head` applies on a DB with pre-existing NULL rows (they backfill); both CHECKs
  exist; INSERT omitting `agent_class` → `user_delegated`; a bogus value → CHECK violation; up→down→up idempotent.
- **Deps:** none (do PREFLIGHT first).
- **Verify:** `cd services/registry-api && python3 -c "import ast; ast.parse(open('alembic/versions/0058_agent_class_not_null_and_workflows_agent_class.py').read())" && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`

### [ ] T2 — Schemas + routers: `agent_class` on create/update/response (agents + workflows), wire the orphan
- **Files:** `services/registry-api/schemas.py` (M); `services/registry-api/routers/agents.py` (M);
  `services/registry-api/routers/composite_workflows.py` (M).
- **Do:** exactly per `contracts/create-patch-api.md` — `AgentCreate.agent_class` defaulted-required;
  **`update_agent` applies `body.agent_class`** (this is the existing orphan — `agents.py:90` sets it on create,
  PATCH never did); `AgentResponse.agent_class: str`; `CompositeWorkflowCreate/Update.agent_class`;
  `CompositeWorkflowResponse.agent_class` + `warnings`; `create_workflow` passes it; `_to_response` carries both;
  add `compute_reactive_approval_warnings` (populates `warnings` in get/update — static high-risk-tool scan, S2 save-time).
- **Accept:** create agent w/o class → 201 + `user_delegated`; create w/ `"bogus"` → 422; PATCH `{agent_class:"daemon"}`
  then GET → `"daemon"` (orphan fixed); create+PATCH workflow class persists; reactive workflow w/ high-risk member → `warnings` non-empty.
- **Deps:** T1.
- **Verify:** `cd services/registry-api && for f in schemas.py routers/agents.py routers/composite_workflows.py; do python3 -c "import ast; ast.parse(open('$f').read())"; done && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`

### [ ] T3 — Deploy reads the column directly — remove the coalesce (M3, closes Slice A backend)
- **Files:** `services/deploy-controller/manifest_builder.py` (M).
- **Do:** `:128` → `agent_class = agent["agent_class"]` (direct index; no `.get`, no `or "user_delegated"`). The
  NOT NULL column (T1) makes the coalesce dead — delete it (No-Bandaid: illegal state now unrepresentable).
- **Accept:** a deployed agent's pod env `AGENTSHIELD_AGENT_CLASS` + label `agentshield.io/agent-class` equal the
  DB value verbatim; a daemon agent deploys as `daemon` (previously always coalesced to `user_delegated`).
- **Deps:** T1, T2.
- **Verify:** `python3 -c "import ast; ast.parse(open('services/deploy-controller/manifest_builder.py').read())" && ! grep -n 'or "user_delegated"' services/deploy-controller/manifest_builder.py`

### [ ] T6a [P] — Studio API client: `agent_class` on create/update/workflow bodies
- **Files:** `studio/src/api/registryApi.ts` (M).
- **Do:** add `agent_class` to `createAgent`/`updateAgent` bodies + `CreateCompositeWorkflowRequest`; add
  `agent_class` + `warnings` to the `CompositeWorkflow` type (types per `contracts/create-patch-api.md`).
- **Accept:** `npm run typecheck` clean; the three request shapes carry `agent_class`.
- **Deps:** T2 (backend contract). **Parallel with T6b/T6c** (different files).
- **Verify:** `cd studio && npm run typecheck && grep -n "agent_class" src/api/registryApi.ts`

### [ ] T6b — Studio wizard: split the 4-way picker into Shape · Trigger · Class (R1)
- **Files:** `studio/src/pages/CreateAgentPage.tsx` (M).
- **Do:** replace the 4-way `AgentTypePicker` with **three independent selectors** — Shape (`reactive|durable`),
  Trigger (`manual/api · schedule · webhook`, reusing existing ScheduleFields/FilterConditionsEditor), Class
  (`user_delegated|daemon`) pre-defaulted `schedule||webhook ? "daemon" : "user_delegated"` (user-overridable);
  rework `createAgentOfType` to send `execution_shape` + `agent_class` + triggers.
- **Accept:** wizard shows three selectors; picking Scheduled defaults Class→daemon (overridable); create posts both fields.
- **Deps:** T6a.
- **Verify:** `cd studio && npm run typecheck && grep -n "agent_class\|execution_shape" src/pages/CreateAgentPage.tsx`

### [ ] T6c [P] — Studio Settings + Workflow Save-modal Class selectors + spec reword
- **Files:** `studio/src/pages/AgentDetailPage.tsx` (M); `studio/src/pages/WorkflowBuilderPage.tsx` (M);
  `docs/spec.md` (M).
- **Do:** `AgentDetailPage.SettingsContent` — Class `<select>` beside Execution Shape, included in the
  `updateAgent` call. `WorkflowBuilderPage` — `saveClass` state + Class `<select>` in the Save modal; send
  `agent_class` on create, PATCH on re-save; `toast.warning` for each `wf.warnings`. `docs/spec.md` — reword
  reactive/durable off "single-shot" (R1) + note both `agent_class` columns NOT NULL.
- **Accept:** Settings + Save-modal edits send `agent_class`; typecheck clean; spec reworded.
- **Deps:** T6a. **Parallel with T6b** (different files).
- **Verify:** `cd studio && npm run typecheck && grep -n "agent_class" src/pages/AgentDetailPage.tsx src/pages/WorkflowBuilderPage.tsx`

### [ ] T7a [P] — Vitest: authoring surfaces
- **Files:** `studio/src/pages/CreateAgentPage.test.tsx` (M); `studio/src/pages/AgentDetailPage.test.tsx` (M);
  `studio/src/pages/WorkflowBuilderPage.test.tsx` (**C**).
- **Do:** mock `../api/registryApi`, render via `renderWithProviders`; assert three selectors render, create
  posts `execution_shape`+`agent_class`, class defaults from trigger, Settings PATCH includes `agent_class`,
  Save-modal class → create payload, reactive+high-risk (mocked `warnings`) → warning toast.
- **Accept:** `npm run test` green.
- **Deps:** T6b, T6c. **Parallel with T7b** (Vitest vs Playwright).
- **Verify:** `cd studio && npm run test`

### [ ] T7b [P] — Playwright: authoring persistence (save→reload→assert)
- **Files:** `studio/e2e/create-agent-wizard.spec.ts` (M); `studio/e2e/agent-detail-modes.spec.ts` (M);
  `studio/e2e/workflow-builder.spec.ts` (M).
- **Do:** drive the real browser, `waitForResponse` on the create/PATCH, reload, assert `agent_class` persisted
  (wizard 3-selector create; Settings class edit; Save-modal class). **Fail — not skip — if the backend fixture
  is unreachable** (retro gate #2).
- **Accept:** `bash scripts/studio-e2e.sh` green for the three specs.
- **Deps:** T6b, T6c. **Parallel with T7a.**
- **Verify:** `bash scripts/studio-e2e.sh`

---

## Phase B — Dispatch vertical (trigger → dispatch → `/run` vs `/chat` → run_steps). Start AFTER Phase A proves save→reload.

### [ ] T4 — Shared durable-dispatch helper + shape-aware production dispatch + production step-update (parity core)
- **Files:** `services/registry-api/durable_dispatch.py` (**C**); `services/registry-api/routers/playground.py` (M);
  `services/registry-api/routers/internal.py` (M).
- **Do:** `durable_dispatch.dispatch_durable_run(...)` exactly per `contracts/shared-dispatch-helper.md` — the
  **single** `/run` POST. Refactor `playground._dispatch_durable_run` into a thin wrapper (behavior-neutral).
  `internal._dispatch_and_complete(..., execution_shape, input_payload, ...)` branches durable→shared helper
  (callback `/internal/runs/{id}/step-update`), reactive→existing `/chat`; `start_internal_run` passes
  `agent.execution_shape` + payload; new `POST /api/v1/internal/runs/{run_id}/step-update` writes
  `RunStep`(AgentRun) + completes the run. **Fail-closed:** a runner dispatch failure marks the AgentRun `failed`
  + fires the alert — never hangs.
- **Accept:** durable agent internal run → hits `/run`, gets `RunStep` rows; reactive → `/chat`, gets `output`,
  no `RunStep`; dispatch failure → run `failed` + alert; playground durable runs behave exactly as before.
- **Deps:** T2 (logically). **Parity gate:** the `/run` POST literal lives ONLY in `durable_dispatch.py`.
- **Verify:** `cd services/registry-api && for f in durable_dispatch.py routers/playground.py routers/internal.py; do python3 -c "import ast; ast.parse(open('$f').read())"; done && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')" && grep -rn "dispatch_durable_run" routers/playground.py routers/internal.py && ! grep -rn '"/run"' routers/playground.py routers/internal.py`

### [ ] T5 — Reactive workflow = awaited+capped (M6) + approval-gate fail-closed + save-time warn (S2)
- **Files:** `services/registry-api/routers/internal.py` (M, `_start_workflow_run`);
  `services/registry-api/workflow_orchestrator.py` (M). (`compute_reactive_approval_warnings` already added in T2.)
- **Do:** `_start_workflow_run` branches on `wf.execution_shape` — reactive →
  `await asyncio.wait_for(orchestrate(..., shape="reactive"), timeout=WORKFLOW_REACTIVE_TIMEOUT_S)` (default 120s),
  timeout → `_fail_parent`, return the refreshed run; durable → existing background path with `shape="durable"`.
  Thread `shape` through `orchestrate(..., mode, shape="durable")` + `_run_sequential_from`; new
  `_park_or_fail(parent_run_id, mode, team, workflow_id, shape)` — parks (durable) / **fails-closed** (reactive,
  `error_message` ~ "set shape=durable"). Explicit `shape` param everywhere — no `getattr`/priority fallthrough.
- **Accept:** reactive workflow returns final output synchronously (one response, no `orchestrator_state`
  checkpoint row); reactive workflow whose member trips an approval gate → run `failed`, caller not blocked;
  durable workflow unchanged (parks as today).
- **Deps:** T2 (warnings field), T4 (internal.py already open — keep edits coherent).
- **Verify:** `cd services/registry-api && for f in routers/internal.py workflow_orchestrator.py; do python3 -c "import ast; ast.parse(open('$f').read())"; done && python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')" && grep -n '_park_or_fail\|shape="reactive"\|shape="durable"' workflow_orchestrator.py routers/internal.py`

---

## Phase C — Golden-path e2e + parity assertion

### [ ] T8 — Backend e2e suite-54 + register (Slice A+B golden path, parity grep)
- **Files:** `scripts/e2e/suite-54-agent-class-shape-dispatch.sh` (**C**); `scripts/e2e/run-all.sh` (M).
- **Do:** `kubectl exec` into registry-api, `python3`/`httpx` against `http://localhost:8000`; IDs
  `T-S54-001..010`; `set -euo pipefail`; **`exit 1` (fail, not skip) on any missing fixture**; cleanup trap.
  Register after suite-53.
- **Cases (real doors):** 001 create w/o class→`user_delegated`; 002 bogus→422; 003 PATCH daemon→GET daemon
  (orphan); 004 create+reload workflow daemon→persisted; 005 deploy daemon agent→pod env `daemon`; 006 durable
  internal run→`run_steps` exist; 007 reactive internal run→no `run_steps`, has `output`; 008 reactive workflow→
  synchronous output, no `orchestrator_state`; 009 reactive workflow approval-gate→failed w/ message; **010
  parity grep**: single `/run` POST, `dispatch_durable_run` called from both routers, zero raw `/run` POST in routers.
- **Accept:** all ten pass on the deployed cluster.
- **Deps:** T2, T3, T4, T5 (backend deployed).
- **Verify:** `bash scripts/e2e/suite-54-agent-class-shape-dispatch.sh && grep -n "suite-54" scripts/e2e/run-all.sh`

---

## Phase D — Ship

### [ ] T9 — Image-tag bumps (BOTH files) + deploy + gap-ledger note
- **Files:** `scripts/deploy-cpe2e.sh` (M); `charts/agentshield/values.yaml` (M);
  `docs/testing/manual-ui-e2e-test-plan.md` (M).
- **Do:** `REGISTRY_API_TAG 0.2.155→0.2.156`, `DEPLOY_CONTROLLER_TAG 0.1.35→0.1.36`, `STUDIO_TAG 0.1.126→0.1.127`
  in `deploy-cpe2e.sh` **and** the mirrored `tag:` in `values.yaml` (registry-api ~`:588`, deploy-controller
  ~`:650`, studio ~`:899`). **`DECLARATIVE_RUNNER_TAG` UNCHANGED** (WS-0 does not touch it). Header note in
  `deploy-cpe2e.sh`. WS-0 gap-ledger note in the manual plan (deferred pieces from plan §8).
  > ⚠️ Confirm current tags before bumping (`grep -nE "REGISTRY_API_TAG|DEPLOY_CONTROLLER_TAG|STUDIO_TAG" scripts/deploy-cpe2e.sh`); the `0.2.155/0.1.35/0.1.126` baselines are the plan's snapshot — increment from the **actual** current values.
- **Accept:** `bash scripts/deploy-cpe2e.sh` builds+deploys; `kubectl rollout status` green for registry-api,
  deploy-controller, studio; suite-54 green against the cluster; Playwright green.
- **Deps:** T1–T8.
- **Verify:** `grep -n "0.2.156\|0.1.36\|0.1.127" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml` (each tag in both files).

---

## Dependency graph
```
PREFLIGHT (confirm head=0057)
   │
  T1 ──► T2 ──┬──► T3            (Slice A backend)
              ├──► T6a ─┬─► T6b ─┐
              │         └─► T6c ─┼─► T7a [P]
              │                  └─► T7b [P]   (Slice A UI + tests)
              ├──► T4 ───────────┐            (Slice B core)
              └──► T5 ───────────┤            (Slice B workflow)
                                 └─► T8 ──► T9 (e2e → ship)
```
- **Phase A before Phase B:** land T1–T3 + T6* and prove save→reload (T7b) BEFORE T4/T5 (plan §9: two verticals,
  prove A then B).
- **`[P]` sets:** {T6a then (T6b ∥ T6c)}; {T7a ∥ T7b}. T4 ∥ T5 backend-wise but both edit `internal.py` — do T4
  first, then T5, to avoid a merge in the same file.

## Parallel execution notes
- Frontend (T6*/T7*) and the T4/T5 dispatch backend are independent once T2 lands — a second worker could take
  the dispatch vertical while the first finishes the UI. Keep the parity grep (T8-010) as the merge gate.
- **Do not** parallelize anything that edits `services/registry-api/routers/internal.py` (T4 + T5) — sequence them.

## Definition-of-Done gate (from plan §4 — confirm before reporting WS-0 done)
- [ ] Real user journey proven: Playwright T7b drives wizard/Settings/Save-modal create → reload → class persisted.
- [ ] Save→reload→assert: T7b (UI) + suite-54 T-S54-003/004 (API re-GET).
- [ ] No orphan: `dispatch_durable_run`, `/step-update`, `_park_or_fail`, `compute_reactive_approval_warnings`,
      `update_agent.agent_class` each have a live caller shipped here (T8 greps).
- [ ] Parity: single `/run` POST (T8-010 grep); reactive fail-closed asserted (T8-009).
- [ ] Gap ledger updated (T9); image tags in BOTH files (T9); declarative-runner tag NOT bumped.

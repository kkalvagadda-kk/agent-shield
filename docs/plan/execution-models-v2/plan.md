# WS-0 Implementation Plan — Foundation: `agent_class` authoring + shape-aware dispatch

**Slice:** WS-0 of Execution Models v2 (spec `docs/design/todo/execution-models-v2-e2e.md` §5;
critique overlay `execution-models-v2-critique-and-fixes.md`). **This plan covers WS-0 ONLY.**
**Companion artifacts:** `research.md` (code-truth), `data-model.md`, `contracts/create-patch-api.md`,
`contracts/shared-dispatch-helper.md`, `quickstart.md`.

> **Read `research.md` first.** It carries three code-vs-doc corrections the cold implementer must
> honor — chief among them: **the migration is `0058`, not `0056`** (head is `0057`, not the doc's `0055`).

## 1. Goal

Make the execution cube **authorable and honest end-to-end** for the WS-0 foundation, so every later
slice (durable engine, daemon identity, scheduled/event) has a real `agent_class` and a shape-aware
dispatch to build on. Concretely, after WS-0:

1. **Class is un-droppable.** `agents.agent_class` **and** `workflows.agent_class` are `NOT NULL`
   (migration `0058`); the `manifest_builder.py:128` NULL-coalesce is **deleted** — a NULL class is
   structurally impossible, not a silent downgrade (M3).
2. **Class is authorable.** The create wizard's 4-way "Agent type" card is split into three independent
   selectors — **Shape** (reactive/durable) · **Trigger** (manual/api · schedule · webhook) · **Class**
   (user_delegated/daemon) — pre-defaulted from intent, user-overridable, sent on create + PATCH; a Class
   selector is added to Agent Settings and the workflow builder Save modal (R1 + agent_class authoring).
3. **Triggered dispatch honors `execution_shape`.** `internal.py` branches durable → runner `/run`
   (real `run_steps`), reactive → `/chat`; the durable-dispatch logic lives in **one shared helper**
   both `playground.py` and `internal.py` call — not mirrored (parity gate).
4. **Reactive workflow is a real synchronous, capped path** (M6); an approval gate in a reactive workflow
   **fails-closed at runtime** with a clear message, plus a best-effort save-time warning (S2).
5. **Spec taxonomy fixed** — reactive/durable reworded off "single-shot" (R1).

**Out of scope (later plans):** WS-1 durable engine (real per-node steps, `PostgresSaver` checkpoint,
HITL park emit, SDK `/run`), WS-2 daemon identity / `X-Run-Principal` headers / reviewer authority / OPA
`user_identity_ok`, WS-3 scheduled operate surface, WS-4 webhook client-id/HMAC, WS-5 Kaniko, WS-6 operate
surface. WS-0 wires the seams those slices extend; it does not build them.

## 2. Architecture

Three collapse points from the spec (§3); WS-0 fixes #1 and #2 and makes the workflow shape real:

```
Authoring vertical (Slice A)                     Dispatch vertical (Slice B)
  Wizard 3-selectors / Settings / WF Save modal    scheduler|event-gateway
      │ agent_class + execution_shape                    │ POST /internal/runs/start
      ▼ createAgent / updateAgent / createWorkflow       ▼ start_internal_run reads execution_shape
  registry-api routers (agents/composite_workflows)      ▼ _dispatch_and_complete(shape=…)
      ▼ agent_class column (NOT NULL, 0058)         durable ├─► durable_dispatch.dispatch_durable_run ──┐
  deploy-controller reads column directly (no coalesce)    │        (SHARED: playground.py also calls) │
      ▼ pod env AGENTSHIELD_AGENT_CLASS             reactive└─► POST /chat (unchanged)                 │
                                                          declarative-runner /run ──► /internal/runs/{id}/step-update
                                                                                        writes RunStep (AgentRun)
```

- **Parity mechanism:** `services/registry-api/durable_dispatch.py::dispatch_durable_run` is the single
  `/run` POST. Sandbox and production differ only by explicit params (callback URL + which run table marks
  failed) — the `sandbox-production-parity-architecture.md` "shared helper, variants pass params" pattern.
- **Reactive workflow:** `_start_workflow_run` awaits `orchestrate(..., shape="reactive")` under a
  wall-clock cap; the orchestrator's `_park_or_fail` fails-closed on an approval gate (reactive) or parks
  (durable) — one helper, explicit `shape` param, no priority fallthrough.
- **Illegal-state elimination:** `NOT NULL` + `DEFAULT` + `CHECK` on `agent_class` (both tables) makes the
  deploy coalesce deletable — the No-Bandaid "make illegal states unrepresentable" rule.

## 3. Tech Stack

Python 3.12 / FastAPI / SQLAlchemy async + Alembic (registry-api); Kubernetes client
(deploy-controller); httpx for in-cluster dispatch; React 18 + Vite + TailwindCSS + React Query +
react-hook-form + Zod (Studio); Vitest + React Testing Library + Playwright (Studio tests); bash + `kubectl
exec` + httpx (backend e2e). No new dependencies.

## 4. Constitution Check (CLAUDE.md — PASS/FAIL each)

| Gate | Verdict | How WS-0 satisfies it |
|---|---|---|
| **DoD #1 — real user journey, not an endpoint** | **PASS** | Playwright drives the wizard 3-selector create, the Settings class edit, and the workflow Save-modal class (`create-agent-wizard`, `agent-detail-modes`, `workflow-builder` specs) — the real browser door, `waitForResponse` on the create/PATCH network call. |
| **DoD #2 — save→reload→assert survived** | **PASS** | Playwright reloads the agent/workflow and asserts `agent_class` persisted; bash suite-54 creates via API, re-GETs, asserts `agent_class` + `execution_shape` from the DB. |
| **DoD #3 — no orphan code** | **PASS** | Every new symbol has a live caller shipped in the same task: `dispatch_durable_run` (called by playground+internal), `/internal/runs/{id}/step-update` (target of the durable branch's callback), `_park_or_fail` (called by all orchestrate modes), `compute_reactive_approval_warnings` (populates response.warnings → toast), the wired `update_agent.agent_class` (was the orphan). Task T8 greps each. |
| **DoD #4 — vertical slices** | **PASS** | Slice A (authoring): UI → API → DB → read-back. Slice B (dispatch): trigger → dispatch → `/run` vs `/chat` → `run_steps`. Each proven before the next capability. |
| **DoD #5 — honest gap ledger** | **PASS** | §"Complexity Tracking" + Execution Notes list every deferred piece tagged deferred(intentional) vs debt; the manual test plan header gets the WS-0 gap note (Task T9). |
| **DoD #6 — reason from running product** | **PASS** | `research.md` grounds every task in `file:line`; corrects the doc's stale `0055`/`0056` → `0058` and the two orphan/absent findings. |
| **No-Bandaid — fix the class** | **PASS** | `agent_class` illegal states made unrepresentable (NOT NULL+CHECK) rather than coalesced; shape threaded as an **explicit parameter** through dispatch + orchestrate (no `getattr`/type-sniffing/priority fallthrough); the deploy coalesce deleted, not guarded. |
| **Parity gate (parity doc + retro)** | **PASS** | Durable dispatch is one shared helper both paths import; suite-54 greps to prove **zero** divergent `/run` POST copy. |
| **Governance fail-loud + fail-closed (retro #4)** | **PASS** | Reactive-workflow approval gate → run **failed** (denied) with a clear message, never swallow-and-proceed; a dispatch error marks the run failed + alerts, never hangs. |
| **Post-Impl — bash e2e in run-all.sh** | **PASS** | `suite-54-agent-class-shape-dispatch.sh` registered in `run-all.sh` (Task T8). |
| **Post-Impl — image bumps in BOTH files** | **PASS** | registry-api 0.2.155→0.2.156, deploy-controller 0.1.35→0.1.36, studio 0.1.126→0.1.127 in `deploy-cpe2e.sh` **and** `values.yaml` (Task T9). declarative-runner unchanged. |
| **Post-Impl — Vitest + Playwright** | **PASS** | Vitest: CreateAgentPage, AgentDetailPage, new WorkflowBuilderPage; Playwright: three specs updated (Task T7). |
| **Post-Impl — alembic idempotent/guarded** | **PASS** | `0058` guarded (`IF NOT EXISTS`, `pg_constraint` guard), single transaction, data-preserving (`data-model.md`). |
| **Experience docs (`docs/experience/playground.md`)** | **PASS (not triggered)** | WS-0's `playground.py` change is a behavior-neutral refactor; no covered playground UX/SSE surface changes (`research.md` "Experience-doc trigger check"). No update owed — recorded, not skipped. |

No FAILs. No constitution deviation requiring justification.

## 5. File Structure (every file created/modified — one-line responsibility)

### Backend — registry-api
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/alembic/versions/0058_agent_class_not_null_and_workflows_agent_class.py` | **C** | Migration: backfill+NOT NULL+CHECK `agents.agent_class`; add `workflows.agent_class` NOT NULL+CHECK. `down_revision="0057"`. |
| `services/registry-api/models.py` | M | `Agent.agent_class` → NOT NULL + CHECK; new `CompositeWorkflow.agent_class` NOT NULL + CHECK; reword `:155` reactive/durable comment (R1). |
| `services/registry-api/schemas.py` | M | `AgentCreate.agent_class` defaulted-required; wire nothing else on create; `AgentResponse.agent_class: str`; add `agent_class` to `CompositeWorkflowCreate/Update`, and `agent_class`+`warnings` to `CompositeWorkflowResponse`. |
| `services/registry-api/routers/agents.py` | M | Wire the `update_agent` orphan: apply `body.agent_class`. |
| `services/registry-api/routers/composite_workflows.py` | M | Pass `agent_class` on create; `_to_response` carries `agent_class`+`warnings`; add `compute_reactive_approval_warnings`. |
| `services/registry-api/durable_dispatch.py` | **C** | Shared `dispatch_durable_run` — the single `/run` POST both paths call (parity core). |
| `services/registry-api/routers/playground.py` | M | Refactor `_dispatch_durable_run` into a thin wrapper over `dispatch_durable_run` (behavior-neutral). |
| `services/registry-api/routers/internal.py` | M | Shape-aware `_dispatch_and_complete`; new `POST /internal/runs/{id}/step-update`; reactive-workflow awaited+capped in `_start_workflow_run`; pass `shape` to `orchestrate`. |
| `services/registry-api/workflow_orchestrator.py` | M | Add `shape` param to `orchestrate` + modes; new `_park_or_fail` (durable park / reactive fail-closed); thread `shape` into `_run_sequential_from`. |

### Backend — deploy-controller
| File | C/M | Responsibility |
|---|---|---|
| `services/deploy-controller/manifest_builder.py` | M | Remove the `:128` NULL coalesce — `agent_class = agent["agent_class"]` (M3). |

### Frontend — Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/api/registryApi.ts` | M | Add `agent_class` to `createAgent`/`updateAgent` bodies + `CreateCompositeWorkflowRequest`; add `agent_class`+`warnings` to `CompositeWorkflow`. |
| `studio/src/pages/CreateAgentPage.tsx` | M | Replace the 4-way `AgentTypePicker` with three selectors (Shape/Trigger/Class); rework `createAgentOfType` to send `execution_shape`+`agent_class`+triggers; class pre-defaulted from trigger intent. |
| `studio/src/pages/AgentDetailPage.tsx` | M | Add a Class selector to `SettingsContent`; include `agent_class` in the `updateAgent` call. |
| `studio/src/pages/WorkflowBuilderPage.tsx` | M | Add `saveClass` state + Class selector to the Save modal; send `agent_class` on create, PATCH on re-save; toast `wf.warnings`. |
| `studio/src/pages/CreateAgentPage.test.tsx` | M | Vitest: three selectors render; create posts `execution_shape`+`agent_class`; class defaults from trigger. |
| `studio/src/pages/AgentDetailPage.test.tsx` | M | Vitest: class selector renders + PATCH includes `agent_class`. |
| `studio/src/pages/WorkflowBuilderPage.test.tsx` | **C** | Vitest: Save modal class selector; create posts `agent_class`; warnings toast on reactive+high-risk. |
| `studio/e2e/create-agent-wizard.spec.ts` | M | Playwright: assert the three selectors (replaces the four-card assertion); create → reload → class persisted. |
| `studio/e2e/agent-detail-modes.spec.ts` | M | Playwright: Settings class edit → reload → persisted. |
| `studio/e2e/workflow-builder.spec.ts` | M | Playwright: Save-modal class → create → reload → persisted. |

### Docs + infra
| File | C/M | Responsibility |
|---|---|---|
| `docs/spec.md` | M | Reword reactive/durable definition off "single-shot" (R1); note `agents.agent_class`/`workflows.agent_class` NOT NULL. |
| `docs/testing/manual-ui-e2e-test-plan.md` | M | Add WS-0 gap-ledger note (deferred pieces). |
| `scripts/e2e/suite-54-agent-class-shape-dispatch.sh` | **C** | Bash e2e: authoring persistence + shape-aware dispatch + parity grep + reactive fail-closed. |
| `scripts/e2e/run-all.sh` | M | Register suite-54. |
| `scripts/deploy-cpe2e.sh` | M | Bump registry-api→0.2.156, deploy-controller→0.1.36, studio→0.1.127 + header note. |
| `charts/agentshield/values.yaml` | M | Mirror the same three tags. |

Every file above appears in a Task below, and every Task file appears here (self-review §11 verified).

## 6. Key Interfaces (names/types authoritative — consistent across all tasks)

- **Shared dispatch** — `durable_dispatch.dispatch_durable_run(*, run_id: str, agent_name: str,
  input_payload: dict | None, callback_url: str, runner_url: str | None = None, timeout_s: float = 10.0)
  -> tuple[bool, str | None]`. Full body in `contracts/shared-dispatch-helper.md`.
- **Production step-update** — `POST /api/v1/internal/runs/{run_id}/step-update`, body
  `{step_number:int, step_name:str, status:str, output?:dict, output_text?:str, run_completed?:bool,
  error_message?:str|None, approval_id?:str|None}` → `{"status":"ok"}`.
- **Shape-aware dispatch** — `_dispatch_and_complete(run_id, agent_name, team, message, execution_shape,
  input_payload, trigger_id=None)`.
- **Orchestrator** — `orchestrate(parent_run_id, team, workflow_id, input_message, mode, shape="durable")`;
  `_park_or_fail(parent_run_id, mode, team, workflow_id, shape) -> None`.
- **Create/patch payloads** (carry `agent_class ∈ {"user_delegated","daemon"}`): full shapes in
  `contracts/create-patch-api.md`. Agent create default `"user_delegated"`; PATCH optional; workflow create
  default `"user_delegated"`; workflow response adds `warnings: list[str]`.
- **Frontend Class default rule** — in `createAgentOfType`: `agentClass ?? (schedule || webhook ?
  "daemon" : "user_delegated")` — pre-defaulted from trigger intent, user-overridable via the Class selector.

## 7. Tasks (dependency-ordered)

Each task: Files · Interface contract · Acceptance · Dependencies · Test cases · Verification command.

---

### T1 — Migration `0058` + ORM `agent_class` NOT NULL on both executables (Slice A foundation)
- **Files:** `alembic/versions/0058_agent_class_not_null_and_workflows_agent_class.py` (C); `models.py` (M).
- **Interface contract:** migration `revision="0058"`, `down_revision="0057"`; upgrade/downgrade exactly per
  `data-model.md`. `Agent.agent_class: Mapped[str]` NOT NULL `server_default 'user_delegated'` + CHECK;
  new `CompositeWorkflow.agent_class: Mapped[str]` NOT NULL `server_default 'user_delegated'` + CHECK.
- **Acceptance:** `alembic upgrade head` applies cleanly on a DB with pre-existing NULL `agents.agent_class`
  rows (they backfill to `user_delegated`); both CHECK constraints exist; a raw
  `INSERT INTO agents(...)` omitting `agent_class` yields `user_delegated`; mappers configure.
- **Dependencies:** none.
- **Test cases:** (a) upgrade→downgrade→upgrade round-trips idempotently; (b) `INSERT ... agent_class='x'`
  → CHECK violation; (c) existing NULL row → `user_delegated` after upgrade.
- **Verification:**
  `cd services/registry-api && python3 -c "import ast; ast.parse(open('alembic/versions/0058_agent_class_not_null_and_workflows_agent_class.py').read())"`
  and `python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`.

### T2 — Schemas + routers: agent_class on create/update/response (agents + workflows), wire the orphan
- **Files:** `schemas.py` (M); `routers/agents.py` (M); `routers/composite_workflows.py` (M).
- **Interface contract:** exactly per `contracts/create-patch-api.md` — `AgentCreate.agent_class` defaulted
  required; `update_agent` applies `body.agent_class`; `AgentResponse.agent_class: str`;
  `CompositeWorkflowCreate/Update.agent_class`; `CompositeWorkflowResponse.agent_class`+`warnings`;
  `create_workflow` passes it; `_to_response` carries both; `compute_reactive_approval_warnings` populates
  `warnings` in get/update.
- **Acceptance:** create agent w/o class → 201 + `agent_class:"user_delegated"`; create w/ `"bogus"` → 422;
  PATCH `{agent_class:"daemon"}` then GET → `"daemon"` (orphan fixed); create+PATCH workflow class persists;
  reactive workflow w/ high-risk-tool member → `warnings` non-empty.
- **Dependencies:** T1.
- **Test cases:** covered by suite-54 T-S54-001..004 (below). Unit-syntax: `ast.parse` on all three.
- **Verification:** `cd services/registry-api && for f in schemas.py routers/agents.py routers/composite_workflows.py; do python3 -c "import ast; ast.parse(open('$f').read())"; done`
  then mapper-configure import as in T1.

### T3 — Deploy reads the column directly — remove the coalesce (M3, Slice A close)
- **Files:** `services/deploy-controller/manifest_builder.py` (M).
- **Interface contract:** `:128` `agent_class = agent["agent_class"]` (direct index; no `.get`, no `or`).
- **Acceptance:** a deployed agent's pod env `AGENTSHIELD_AGENT_CLASS` + label `agentshield.io/agent-class`
  equal the DB `agent_class` verbatim; a daemon agent deploys as `daemon` (previously impossible — always
  coalesced to `user_delegated`).
- **Dependencies:** T1 (column NOT NULL), T2 (response always carries it).
- **Test cases:** suite-54 T-S54-005 (deploy daemon agent → pod env is `daemon`).
- **Verification:** `python3 -c "import ast; ast.parse(open('services/deploy-controller/manifest_builder.py').read())"`;
  `grep -n 'or "user_delegated"' services/deploy-controller/manifest_builder.py` → **no output**.

### T4 — Shared durable-dispatch helper + shape-aware production dispatch + production step-update (Slice B core, parity)
- **Files:** `durable_dispatch.py` (C); `routers/playground.py` (M); `routers/internal.py` (M).
- **Interface contract:** `durable_dispatch.dispatch_durable_run(...)` exactly per
  `contracts/shared-dispatch-helper.md`; `playground._dispatch_durable_run` refactored to a thin wrapper
  (behavior-neutral); `internal._dispatch_and_complete(..., execution_shape, input_payload, ...)` branches
  durable→shared helper (callback `/internal/runs/{id}/step-update`), reactive→existing `/chat`;
  `start_internal_run` passes `agent.execution_shape` + payload; new
  `POST /api/v1/internal/runs/{run_id}/step-update` writes `RunStep`(AgentRun) + completes the run.
- **Acceptance:** a durable agent's internal run hits `/run` and gets `RunStep` rows; a reactive agent's
  internal run hits `/chat`, gets `output`, **no** `RunStep`; a runner dispatch failure marks the AgentRun
  `failed` + fires the alert (never hangs); playground durable runs behave exactly as before.
- **Dependencies:** T1 (nothing structural, but keeps DB consistent); logically after T2.
- **Test cases:** suite-54 T-S54-006 (durable→run_steps), T-S54-007 (reactive→no run_steps), T-S54-010
  (parity grep: single `/run` POST).
- **Verification:** `ast.parse` on all three + mapper import; `grep -rn 'dispatch_durable_run'
  services/registry-api/routers/playground.py services/registry-api/routers/internal.py` shows both call it;
  `grep -rn '"/run"' services/registry-api/routers/playground.py services/registry-api/routers/internal.py`
  → **no output** (the POST literal lives only in `durable_dispatch.py`).

### T5 — Reactive workflow = awaited+capped (M6) + approval-gate fail-closed + save-time warn (S2)
- **Files:** `routers/internal.py` (M, `_start_workflow_run`); `workflow_orchestrator.py` (M);
  `routers/composite_workflows.py` (already touched in T2 for `compute_reactive_approval_warnings`).
- **Interface contract:** `_start_workflow_run` branches on `wf.execution_shape` — reactive →
  `await asyncio.wait_for(orchestrate(..., shape="reactive"), timeout=WORKFLOW_REACTIVE_TIMEOUT_S)`,
  timeout→`_fail_parent`, return the refreshed run; durable → existing background path with `shape="durable"`.
  `orchestrate(..., mode, shape="durable")` threads `shape`; `_park_or_fail(parent_run_id, mode, team,
  workflow_id, shape)` parks (durable) / fails-closed (reactive); `_run_sequential_from` takes `shape`.
- **Acceptance:** a reactive workflow returns its final output synchronously (one response, no
  `orchestrator_state` checkpoint row); a reactive workflow whose member trips an approval gate → run
  `failed`, `error_message` contains "set shape=durable", caller not blocked; durable workflow behavior
  unchanged (parks as today).
- **Dependencies:** T2 (warnings field), T4 (internal.py already open; keep edits coherent).
- **Test cases:** suite-54 T-S54-008 (reactive workflow synchronous output, no checkpoint), T-S54-009
  (reactive approval gate → failed w/ message).
- **Verification:** `ast.parse` on both files + mapper import;
  `grep -n '_park_or_fail\|shape="reactive"\|shape="durable"' services/registry-api/workflow_orchestrator.py services/registry-api/routers/internal.py` shows the wiring.

### T6 — Studio: split the wizard, add Class selectors, client + spec reword (Slice A UI)
- **Files:** `studio/src/api/registryApi.ts` (M); `CreateAgentPage.tsx` (M); `AgentDetailPage.tsx` (M);
  `WorkflowBuilderPage.tsx` (M); `docs/spec.md` (M).
- **Interface contract:** client bodies carry `agent_class` (types in `contracts/create-patch-api.md`).
  `CreateAgentPage`: three selectors — Shape `reactive|durable`, Trigger (manual/api · schedule · webhook,
  reuses the existing ScheduleFields/FilterConditionsEditor), Class `user_delegated|daemon` pre-defaulted
  `schedule||webhook ? "daemon" : "user_delegated"`; `createAgentOfType` sends `execution_shape` +
  `agent_class` + triggers. `AgentDetailPage.SettingsContent`: Class `<select>` beside Execution Shape,
  included in the `updateAgent` call. `WorkflowBuilderPage`: `saveClass` state + Class `<select>` in the
  Save modal; `createCompositeWorkflow({..., agent_class: saveClass})`; `handleResave` PATCHes class when
  changed; `toast.warning` for each `wf.warnings`. `docs/spec.md`: reactive/durable reworded off "single-shot".
- **Acceptance:** `npm run typecheck` clean; wizard shows three independent selectors; picking Scheduled
  defaults Class to daemon (overridable); create posts both fields; Settings + Save-modal edits send class.
- **Dependencies:** T2 (backend accepts the fields).
- **Test cases:** T7 Vitest + Playwright.
- **Verification:** `cd studio && npm run typecheck`; `grep -rn "agent_class" studio/src/pages/CreateAgentPage.tsx studio/src/pages/AgentDetailPage.tsx studio/src/pages/WorkflowBuilderPage.tsx studio/src/api/registryApi.ts` shows wiring in each.

### T7 — Studio tests: Vitest + Playwright for the new authoring surfaces
- **Files:** `CreateAgentPage.test.tsx` (M); `AgentDetailPage.test.tsx` (M); `WorkflowBuilderPage.test.tsx`
  (C); `create-agent-wizard.spec.ts` (M); `agent-detail-modes.spec.ts` (M); `workflow-builder.spec.ts` (M).
- **Interface contract:** Vitest mocks `../api/registryApi`, renders via `renderWithProviders`, asserts the
  three selectors + that the create/PATCH mock is called with `agent_class` (+ default-from-trigger).
  Playwright drives the real browser, `waitForResponse` on the create/PATCH, reloads and asserts persistence
  (fails — not skips — if the backend fixture is unreachable).
- **Acceptance:** `npm run test` green; `bash scripts/studio-e2e.sh` green for the three specs (assert wiring
  + persistence + network calls, not agent execution — the accepted boundary).
- **Dependencies:** T6.
- **Test cases:** wizard 3-selector render; class default flips to daemon on Scheduled; create payload carries
  both fields; Settings PATCH carries `agent_class`; workflow Save modal class → create payload; reactive+
  high-risk-tool → warning toast (Vitest, mocked `warnings`).
- **Verification:** `cd studio && npm run test`; `bash scripts/studio-e2e.sh`.

### T8 — Backend e2e suite-54 + register (Slice A+B golden path, parity assertion)
- **Files:** `scripts/e2e/suite-54-agent-class-shape-dispatch.sh` (C); `scripts/e2e/run-all.sh` (M).
- **Interface contract:** `kubectl exec` into the registry-api pod, `python3`/`httpx` against
  `http://localhost:8000`; test IDs `T-S54-001`..`T-S54-010`; `set -euo pipefail`; `exit 1` (fail, not skip)
  on any missing fixture; cleanup trap deletes created agents/workflows. Register in `run-all.sh` after
  suite-53.
- **Acceptance:** all ten cases pass on the deployed cluster.
- **Test cases (real doors):**
  - `T-S54-001` create agent w/o class → 201, `agent_class="user_delegated"` (M3: explicit default persisted).
  - `T-S54-002` create agent `agent_class="bogus"` → 422.
  - `T-S54-003` PATCH agent `agent_class="daemon"` → GET returns `daemon` (orphan wired).
  - `T-S54-004` create+reload workflow `agent_class="daemon"` → persisted (save→reload→assert).
  - `T-S54-005` deploy a daemon agent → pod env `AGENTSHIELD_AGENT_CLASS=daemon` (coalesce removed).
  - `T-S54-006` durable agent internal run → `run_steps` rows exist (dispatch hit `/run`).
  - `T-S54-007` reactive agent internal run → **no** `run_steps`, run has `output` (dispatch hit `/chat`).
  - `T-S54-008` reactive workflow run → synchronous `output`, **no** `orchestrator_state` checkpoint.
  - `T-S54-009` reactive workflow w/ approval-gate member → run `failed`, `error_message` ~ "set shape=durable".
  - `T-S54-010` **parity grep** inside the pod's mounted source (or the repo checkout used by CI): a single
    `/run` POST — `dispatch_durable_run` called from both routers, zero raw `/run` POST in the routers.
- **Dependencies:** T2, T3, T4, T5.
- **Verification:** `bash scripts/e2e/suite-54-agent-class-shape-dispatch.sh`; `grep -n "suite-54"
  scripts/e2e/run-all.sh`.

### T9 — Image-tag bumps + deploy + gap-ledger note (ship)
- **Files:** `scripts/deploy-cpe2e.sh` (M); `charts/agentshield/values.yaml` (M);
  `docs/testing/manual-ui-e2e-test-plan.md` (M).
- **Interface contract:** `REGISTRY_API_TAG 0.2.155→0.2.156`, `DEPLOY_CONTROLLER_TAG 0.1.35→0.1.36`,
  `STUDIO_TAG 0.1.126→0.1.127` in `deploy-cpe2e.sh` **and** the mirrored `tag:` values in `values.yaml`
  (registry-api `:588`, deploy-controller `:650`, studio `:899`); `DECLARATIVE_RUNNER_TAG` **unchanged**
  (0.1.37). Header note in `deploy-cpe2e.sh` describing WS-0. Gap-ledger note in the manual plan.
- **Acceptance:** `bash scripts/deploy-cpe2e.sh` builds+deploys; `kubectl rollout status` green for
  registry-api, deploy-controller, studio; suite-54 green against the deployed cluster; Playwright green.
- **Dependencies:** T1–T8.
- **Test cases:** post-deploy suite-54 + `scripts/studio-e2e.sh` both green.
- **Verification:** `grep -n "0.2.156\|0.1.36\|0.1.127" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml`
  (each tag present in both files).

## 8. Complexity Tracking / Gap Ledger

| Item | Status | Note |
|---|---|---|
| Real per-node durable `run_steps` (vs the 2-step skeleton) | **deferred (intentional) → WS-1** | WS-0 dispatch reaching `/run` + skeleton steps proves the branch; WS-1 replaces the skeleton. |
| `PostgresSaver` checkpoint / crash-restart resume | deferred (intentional) → WS-1 | WS-0 does not persist graph checkpoints. |
| HITL park emit on durable single-agent | deferred (intentional) → WS-1 | WS-0's production callback carries `awaiting_approval` but no park-emit producer yet. |
| Non-sequential durable auto-advance (cond/handoff/supervisor) | deferred (intentional) → WS-1/D3 | WS-0 only adds the reactive fail-closed arm; durable park behavior unchanged (still no auto-advance). |
| Daemon service-identity `run_by` / OPA `user_identity_ok` / `X-Run-Principal` | deferred (intentional) → WS-2 | WS-0 stores + authors `agent_class`; runtime identity semantics are WS-2. |
| S2 save-time warn precision | deferred (intentional) | Best-effort (static high-risk-tool scan); dynamic OPA-risk gates are only catchable at runtime (the authoritative fail-closed seam WS-0 ships). |
| Workflow **deploy-controller** pods reading `agent_class` | deferred (intentional) | Column authored + persisted in WS-0; workflow production pods are a later slice. |
| `WORKFLOW_REACTIVE_TIMEOUT_S` cap value | not-yet-tuned (debt, low) | Default 120s; the shared per-agent run-timeout worker unification is WS-6 (OQ-10). |

No orphan flags: every field/endpoint/symbol WS-0 introduces has its consumer shipped in the same task
(see Constitution DoD #3 row). Recorded in `docs/testing/manual-ui-e2e-test-plan.md` (Task T9).

## 9. Execution Notes

- **Do the migration correction first.** `0058` / `down_revision="0057"` — not the doc's `0056`/`0055`.
  Confirm with `ls services/registry-api/alembic/versions/ | tail -3` before writing the file.
- **Keep the playground refactor behavior-neutral.** T4's `_dispatch_durable_run` change must not alter the
  sandbox UX — it only moves the `/run` POST into the shared helper. Re-run suite-20 (durable playground)
  after T4 to confirm no regression.
- **Thread `shape` as a real parameter** everywhere (dispatch + orchestrate). Do not sniff
  `wf.execution_shape` inside a shared helper via `getattr` — pass it. (No-Bandaid rule.)
- **declarative-runner is untouched** in WS-0 — do not bump its tag; its 2-step `/run` skeleton is the
  intended WS-0 target and WS-1's replacement point.
- **Two verticals, prove A then B.** Land T1–T3 + T6 (authoring) and prove save→reload before T4–T5
  (dispatch). Do not build all backend then all UI.
- **Deploy via `scripts/deploy-cpe2e.sh` only** (never bare `helm upgrade`); it builds images + secrets +
  helm + rollout wait + seed.
- **Follow-on plans (do not start here):** WS-1…WS-6 each get their own `/plan` invocation off this same
  spec once WS-0 is green.

## 10. Sequence diagram — shape-aware triggered dispatch (the WS-0 heart)

```
scheduler/event-gateway ──POST /internal/runs/start (run_by, trigger)──► registry-api
  start_internal_run: load Agent, read execution_shape, create AgentRun(status=running)
      ├─ execution_shape == "reactive" ─► _dispatch_and_complete → POST {agent}-production/chat
      │                                     └─ record completed/failed on AgentRun (sync)
      └─ execution_shape == "durable"  ─► _dispatch_and_complete
                                            └─ durable_dispatch.dispatch_durable_run
                                                 └─ POST declarative-runner/run {callback=/internal/runs/{id}/step-update}
declarative-runner ──POST /internal/runs/{id}/step-update (per step, then run_completed)──► registry-api
  step-update: upsert RunStep(run_id=AgentRun.id); on run_completed → AgentRun.status=completed
```
(Sandbox path is identical except the callback is `/playground/runs/{id}/step-update` and the row is a
`PlaygroundRun` — the ONLY differences, both explicit params to the one shared helper.)

## 11. Self-review (run before handing off — all clear)
- **Spec coverage:** R1 (T1 comment + T6 wizard/spec), agent_class authoring (T2/T6), M3 (T1/T3),
  workflows.agent_class (T1/T2/T6), shape-aware dispatch + shared helper (T4), M6 (T5), S2 runtime+save (T5/T2)
  — all WS-0 items mapped to a task. WS-1..6 explicitly excluded.
- **Placeholder scan:** no TBD/TODO/"handle edge cases"; every value concrete (tags, timeout default,
  migration id, constraint names, endpoint paths).
- **Interface consistency:** `dispatch_durable_run`, `_dispatch_and_complete`, `orchestrate(shape=…)`,
  `_park_or_fail`, `/internal/runs/{id}/step-update`, `agent_class` domain — identical across plan +
  contracts + data-model.
- **Dependency correctness:** T1→T2→{T3,T4}; T2→T6→T7; T4/T5→T8; all→T9. No forward references.
- **File-path consistency:** every File-Structure row maps to a Task and vice-versa; all paths absolute-safe
  (repo-relative, exist or marked **C**).

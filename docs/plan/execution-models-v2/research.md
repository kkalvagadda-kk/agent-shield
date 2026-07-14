# WS-0 Research — code-truth grounding (read before implementing)

Every claim below was verified against the running code on 2026-07-12 (branch `main`),
not the design doc. Where the design doc and the code disagree, the code wins and the
delta is called out. Cite these `file:line` anchors in the implementation.

## Scope reminder

This plan covers **WS-0 only** (Foundation: `agent_class` authoring + shape-aware dispatch).
WS-1 (durable engine real steps / checkpoint / HITL park), WS-2 (daemon identity + reviewer
authority + `X-Run-Principal` headers), WS-3, WS-4, WS-5, WS-6 are **follow-on plans** — do
not build them here. Anywhere WS-0 touches a seam those slices will extend, this doc notes
"WS-1/2 extends here" so the cold implementer does not over-reach.

---

## Correction 1 (BLOCKER for the migration) — the latest migration is `0057`, not `0055`

The design doc (`execution-models-v2-e2e.md:109`) and the fix doc allocate `0056` for the WS-0
migration and assert "latest alembic migration = `0055`." **That is stale.** The real head:

```
services/registry-api/alembic/versions/
  0055_agent_identity_production_deployment_id.py   revision=0055 down_revision=0054
  0056_auth_config_credentials_encrypted.py         revision=0056 down_revision=0055
  0057_playground_run_user_feedback.py              revision=0057 down_revision=0056   ← HEAD
```

**The WS-0 migration MUST be `0058` with `down_revision = "0057"`.** Using `0056` would collide
with an existing revision and break `alembic upgrade head`. This is exactly CLAUDE.md rule 6
("reason from the running product, not the design doc"). All artifacts in this plan say `0058`.

---

## Correction 2 — `agent_class` is already accepted on agent create, but the UPDATE path silently drops it (orphan)

- `schemas.py:78` — `AgentCreate.agent_class: str | None = Field(None, pattern="^(daemon|user_delegated)$")` — accepted.
- `schemas.py:88` — `AgentUpdate.agent_class: str | None = Field(None, pattern=...)` — accepted **in the schema**.
- `routers/agents.py:90` — `create_agent` writes `agent_class=body.agent_class` ✅.
- `routers/agents.py:271-322` — `update_agent` applies `description`, `status`, `metadata`,
  `execution_shape` (`:309`), `memory_enabled` (`:312`) — but **never `agent_class`**. So a PATCH
  carrying `agent_class` is accepted (200) and silently discarded. WS-0 must wire it (Task T2).
  `update_agent` is registered for **both PUT and PATCH** (`agents.py:265` `methods=["PUT","PATCH"]`).

## Correction 3 — `workflows.agent_class` does not exist at all

- `models.py:316` `CompositeWorkflow` (`__tablename__ = "workflows"`) has `execution_shape`,
  `orchestration`, `memory_enabled`, `status`, `publish_status` — **no `agent_class` column**.
- `schemas.py:437` `CompositeWorkflowCreate` / `:446` `Update` / `:454` `Response` — no `agent_class`.
- `routers/composite_workflows.py:127` `create_workflow` builds `CompositeWorkflow(...)` without it;
  `:79 _to_response` omits it; `:167 update_workflow` uses `model_dump(exclude_none=True)` + `setattr`,
  so once `agent_class` is added to `CompositeWorkflowUpdate` it will apply automatically.
- Studio: `WorkflowBuilderPage.tsx` Save modal (`:806-817`) has an Execution-Shape selector and
  (`:819-840`) an Orchestration selector — **no Class selector**. `handleFirstSave` (`:267`) posts
  `createCompositeWorkflow({name, team, orchestration: saveOrchestration, execution_shape: saveShape})`.
  **Note:** `WorkflowPropertiesPanel.tsx` is a per-node (member/edge) editor, **not** the workflow-level
  Save modal — the Class selector goes in `WorkflowBuilderPage.tsx`, not the properties panel.

## Correction 4 — the deploy-time NULL coalesce band-aid to remove

- `services/deploy-controller/manifest_builder.py:128`
  `agent_class = agent.get("agent_class") or "user_delegated"`.
  Also injected into the pod as env `AGENTSHIELD_AGENT_CLASS` (`:154`) and the pod label
  `agentshield.io/agent-class` (`:140`). M3 removes the coalesce → `agent_class = agent["agent_class"]`.
  Safe once the column is `NOT NULL` (Task T1) and the API response always carries it (Task T2).
  Direct-index (not `.get`) so a truly missing field fails **loud** instead of silently downgrading —
  the No-Bandaid rule.

---

## The dispatch seam (shape-aware triggered dispatch + parity)

### Sandbox durable dispatch — the logic to share
`routers/playground.py:225 _dispatch_durable_run(run_id, agent_name, input_payload, db)`:
- Resolves the agent + running `Deployment`.
- `runner_url = os.getenv("DECLARATIVE_RUNNER_URL", "http://declarative-runner.agentshield-platform.svc.cluster.local:8080")`.
- `callback_url = os.getenv("REGISTRY_API_INTERNAL_URL", "http://registry-api.agentshield-platform.svc.cluster.local:8000")`.
- `POST {runner_url}/run` with body
  `{"agent_name", "run_id", "input_payload": input_payload or {}, "callback_url": f"{callback_url}/api/v1/playground/runs/{run_id}/step-update"}`.
- On failure → marks the `PlaygroundRun` `failed`.
- Called from `create_playground_run` (`:148-151`) only when `shape == "durable"`.

### Production triggered dispatch — currently reactive-only, hardcodes `/chat`
`routers/internal.py:41 _dispatch_and_complete(run_id, agent_name, team, message, trigger_id=None)`:
- `url = f"http://{agent_name}-production.{ns}.svc.cluster.local:8080/chat"` (`:53`) — **hardcoded `/chat`,
  never reads `execution_shape`.** This is collapse-point #2.
- Synchronous `POST /chat`, then records `completed`/`failed` on the `AgentRun` and fires failure alerting.
- `start_internal_run` (`:199`) resolves the `Agent` (`:208`), requires a running `Deployment` (`:216`),
  creates the `AgentRun` with `status="running"` (`:248`), then `asyncio.create_task(_dispatch_and_complete(...))` (`:273`).

### The declarative-runner `/run` contract (already exists; WS-1 replaces the body)
`services/declarative-runner/main.py:536 DurableRunRequest {agent_name, run_id, input_payload, callback_url}`;
`:543 @app.post("/run")` → `_execute_durable_run` (`:554`) POSTs step updates to `req.callback_url`.
Today it emits a **2-step skeleton** (`input_processing` `:567`, `agent_execution` `:583`) — that is the
WS-1 target, **not WS-0**. For WS-0, hitting `/run` and getting the skeleton `run_steps` is sufficient to
prove the dispatch branched correctly.

### The sandbox step-update callback (the shape to mirror for production)
`routers/playground.py:284 step_update_callback` (`POST /api/v1/playground/runs/{run_id}/step-update`):
upserts a `RunStep` keyed by `(run_id, step_number)`, sets `awaiting_approval`→run `blocked`, and on
`run_completed` marks the `PlaygroundRun` `completed`/`failed`. WS-0 adds the **production** equivalent
`POST /api/v1/internal/runs/{run_id}/step-update` writing `RunStep` against the `AgentRun`.

### `RunStep` is polymorphic — no schema change needed for production steps
`models.py:1554 RunStep`: `run_id` (`:1572`) is a bare UUID (no FK) — "may point at either `agent_runs.id`
(production) or `playground_runs.id`" (`:1568-1571`). `UniqueConstraint(run_id, step_number)` (`:1557`).
So the production callback writes `RunStep(run_id=agent_run.id, ...)` with **no migration**.

### Parity conclusion (the retro's root-cause fix)
The `/run` POST body + error handling is **identical** across sandbox and production; only the
`callback_url` and the run-status table (`PlaygroundRun` vs `AgentRun`) differ. Per
`sandbox-production-parity-architecture.md` ("shared helper, variants pass explicit parameters") and the
2026-07-11 retro, WS-0 extracts one shared `services/registry-api/durable_dispatch.py::dispatch_durable_run`
that **both** `playground.py` and `internal.py` import. The e2e greps to prove no divergent `/run` POST copy.

---

## Reactive workflow = fire-and-forget today (M6 target)

`routers/internal.py:95 _start_workflow_run` always ends with
`asyncio.create_task(orchestrate(str(run.id), wf.team, str(wf.id), message, wf.orchestration))` (`:186`)
after trying the production orchestrator pod (`:181 dispatch_to_orchestrator_pod`). It **never reads
`wf.execution_shape`** — so a reactive workflow is cosmetic (always the background orchestrator). M6:
branch on `wf.execution_shape` — reactive → `await orchestrate(...)` under a wall-clock cap and return the
final output; durable → today's background path.

## The orchestrator's pause path (S2 runtime fail-closed target)

`workflow_orchestrator.py`:
- `orchestrate(parent_run_id, team, workflow_id, input_message, mode)` (`:600`) routes to
  `orchestrate_graph_sequential` / `_conditional` / `_handoff` / `_supervisor`; wraps all in try/except and
  marks the run `failed` on any raise (`:611`).
- Every mode detects a paused member via `_run_step` (`:262`) returning `awaiting_approval` (authoritative
  pending-`Approval` probe, `:298-306`), then **parks**: sequential checkpoints inline (`:384-394`),
  non-sequential call `_halt_for_approval` (`:249`) at `:467,513,564,577`.
- **S2:** a *reactive* workflow must **fail-closed** at those same sites (clear message, caller not blocked)
  instead of parking. WS-0 threads an explicit `shape` parameter into `orchestrate` + the mode functions and
  routes the `awaiting_approval` branch through one new helper `_park_or_fail(...)` — durable parks (existing),
  reactive fails. Explicit parameter, no `getattr`/priority-fallthrough (No-Bandaid rule). Non-sequential
  **auto-advance stays deferred to WS-1/D3** — WS-0 only adds the reactive-fail branch; durable behavior is
  byte-for-byte unchanged.

## S2 save-time warn — tool risk is a real, queryable signal
`models.py:1012 Tool.risk_level` ∈ `('low','medium','high','critical')`. A member's high-risk tool is
reachable via `WorkflowMember → Agent → AgentTool → Tool.risk_level IN ('high','critical')`. WS-0 computes a
non-blocking warning list on the workflow create/update response when `execution_shape='reactive'`.

---

## Frontend truth

- `studio/src/api/registryApi.ts:24 Agent` already has `agent_class: string | null` (`:32`).
  `createAgent` body (`:210`) and `updateAgent` body (`:224`) **omit** `agent_class` → the wizard/Settings
  cannot send it today. `CompositeWorkflow` (`:526`) and `CreateCompositeWorkflowRequest` (`:569`) omit it too.
- `studio/src/pages/CreateAgentPage.tsx:19` — the 4-way `type AgentType = "reactive"|"durable"|"scheduled"|"event-driven"`;
  `AGENT_TYPE_CARDS` (`:24`) + `AgentTypePicker` (`:31`). `createAgentOfType` (`:160`) collapses type →
  `execution_shape` (`:169`) + a trigger, **sends no `agent_class`**. Two create surfaces reuse it: no-code
  (`:485,518`) and code/SDK (`:728,740`). R1 replaces the 4-way picker with three selectors (Shape/Trigger/Class).
- `studio/src/pages/AgentDetailPage.tsx:426 SettingsContent` — has an `execShape` selector (`:518-527`) and
  a `save` mutation calling `updateAgent` (`:449`). **No `agent_class` selector.** WS-0 adds one.
- Existing tests to update (do not delete): `studio/src/pages/CreateAgentPage.test.tsx`,
  `studio/src/pages/AgentDetailPage.test.tsx`; `studio/e2e/create-agent-wizard.spec.ts` (currently asserts
  the four cards — rewrite to the three selectors), `studio/e2e/agent-detail-modes.spec.ts`,
  `studio/e2e/workflow-builder.spec.ts`. New Vitest file: `studio/src/pages/WorkflowBuilderPage.test.tsx`.

## Infra truth (image tags + e2e)

- Current tags — `deploy-cpe2e.sh`: `REGISTRY_API_TAG=0.2.155` (`:111`), `DEPLOY_CONTROLLER_TAG=0.1.35`
  (`:113`), `STUDIO_TAG=0.1.126` (`:114`), `DECLARATIVE_RUNNER_TAG=0.1.37` (`:116`).
  `charts/agentshield/values.yaml`: registry-api `tag: "0.2.155"` (`:588`), deploy-controller `tag: "0.1.35"`
  (`:650`) + `declarativeRunnerTag: "0.1.37"` (`:657`), studio `tag: "0.1.126"` (`:899`).
- **WS-0 changes registry-api, deploy-controller, studio only.** declarative-runner is UNCHANGED in WS-0
  (its 2-step `/run` skeleton is reused as-is; real steps are WS-1) → do **not** bump `DECLARATIVE_RUNNER_TAG`.
- e2e: `scripts/e2e/run-all.sh` registers suites 1..53 (`run_suite` lines); last is `suite-53-cost-tracking.sh`.
  New suite = `suite-54-agent-class-shape-dispatch.sh`. Pattern (see `suite-19-execution-shape.sh`): `kubectl
  exec` into the `registry-api` pod, run `python3 -c` `httpx` assertions against `http://localhost:8000`.
- Scheduler stamps `run_by="serviceaccount:scheduler"` on the internal run body
  (`services/scheduler/main.py:113`). Daemon **service-identity** `run_by` is WS-2 — WS-0 asserts only the
  shape-aware `/run` vs `/chat` branch + `run_steps` presence, not the identity.

## Experience-doc trigger check
The CLAUDE.md experience-doc list covers `playground.py` but only for **playground UX/SSE** changes. WS-0's
`playground.py` edit is a pure refactor (extract `_dispatch_durable_run`'s body into the shared helper) with
**no external behavior change**, and `internal.py`/`CreateAgentPage.tsx`/`AgentDetailPage.tsx`/`WorkflowBuilderPage.tsx`
are **not** on the list. → No `docs/experience/playground.md` update is triggered by WS-0. (Recorded so the
implementer does not skip a required doc — this is a deliberate "not triggered," not an omission.)

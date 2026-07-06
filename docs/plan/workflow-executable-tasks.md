# Workflow Executable — Implementation Tasks

**Generated**: 2026-07-05  
**Source plan**: `docs/plan/workflow-executable-plan.md`  
**Spec context**: `docs/spec.md` §2.6/§4.5 + `docs/decisions.md` Decision 22  
**Constitution**: `CLAUDE.md`

**Total tasks:** 36 (33 implementation + 3 checkpoint)  
**Phases:** 5 implementation (W1–W5) + 3 checkpoint gates (CP-Wa/Wb/Wc)  
**Parallel opportunities:** noted inline with [P]  
**Checkpoint phases:** CP-Wa (after W1), CP-Wb (after W3), CP-Wc (after W5)

---

## Summary Table

| Phase | Type | Deliverable | Tasks | Gate |
|---|---|---|---|---|
| W1 | Implementation | Rename workflows→agent_graphs + new composite tables + CRUD router | T001–T009 | CP-Wa |
| CP-Wa | Checkpoint | GET /agent-graphs 200 + GET /workflows 200 + create/dup/get/delete composite workflow | — | — |
| W2 | Implementation | TS serializer + registryApi rename + canvas/routing updates | T010–T016 | — |
| W3 | Implementation | WorkflowOrchestrator + run dispatch + run tree + internal.py | T017–T022 | CP-Wb |
| CP-Wb | Checkpoint | Composite run creates parent AgentRun + children have parent_run_id | — | — |
| W4 | Implementation | WorkflowMemberNode + AddAgentModal + WorkflowBuilderPage | T023–T027 | — |
| W5 | Implementation | suite-29 + image tag bumps + regression verification | T028–T033 | CP-Wc |
| CP-Wc | Checkpoint | Build images, helm deploy, suite-29 green, regression suites green, TS clean | — | — |

---

## Phase W1 — Executable Data Model

**Goal**: Rename `workflows → agent_graphs` in DB, models, and routers. Create new `workflows` (composite) and `workflow_members` tables. Expose composite CRUD API. Backend only — no frontend changes.

**Order within phase**: Migrations → Models → Schemas → Routers (parallel) → Main + Children endpoint → E2E URL fixes (parallel) → Syntax verification → CP-Wa

---

- [X] [T001] Migration 0026 — rename `workflows → agent_graphs`, `workflow_versions → agent_graph_versions`, `agent_versions.workflow_id → agent_graph_id`; recreate indexes + FK with new names; downgrade reverses all renames — `services/registry-api/alembic/versions/0026_rename_workflows_to_agent_graphs.py`

- [X] [T002] Migration 0027 — create `workflows` (composite) and `workflow_members` tables with all CHECK constraints; add nullable `workflow_id` FK to `agent_triggers` (+ `ck_agent_triggers_target` CHECK) and `agent_runs`; partial indexes on new FKs; downgrade drops all additions — `services/registry-api/alembic/versions/0027_add_composite_workflows.py`  
  *Depends: T001*

- [X] [T003] models.py — rename class `Workflow → AgentGraph` (`__tablename__ = "agent_graphs"`); rename `WorkflowVersion → AgentGraphVersion`; rename `AgentVersion.workflow_id → agent_graph_id` + FK target; add `CompositeWorkflow` + `WorkflowMember` classes per Key Interfaces; add `workflow_id` nullable FK mapped columns to `AgentRun` and `AgentTrigger`; update `__all__` — `services/registry-api/models.py`  
  *Depends: T001, T002*

- [X] [T004] schemas.py — rename `WorkflowCreate → AgentGraphCreate` and all 7 `Workflow*` schema classes to `AgentGraph*`; rename `AgentVersionCreate.workflow_id → agent_graph_id`; update `InternalRunStartRequest` (make `agent_name: str | None`, add `workflow_id: uuid.UUID | None`, add `@model_validator` for exactly-one); add `CompositeWorkflowCreate`, `CompositeWorkflowUpdate`, `CompositeWorkflowResponse`, `CompositeWorkflowWithMembersResponse`, `WorkflowMemberCreate`, `WorkflowMemberResponse`, `WorkflowRunCreate`, `WorkflowRunStartResponse`, `WorkflowRunTreeResponse`; update `__all__` — `services/registry-api/schemas.py`  
  *Depends: T003*

- [X] [T005] [P] routers/workflows.py — repurpose as agent-graphs router: change `prefix = "/api/v1/agent-graphs"`, `tags = ["agent-graphs"]`; replace all `Workflow` model/schema imports with `AgentGraph` equivalents; update variable names and docstrings; no behavioral changes — `services/registry-api/routers/workflows.py`  
  *Depends: T003, T004*

- [X] [T006] [P] routers/composite_workflows.py — create new router (`prefix = "/api/v1/workflows"`): `GET /` list (team-filtered), `POST /` create (409 on duplicate name+team), `GET /{id}` get with members, `PATCH /{id}` update, `DELETE /{id}` archive (status='archived'); `POST /{id}/members` add member (validates agent.team == workflow.team), `DELETE /{id}/members/{agent_id}` remove member; stub `POST /{id}/runs` and `GET /{id}/runs/{run_id}/tree` returning 501 (completed in T020) — `services/registry-api/routers/composite_workflows.py`  
  *Depends: T003, T004*

- [X] [T007] main.py + agent_runs.py — change workflows router import alias to `agent_graphs_router`; add `from routers.composite_workflows import router as composite_workflows_router`; register with `app.include_router`; add `GET /api/v1/agent-runs/{run_id}/children` endpoint returning child runs ordered by `started_at` — `services/registry-api/main.py`, `services/registry-api/routers/agent_runs.py`  
  *Depends: T005, T006*

- [X] [T008] [P] Update e2e suite URL references — replace all `/api/v1/workflows` with `/api/v1/agent-graphs` in the three suites that test canvas graph endpoints; grep must return nothing for `/api/v1/workflows` in each file after edit — `scripts/e2e/suite-2-lifecycle.sh`, `scripts/e2e/suite-8-playground.sh`, `scripts/e2e/suite-14-consumer-chat.sh`  
  *Depends: T005*

- [X] [T009] Python syntax verification — run `python3 -c "import ast; ast.parse(open('$f').read())"` on all Phase W1 Python files: `0026_rename_workflows_to_agent_graphs.py`, `0027_add_composite_workflows.py`, `models.py`, `schemas.py`, `routers/workflows.py`, `routers/composite_workflows.py`, `main.py`, `routers/agent_runs.py`; fix any syntax error before proceeding — no new files  
  *Depends: T007, T008*

---

## Checkpoint CP-Wa — Alpha: Rename + CRUD Smoke

*Gate after W1. Must pass before starting Phase W2.*  
*Proves: old canvas-graph endpoint is live at /api/v1/agent-graphs; composite /api/v1/workflows endpoint is live; create/duplicate/get/archive operations work; /children endpoint reachable.*

- [X] [CP-Wa] Create and run Checkpoint Alpha smoke script — `scripts/smoke-test-cp-wa-rename-crud.sh`

```bash
#!/usr/bin/env bash
# Checkpoint Alpha — Rename + CRUD Smoke (Decision 22)
# Proves: /agent-graphs endpoint live, composite /workflows endpoint live,
# create/duplicate/get/delete composite workflow all correct.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== Checkpoint Alpha: Rename + CRUD Smoke ==="

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, time

B = 'http://localhost:8000/api/v1'

# T-CPA-001: canvas-graph endpoint reachable at new URL
r = httpx.get(f'{B}/agent-graphs', timeout=5)
assert r.status_code == 200, f'T-CPA-001 FAIL: {r.status_code}'
print('PASS T-CPA-001: GET /agent-graphs 200')

# T-CPA-002: composite workflows endpoint reachable
r = httpx.get(f'{B}/workflows', timeout=5)
assert r.status_code == 200, f'T-CPA-002 FAIL: {r.status_code}'
print('PASS T-CPA-002: GET /workflows (composite) 200')

# T-CPA-003: create composite workflow
ts = int(time.time())
r = httpx.post(f'{B}/workflows',
    json={'name': f'smoke-wf-{ts}', 'team': 'platform', 'orchestration': 'sequential'},
    timeout=5)
assert r.status_code == 201, f'T-CPA-003 FAIL: {r.text}'
wf_id = r.json()['id']
print('PASS T-CPA-003: POST /workflows 201, id=' + wf_id)

# T-CPA-004: duplicate name + team → 409
r = httpx.post(f'{B}/workflows',
    json={'name': f'smoke-wf-{ts}', 'team': 'platform', 'orchestration': 'sequential'},
    timeout=5)
assert r.status_code == 409, f'T-CPA-004 FAIL: expected 409, got {r.status_code}'
print('PASS T-CPA-004: duplicate name 409')

# T-CPA-005: get by id — member_count == 0
r = httpx.get(f'{B}/workflows/{wf_id}', timeout=5)
assert r.status_code == 200, f'T-CPA-005 FAIL get: {r.status_code}'
assert r.json().get('member_count', -1) == 0, f'T-CPA-005 FAIL member_count: {r.json()}'
print('PASS T-CPA-005: GET /workflows/{id} member_count=0')

# T-CPA-006: get non-existent → 404
r = httpx.get(f'{B}/workflows/00000000-0000-0000-0000-000000000000', timeout=5)
assert r.status_code == 404, f'T-CPA-006 FAIL: {r.status_code}'
print('PASS T-CPA-006: GET non-existent 404')

# T-CPA-007: archive workflow → 204
r = httpx.delete(f'{B}/workflows/{wf_id}', timeout=5)
assert r.status_code == 204, f'T-CPA-007 FAIL: {r.status_code}'
print('PASS T-CPA-007: DELETE 204')

# T-CPA-008: /children endpoint reachable (empty list for unknown run)
r = httpx.get(f'{B}/agent-runs/00000000-0000-0000-0000-000000000001/children', timeout=5)
assert r.status_code in (200, 404), f'T-CPA-008 FAIL: {r.status_code}'
print('PASS T-CPA-008: GET /agent-runs/.../children endpoint reachable')

print('')
print('=== Checkpoint Alpha: ALL PASS ===')
"

echo "PASS"
```

---

## Phase W2 — Workflow Definition JSON + Frontend API Layer

**Goal**: Update the TypeScript serializer, the registry API client, and the canvas page to use the renamed `/api/v1/agent-graphs/` endpoint. Create the AgentGraphsPage. Pivot WorkflowsPage to list composite workflows. Wire new routes.

**Order within phase**: Serializer types → API client → Canvas + AgentGraphsPage (parallel) → WorkflowsPage + routing → TS check → Manual verify

---

- [X] [T010] workflowSerializer.ts — add `CompositeWorkflowNode` interface, `CompositeWorkflowDefinition` interface, `serializeCompositeWorkflow(nodes, edges, orchestration)` function, `deserializeCompositeWorkflow(definition)` function; existing `serializeWorkflow` / `deserializeWorkflow` unchanged — `studio/src/utils/workflowSerializer.ts`  
  *Depends: T004 (schema shapes, cross-phase from W1)*

- [X] [T011] registryApi.ts — rename `listWorkflows → listAgentGraphs`, `getWorkflow → getAgentGraph`, `createWorkflow → createAgentGraph`, `updateWorkflow → updateAgentGraph`, `deployWorkflow → deployAgentGraph`; update all URL strings to `/api/v1/agent-graphs`; add TypeScript interfaces `CompositeWorkflow`, `CompositeWorkflowWithMembers`, `WorkflowMember`, `WorkflowRunResult`, `WorkflowRunTree`; add 10 composite workflow API functions: `listCompositeWorkflows`, `createCompositeWorkflow`, `getCompositeWorkflow`, `updateCompositeWorkflow`, `deleteCompositeWorkflow`, `addWorkflowMember`, `removeWorkflowMember`, `triggerWorkflowRun`, `getWorkflowRunTree`, `listWorkflowRuns` — `studio/src/api/registryApi.ts`  
  *Depends: T010*

- [X] [T012] [P] CanvasPage.tsx + Canvas.tsx — replace `getWorkflow` import with `getAgentGraph`; update `queryKey: ['agent-graph', id]` and `queryFn`; replace `updateWorkflow` + `deployWorkflow` with `updateAgentGraph` + `deployAgentGraph` at all call sites — `studio/src/pages/CanvasPage.tsx`, `studio/src/components/Canvas.tsx`  
  *Depends: T011*

- [X] [T013] [P] AgentGraphsPage.tsx — create list page for agent graphs (canvas builder): calls `listAgentGraphs()`, renders name/team/status table, "New Agent Graph" button → `/agent-graphs/new`, row click → `/agent-graphs/:id`, title "Agent Graphs", empty state copy — `studio/src/pages/AgentGraphsPage.tsx`  
  *Depends: T011*

- [X] [T014] WorkflowsPage.tsx + main.tsx — pivot `WorkflowsPage` to composite workflow list: call `listCompositeWorkflows()`, render name/team/orchestration/status/member_count columns, "New Workflow" button → `/workflows/new`, row click → `/workflows/:id/builder`, empty state "No workflows yet. Create one to compose existing agents."; add routes `/agent-graphs`, `/agent-graphs/new`, `/agent-graphs/:id` (→CanvasPage), `/workflows` (→WorkflowsPage), `/workflows/new` (→WorkflowBuilderPage stub), `/workflows/:id/builder` (→WorkflowBuilderPage stub); update sidebar nav to add "Workflows" link → `/workflows` and relabel canvas item to "Agent Graphs" → `/agent-graphs` — `studio/src/pages/WorkflowsPage.tsx`, `studio/src/main.tsx`  
  *Depends: T012, T013*

- [X] [T015] TypeScript validation — run `cd studio && npx tsc --noEmit`; zero type errors required before proceeding to W3 — no new files  
  *Depends: T014*

- [ ] [T016] MANUAL browser verification — navigate to `/agent-graphs` (confirms old canvas list renders), navigate to `/workflows` (confirms composite workflow list renders with empty state); confirm sidebar shows both "Agent Graphs" and "Workflows" nav items; record PASS — no new files  
  *Depends: T015*

---

## Phase W3 — Run-Tree Orchestration

**Goal**: A composite workflow run creates a parent `AgentRun` row, then sequentially invokes each member agent creating child `AgentRun` rows with `parent_run_id` set. The `/tree` endpoint returns the full run hierarchy.

**Order within phase**: Orchestrator class → Runner config + Internal router (parallel) → Run dispatch endpoints → Frontend store state (parallel with backend) → Syntax verification → CP-Wb

---

- [X] [T017] orchestrator.py — create `WorkflowOrchestrator` class: `__init__(workflow_id, parent_run_id, registry_url)`; `run_sequential(members, input_payload)` iterates `members` sorted by position — for each member: calls `_create_child_run` then `_dispatch_agent`, passes prior output as next input, PATCHes child run status on completion/failure, PATCHes parent run on all-complete or first-failure; `_create_child_run(agent_name, team, parent_run_id, input_msg)` POSTs to registry-api to create child AgentRun; `_dispatch_agent(agent_name, team, input_msg)` POSTs to agent pod `/chat` — `services/declarative-runner/orchestrator.py`  
  *Depends: T007 (cross-phase from W1)*

- [X] [T018] [P] declarative-runner config.py + main.py — add `COMPOSITE_WORKFLOW_ID: str | None = os.getenv("COMPOSITE_WORKFLOW_ID")` to config; add `WorkflowRunRequest` Pydantic model; add `POST /workflow-run` endpoint that returns 404 when `cfg.COMPOSITE_WORKFLOW_ID` is not set, otherwise fires `asyncio.create_task(orch.run_sequential(...))` and returns `{"status": "accepted", "parent_run_id": req.parent_run_id}` — `services/declarative-runner/config.py`, `services/declarative-runner/main.py`  
  *Depends: T017*

- [X] [T019] [P] routers/internal.py — in `POST /api/v1/internal/runs/start` handler: import `CompositeWorkflow`, `WorkflowMember`; detect `body.workflow_id` set branch; look up `CompositeWorkflow` by id (404 if missing, 422 if archived); create parent `AgentRun` with `agent_name=workflow.name`, `workflow_id=workflow.id`, `team=workflow.team`, `status="queued"`; fetch members ordered by position; fire `asyncio.create_task(_orchestrate(parent_run.id, workflow, members, body.trigger_payload or {}, db_url))`; return `{"run_id": str(parent_run.id)}` — `services/registry-api/routers/internal.py`  
  *Depends: T017*

- [X] [T020] routers/composite_workflows.py — replace the T006 stubs: implement `POST /{workflow_id}/runs` (validate members non-empty, orchestration==sequential, all positions non-null; create parent AgentRun; background orchestration task; return 202 `WorkflowRunStartResponse`); implement `GET /{workflow_id}/runs/{run_id}/tree` (fetch parent + children by `parent_run_id`; 404 if run.workflow_id != workflow_id; return `WorkflowRunTreeResponse`); add `GET /{workflow_id}/runs` list endpoint with `limit`/`offset`/`status` query params — `services/registry-api/routers/composite_workflows.py`  
  *Depends: T006, T019*

- [X] [T021] [P] workflowStore.ts — add `compositeWorkflowId: string | null` (initial `null`) and `compositeWorkflowName: string | null` to Zustand state; add `markCompositeWorkflowSaved(id, name, team): void` action; add `resetCompositeCanvas(): void` action (clears composite fields, leaves agent-graph canvas state intact) — `studio/src/stores/workflowStore.ts`  
  *Depends: T010 (cross-phase from W2); parallelizable with T017–T020 (different service)*

- [X] [T022] Python syntax verification — run `python3 -c "import ast; ast.parse(open('$f').read())"` on all Phase W3 Python files: `orchestrator.py`, `declarative-runner/config.py`, `declarative-runner/main.py`, `routers/internal.py`, `routers/composite_workflows.py`; fix any syntax error before proceeding — no new files  
  *Depends: T018, T019, T020*

---

## Checkpoint CP-Wb — Beta: Run-Tree Smoke

*Gate after W3. Must pass before starting Phase W4.*  
*Proves: POST /workflows/{id}/runs returns 202; GET /workflows/{id}/runs/{run_id}/tree returns parent with workflow_id set + children with parent_run_id set.*  
*Requires: at least one active agent deployed in the platform team. Script prints SKIP and exits 0 if none exist.*

- [X] [CP-Wb] Create and run Checkpoint Beta smoke script — `scripts/smoke-test-cp-wb-run-tree.sh`

```bash
#!/usr/bin/env bash
# Checkpoint Beta — Run-Tree Smoke (Decision 22)
# Proves: composite workflow run creates parent AgentRun and children with parent_run_id.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== Checkpoint Beta: Run-Tree Smoke ==="

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, time

B = 'http://localhost:8000/api/v1'

# Find active agents for smoke run
agents_r = httpx.get(f'{B}/agents', timeout=5)
agents = agents_r.json().get('items', agents_r.json())
active = [a for a in agents if a.get('status') == 'active']
if len(active) < 1:
    print('SKIP: no active agents; deploy at least one agent to run CP-Wb')
    exit(0)

team = active[0]['team']
same_team = [a for a in active if a['team'] == team]
agent1 = same_team[0]
agent2 = same_team[1] if len(same_team) > 1 else same_team[0]
ts = int(time.time())

# T-CPB-001: create composite workflow
r = httpx.post(f'{B}/workflows',
    json={'name': f'cpb-wf-{ts}', 'team': team, 'orchestration': 'sequential'},
    timeout=5)
assert r.status_code == 201, f'T-CPB-001 FAIL: {r.text}'
wf_id = r.json()['id']

# T-CPB-002: add two member agents with positions
r1 = httpx.post(f'{B}/workflows/{wf_id}/members',
    json={'agent_id': agent1['id'], 'position': 1}, timeout=5)
assert r1.status_code == 201, f'T-CPB-002a FAIL: {r1.text}'
r2 = httpx.post(f'{B}/workflows/{wf_id}/members',
    json={'agent_id': agent2['id'], 'position': 2}, timeout=5)
assert r2.status_code == 201, f'T-CPB-002b FAIL: {r2.text}'
print('PASS T-CPB-001/002: create workflow + 2 members')

# T-CPB-003: trigger sequential run → 202
r = httpx.post(f'{B}/workflows/{wf_id}/runs',
    json={'input_payload': {'message': 'smoke test'}, 'run_by': 'cpb'},
    timeout=5)
assert r.status_code == 202, f'T-CPB-003 FAIL: {r.text}'
run_id = r.json()['run_id']
print(f'PASS T-CPB-003: trigger run {run_id}')

# T-CPB-004: poll run tree for parent.workflow_id + children.parent_run_id
tree = None
for _ in range(20):
    tree_r = httpx.get(f'{B}/workflows/{wf_id}/runs/{run_id}/tree', timeout=5)
    if tree_r.status_code == 200:
        tree = tree_r.json()
        status = tree['parent']['status']
        nc = len(tree.get('children', []))
        print(f'  status={status}, children={nc}')
        if status in ('completed', 'failed') or nc >= 1:
            break
    time.sleep(3)

assert tree is not None, 'T-CPB-004 FAIL: run tree never returned 200'
assert tree['parent']['workflow_id'] == wf_id, \
    f'T-CPB-004 FAIL: parent.workflow_id={tree[\"parent\"].get(\"workflow_id\")} != {wf_id}'
for c in tree.get('children', []):
    assert c['parent_run_id'] == run_id, \
        f'T-CPB-004 FAIL: child.parent_run_id={c[\"parent_run_id\"]} != {run_id}'
print('PASS T-CPB-004: run tree parent.workflow_id + children.parent_run_id correct')

print('')
print('=== Checkpoint Beta: ALL PASS ===')
"

echo "PASS"
```

---

## Phase W4 — Studio Workflow Builder

**Goal**: Studio users can build composite workflows by picking from their existing agents, save the definition, trigger a run, and view the run-tree output in the browser.

**Order within phase**: Node + Modal → Builder page → Docs update → TS check → Manual verify

---

- [X] [T023] WorkflowMemberNode.tsx + AddAgentModal.tsx — `WorkflowMemberNode`: React Flow custom node (position badge, agent icon + agent_name, role chip, left+right handles; blue border when selected, slate border default); `AddAgentModal`: modal picker that calls `listAgents({ team })`, renders scrollable agent list with name/description/execution_shape, search input to filter by name, "Add to Workflow" button per row fires `onAdd(agent)`, supports multiple adds before close — `studio/src/nodes/WorkflowMemberNode.tsx`, `studio/src/components/AddAgentModal.tsx`  
  *Depends: T011, T021*

- [X] [T024] WorkflowBuilderPage.tsx — full-screen React Flow canvas with `nodeTypes = { workflow_member: WorkflowMemberNode }`; on load with `:id` param: `getCompositeWorkflow(id)` then `deserializeCompositeWorkflow`; on load without `:id`: empty canvas with onboarding prompt; toolbar: "Add Existing Agent" button → `AddAgentModal(team=currentTeam)`; `onAdd` callback adds `WorkflowMemberNode` with next sequential position; Save: first save opens name+orchestration-mode modal then `createCompositeWorkflow` + `addWorkflowMember` per node, subsequent saves `PATCH /workflows/{id}/members`; "Run Workflow" button: `triggerWorkflowRun` → poll `getWorkflowRunTree` → render run-tree status panel (parent status + child rows: agent name / status / latency); "Back to Workflows" breadcrumb; uses `compositeWorkflowId`/`compositeWorkflowName` from `workflowStore` — `studio/src/pages/WorkflowBuilderPage.tsx`  
  *Depends: T023, T021, T014*

- [X] [T025] docs/experience/playground.md — add section documenting composite workflow builder UX: WorkflowBuilderPage canvas (agent picker, position ordering, save flow), run-tree status panel (parent + child run rows, status badges, latency), routing changes (/workflows list, /workflows/new, /workflows/:id/builder), empty state and onboarding copy — `docs/experience/playground.md`  
  *Depends: T024*

- [X] [T026] TypeScript validation — run `cd studio && npx tsc --noEmit`; zero errors across all modified/created `.ts` and `.tsx` files — no new files  
  *Depends: T024*

- [X] [T027] MANUAL browser verification — open `/workflows/new`, add two agents from the modal, confirm they appear as WorkflowMemberNode nodes on the canvas; save the workflow (confirm name modal appears, confirm network request `POST /api/v1/workflows` fires); navigate to `/workflows/:id/builder` and confirm nodes reload; trigger a run and confirm run-tree panel appears with parent status row; record PASS in PR description — no new files  
  *Depends: T026*

---

## Phase W5 — E2E Tests + Image Bumps

**Goal**: Prove the composite workflow feature end-to-end with a runnable bash test suite. Bump all three affected image tags. Confirm no regression in existing suites.

**Order within phase**: Suite creation → Register + image bumps (parallel) → Verification runs (parallel) → CP-Wc

---

- [X] [T028] suite-29-workflow-composite.sh — create e2e suite following suite-28 pattern: 10 test cases T-S29-001 through T-S29-010; pass()/fail() counters; cleanup trap; kubectl exec python3 httpx assertions for: composite workflow CRUD happy path, duplicate name 409, add member same team (201), add member different team (422), trigger sequential run (202), run tree returns parent+children with correct parent_run_id/workflow_id, child AgentRuns carry workflow_id=NULL, parent AgentRun carries workflow_id set, remove member decrements member_count, archive workflow then run trigger → 422 or 404; script exits non-zero on any FAIL; chmod +x applied — `scripts/e2e/suite-29-workflow-composite.sh`  
  *Depends: T020*

- [X] [T029] [P] run-all.sh — add `run_suite "Suite 29: Composite Workflow (Decision 22)" "suite-29-workflow-composite.sh"` immediately after the suite-28 line — `scripts/e2e/run-all.sh`  
  *Depends: T028*

- [X] [T030] [P] deploy-cpe2e.sh + values.yaml — bump `REGISTRY_API_TAG` from `0.2.55` to `0.2.56`; bump `STUDIO_TAG` from `0.1.42` to `0.1.43`; bump `DECLARATIVE_RUNNER_TAG` from `0.1.6` to `0.1.7`; update header comment to include "Decision 22 — composite workflows (rename agent_graphs, workflow members, run-tree orchestration)"; mirror the three same tag values in `charts/agentshield/values.yaml` — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`  
  *Depends: T028*

- [X] [T031] [P] Run and verify suite-29 — build + deploy images (`bash scripts/deploy-cpe2e.sh` + `helm upgrade --install ...`); run `bash scripts/e2e/suite-29-workflow-composite.sh`; all 10 test cases T-S29-001…T-S29-010 must PASS; suite exit code must be 0 — no new files  
  *Depends: T028, T030*

- [X] [T032] [P] Run regression verification — run `bash scripts/e2e/suite-2-lifecycle.sh`, `bash scripts/e2e/suite-8-playground.sh`, `bash scripts/e2e/suite-14-consumer-chat.sh` against the new image; all three suites must exit 0; confirms URL rename in T008 is correct and no other regressions — no new files  
  *Depends: T008, T030*

- [X] [T033] Final TypeScript build verification — `cd studio && npx tsc --noEmit`; zero errors; final confirmation that all W2 + W4 TypeScript changes compile clean together — no new files  
  *Depends: T024, T030*

---

## Checkpoint CP-Wc — Gamma: Full End-to-End

*Gate after W5. Confirms complete feature delivery: images built, deployed, suite-29 green, no regressions, TypeScript clean.*

- [X] [CP-Wc] Create and run Checkpoint Gamma full e2e script — `scripts/smoke-test-cp-wc-full-e2e.sh`

```bash
#!/usr/bin/env bash
# Checkpoint Gamma — Full End-to-End (Decision 22: executable = Agent | Workflow)
# Proves: images built, helm deployed, suite-29 green (10/10),
# regression suites 2/8/14 green, TypeScript build clean.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== Checkpoint Gamma: Full End-to-End ==="
echo "    Namespace: ${NAMESPACE}"
echo "    Repo:      ${REPO_ROOT}"
echo ""

# Step 1: build and push updated images
echo "--> Step 1: Building and pushing images..."
bash "${REPO_ROOT}/scripts/deploy-cpe2e.sh"
echo "    Images built."

# Step 2: helm upgrade
echo "--> Step 2: Deploying via Helm (--wait --timeout 5m)..."
helm upgrade --install agentshield "${REPO_ROOT}/charts/agentshield" \
  --namespace "${NAMESPACE}" --wait --timeout 5m
echo "    Deploy complete."

# Step 3: verify pods are running
echo "--> Step 3: Pod status..."
kubectl get pods -n "${NAMESPACE}"

# Step 4: run new composite workflow e2e suite
echo ""
echo "--> Step 4: Suite 29 — Composite Workflow (T-S29-001…T-S29-010)..."
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/e2e/suite-29-workflow-composite.sh"
echo "    Suite 29: PASS"

# Step 5: regression suites — canvas graph URL rename must not break these
echo ""
echo "--> Step 5: Regression — Suite 2 (Agent Lifecycle)..."
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/e2e/suite-2-lifecycle.sh"
echo "    Suite 2: PASS"

echo "--> Step 5: Regression — Suite 8 (Playground)..."
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/e2e/suite-8-playground.sh"
echo "    Suite 8: PASS"

echo "--> Step 5: Regression — Suite 14 (Consumer Chat)..."
NAMESPACE="${NAMESPACE}" bash "${REPO_ROOT}/scripts/e2e/suite-14-consumer-chat.sh"
echo "    Suite 14: PASS"

# Step 6: TypeScript build clean
echo ""
echo "--> Step 6: TypeScript build verification..."
cd "${REPO_ROOT}/studio"
npx tsc --noEmit
echo "    TS: PASS (zero errors)"

echo ""
echo "=== Checkpoint Gamma: ALL PASS ==="
echo "PASS"
```

---

## Implementation Notes

### Migration order (strict)

0026 must fully apply before 0027 runs. Migration 0027 depends on the `agent_graphs` table existing. Alembic's sequential version numbering enforces this via the init container on registry-api startup.

### Stale test data

Suite-29 creates composite workflows with timestamped names (e.g., `cpb-wf-1720166400`). Add them to the post-run cleanup list in `run-all.sh` if they accumulate.

### Breaking change — canvas URL

The canvas endpoint moves from `/api/v1/workflows/` to `/api/v1/agent-graphs/`. Any caller outside this repo (CI scripts, Postman collections, SDK examples) that targets the old URL will receive 404 after the registry-api:0.2.56 image is deployed. T008 updates the e2e suites; notify external callers separately.

### Deferred (out of scope for this plan)

- Supervisor + handoff orchestration modes (column and CHECK constraint are in place; orchestrator raises 422 for these modes)
- Deploy-controller workflow pod creation
- SSE streaming for workflow run tree
- Workflow publish gate (mirrors Decision 20 agent lifecycle)
- Trigger wiring for `workflow_id` (FK + CHECK are added in 0027; scheduler needs a one-line change to pass `workflow_id` to `internal/runs/start`)

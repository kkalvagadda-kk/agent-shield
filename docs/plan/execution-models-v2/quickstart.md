# WS-0 Quickstart — implement + verify

Cold-start guide for an agent with zero conversation history. Read `research.md` first (it holds the
three code-vs-doc corrections), then `plan.md` §7 for the task order. This file is the run sheet.

## 0. Confirm the migration head before writing anything
```bash
cd /Users/kkalyan/repo/agent-platform
ls services/registry-api/alembic/versions/ | sort | tail -3
# EXPECT the head to be 0057_playground_run_user_feedback.py.
# The WS-0 migration is 0058 (down_revision="0057"). The design doc's "0056/0055" is stale — ignore it.
```

## 1. Build order (plan §7)
```
T1  migration 0058 + models (agents+workflows agent_class NOT NULL + CHECK)
T2  schemas + routers (agent_class create/update/response; wire update_agent orphan; workflow warnings)
T3  deploy-controller manifest_builder — delete the NULL coalesce (:128)
T4  durable_dispatch.py shared helper + shape-aware _dispatch_and_complete + /internal/runs/{id}/step-update
T5  reactive workflow awaited+capped + _park_or_fail fail-closed + save-time warn
T6  Studio — split wizard (Shape/Trigger/Class), Settings class, workflow Save-modal class, client, spec reword
T7  Vitest + Playwright
T8  scripts/e2e/suite-54 + register in run-all.sh
T9  bump tags (deploy-cpe2e.sh + values.yaml) + deploy + gap-ledger note
```
Prove Slice A (authoring: T1–T3, T6) save→reload before starting Slice B (dispatch: T4–T5).

## 2. Per-change static verification (run after each backend edit)
```bash
cd services/registry-api
python3 -c "import ast; ast.parse(open('models.py').read())"
python3 -c "import ast; ast.parse(open('schemas.py').read())"
python3 -c "import ast; ast.parse(open('durable_dispatch.py').read())"
python3 -c "import ast; ast.parse(open('routers/internal.py').read())"
python3 -c "import ast; ast.parse(open('routers/composite_workflows.py').read())"
python3 -c "import ast; ast.parse(open('routers/agents.py').read())"
python3 -c "import ast; ast.parse(open('workflow_orchestrator.py').read())"
python3 -c "import ast; ast.parse(open('alembic/versions/0058_agent_class_not_null_and_workflows_agent_class.py').read())"
# ORM/schema change → confirm mappers configure:
python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('mappers ok')"
python3 -c "import ast; ast.parse(open('../deploy-controller/manifest_builder.py').read())"
```

## 3. Parity + orphan greps (must hold before you call WS-0 done)
```bash
# ONE /run POST implementation (parity core):
grep -rn "dispatch_durable_run" services/registry-api/routers/playground.py services/registry-api/routers/internal.py
#   → both files call it.
grep -rn '"/run"' services/registry-api/routers/playground.py services/registry-api/routers/internal.py
#   → NO output (the raw POST literal lives only in durable_dispatch.py).

# coalesce band-aid gone (M3):
grep -n 'or "user_delegated"' services/deploy-controller/manifest_builder.py
#   → NO output.

# update_agent orphan wired:
grep -n "agent.agent_class = body.agent_class" services/registry-api/routers/agents.py
#   → one hit.

# every new symbol has a caller:
grep -rn "compute_reactive_approval_warnings\|_park_or_fail\|step-update" services/registry-api/routers services/registry-api/workflow_orchestrator.py
```

## 4. Frontend verification (plan T6/T7)
```bash
cd studio
npm run typecheck            # must be clean
npm run test                 # Vitest — CreateAgentPage, AgentDetailPage, WorkflowBuilderPage green
grep -rn "agent_class" src/pages/CreateAgentPage.tsx src/pages/AgentDetailPage.tsx src/pages/WorkflowBuilderPage.tsx src/api/registryApi.ts
#   → wiring present in all four.
```

## 5. Deploy (plan T9 — never bare helm)
```bash
# Bump BOTH files first:
#   scripts/deploy-cpe2e.sh: REGISTRY_API_TAG 0.2.155→0.2.156, DEPLOY_CONTROLLER_TAG 0.1.35→0.1.36, STUDIO_TAG 0.1.126→0.1.127
#   charts/agentshield/values.yaml: registry-api tag (:588), deploy-controller tag (:650), studio tag (:899)
#   DECLARATIVE_RUNNER_TAG stays 0.1.37 (unchanged in WS-0).
grep -n "0.2.156\|0.1.36\|0.1.127" scripts/deploy-cpe2e.sh charts/agentshield/values.yaml   # each in BOTH files
bash scripts/deploy-cpe2e.sh
kubectl rollout status deploy/agentshield-registry-api -n agentshield-platform
kubectl rollout status deploy/agentshield-deploy-controller -n agentshield-platform
kubectl rollout status deploy/agentshield-studio -n agentshield-platform
```

## 6. Golden-path acceptance (run last — the real doors)
```bash
# Backend golden path + parity (plan T8):
bash scripts/e2e/suite-54-agent-class-shape-dispatch.sh      # T-S54-001..010 all PASS
grep -n "suite-54" scripts/e2e/run-all.sh                    # registered

# Browser golden path (plan T7):
bash scripts/studio-e2e.sh                                   # create-agent-wizard, agent-detail-modes, workflow-builder green
```

## 7. Manual smoke (the user-observable end state — DoD)
1. Studio → **Create Agent** → No-code. Confirm **three** independent selectors: Shape, Trigger, Class.
   Pick **Durable** + **Scheduled** → Class auto-selects **daemon** (override to user_delegated works).
   Create → open the agent → **Settings** shows Class = daemon → reload → still daemon.
2. Studio → **Workflows → Builder** → add members → **Save**. Save modal shows a **Class** selector.
   Save daemon → reload the builder → class persisted. Set Shape=reactive with a high-risk-tool member →
   a non-blocking **warning toast** appears.
3. Fire a scheduled **durable** agent (or `POST /api/v1/internal/runs/start`) → the run shows real
   `run_steps` (dispatch hit `/run`); a **reactive** agent's run has plain `output`, no `run_steps`.

## 8. Definition-of-Done statement to report back
- **Journey proven:** Playwright `create-agent-wizard` / `agent-detail-modes` / `workflow-builder` drive the
  real browser; suite-54 drives `/internal/runs/start` (the real dispatch door).
- **Save→reload→assert:** suite-54 T-S54-004 (workflow) + Playwright reloads (agent class) confirm persistence.
- **No orphan:** §3 greps — `dispatch_durable_run`, `/internal/runs/{id}/step-update`, `_park_or_fail`,
  `compute_reactive_approval_warnings`, wired `update_agent.agent_class` all have shipped callers.
- **Parity:** one `/run` POST (grep); coalesce deleted (grep).
- **Gap ledger:** WS-1/WS-2 deferrals recorded in `plan.md` §8 + `docs/testing/manual-ui-e2e-test-plan.md`.

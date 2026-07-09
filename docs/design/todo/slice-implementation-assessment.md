# Slice 1–5 Implementation Assessment

**Date:** 2026-07-09  
**Status:** Post-fix (B1–B9 fixed, G6/G8/G19/G20/G22/G24/G25 fixed 2026-07-09; registry-api:0.2.114, studio:0.1.96)  
**Scope:** Agent + Workflow lifecycle from creation through production consumption

---

## Executive Summary

The platform has the skeleton of a full agent/workflow lifecycle (create → version → deploy → eval → publish → catalog → consume). The happy path for **agents** works end-to-end — all 7 blocking bugs (B1–B7) are fixed and deployed (0.2.111/0.1.92). **Workflows** are ~70% complete — publish has eval gates (B4/B5 fixed), deploy gates work, but publish UI button (G11) is still missing. The versioning model is clean for agents (orphan version creation removed in B3). Evaluation now correctly targets **deployments** (G20/G22 fixed) — the UI lists running sandbox deployments, and the backend auto-resolves agent_name + version_id from the deployment. Remaining: workflow eval Job still invokes a single agent instead of the orchestration endpoint (G21), and 18 open gaps across UX, RBAC, and lifecycle completeness.

---

## Slice 1: Deployment Lifecycle + Shared Overview

### What Works
- Agent sandbox deploy + auto-version creation (config snapshot)
- Full lifecycle actions: suspend → resume → terminate → upgrade
- Deploy-controller reconciles sandbox + production pods
- TTL auto-cleanup worker
- DeployModal (replicas + TTL) from list and versions tab
- Workflow version/deployment tables exist with lifecycle actions

### Bugs
| ID | Severity | Description |
|---|---|---|
| ~~B1~~ | CRITICAL | ✅ FIXED — `ProductionDeployment` CHECK constraint expanded via migration 0047. |
| ~~B2~~ | HIGH | ✅ FIXED — Rollback now copies `llm_secret_name` from live deployment. |

### Gaps
| ID | Description | Impact |
|---|---|---|
| G1 | Agent and workflow overview pages are separate implementations, not a shared `kind`-aware component | Maintenance burden; feature drift between agent/workflow |
| G2 | Workflow deployment has no memory tab | Can't inspect workflow-level memory |
| G3 | Workflow deployment has no upgrade action | Can't roll a workflow deployment to a new version without terminate+redeploy |
| G4 | `suspended_at` timestamp not tracked | Can't report suspension duration or audit when suspensions happen |

---

## Slice 2: Version Management UX

### What Works
- Versions tab as list (agent + workflow)
- Per-row Deploy (opens modal with version_id)
- Per-row Delete with cascade-terminate of dependent deployments
- Delete blocks if version is published (agent)
- Config snapshot stored in `agent_versions.config` JSONB

### Bugs
| ID | Severity | Description |
|---|---|---|
| B3 | MEDIUM | `DeployAgentPage` Step 1 creates a version; Step 2 ignores it and auto-creates ANOTHER. Users create 2 versions per deploy (one orphaned). |

### Gaps
| ID | Description | Impact |
|---|---|---|
| G5 | No config viewer on version row | Users can't see what instructions/tools/model were snapshotted — can't verify what they're deploying |
| ~~G6~~ | ~~`DeployAgentPage` still shows "Step 1: Create Version" with image tag input~~ | ✅ FIXED — Step 1 removed; single Deploy button |
| G7 | No version diff (compare v1 vs v2) | Users iterating on agents can't see what changed between versions |
| G8 | Workflow version `eval_passed` cannot be updated post-creation (no PATCH endpoint) | Even if workflow eval worked, there's no way to mark a workflow version as passing |

---

## Slice 3: Workflow Artifact Page + Rich Deploy

### What Works
- `/workflows/:id` page with Deployments/Versions/Settings tabs
- WorkflowMiniGraph in deployment overview
- Version snapshot captures members + edges + orchestration

### Bugs
| ID | Severity | Description |
|---|---|---|
| B4 | MEDIUM | `publish_workflow()` has NO eval gate. Unevaluated workflow publishable to catalog. |
| B5 | MEDIUM | `deploy_workflow()` has NO eval gate for production environment. |

### Gaps
| ID | Description | Impact |
|---|---|---|
| G9 | No member-dependency check before workflow deploy | Workflow can be "deployed" when member agents aren't running → immediate runtime failure |
| G10 | No member-health display on workflow overview | User has no visibility into which members are degraded |
| G11 | No Publish button on WorkflowDetailPage | Backend supports workflow publish but no UI entry point exists |
| G12 | No per-step execution breakdown in workflow overview | Can't see which step in the workflow is slow/failing |

---

## Slice 4: Production/Catalog Parity

### What Works
- CatalogPage grid with type filters (agent, workflow, tool, skill)
- CatalogDetailPage with versions/runs/stats/deploy tabs
- Admin approval UI (promote/reject with risk assessment)
- Production deploy from catalog
- Production lifecycle actions (suspend/resume/upgrade) — except terminate

### Bugs
| ID | Severity | Description |
|---|---|---|
| B1 | CRITICAL | (Same as Slice 1) Production terminate crashes on CHECK constraint. |

### Gaps
| ID | Description | Impact |
|---|---|---|
| G13 | No `GET /catalog/{id}/deployments/{did}` — can't fetch single production deployment | Frontend must list all then filter client-side |
| G14 | RBAC table exists but zero enforcement on catalog actions | Any authenticated user can deploy/terminate any catalog artifact |
| G15 | Approve does not auto-deploy. Requires separate manual deploy step. | Extra friction; may be intentional but undocumented |

---

## Slice 5: Memory Isolation + Deployment Chat + TTL

### What Works
- Per-deployment memory isolation (`deployment_id` column, suite-43 verified)
- Deployment-scoped chat (`/agents/:name/d/:depId/chat`)
- TTL auto-cleanup worker in deploy-controller
- Catalog chat (`/catalog/:artifactId/chat`)

### Gaps
| ID | Description | Impact |
|---|---|---|
| G16 | No chat surface for workflow deployments | Workflows with interactive steps have no consumer-facing UI |
| G17 | No workflow deployment memory | WorkflowDeploymentOverviewPage has no Memory tab |

---

## Cross-cutting: Versioning Model

### What Works
- Agent: auto-create version on deploy (backend snapshots config)
- Agent: explicit version creation (SDK agents with image_tag)
- Agent: `eval_passed` auto-set from EvalRun when score >= 0.7
- Workflow: explicit snapshot via POST /workflows/{id}/versions

### Bugs
| ID | Severity | Description |
|---|---|---|
| B6 | LOW | Workflow eval Job targets single agent, not workflow orchestration. Eval results for workflows are meaningless. |

### Gaps
| ID | Description | Impact |
|---|---|---|
| G18 | `adversarial_eval_passed` has no auto-set path and no UI trigger | High/critical-risk agents require manual PATCH — no user-facing flow |
| G19 | Workflow version eval_passed cannot be updated (no PATCH) | Even if eval worked, can't mark workflow version passed |

---

## Cross-cutting: Evaluation Architecture (Design Flaw)

### Current State
- `EvalRun` targets `agent_name` or `workflow_id` — the **artifact**
- Eval runner synthesizes a call against the agent's current metadata
- Results are linked to agent + optionally to `agent_version_id`

### Problem
Artifact is mutable. Between eval-start and eval-finish, agent config can change. Results describe a stale state. No way to eval "version N specifically" without deploying it first.

### Correct Model
| Aspect | Before Fix | After Fix (0.2.111 / 0.1.92) |
|---|---|---|
| Target | Agent name (artifact) | Sandbox deployment (pinned version) ✅ |
| Invocation | Synthetic call against metadata | Real invocation of deployed runtime |
| Result linkage | agent_name + version_id (optional) | deployment_id → version_id (deterministic) ✅ |
| Workflow eval | Calls single member agent | Invokes workflow deployment endpoint (DB wired incl. workflow_version_id, Job still targets single agent) |
| UI entry point | Datasets page dropdown picks agent name | Datasets page dropdown picks running deployment ✅ |
| Guarantee | None — config may change mid-eval | Deployment pins version; eval is reproducible ✅ |

### Impact
- **~~Bug G20:~~** ✅ FIXED — `EvalRunCreate` accepts `sandbox_deployment_id` / `workflow_deployment_id`; UI lists running sandbox deployments; backend auto-resolves `agent_name` + `version_id` from deployment
- **Bug G21:** Workflow eval still targets single agent in the K8s Job (not the full orchestration endpoint) — DB linkage (`workflow_deployment_id`) is wired but the eval-runner Job needs a workflow invocation mode
- **~~Bug G22:~~** ✅ FIXED — Eval dropdown now lists deployments (not agents), making it contextual to what's actually running

---

## Test Coverage Gaps (Critical)

| Flow | Backend E2E | Playwright | Vitest |
|---|---|---|---|
| Catalog (list/detail/deploy) | NONE | NONE | NONE |
| Consumer Chat UI | API-only | NONE | NONE |
| All Admin pages (5) | partial | NONE | NONE |
| Tools/Skills/Providers CRUD | NONE | NONE | NONE |
| Observability pages (3) | bypasses API | NONE | NONE |
| DeployAgentPage flow | API-only | NONE | NONE |
| DatasetsPage / EvalResultsPage | API-only | NONE | NONE |

---

## Consolidated Bug/Gap Registry

### Bugs (9) — ALL FIXED 2026-07-09 (registry-api 0.2.112, studio 0.1.94)
| ID | Sev | Slice | Status | Description | Fix |
|---|---|---|---|---|---|
| B1 | CRIT | 1,4 | **FIXED** | ProductionDeployment CHECK constraint missing terminating/terminated | Migration 0047 (constraint name: `production_deployments_status_check`) |
| B2 | HIGH | 1 | **FIXED** | Rollback loses llm_secret_name | deployments.py:646 copies from live deployment |
| B3 | MED | 2 | **FIXED** | DeployAgentPage creates orphan version | Removed Step 1 "Create Version"; single Deploy button |
| B4 | MED | 3 | **FIXED** | Workflow publish has no eval gate | composite_workflows.py:623 checks latest version eval_passed |
| B5 | MED | 3 | **FIXED** | Workflow deploy has no eval gate | composite_workflows.py:837 gates production on eval_passed |
| B6 | LOW | Cross | **FIXED** | Eval can't target deployments | Migration 0048 adds sandbox_deployment_id + workflow_deployment_id to eval_runs; UI lists deployments (0.1.92); backend auto-resolves agent_name from deployment (0.2.111) |
| B7 | LOW | 1 | **FIXED** | Agent soft-delete doesn't cascade | agents.py:336 terminates active sandbox deployments |
| B8 | HIGH | 1 | **FIXED** | Deploy 500 when duplicate AssetGrant rows exist for a tool/team | deployments.py grant queries used `scalar_one_or_none()` → `MultipleResultsFound`; fixed to `select(id).limit(1)` existence check (0.2.112) |
| B9 | LOW | 2 | **FIXED** | DeployAgentPage was an unnecessary intermediate page (extra click to deploy) | Removed DeployAgentPage; deploy is now a modal (DeployModal) on AgentDetailPage + MyAgentsPage. No separate route. (0.1.94) |

### Additional UX fixes (2026-07-09)
| Fix | Description |
|---|---|
| Upgrade modal | Replaced inline clipped version picker with proper modal — sandbox (DeploymentActions) + production (CatalogDetailPage) |
| Rollback button | Added to DeploymentActions — one-click revert via POST /rollback, visible when `previous_version_id` set |
| Icon-only actions | Suspend/Resume/Terminate changed to icon-only buttons (Trash2 for terminate) with title tooltips across all 4 pages: DeploymentActions, CatalogDetailPage, WorkflowDetailPage, WorkflowDeploymentOverviewPage |
| Eval targets deployments | DatasetsPage "Run Eval" modal now lists running sandbox deployments (agent + workflow) instead of agent/workflow names. New endpoint: `GET /api/v1/deployments/workflows` for global workflow deployment listing. `EvalRunCreate.agent_name` made optional — auto-resolved from deployment. |
| Playground lists deployments | VersionSelector (playground sidebar) now lists running sandbox deployments instead of agents. WorkflowSelector now lists running sandbox workflow deployments instead of workflows. Both show "no deployments" empty state when none are running. (studio 0.1.93) |
| Deploy is now inline | Removed DeployAgentPage (separate `/deploy` route). Deploy is a modal (DeployModal with replicas + TTL) triggered directly from AgentDetailPage header and MyAgentsPage cards. Workflow and production already deployed inline — no change needed. (studio 0.1.94) |
| Workflow eval publish path | EvalResultsPage now detects agent vs workflow evals. Workflow evals show "Mark Workflow Version Passed" + "Publish Workflow" + "Back to Workflow" (navigates to `/workflows/{id}`). Migration 0049 adds `workflow_version_id` to eval_runs. Backend auto-promotes workflow version `eval_passed` on passing score. PATCH endpoint for workflow versions added. Re-run forwards deployment IDs. (0.2.113/0.1.95) |
| Playground promotion path | PlaygroundPage sidebar now shows "Mark Version Passed" + "Publish Agent/Workflow" buttons when a deployment is selected. Both agent and workflow promotion work from the manual eval console. VersionSelector/WorkflowSelector now expose deployment version_id. (0.1.96) |
| Approval queue fixes | AdminPublishRequestsPage: (1) workflow names now resolve correctly (was using AgentGraphs API instead of CompositeWorkflows), (2) asset names/teams resolved server-side in `PublishRequestResponse.asset_name/asset_team` (eliminates visibility filter issue for admin). Backend: (3) rejected workflows now revert `publish_status` to `private` (was only agents). (0.2.114/0.1.96) |

### Gaps (24)
| ID | Slice | Effort | Status | Description |
|---|---|---|---|---|
| G1 | 1 | Large | OPEN | Overview pages not shared/kind-aware |
| G2 | 1 | Small | OPEN | Workflow deployment no memory tab |
| G3 | 1 | Small | OPEN | Workflow deployment no upgrade action |
| G4 | 1 | Small | OPEN | No suspended_at timestamp |
| G5 | 2 | Medium | OPEN | No config viewer on version row |
| G6 | 2 | Medium | **FIXED** | DeployAgentPage confusing for declarative agents (removed Step 1) |
| G7 | 2 | Medium | OPEN | No version diff |
| G8 | 2 | Small | **FIXED** | WorkflowVersion PATCH endpoint added (`PATCH /workflows/{id}/versions/{vid}` with eval_passed + notes) (0.2.113) |
| G9 | 3 | Medium | OPEN | No member-dependency check on workflow deploy |
| G10 | 3 | Medium | OPEN | No member-health display |
| G11 | 3 | Small | OPEN | No Publish button on WorkflowDetailPage |
| G12 | 3 | Large | OPEN | No per-step execution breakdown |
| G13 | 4 | Small | OPEN | No single production deployment GET |
| G14 | 4 | Large | OPEN | RBAC no enforcement |
| G15 | 4 | N/A | OPEN | Approve doesn't auto-deploy (may be intentional) |
| G16 | 5 | Medium | OPEN | No workflow deployment chat |
| G17 | 5 | Small | OPEN | No workflow deployment memory tab |
| G18 | Cross | Medium | OPEN | adversarial_eval_passed no auto-set |
| G19 | Cross | Small | **FIXED** | WorkflowVersion eval_passed PATCH + auto-promote from passing eval (migration 0049 adds workflow_version_id to eval_runs; eval_runner auto-sets eval_passed on workflow version when score >= 0.7) (0.2.113) |
| G20 | Cross | Large | **FIXED** | Eval targets artifact not deployment (migration 0048 + UI lists deployments in 0.1.92 + backend auto-resolves in 0.2.111) |
| G21 | Cross | Large | OPEN | Workflow eval Job still targets single agent — DB linkage wired (`workflow_deployment_id`) but eval-runner needs workflow invocation mode |
| G22 | Cross | Medium | **FIXED** | Eval dropdown now lists running sandbox deployments instead of agent names (0.1.92) |
| G23 | Cross | Medium | OPEN | No "Fork from Catalog" to sandbox (lifecycle loop incomplete) |
| G24 | 1 | Small | **FIXED** | No rollback button in UI → added Rollback button in DeploymentActions (shows when previous_version_id set) |

---

## Recommended Fix Order

**Phase 1 — DB crashes + data loss (blocks production use):**
1. B1 — Migration to fix ProductionDeployment CHECK constraint
2. B2 — Copy llm_secret_name on rollback

**Phase 2 — Workflow parity (blocks workflow publish path):**
3. B4+B5 — Add eval gate to workflow publish + deploy
4. G8+G19 — Add PATCH endpoint for WorkflowVersion.eval_passed
5. G11 — Add Publish button to WorkflowDetailPage

**Phase 3 — UX confusion (blocks user confidence):** ✅ DONE
6. ~~B3+G6 — Remove/simplify DeployAgentPage for declarative agents~~
7. G5 — Config viewer in version rows
8. ~~G22 — Eval dropdown now lists running deployments~~

**Phase 4 — Eval architecture (blocks workflow eval + reproducibility):** PARTIALLY DONE
9. ~~G20 — Eval targets deployment not artifact~~ ✅
10. B6+G21 — Workflow eval invokes deployment endpoint (DB wired, Job still targets single agent)

**Phase 5 — Remaining gaps (quality of life):**
11. G2+G17 — Workflow deployment memory tab
12. G3 — Workflow deployment upgrade action
13. G9+G10 — Member-dependency check + health display
14. G7 — Version diff

**Phase 6 — Lifecycle completeness (catalog → sandbox loop):**
15. G23 — "Fork from Catalog" to sandbox (completes the lifecycle loop)

**Deferred (large, lower impact):**
- G1 — Shared overview component (refactor, no user-facing impact)
- G12 — Per-step breakdown (nice-to-have)
- G14 — RBAC enforcement (separate initiative)
- G16 — Workflow deployment chat

---

## G23: Fork from Catalog to Sandbox

### Problem
Lifecycle is one-way: sandbox → publish → catalog. No reverse path. A developer who sees a published artifact in the catalog cannot fork it back to sandbox for iteration. They'd have to manually recreate the agent with the same config.

### Correct Model
| Aspect | Description |
|---|---|
| Endpoint | `POST /api/v1/catalog/{artifact_id}/fork` |
| Input | Optional: target team, new name (defaults to `{original}-fork`) |
| Behavior | Creates new Agent in caller's team, seeded from the published version's `config_snapshot` (instructions, tools, execution_shape, llm_provider_id). No versions created — starts fresh for iteration. |
| UI | "Fork to Sandbox" button on CatalogDetailPage |
| Post-fork flow | Developer iterates → deploys to sandbox → evals → re-publishes (new publish request, admin review) |
| Guard | Requires AssetGrant for the artifact (only teams with access can fork) |

### Lifecycle Loop (complete)
```
create → version → deploy → eval → publish → [admin approve] → catalog
                                                                    ↓
                                                              fork to sandbox
                                                                    ↓
                                                          iterate → re-publish
```

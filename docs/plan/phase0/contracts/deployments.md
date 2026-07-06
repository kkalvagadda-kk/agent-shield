# Contract — POST /api/v1/agents/{name}/deploy (Slice B)

Handler: `deploy_agent` in `services/registry-api/routers/deployments.py`. Body: `DeploymentCreate`. Header: `X-User-Team` (optional; defaults to `agent.team`).

**Request body** (`DeploymentCreate`) — `environment` pattern widened to include `sandbox`:
```json
{ "version_id": "…", "replicas": 1, "environment": "sandbox" }
```
`environment` ∈ `production | staging | sandbox` (Pydantic). `canary` remains in the DB CHECK but is not offered by this request schema.

## Pre-flight gates (order)
1. 404 if agent/version missing.
2. **403** `deployer_not_in_owner_team` unless deployer team matches owner or has a cross-team `AssetGrant`.
3. **422** `critical_risk_tool_not_deployable` if any tool is `critical`.
4. **422** `tool_grants_missing` if any bound tool lacks an active grant for the deployer team.
5. **eval gate — PRODUCTION ONLY (changed):** when `environment == "production"`:
   - **422** if `version.eval_passed == false`.
   - **422** if the version/agent has `high`/`critical`-risk tools and `version.adversarial_eval_passed == false`.
   For `sandbox` / `staging` / `canary` these two checks are **skipped** (ungated).

## 201 Created — sandbox deploy, ungated
```json
{
  "id": "…", "agent_id": "…", "version_id": "…",
  "environment": "sandbox", "status": "pending", "replicas": 1,
  "k8s_namespace": "agents-platform", "k8s_deployment_name": null,
  "deployed_at": "2026-07-03T18:00:00Z"
}
```
`environment: "sandbox"` is accepted by the `ck_deployments_env` CHECK after migration `0015`. A rejected value would surface as a 500 on INSERT — so a 201 here also proves the constraint change landed.

## 422 — production deploy, eval not passed
```json
{ "detail": "Version '…' has not passed evaluation (eval_passed=False). Run evals before deploying to production." }
```

## 422 — production deploy, adversarial not passed (risky tools)
```json
{ "detail": "Version '…' has not passed adversarial evaluation (adversarial_eval_passed=False). Run adversarial evals before deploying to production." }
```

## Behavior matrix
| `environment` | `eval_passed` | risky + `adversarial_passed=false` | Result |
|---|---|---|---|
| `sandbox` | false | — | **201** (ungated) |
| `sandbox` | false | yes | **201** (ungated) |
| `staging` | false | — | **201** (ungated) |
| `production` | false | — | **422** eval |
| `production` | true | yes | **422** adversarial |
| `production` | true | no | **201** |

Tests: T-S17-001 (sandbox 201 + CHECK), T-S17-002 (production 422), T-S17-007 (create-without-eval then sandbox 201); existing T-S6-LG-001/002 (production adversarial gate, deploy to default `production`).

## Not changed
Rollback, list, and the deploy-controller `PATCH /deployments/{id}` are unaffected. Sandbox deployments reconcile exactly like any other environment (the controller does not branch on `environment`). The playground `/stream` selects the newest `status='running'` deployment regardless of environment.

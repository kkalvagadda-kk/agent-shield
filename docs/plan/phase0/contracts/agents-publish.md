# Contract — POST /api/v1/agents/{name}/publish (Slice B)

Handler: `publish_agent` in `services/registry-api/routers/agents.py`. Header: `X-User-Sub` (defaults `system`). Body: `AgentPublishRequest` `{ "dependency_declaration": {} }`.

**Purpose:** submit an agent for catalog publish. Under Decision 20 this is now the eval gate.

## Check order (first failure wins)
1. **404** — agent not found.
2. **422 `critical_risk_not_publishable`** — any bound tool has `risk_level='critical'` (existing).
3. **422 `no_version_to_publish`** — agent has zero versions (NEW).
4. **422 `eval_not_passed`** — latest version (max `version_number`) has `eval_passed=false` (NEW).
5. **422 `adversarial_eval_not_passed`** — latest version has `high`/`critical`-risk declared or bound tools and `adversarial_eval_passed=false` (NEW).
6. **202** — create `PublishRequest`, set `agent.publish_status='pending_review'`.

## 202 Accepted
```json
{ "publish_request_id": "9a2f…" }
```
Side effect: `agent.publish_status` → `pending_review`.

## 422 examples
```json
{ "detail": { "error": "critical_risk_not_publishable" } }
```
```json
{ "detail": { "error": "no_version_to_publish" } }
```
```json
{ "detail": { "error": "eval_not_passed", "version_number": 3 } }
```
```json
{ "detail": { "error": "adversarial_eval_not_passed", "version_number": 3 } }
```

## 404
```json
{ "detail": "Agent 'no-such-agent' not found." }
```

## Behavior matrix
| Latest version | Bound/declared risk | Result |
|---|---|---|
| none | — | 422 `no_version_to_publish` |
| `eval_passed=false` | — | 422 `eval_not_passed` |
| `eval_passed=true` | low/medium | **202** |
| `eval_passed=true`, `adversarial=false` | high (e.g. `issue_refund`) | 422 `adversarial_eval_not_passed` |
| `eval_passed=true`, `adversarial=true` | high | **202** |
| any | a `critical` bound tool present | 422 `critical_risk_not_publishable` (checked first) |

Tests: T-S17-003/004/005/006; existing T-S6-002 (critical). Downstream publish suites (`suite-6`, `suite-14`, `suite-15`) create an `eval_passed=true, adversarial_eval_passed=true` version before publishing so they satisfy checks 3–5.

## `eval_passed` is set via
`PATCH /api/v1/agents/{name}/versions/{version_id}` with `{ "eval_passed": true }` (and/or `{ "adversarial_eval_passed": true }`). Auto-setting from a passing `EvalRun` (T-4) is **out of scope**; until it ships, this PATCH is how the gate is satisfied.

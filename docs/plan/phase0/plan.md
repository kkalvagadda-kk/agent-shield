# Phase 0 — Fix the Pre-Publish Evaluation Loop (Decision 20)

**Goal:** Make batch eval work against real agents and move the `eval_passed` gate from deploy to publish, so an agent can be deployed to an ungated sandbox, evaluated in the playground, and only reach the catalog once it has actually passed evaluation.

**Architecture:** Two independent sub-changes on the existing `registry-api` (FastAPI + SQLAlchemy 2.0 + Alembic/Postgres), the `eval-runner` K8s Job image, and the `studio` React app. **Slice A** fixes the eval-runner's 403 crash (service-identity bypass), stops one bad item from killing the whole Job (per-item try/except), and replaces the keyword scorer with the real Haiku judge (eval-runner polls registry-api for the run's judge score). **Slice B** removes/scopes the deploy-time eval gate, adds it to `publish_agent`, introduces the `sandbox` deployment environment, and relabels/de-hardcodes the Studio deploy page. The slices share no code paths and can be built in either order.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 / Alembic / Postgres; `httpx` (eval-runner); React + TypeScript + Vite + React Query (Studio); bash + `kubectl exec` + Python `urllib`/`httpx` e2e suites; Docker images deployed via Helm (`scripts/deploy-cpe2e.sh`).

---

## Constitution Check

| Principle (CLAUDE.md) | Status | Justification |
|---|---|---|
| E2E suite for every endpoint/behavior change | PASS | Slice A extends `suite-8-playground.sh` (owner bypass + new GET run endpoint) and `suite-9-eval.sh` (eval-runner→registry contract). Slice B adds `suite-17-eval-gate.sh` (deploy/publish gate matrix) and updates `suite-6/14/15` publish tests for the new gate. All registered in `run-all.sh`; `T-SNN-00X` IDs; scripts `chmod +x`. |
| Image version bump per modified image | PASS | `registry-api` 0.2.34→0.2.35 (A1) →0.2.36 (B1); `eval-runner` 0.1.0→0.1.1 (A2); `studio` 0.1.31→0.1.32 (B2). No tag reused; `deploy-cpe2e.sh` header comment updated in each task. |
| Verification gates (tsc / ast.parse) | PASS | Every Python edit is followed by `python3 -c "import ast; ast.parse(open('<file>').read())"`; the Studio edit is followed by `npx tsc --noEmit` in `studio/`. |
| Migrations via Alembic init container | PASS | New migration `0015_deployments_env_add_sandbox.py` (revises `0014`); DROP+ADD the `ck_deployments_env` CHECK. Runs in the existing registry-api init container on the 0.2.36 image. |

No constitution principle is violated → no Complexity Tracking section.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `services/registry-api/routers/playground.py` | Modify | Owner-check bypass for reserved `eval-runner` identity; new `GET /playground/runs/{run_id}` returning judge fields. |
| `services/registry-api/schemas.py` | Modify | Add `judge_score`/`judge_status`/`judge_reason` to `PlaygroundRunResponse`; add `sandbox` to `DeploymentCreate.environment` pattern. |
| `services/registry-api/routers/deployments.py` | Modify | Scope the `eval_passed` + adversarial gates to `environment=='production'` (ungate sandbox/staging/canary). |
| `services/registry-api/routers/agents.py` | Modify | Add `eval_passed` (+ adversarial when risky) gate to `publish_agent`; import `AgentVersion`. |
| `services/registry-api/models.py` | Modify | Add `'sandbox'` to the `Deployment.environment` CHECK (`ck_deployments_env`). |
| `services/registry-api/alembic/versions/0015_deployments_env_add_sandbox.py` | Create | DROP+ADD `ck_deployments_env` to include `sandbox`; `down_revision="0014"`. |
| `services/eval-runner/main.py` | Modify | Per-item try/except around run-create; poll registry-api for Haiku judge score; keyword fallback. |
| `studio/src/pages/DeployAgentPage.tsx` | Modify | Relabel "Deploy to Production" → sandbox; remove hardcoded `eval_passed: true`; deploy with `environment: "sandbox"`. |
| `scripts/e2e/suite-8-playground.sh` | Modify | Add `T-S8-022/023/024` (eval-runner bypass 201, non-owner 403, GET run judge fields). |
| `scripts/e2e/suite-9-eval.sh` | Modify | Add `T-S9-011/012` (eval-runner identity starts a run it does not own; GET run exposes judge fields). |
| `scripts/e2e/suite-17-eval-gate.sh` | Create | `T-S17-001..005` — sandbox deploy ungated, production deploy gated, publish gate matrix. |
| `scripts/e2e/suite-6-asset-lifecycle.sh` | Modify | Create an `eval_passed=true` version before `T-S6-003` publish (new gate). |
| `scripts/e2e/suite-14-consumer-chat.sh` | Modify | Create an `eval_passed=true` version for `s14-promote-test` before its publish call. |
| `scripts/e2e/suite-15-artifact-isolation.sh` | Modify | Create an `eval_passed=true` version for `${ALICE_AGENT}` before `T-S15-006` publish. |
| `scripts/e2e/run-all.sh` | Modify | Register `suite-17-eval-gate.sh`. |
| `scripts/deploy-cpe2e.sh` | Modify | Bump `REGISTRY_API_TAG` (0.2.35 then 0.2.36), `EVAL_RUNNER_TAG` (0.1.1), `STUDIO_TAG` (0.1.32); update header comments. |

---

## Key Interfaces

### 1. `create_playground_run` owner check (playground.py)
Reserved service identities bypass the `created_by` owner check. Add near the top of the module:
```python
_SERVICE_IDENTITIES = {"eval-runner"}
```
Replace the owner check (currently lines 67–73):
```python
caller = x_user_sub or "dev"
if (
    x_user_sub
    and x_user_sub not in _SERVICE_IDENTITIES
    and agent.created_by
    and agent.created_by != x_user_sub
):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only the agent owner can run it in the playground.",
    )
```
Behavior: `X-User-Sub: eval-runner` → 201 for ANY agent; other non-owner `X-User-Sub` → 403; owner or no header → 201.

### 2. New `GET /api/v1/playground/runs/{run_id}` (playground.py)
```python
@router.get("/runs/{run_id}", response_model=PlaygroundRunResponse,
            summary="Get a single playground run (includes judge fields)")
async def get_playground_run(run_id: str, db: AsyncSession = Depends(get_db)) -> PlaygroundRunResponse:
    # 422 on non-UUID, 404 if not found, else 200 PlaygroundRunResponse
```
No owner check (consistent with `/stream` and `/trace`). Response now carries `judge_score` (float|null), `judge_status` (str|null: `completed`/`timeout`/`error`/`no_provider`), `judge_reason` (str|null).

### 3. `PlaygroundRunResponse` additions (schemas.py)
```python
judge_score: Optional[float] = None
judge_status: Optional[str] = None
judge_reason: Optional[str] = None
```

### 4. `DeploymentCreate.environment` (schemas.py)
```python
environment: str = Field("production", pattern="^(production|staging|sandbox)$")
```

### 5. Deploy gate scoping (deployments.py)
Wrap the existing eval gate (lines 235–244) **and** adversarial gate (lines 246–263) in:
```python
if body.environment == "production":
    # gate 3: eval_passed ... 422
    # gate 3b: adversarial_eval_passed when risky ... 422
```
Sandbox/staging/canary deploys skip both gates.

### 6. `publish_agent` eval gate (agents.py)
Import `AgentVersion`. After the existing `critical_risk_not_publishable` block (line 388), before "Determine highest risk level":
```python
latest_version = (await db.execute(
    select(AgentVersion).where(AgentVersion.agent_id == agent.id)
    .order_by(AgentVersion.version_number.desc()).limit(1)
)).scalar_one_or_none()
if latest_version is None:
    raise HTTPException(422, detail={"error": "no_version_to_publish"})
if not latest_version.eval_passed:
    raise HTTPException(422, detail={"error": "eval_not_passed",
                                     "version_number": latest_version.version_number})
version_tools = latest_version.tools or []
has_risky = any(isinstance(t, dict) and t.get("risk", "low") in ("high", "critical")
                for t in version_tools) or any(t.risk_level in ("high", "critical") for t in tools)
if has_risky and not latest_version.adversarial_eval_passed:
    raise HTTPException(422, detail={"error": "adversarial_eval_not_passed",
                                     "version_number": latest_version.version_number})
```
(`422` = `status.HTTP_422_UNPROCESSABLE_ENTITY`.)

### 7. eval-runner judge fetch (eval-runner/main.py)
```python
_JUDGE_POLL_TIMEOUT = float(os.environ.get("JUDGE_POLL_TIMEOUT", "45"))
_JUDGE_POLL_INTERVAL = float(os.environ.get("JUDGE_POLL_INTERVAL", "3"))
_JUDGE_PASS_THRESHOLD = float(os.environ.get("JUDGE_PASS_THRESHOLD", "0.7"))

async def _poll_for_judge(client: httpx.AsyncClient, run_id: str) -> float | None:
    """Return the Haiku judge score (0.0-1.0) once judge_status is terminal & a
    score is present; None if the judge errored/timed out or the window elapsed."""
```
Pass rule: `passed = judge_score >= _JUDGE_PASS_THRESHOLD`. Fallback (judge unavailable): keyword substring match as today; if no `expected_output`, pass-by-default (unchanged).

---

## Tasks

Tasks are dependency-ordered. `[P]` marks logically parallelizable tasks (see Execution Notes for the registry-api tag-ordering caveat).

### Task A1 — registry-api: playground owner bypass + single-run GET + judge fields
**Files (Modify):**
- `services/registry-api/routers/playground.py` — add `_SERVICE_IDENTITIES = {"eval-runner"}` near line 38; rewrite owner check (lines 67–73) per Key Interface 1; add `get_playground_run` endpoint (Key Interface 2) immediately after `list_playground_runs` (after line 137).
- `services/registry-api/schemas.py` — add three judge fields to `PlaygroundRunResponse` (after `completed_at`, line 755) per Key Interface 3.
- `scripts/e2e/suite-8-playground.sh` — add `T-S8-022/023/024` before the Cleanup section (line 626); update the header test-ID list (lines 8–28).
- `scripts/deploy-cpe2e.sh` — `REGISTRY_API_TAG="0.2.34"` → `"0.2.35"` (line 38); update the header comment (line 8) to note "playground eval-runner identity bypass + GET /playground/runs/{id} judge fields".

**Interface contract:** Exactly Key Interfaces 1, 2, 3. `judge_status` terminal values: `completed`, `timeout`, `error`, `no_provider` (as written by `judge.py._write_score` / `score_run`).

**Acceptance criteria:**
- `POST /playground/runs` with `X-User-Sub: eval-runner` for an agent whose `created_by != eval-runner` → **201** with `run_id` + `stream_url`.
- `POST /playground/runs` with `X-User-Sub: some-other-user` for an agent owned by `smoke-user` → **403**.
- `POST /playground/runs` with `X-User-Sub: smoke-user` (owner) or no header → **201**.
- `GET /playground/runs/{run_id}` → **200** and the JSON contains keys `judge_score`, `judge_status`, `judge_reason` (values may be `null`); non-UUID → **422**; unknown UUID → **404**.
- Existing `T-S8-001..021` still pass (owner path and no-header path unchanged).

**Dependencies:** none.

**Test cases (suite-8 additions):**
- `T-S8-022` — POST `/playground/runs` `{agent_name: pg-s8-run-agent}` header `X-User-Sub: eval-runner` → 201, `run_id` present.
- `T-S8-023` — same POST with `X-User-Sub: mallory-not-owner` → 403 (owner check still enforced for real users).
- `T-S8-024` — GET `/playground/runs/{RUN_ID}` → 200; assert `'judge_score' in d and 'judge_status' in d and 'judge_reason' in d`.

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/routers/playground.py').read())"
python3 -c "import ast; ast.parse(open('services/registry-api/schemas.py').read())"
chmod +x scripts/e2e/suite-8-playground.sh
# after deploy: NAMESPACE=agentshield-platform bash scripts/e2e/suite-8-playground.sh
```

### Task A2 — eval-runner: per-item try/except + real judge poll
**Files (Modify):**
- `services/eval-runner/main.py` — add `import time` (top); add `_JUDGE_POLL_TIMEOUT`/`_JUDGE_POLL_INTERVAL`/`_JUDGE_PASS_THRESHOLD` constants after line 33; add `_poll_for_judge` helper (Key Interface 7) before `run_eval`; wrap the run-create block (lines 59–67) in try/except that records the item as failed and `continue`s on error; replace the scoring block (lines 89–99) to prefer the polled judge score, then keyword fallback.
- `scripts/e2e/suite-9-eval.sh` — add a `wait_for_eval_terminal <id> <timeout_s>` helper and `T-S9-011..015` after `T-S9-010` (before Cleanup, line 396); update header docstring.
- `scripts/deploy-cpe2e.sh` — `EVAL_RUNNER_TAG="0.1.0"` → `"0.1.1"` (line 42); update header comment (line 12) to "batch eval 403 fix (service-identity) + Haiku judge poll".

**Interface contract:** Replacement loop body:
```python
# 2. Start playground run per item (isolated — one failure must not kill the Job)
run_id = None
try:
    run_resp = await client.post("/api/v1/playground/runs", json=run_body,
                                 headers={"X-User-Sub": "eval-runner"})
    run_resp.raise_for_status()
    run_id = run_resp.json().get("run_id")
except Exception as exc:
    logger.warning("item=%d run-create failed: %s", idx, exc)
    results.append({"passed": False, "score": 0.0})
    try:
        await client.post(f"/api/v1/playground/eval-runs/{EVAL_RUN_ID}/results",
            json={"dataset_item_idx": idx, "input_message": input_text, "response": "",
                  "judge_score": 0.0, "judge_reasoning": f"run-create failed: {exc}",
                  "passed": False},
            headers={"X-User-Sub": "eval-runner"})
    except Exception:
        pass
    continue
# 3. Collect SSE stream (unchanged) -> response_text
# 4. Score: prefer Haiku judge, else keyword fallback
judge_score = await _poll_for_judge(client, run_id)
if judge_score is not None:
    score = judge_score
    passed = judge_score >= _JUDGE_PASS_THRESHOLD
    reasoning = "llm-judge (haiku)"
elif expected:
    passed = expected.lower() in response_text.lower()
    score = 1.0 if passed else 0.0
    reasoning = "keyword match (judge unavailable)"
else:
    passed = True
    score = 1.0
    reasoning = "no expected output — pass by default"
```
(`_poll_for_judge` per Key Interface 7: poll `GET /api/v1/playground/runs/{run_id}` every `_JUDGE_POLL_INTERVAL`s up to `_JUDGE_POLL_TIMEOUT`s; return `float(judge_score)` when `judge_status=="completed"` and `judge_score is not None`; return `None` when `judge_status in ("timeout","error","no_provider")` or the window elapses.)

**Acceptance criteria:**
- Batch eval runs the developer's real loop end-to-end: `POST /eval-runs` launches the real K8s Job, which starts a `playground_run` per item **via the eval-runner service identity for an agent it does not own** (no 403), records one `EvalRunResult` per item, and drives the `EvalRun` from `running` → `completed`. The EvalRun is **never** left stuck at `running`.
- A failing item (run-create raises, e.g. the agent 404s) records one failed `EvalRunResult` (`passed=false`, reasoning `run-create failed: ...`) and the loop continues; `run_eval()` still completes and the terminal `PATCH /eval-runs/{id}` sets `status=completed`. All items failing → `failed_count == total`, still `completed`.
- When the run has a Haiku `judge_status=completed`, the recorded `judge_score` equals the run's `judge_score`, `reasoning="llm-judge (haiku)"`, and `passed = score >= 0.7`.
- When no judge score is available within the poll window, the item falls back to keyword match (`reasoning="keyword match (judge unavailable)"`) or pass-by-default when no `expected_output`.

**Dependencies:** **A1** (needs the eval-runner identity bypass and the `GET /playground/runs/{id}` judge fields).

**Test cases (suite-9 additions — real end-to-end batch eval + the contract it depends on):**
Add a `wait_for_eval_terminal <eval_run_id> <timeout_s>` bash helper that polls `GET /eval-runs/{id}` until `status in ("completed","failed")` or the timeout (default 150s). If the window elapses while still `pending`/`running` **and no eval-runner Job pod was scheduled** (environment cannot run Jobs), the two real-Job tests degrade to a `check_manual` note instead of FAIL (matching the codebase convention for environment-dependent checks) so `run-all.sh` stays green; if a Job pod ran but the EvalRun stayed `running`, that is a FAIL (the bug).
- `T-S9-011` — bypass: `POST /playground/runs` `{agent_name: $AGENT_NAME}` header `X-User-Sub: eval-runner` where `$AGENT_NAME` was created **without** an `X-User-Sub` (so `created_by='system' != 'eval-runner'`) → 201 (service identity runs an agent it does not own); capture `run_id`.
- `T-S9-012` — GET `/playground/runs/{run_id}` → 200; assert keys `judge_score`, `judge_status`, `judge_reason` present.
- `T-S9-013` — **real batch eval completes**: create a 2-item dataset + agent `$AGENT_NAME` (no deployment); `POST /eval-runs` → `wait_for_eval_terminal 150` → assert `status=='completed'` (not `running`); `GET /eval-runs/{id}/results` → `len(results) == total_items`; each result has `judge_score` and `passed` set. Assert the item `reasoning` is one of `llm-judge (haiku)` / `keyword match (judge unavailable)` (proves the judge poll ran, not the removed blind keyword scorer).
- `T-S9-014` — **failed item does not crash the Job**: create a 2-item dataset + `POST /eval-runs` with `agent_name='does-not-exist-agent-s9'` → every item's run-create 404s → `wait_for_eval_terminal 150` → assert `status=='completed'`, `failed_count == total_items`, and each recorded result has `passed==False` with reasoning containing `run-create failed` (proves per-item try/except + the Job still reaches `completed`).
- `T-S9-015` — MANUAL: with a real agent deployment **and** an Anthropic key configured for the team, re-run T-S9-013 and confirm at least one result has `reasoning=='llm-judge (haiku)'` and `judge_score` equals the source run's Haiku `judge_score` (the CPE2E cluster runs LLM off by default, so the Haiku path is manually verified).

**Verification command:**
```bash
python3 -c "import ast; ast.parse(open('services/eval-runner/main.py').read())"
chmod +x scripts/e2e/suite-9-eval.sh
# after deploy: NAMESPACE=agentshield-platform bash scripts/e2e/suite-9-eval.sh
```

### Task B1 — registry-api: sandbox env + move eval gate deploy→publish (+ migration + e2e)
**Files:**
- **Modify** `services/registry-api/models.py` — `Deployment.__table_args__` CHECK (lines 370–373): `"environment IN ('production','staging','canary','sandbox')"` (keep name `ck_deployments_env`).
- **Create** `services/registry-api/alembic/versions/0015_deployments_env_add_sandbox.py` — see data-model.md for full body; `revision="0015"`, `down_revision="0014"`.
- **Modify** `services/registry-api/schemas.py` — `DeploymentCreate.environment` pattern → `^(production|staging|sandbox)$` (line 205).
- **Modify** `services/registry-api/routers/deployments.py` — wrap gate 3 (lines 235–244) and gate 3b (lines 246–263) in `if body.environment == "production":` (Key Interface 5); update the docstring bullet (line 150) to "Version must have `eval_passed=True` **only when `environment=production`**".
- **Modify** `services/registry-api/routers/agents.py` — import `AgentVersion` (line 29); insert the eval gate (Key Interface 6) after line 388; update `publish_agent` docstring (lines 361–366) to list the new 422s.
- **Create** `scripts/e2e/suite-17-eval-gate.sh` — `T-S17-001..005` (below); `chmod +x`.
- **Modify** `scripts/e2e/run-all.sh` — add `run_suite "Suite 17: Eval Gate (Decision 20)"  "suite-17-eval-gate.sh"` after the Suite 16 line (line 65).
- **Modify** `scripts/e2e/suite-6-asset-lifecycle.sh` — after the critical-tool unbind (line 217) and before `PUBLISH_REQUEST_ID_1` (line 219), create a version for `${AGENT_NAME}` with `{"image_tag":"registry.internal/publish-test:v1","eval_passed":true,"adversarial_eval_passed":true}` (expect 201).
- **Modify** `scripts/e2e/suite-14-consumer-chat.sh` — inside the `T-S14-003` python block, after the agent create (line 88) and before the publish POST (line 94), `httpx.post('.../agents/s14-promote-test/versions', json={'eval_passed': True, 'adversarial_eval_passed': True}, timeout=5)`.
- **Modify** `scripts/e2e/suite-15-artifact-isolation.sh` — before the `T-S15-006` publish (line 274), create a version for `${ALICE_AGENT}` with `{"eval_passed":true,"adversarial_eval_passed":true}` header `X-User-Sub: user-alice` (expect 201).
- **Modify** `scripts/deploy-cpe2e.sh` — `REGISTRY_API_TAG` → `"0.2.36"` (line 38); update header comment (line 8) to "+ Decision 20: sandbox env, eval gate moved deploy→publish".

**Interface contract:** Key Interfaces 4, 5, 6, and the migration in data-model.md. New publish 422 error bodies: `{"error":"eval_not_passed","version_number":N}`, `{"error":"adversarial_eval_not_passed","version_number":N}`, `{"error":"no_version_to_publish"}`. Existing `{"error":"critical_risk_not_publishable"}` unchanged and still checked first.

**Acceptance criteria:**
- `POST /agents/{name}/deploy` `{environment:"sandbox"}` for a version with `eval_passed=false` and no tools → **201** (`status=pending`, `environment=sandbox`).
- `POST /agents/{name}/deploy` `{environment:"production"}` for the same `eval_passed=false` version → **422** (eval gate).
- `POST /agents/{name}/publish` when the agent's latest version has `eval_passed=false` → **422 `eval_not_passed`**.
- After `PATCH /versions/{id} {eval_passed:true}`, `POST /agents/{name}/publish` → **202** with `publish_request_id`; agent `publish_status=pending_review`.
- `POST /agents/{name}/publish` for an agent with **no** versions → **422 `no_version_to_publish`**.
- Migration `0015` applies cleanly; DB accepts `environment='sandbox'`; `alembic downgrade` outline documented.
- Updated suite-6/14/15 publish tests pass (they now create an eval-passed version first). Deploy-gate tests `T-S6-LG-001/002` still pass (they deploy to the default `production` environment, which stays gated).

**Dependencies:** none functionally; **registry-api image tag is sequenced after A1** (0.2.36 assumes A1 took 0.2.35) — see Execution Notes.

**Test cases (`suite-17-eval-gate.sh`)** — framed around the developer's create → deploy-to-sandbox → publish loop:
- `T-S17-001` — **deploy-to-sandbox succeeds without eval**: create `eval-gate-s17-agent` (team `platform`, no tools) + a version (`eval_passed` defaults false → the version was created WITHOUT forcing eval); deploy `{environment:"sandbox"}` → **201**, assert `status=='pending'` and `environment=='sandbox'`. (Also proves the DB `ck_deployments_env` CHECK accepts `'sandbox'` — a rejected value would surface as a 500 on INSERT.)
- `T-S17-002` — **production stays gated**: deploy the same `eval_passed=false` version `{environment:"production"}` → **422**; assert detail mentions `eval`.
- `T-S17-003` — **publish blocked, eval not passed**: `POST /agents/eval-gate-s17-agent/publish` → **422**; assert `detail.error == "eval_not_passed"`.
- `T-S17-004` — **publish succeeds after eval passes**: `PATCH /agents/eval-gate-s17-agent/versions/{id} {eval_passed:true}`; then publish → **202** with `publish_request_id`; `GET /agents/eval-gate-s17-agent` → `publish_status=='pending_review'`.
- `T-S17-005` — **no version edge case**: create `eval-gate-s17-noversion` (no versions); publish → **422**; assert `detail.error == "no_version_to_publish"`.
- `T-S17-006` — **adversarial publish gate**: create `eval-gate-s17-risky` + a version with `tools:[{name:"issue_refund",risk:"high"}]`, `eval_passed:true`, `adversarial_eval_passed:false`; publish → **422** `detail.error == "adversarial_eval_not_passed"`; then `PATCH .../versions/{id} {adversarial_eval_passed:true}`; publish → **202**.
- `T-S17-007` — **create-version-without-eval then sandbox-deploy** (the flow Studio now performs): `POST /agents/eval-gate-s17-agent/versions {image_tag:"registry.internal/x:v9"}` with **no** `eval_passed` field → 201 and `eval_passed==false`; then `POST /agents/eval-gate-s17-agent/deploy {version_id, environment:"sandbox"}` → 201. Cleanup: soft-delete `eval-gate-s17-agent`, `eval-gate-s17-noversion`, `eval-gate-s17-risky`.

**Verification command:**
```bash
for f in models.py schemas.py routers/deployments.py routers/agents.py \
         alembic/versions/0015_deployments_env_add_sandbox.py; do
  python3 -c "import ast; ast.parse(open('services/registry-api/$f').read())"; done
chmod +x scripts/e2e/suite-17-eval-gate.sh
# after deploy: NAMESPACE=agentshield-platform bash scripts/e2e/suite-17-eval-gate.sh
#              bash scripts/e2e/suite-6-asset-lifecycle.sh
#              bash scripts/e2e/suite-14-consumer-chat.sh
#              bash scripts/e2e/suite-15-artifact-isolation.sh
```

### Task B2 — Studio: relabel deploy step, drop hardcoded eval_passed, deploy to sandbox
**Files (Modify):**
- `studio/src/pages/DeployAgentPage.tsx`:
  - `createVersionMutation` (line 79): `createVersion(name!, { image_tag: imageTag || undefined })` (remove `eval_passed: true`).
  - `deployMutation` (lines 88–103): replace the `passing`-filter logic — deploy the newest existing version if any (`versions[0]`, list is newest-first), else create one **without** `eval_passed`; call `deployAgent(name!, { version_id: versionId, environment: "sandbox" })`.
  - Step 2 heading (line 200): `Deploy to Production` → `Deploy to Sandbox`; add a one-line helper under it: "Ungated test deploy — evaluate here before publishing."
  - `onSuccess` toast (line 105): keep, or reword to "Sandbox deployment triggered — polling for status…".
- `scripts/deploy-cpe2e.sh` — `STUDIO_TAG="0.1.31"` → `"0.1.32"` (line 41); update header comment (line 9) to "DeployAgentPage: sandbox label + no hardcoded eval_passed".

**Interface contract:** New `deployMutation.mutationFn`:
```tsx
mutationFn: async () => {
  let versionId: string;
  const existing = versions ?? [];
  if (existing.length > 0) {
    versionId = existing[0].id;            // listVersions is newest-first
  } else {
    const v = await createVersion(name!, { image_tag: imageTag || undefined });
    versionId = v.id;
    await refetchVersions();
  }
  return deployAgent(name!, { version_id: versionId, environment: "sandbox" });
},
```
`deployAgent`/`createVersion` signatures in `studio/src/api/registryApi.ts` already accept `environment?` and optional `eval_passed?` — no API-client change required.

**Acceptance criteria:**
- `npx tsc --noEmit` passes in `studio/`.
- Step 2 reads "Deploy to Sandbox" with the helper subtext; no occurrence of `eval_passed: true` remains in `DeployAgentPage.tsx`.
- The deploy call sends `environment: "sandbox"` (verified against the B1 sandbox contract — the API returns 201 without eval).

**Dependencies:** **B1** (backend must accept `environment: "sandbox"` in both the Pydantic pattern and the DB CHECK, or the deploy call 422s).

**Test cases:**
- Code assertion (part of this task): `grep -n "eval_passed" studio/src/pages/DeployAgentPage.tsx` returns **no matches** — proves `createVersion` no longer forces `eval_passed: true`.
- API assertion (covered by `T-S17-001`): a version created **without** `eval_passed` (defaults false) can still be deployed to `sandbox` → 201 — the exact backend contract the Studio flow now exercises.
- `T-S17-007` (defined in `suite-17-eval-gate.sh` by Task B1) — **Studio-equivalent flow**: `POST /agents/eval-gate-s17-agent/versions {image_tag:"registry.internal/x:v9"}` (no `eval_passed` field) → 201 with `eval_passed==false`; then `POST /agents/eval-gate-s17-agent/deploy {version_id, environment:"sandbox"}` → 201. Mirrors DeployAgentPage's create-then-deploy without the hardcoded flag.
- MANUAL — open Studio → Agents → an agent → Deploy: Step 2 reads "Deploy to Sandbox" with the "Ungated test deploy…" subtext (label change); clicking Deploy on an agent whose version has `eval_passed=false` succeeds (no 422); the deployment history row shows `environment=sandbox`.

**Verification command:**
```bash
cd studio && npx tsc --noEmit
grep -n "eval_passed" studio/src/pages/DeployAgentPage.tsx   # expect no matches
```

### Task Z — Run the full e2e suite (completion gate)
**Files:** none (execution only).

**Interface contract:** the entire e2e matrix must pass green after all images are built and deployed.

**Acceptance criteria:**
- `NAMESPACE=agentshield-platform bash scripts/e2e/run-all.sh` exits 0 with **STATUS: ALL PASS** — every suite green, including the new/updated `suite-8`, `suite-9`, `suite-17`, and the publish-gate-updated `suite-6`, `suite-14`, `suite-15`.
- The images under test are the bumped tags: `registry-api:0.2.36`, `eval-runner:0.1.1`, `studio:0.1.32` (verify with `kubectl get deploy -n agentshield-platform -o wide` / the eval Job's image).
- `cd studio && npx tsc --noEmit` passes; `python3 -c "import ast; ast.parse(...)"` passes for every modified Python file.

**Dependencies:** **A1, A2, B1, B2** (all). This is the LAST task.

**Test cases:** the aggregate run of every suite listed in `run-all.sh`.

**Verification command:**
```bash
NAMESPACE=agentshield-platform bash scripts/e2e/run-all.sh   # must print STATUS: ALL PASS, exit 0
```

---

## E2E Coverage Matrix

Every added/changed behavior maps to at least one test ID. Nothing changed is left unverified.

| Slice | Changed behavior (function / endpoint) | Test ID(s) |
|---|---|---|
| A | `create_playground_run` — `eval-runner` service identity bypass → 201 for an agent it does not own | T-S8-022, T-S9-011 |
| A | `create_playground_run` — non-owner, non-service caller still → 403 | T-S8-023 |
| A | `create_playground_run` — owner / no-header path unchanged → 201 | T-S8-001 (existing), T-S9-013 |
| A | new `GET /playground/runs/{id}` — 200 with `judge_score`/`judge_status`/`judge_reason`; 422 bad UUID; 404 unknown | T-S8-024, T-S9-012 |
| A | eval-runner batch eval completes end-to-end (`running`→`completed`, per-item results, judge poll used not blind keyword) | T-S9-013 |
| A | eval-runner per-item try/except — a failing item does not crash the Job; EvalRun still reaches `completed` | T-S9-014 |
| A | eval-runner Haiku judge score used when a provider is configured | T-S9-015 (MANUAL) |
| B | `deployments` — deploy to `environment=sandbox` with `eval_passed=false` → 201 (ungated); `ck_deployments_env` accepts `sandbox` | T-S17-001 |
| B | `deployments` — deploy to `environment=production` with `eval_passed=false` → 422 (still gated) | T-S17-002 |
| B | existing production adversarial deploy gate still fires | T-S6-LG-001/002 (existing, unchanged) |
| B | `publish_agent` — 422 `eval_not_passed` when latest version `eval_passed=false` | T-S17-003 |
| B | `publish_agent` — 202 after `eval_passed=true` | T-S17-004 |
| B | `publish_agent` — 422 `no_version_to_publish` when agent has no versions | T-S17-005 |
| B | `publish_agent` — 422 `adversarial_eval_not_passed` for a high-risk-tool agent, 202 after adversarial passes | T-S17-006 |
| B | `publish_agent` — existing `critical_risk_not_publishable` still checked first | T-S6-002 (existing) |
| B | Studio flow — create version WITHOUT `eval_passed`, then sandbox-deploy succeeds | T-S17-007 |
| B | Studio `DeployAgentPage` — no hardcoded `eval_passed: true`; "Deploy to Sandbox" label | `grep` assertion (Task B2) + MANUAL |
| A/B | full regression | Task Z — `run-all.sh` green |

---

## Execution Notes

**Recommended order:** `A1 → A2` (Slice A), `B1 → B2` (Slice B), then **`Z` last**. Slice A and Slice B are functionally independent and may be built in parallel; the only coupling is the shared `registry-api` image tag. Task Z (`run-all.sh` green) is the completion gate and depends on A1, A2, B1, and B2.

**Dependency graph:**
- A2 depends on A1 (bypass + judge GET endpoint).
- B2 depends on B1 (schema + DB CHECK must accept `sandbox`).
- A1 `[P]` and B1 `[P]` are logically independent, **but both bump `registry-api`**. Tags must stay unique and monotonic: build A1 first → `registry-api:0.2.35`, then B1 → `0.2.36`. If B1 is built first instead, it takes `0.2.35` and A1 takes `0.2.36` — the rule is "increment from the current value in `deploy-cpe2e.sh`; never reuse a tag." The tag numbers in the tasks assume the A-then-B order.
- A2 (`eval-runner`) `[P]` and B2 (`studio`) `[P]` touch distinct images and can be built anytime after their in-slice dependency.

**Pre-flight scan (B1):** `grep -rn "/publish" scripts/e2e/` before implementing — the three known publish callers (`suite-6`, `suite-14`, `suite-15`) are updated in B1; confirm no new publish caller was added since.

**Deploy + test loop (any task):** `bash scripts/deploy-cpe2e.sh` (or targeted `docker build` + `kubectl set image` + `kubectl rollout status`), then run the affected suites and `bash scripts/e2e/run-all.sh`. See quickstart.md.

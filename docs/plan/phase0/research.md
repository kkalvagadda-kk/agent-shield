# Phase 0 — Research & Decisions

Scope: the first slice of the execution-modes roadmap — fix the pre-publish evaluation loop (Decision 20; todos T-1, T-2, T-3, T-5, T-10). Durable/scheduled/event/workflow/memory work (Decisions 21–22, todos T-4/T-6..T-11) is explicitly out of scope and appears only as forward context / follow-ups.

---

## D1. How the eval-runner should be allowed to run any agent in the playground

**Context:** `POST /api/v1/playground/runs` (`create_playground_run`) rejects any caller whose `X-User-Sub != agent.created_by` with 403 ("Only the agent owner can run it in the playground."). The eval-runner Job posts with `X-User-Sub: eval-runner` and does **not** wrap the `raise_for_status()`, so the 403 propagates, `run_eval()` crashes, the K8s Job fails, and the `EvalRun` row is stuck at `status='running'` forever. Batch eval is non-functional against real (non-`system`) agents.

**Options considered:**
| Option | How | Trade-off |
|---|---|---|
| **A: Service-identity bypass** (chosen) | Treat `X-User-Sub == 'eval-runner'` as a reserved identity allowed to run any agent; skip the owner check for it. | One-line, explicit, auditable. The eval-runner already authenticates as a fixed identity via the header. Real users are still owner-checked. |
| B: Impersonate the owner | eval-runner looks up `agent.created_by` and sends it as `X-User-Sub`. | Extra round-trip; the run's `user_id` becomes the owner, muddying "who ran this"; the eval-runner would masquerade as a human. |
| C: Drop the owner check when `context=playground` | Remove owner gating for all playground runs. | Over-broad — any authenticated user could run any private agent in the playground, defeating artifact isolation (suite-15). |

**Decision:** **Option A — service-identity bypass** (this was the decided approach in the slice brief). Implemented as a module-level set `_SERVICE_IDENTITIES = {"eval-runner"}` so future service callers are a one-line addition. The owner check keeps rejecting non-owner human callers (403), preserving the private/pending_review visibility model.

**Second half of T-1 — resilience:** wrap the eval-runner's per-item run-create in try/except so one failed item records a failed `EvalRunResult` and the loop `continue`s. Even with the bypass, a transient 5xx / network blip on one item must not abort the whole Job. Combined with the bypass, this guarantees the Job always reaches the terminal `PATCH /eval-runs/{id} {status:"completed"}` and the EvalRun never hangs at `running`.

**Assumption:** the `eval-runner` header value is trusted inside the cluster (registry-api sits behind the platform network boundary; the header is not settable by external callers reaching the public surface). This matches the existing trust model where deploy-controller and eval-runner already call internal endpoints with service headers.

---

## D2. Judge integration — poll registry-api vs eval-runner calls the judge directly

**Context:** the eval-runner currently scores each item by keyword substring (`expected.lower() in response.lower()` → 1.0/0.0). The interactive playground already runs the real LLM judge (`services/registry-api/judge.py`, Claude Haiku `claude-haiku-4-5-20251001`) on every completed run via `_complete_run()` → `score_run()`, writing `judge_score`/`judge_status`/`judge_reason` onto the `PlaygroundRun` row.

**Options considered:**
| Option | How | Trade-off |
|---|---|---|
| **A: eval-runner polls registry-api for the run's judge score** (chosen) | After collecting the stream response, poll `GET /playground/runs/{id}` until `judge_status` is terminal; use `judge_score`. Keyword fallback if unavailable. | Zero judge-logic duplication; single source of truth for scoring; the eval-runner needs no Anthropic key or provider-resolution logic. Cost: a short poll wait; a new read endpoint. |
| B: eval-runner calls the judge directly | Give the eval-runner its own copy of `_call_judge` (Anthropic key + prompt). | Duplicates the judge prompt/model/threshold in a second image; needs the Anthropic key mounted into the Job; two code paths to keep in sync; drift risk. |

**Decision & recommendation:** **Option A.** The judge already fires automatically for every playground run (including eval-runner runs, since the run goes through `_complete_run` on stream end). The eval-runner should **reuse** that score, not reimplement it. Requires (a) a read path exposing the judge fields — new `GET /api/v1/playground/runs/{run_id}` returning `judge_score/judge_status/judge_reason` (added to `PlaygroundRunResponse`), and (b) an eval-runner poll loop.

**Poll parameters (defaults, env-overridable):** `JUDGE_POLL_TIMEOUT=45s`, `JUDGE_POLL_INTERVAL=3s`. Rationale: `judge.py` uses a 30s judge timeout; the judge is launched from a FastAPI `BackgroundTask` after the stream response completes, so 45s comfortably covers judge latency + write. Terminal `judge_status` values `timeout`/`error`/`no_provider` short-circuit the poll to the keyword fallback immediately.

**Pass threshold:** `JUDGE_PASS_THRESHOLD=0.7` → `passed = judge_score >= 0.7`. The judge scale is 1.0 = excellent, 0.5 = acceptable, 0.0 = poor; 0.7 requires better-than-"acceptable" to count as a pass. Configurable via env so it can be tuned without a rebuild. `overall_score` remains `passed_count / total` (unchanged).

**Fallback semantics:** if the poll returns no score (no provider configured, judge error/timeout, or window elapsed): fall back to keyword substring match when `expected_output` exists, else pass-by-default when it does not — exactly today's behavior, so batch eval degrades gracefully rather than failing.

**Testability caveat:** a real judge score requires a live agent pod producing output **and** an Anthropic key configured for the team; the CPE2E cluster runs safety/LLM off by default. So the e2e validates the *contract* the eval-runner depends on (bypass → 201; `GET run` exposes judge fields) and leaves the full scored path as a MANUAL/integration check. The scoring/fallback/try-except logic is validated by `ast.parse` + review.

---

## D3. Deploy-gate: remove entirely vs scope to `environment='production'`

**Context:** Decision 20 says move the `eval_passed` gate from deploy to publish and make deploy-to-sandbox ungated. `deployments.py` currently blocks any deploy with 422 when `version.eval_passed` is false (plus an adversarial gate for risky tools).

**Options considered:**
| Option | How | Trade-off |
|---|---|---|
| A: Remove the deploy gate entirely | Delete gates 3 and 3b. | Simplest, but drops the one guard that keeps unevaluated code out of the *production* environment — and production deploy is a real, separate surface from publish-to-catalog. |
| **B: Scope the gate to `environment=='production'`** (chosen) | Wrap gates 3 + 3b in `if body.environment == "production":`. | Sandbox/staging/canary become ungated (unblocking the playground eval loop) while production deploys stay eval-gated. Preserves defense-in-depth; matches Decision 20's own parenthetical ("or scope it to `environment=production` only"). |

**Decision & recommendation:** **Option B — scope to production.** Decision 20's implications list allows either, and scoping is strictly safer: it unblocks the sandbox loop (the actual bug) without silently ungating production. This also keeps the existing suite-6 deploy-gate tests (`T-S6-LG-001/002`, which deploy to the default `production` environment) green with no rework. Publish becomes the primary user-facing gate (D4); the production-scoped deploy gate is complementary, not redundant.

---

## D4. Where the eval gate lands on publish, and which version it checks

**Context:** `publish_agent` (`agents.py`) currently only blocks critical-risk tools (`critical_risk_not_publishable`). Decision 20 moves the `eval_passed` (+ adversarial) gate here.

**Decision:**
- Check the agent's **latest version by `version_number` desc**. Rationale: `version_number` is monotonic and unique per agent (`uq_agent_versions`), so "latest" is deterministic (unlike `created_at`, which can tie). Publish is agent-level (`agents.publish_status`), and the natural contract is "the newest thing you'd ship must have passed."
- Ordering of checks: `critical_risk_not_publishable` (existing, first) → `no_version_to_publish` (agent has zero versions) → `eval_not_passed` → `adversarial_eval_not_passed` (only when the latest version's declared tools or the agent's bound tools include `high`/`critical` risk). Critical stays first so its dedicated error is preserved.
- `no_version_to_publish` (422): publishing an agent with no evaluated version is meaningless under Decision 20; making it explicit is clearer than a generic 500 or a silent pass.

**Risky-tool detection** mirrors the deploy gate exactly (`t.get("risk","low") in ("high","critical")` over `version.tools`, plus `t.risk_level in ("high","critical")` over bound `AgentTool`s) so deploy and publish agree on what "risky" means.

**Downstream e2e impact (important):** three existing suites publish agents that today have no `eval_passed` version and would newly 422:
- `suite-6-asset-lifecycle.sh` (`publish-test-s6-agent`, T-S6-003/006),
- `suite-14-consumer-chat.sh` (`s14-promote-test`, T-S14-003),
- `suite-15-artifact-isolation.sh` (`${ALICE_AGENT}`, T-S15-006).

Each is updated to create a version with `eval_passed:true, adversarial_eval_passed:true` before its publish call. This is a required part of the behavior change, not optional cleanup. (`T-4` — auto-setting `eval_passed` from a passing `EvalRun` — is out of scope; until it ships, `eval_passed` is set via `PATCH /versions`, which the tests and the gate rely on.)

---

## D5. How `sandbox` interacts with the existing deploy flow

**Context:** OQ-D / T-10 introduce a distinct `environment=sandbox` the playground streams to. Today `deployments.environment` CHECK allows `production|staging|canary`; `DeploymentCreate.environment` Pydantic pattern allows only `production|staging`.

**Decision:**
- **DB CHECK** (`ck_deployments_env`) → `production|staging|canary|sandbox` (keep `canary`, which already exists in the DB CHECK). Migration `0015` does DROP+ADD (Postgres cannot ALTER a CHECK in place). Downgrade re-adds the original three-value CHECK; it fails if any `sandbox` rows exist (documented — acceptable for a dev-cluster rollback).
- **Pydantic** `DeploymentCreate.environment` pattern → `^(production|staging|sandbox)$`. `canary` is intentionally left out of the request schema (it was already absent and is created by other internal flows, not by this endpoint's callers); adding `sandbox` is the only change in scope.
- **Deploy flow for sandbox:** identical to any other environment except gates 3/3b are skipped (D3). Team gate, tool-grant gate, and critical-risk gate still apply — a sandbox deploy still cannot bind ungranted or critical-risk tools. Namespace derivation, LLM secret writing, and OPA policy generation are unchanged. The deploy-controller reconciles a `sandbox` Deployment like any other (it does not branch on `environment`). The playground stream endpoint already selects the newest `status='running'` deployment regardless of environment, so streaming to the sandbox pod needs no change.

**Assumption:** Studio drives sandbox deploys by sending `environment: "sandbox"` (B2). The `deployAgent` API client already accepts `environment?`, so no client-type change is needed. Existing production/staging deploy callers are unaffected (default remains `production`).

---

## Cross-cutting assumptions

- **Migration numbering:** latest is `0014_agents_created_by_not_null.py`; the new one is `0015`, `down_revision="0014"`. `alembic/env.py` autoloads `models` and uses `Base.metadata`; migrations run in the registry-api init container on deploy of the `0.2.36` image.
- **`judge_score` type:** the `PlaygroundRun.judge_score` column is `Numeric(4,3)`; Pydantic v2 coerces `Decimal`→`float` for the new `judge_score: Optional[float]` response field, so JSON emits a plain number.
- **Route ordering:** `GET /runs/{run_id}` does not shadow `GET /runs/{run_id}/stream|/trace` (those carry extra path segments) nor `GET /runs` (different template).
- **No behavior change to `/runs` list:** it already serializes `PlaygroundRunResponse`; the added judge fields appear there too (additive, non-breaking).

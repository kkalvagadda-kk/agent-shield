# E-1 Tasks — Durable trajectory + tool-call eval

**Slice:** Eval v2 Phase E-1 (durable trajectory + tool-call scoring). Depends on **WS-1 (DONE — real `run_steps` + durable harness)** and **E-0 (composite plumbing + `/eval/score` skeleton + the `mode`/`dimension_weights`/`dimension_scores`/`eval_detail`/`run_id` columns)**.
**Sources:** `e1/plan.md`, `e1/data-model.md`, `e1/contracts/eval-score-api.md`, consolidated `eval-v2/plan.md`, `eval-v2/README.md` ("Verification standard — the suite-58/59 bar, no fakes").

**Total tasks:** 29 (20 implementation + 9 checkpoint)
**Phases:** 11 (8 implementation + 3 checkpoint gates)
**Parallel opportunities:** noted inline with `[P]`
**Checkpoint phases:** CP1 (after Phase 3), CP2 (after Phase 5), CP3 (after Phase 8 — the mandatory no-fakes real-durable-eval gate)

> ⚠️ **Re-ground before you cut code.** `file:line`, image tags, and suite numbers below are indicative against the 2026-07-13 tree. The plan's grounding note verified the E-1 anchors are usable today (`sdk/agentshield_sdk/durable.py`, `routers/playground.py:260/355/625`, `eval_runner.py:~301-330`), but re-confirm each against live code. **E-0 must be merged first** — E-1 fills the `durable` branch of E-0's `mode` dispatch and populates E-0's columns; it adds **no migration**.

> 🚨 **NO-FAKES is the acceptance, not a nicety.** This build shipped 11 live-only bugs green because suites faked the dispatch→pod→callback→resume seam (`docs/bugs/durable-workflow-live-path.md`). E-1's gate (Phase 8 / CP3) MUST drive a REAL durable dataset → a REAL `EvalRun` → the REAL eval-runner Job → a REAL durable agent dispatched to a REAL pod → real `run_steps` callbacks → the REAL `judge.py` scorers → persisted `dimension_scores`, read back. **No faked `_run_step`, no mocked judge, no hand-built trajectory fixture, no `page.route` stub.** The trajectory/tool-call scores must come from a real durable run's real `run_steps`.

---

## Phase 1 — Setup & Grounding
_Producer confirmation + image tags so every checkpoint deploys new code._

- [ ] [T001] Confirm/extend the durable harness so a tool-boundary `StepUpdate.output` carries `{tool, args}` (the one place E-1 must match the producer — `e1/data-model.md` §3). If already emitted, add a passing assertion; if not, add it to the tool-boundary emit. — `sdk/agentshield_sdk/durable.py`
- [ ] [T002] [P] Bump image tags for the three services E-1 changes (`REGISTRY_API_TAG 0.2.167→0.2.168`, `EVAL_RUNNER_TAG 0.1.4→0.1.5`, `STUDIO_TAG 0.1.132→0.1.133`) in **both** files, with a comment header describing E-1. — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`

---

## Phase 2 — Foundational: durable item schema + code scorers
_Blocking prerequisite for scoring, the runner, and authoring. Pure code, deterministic (no LLM)._

- [ ] [T003] Add `DurableDatasetItem` discriminated-union variant (`kind:"durable"`, `input_payload`, optional `expected_output`, `expected_trajectory{match_mode, steps[]{tool, args_match?, expect_approval?}}`) and extend `EvalScoreRequest` with `actual_trajectory` + optional `dimension_weights`; validate on the common envelope (`e1/data-model.md` §1). — `services/registry-api/schemas.py`
- [ ] [T004] Implement `score_trajectory(actual_steps, expected_trajectory, match_mode) -> (float, dict)` — the four match modes (`exact|ordered|superset|unordered`, `e1/plan.md` §2.2) over the ordered tool list; detail `{missing[], extra[], order_ok}`. — `services/registry-api/judge.py`
- [ ] [T005] Implement `score_tool_calls(actual_steps, expected_steps) -> (float, dict)` (tool-name exact + `args_match ⊆ actual.args` dict-subset; `tool_diffs[]` detail) and a `weighted_mean(dims, weights)` helper. — `services/registry-api/judge.py`

---

## Phase 3 — Durable scoring dispatch
_Wire the two scorers behind the single mode-aware endpoint (`e1/contracts/eval-score-api.md`)._

- [ ] [T006] Fill the `mode=="durable"` branch of `POST /playground/eval/score`: `score_response` (E-0 LLM) + `score_trajectory` + `score_tool_calls` → `weighted_mean` (durable defaults `0.4/0.4/0.2`, overridable by `dimension_weights`); reference-free durable (no `expected_trajectory`) degrades to `{response}`; return `{composite, dimension_scores, detail{expected_trajectory, actual_trajectory, tool_diffs, approvals}}`. — `services/registry-api/routers/playground.py`

---

## Checkpoint 1 — Scorers + scoring endpoint
_Gate: Phases 1-3 complete. Run before Phase 4._
_What you prove: the real `judge.py` scorers, called through the real `/eval/score` durable dispatch on a fixture payload, return three dimensions + a correct composite and degrade gracefully — no runner, no pod yet._

- [ ] [CP1a] Deploy script: build+push registry-api at the bumped tag, `helm upgrade`, wait for the registry-api rollout (no CrashLoop). — `scripts/deploy-cp-e1-scorers.sh`
- [ ] [CP1b] Infra smoke test: registry-api pod Running/Ready; `POST /playground/eval/score` reachable (rejects a malformed body with 422, not 5xx); `grep` proves `score_trajectory`/`score_tool_calls`/`weighted_mean` exist in the running image. — `scripts/smoke-test-cp-e1-infra.sh`
- [ ] [CP1c] Behaviour smoke test (`kubectl exec` into registry-api): POST a durable payload with a matching in-order trajectory → assert `dimension_scores.{response,trajectory,tool_call}` present and `composite` in range; POST a **wrong-order** trajectory under `match_mode=ordered` → assert `trajectory < 1.0`; POST an item with **no** `expected_trajectory` → assert `dimension_scores == {response}` (graceful degrade). — `scripts/smoke-test-cp-e1-behaviour.sh`

> **To run:** `bash scripts/deploy-cp-e1-scorers.sh` → wait for pods → `bash scripts/smoke-test-cp-e1-infra.sh && bash scripts/smoke-test-cp-e1-behaviour.sh`
> **Pass criteria:** all assertions exit 0, no pod in CrashLoopBackOff.

---

## Phase 4 — Eval-runner durable branch
_Start a real durable run, poll the real `run_steps`, build the actual trajectory, score. Reuse the existing `POST /playground/runs` durable launch — NO second launch path (parity)._

- [ ] [T007] `create_eval_run` resolves `mode=durable` from the executable's `execution_shape` and validates it against `dataset.mode` (mechanism from E-0; verify the durable path resolves and rejects a mode/dataset mismatch). — `services/registry-api/routers/eval_runner.py`
- [ ] [T008] Pass `MODE` (and the durable execution shape) into the eval-runner Job env when creating the K8s Job. — `services/registry-api/k8s.py`
- [ ] [T009] Fill the `MODE=durable` branch: `POST /playground/runs` with `execution_shape=durable` + the eval self-approve flag; poll `GET /playground/runs/{id}/steps` to terminal status (reuse the sandbox self-approval path so gated steps proceed); project `RunStep` rows → `actual_trajectory` (`e1/data-model.md` §3); `POST /eval/score` mode=durable; write `dimension_scores`+`eval_detail`+`run_id` on the result row; **fail-closed** — a poll-timeout item is recorded **failed** with a reason, never scored on an empty trajectory. — `services/eval-runner/main.py`

---

## Phase 5 — Durable dataset authoring + HITL-arg scoring
_Author the durable item; assert `expect_approval` parked with matching args._

- [ ] [T010] Accept/validate the `durable` item variant on dataset create/update (discriminated union from T003); reject a malformed `expected_trajectory` with 422. — `services/registry-api/routers/datasets.py`
- [ ] [T011] `expect_approval` scoring: a step with `expect_approval:true` must have projected `status=="awaiting_approval"` (or non-null `approval_id`) **and** its args satisfy `args_match`; record `detail.approvals[]`; a gate that did not park **fails** that step's tool_call dimension (fail-closed). Add the projection assertion in the runner and the scoring assertion in the judge. — `services/registry-api/judge.py`, `services/eval-runner/main.py`
- [ ] [T012] Durable item editor in `DatasetsPage`: `input_payload`, `expected_trajectory` steps (tool + `args_match` + `expect_approval`), rendered only when `dataset.mode=='durable'`; validate on save. — `studio/src/pages/DatasetsPage.tsx`

---

## Checkpoint 2 — Durable eval-runner + authoring round-trip
_Gate: Phases 4-5 complete. Run before Phase 6._
_What you prove: a durable dataset authored through the real API survives save→reload with its `steps[]`/`args_match`/`expect_approval` intact, and the eval-runner Job env carries `MODE=durable`._

- [ ] [CP2a] Deploy script: build+push registry-api + eval-runner + studio at bumped tags, `helm upgrade`, wait for rollouts. — `scripts/deploy-cp-e1-runner.sh`
- [ ] [CP2b] Infra smoke test: registry-api + studio pods Ready; the eval-runner image tag resolves in the cluster; a created eval Job carries `MODE` in its pod env (`kubectl get job … -o jsonpath`). — `scripts/smoke-test-cp-e1-runner-infra.sh`
- [ ] [CP2c] Behaviour smoke test (real API, save→reload): create a `durable` dataset with an `expected_trajectory` (incl. an `expect_approval` step) → re-GET the dataset → assert the `steps[]`, `args_match`, and `expect_approval` survived; assert a malformed durable item is rejected 422. — `scripts/smoke-test-cp-e1-runner-behaviour.sh`

> **To run:** `bash scripts/deploy-cp-e1-runner.sh` → wait for pods → `bash scripts/smoke-test-cp-e1-runner-infra.sh && bash scripts/smoke-test-cp-e1-runner-behaviour.sh`
> **Pass criteria:** dataset round-trip survives reload; MODE present on the Job; no CrashLoop.

---

## Phase 6 — Results render + API types
_Read the durable dimensions in the UI so no column is orphaned (DoD #3)._

- [ ] [T013] Types for the durable item, `expected_trajectory`, and per-dimension results (`dimension_scores`, `eval_detail.tool_diffs`/`approvals`, `run_id`). — `studio/src/api/playgroundApi.ts`, `studio/src/api/registryApi.ts`
- [ ] [T014] `EvalResultsPage`: per-dimension score columns (response/trajectory/tool_call), a tool-diff panel (`eval_detail.tool_diffs`), an expected-vs-actual step diff, and a `run_id` deep-link to the run tree / StepTracker. — `studio/src/pages/EvalResultsPage.tsx`

---

## Phase 7 — Frontend tests + experience doc
- [ ] [T015] [P] Vitest: durable item editor validation (valid/invalid `expected_trajectory`, `expect_approval` toggle). — `studio/src/pages/DatasetsPage.test.tsx`
- [ ] [T016] [P] Vitest: trajectory dimension columns + tool-diff panel render from mocked results. — `studio/src/pages/EvalResultsPage.test.tsx`
- [ ] [T017] [P] Update the experience doc: durable datasets, trajectory/tool-call dimensions, results panel, `run_id` deep-link. — `docs/experience/playground.md`

---

## Phase 8 — NO-FAKES real durable eval e2e + Playwright
_The gate E-1 exists for. Real dataset → real EvalRun → real eval-runner Job → real durable pod → real `run_steps` → real judge → persisted scores, read back. Style: `scripts/e2e/suite-58/59`._

- [ ] [T018] **NO-FAKES real durable eval suite** (`T-S61-00X`): create real agent(s) + **deploy real pods**; create a real `durable` `PlaygroundDataset` (real items, incl. a wrong-tool item and a gated `expect_approval` item) via the real API; launch a real `EvalRun` → the **real eval-runner Job** dispatches a **real durable agent to a real pod**, real step-update callbacks write real `run_steps`, the **real `judge.py`** scores. Then assert (save→reload from DB): `dimension_scores.{trajectory,tool_call}` + `composite` persisted; the **wrong-tool item fails the composite** (the core Eval v2 win); a **wrong-order** item scores `<1.0` under `ordered`; the **gated-step** item's `eval_detail.approvals[]` shows `parked:true, args_matched:true`; a poll-timeout item is recorded failed (fail-closed). **NO faked `_run_step`, NO mocked judge, NO hand-built trajectory fixture** — the trajectory comes from the real run's real `run_steps`. Fails (not skips) if the durable agent fixture is unreachable. — `scripts/e2e/suite-61-eval-v2-durable.sh`
- [ ] [T019] Register suite-61 in the runner. — `scripts/e2e/run-all.sh`
- [ ] [T020] Playwright journey (real, against deployed Studio — no `page.route` stub): author a `durable` dataset item in `DatasetsPage` (save→reload survives), launch the eval, assert the trajectory/tool-call columns + tool-diff render in `EvalResultsPage` and the `run_id` deep-link resolves; use `waitForResponse` on the real eval + dataset network calls. — `studio/e2e/eval-v2-durable.spec.ts`

---

## Checkpoint 3 — The no-fakes real-durable-eval gate (MANDATORY)
_Gate: Phases 1-8 complete. This is the acceptance gate for E-1._
_What you prove: a durable agent that answers correctly but calls the wrong tools **fails** the publish gate, proven end-to-end through the real dispatch→pod→callback→judge path with the score read back from the DB — no fakes anywhere in the seam that hid the 11 bugs._

- [ ] [CP3a] Deploy script: build+push registry-api + eval-runner + studio at bumped tags, `helm upgrade`, wait for all rollouts + confirm a durable agent fixture pod is deployable. — `scripts/deploy-cp-e1-gate.sh`
- [ ] [CP3b] Infra smoke test: registry-api/studio Ready; eval-runner image resolvable; a durable agent fixture deploys to Running (real pod) — the exact class of resource the no-fakes suite dispatches to. — `scripts/smoke-test-cp-e1-gate-infra.sh`
- [ ] [CP3c] Gate smoke test: run `bash scripts/e2e/suite-61-eval-v2-durable.sh` and assert it exits 0; then independently re-GET the eval-run results from the API and assert (save→reload) the wrong-tool item's `composite` is below `pass_threshold` and its `eval_passed` is false, while the correct item passes — the gate flipping on real trajectory scores. — `scripts/smoke-test-cp-e1-gate-behaviour.sh`

> **To run:** `bash scripts/deploy-cp-e1-gate.sh` → wait for pods → `bash scripts/smoke-test-cp-e1-gate-infra.sh && bash scripts/smoke-test-cp-e1-gate-behaviour.sh` → `bash scripts/studio-e2e.sh` (Playwright)
> **Pass criteria:** suite-61 green in `run-all.sh`; wrong-tool item fails the composite/gate on read-back; Playwright journey green; no CrashLoop. **This is DONE for E-1.**

---

## Summary

| Phase | Name | Tasks | Gate |
|---|---|---|---|
| 1 | Setup & Grounding | T001–T002 | — |
| 2 | Foundational: durable schema + code scorers | T003–T005 | — |
| 3 | Durable scoring dispatch | T006 | — |
| **CP1** | **Scorers + scoring endpoint** | CP1a–CP1c | after Phase 3 |
| 4 | Eval-runner durable branch | T007–T009 | — |
| 5 | Durable authoring + HITL-arg scoring | T010–T012 | — |
| **CP2** | **Durable runner + authoring round-trip** | CP2a–CP2c | after Phase 5 |
| 6 | Results render + API types | T013–T014 | — |
| 7 | Frontend tests + experience doc | T015–T017 | — |
| 8 | NO-FAKES real durable eval e2e + Playwright | T018–T020 | — |
| **CP3** | **No-fakes real-durable-eval gate (MANDATORY)** | CP3a–CP3c | after Phase 8 — E-1 acceptance |

**MVP scope:** target **CP1** first — the deterministic scorers behind the real `/eval/score` endpoint are the load-bearing new logic and the cheapest to prove. CP2 closes the authoring + runner round-trip. **CP3 is the definition of done** (the no-fakes real durable gate); E-1 is not shippable until CP3 is green and suite-61 is in `run-all.sh`.

## Gap ledger (carried from `e1/plan.md` §7)
- **deferred (intentional):** LLM-semantic tool-arg *appropriateness* (E-1 ships exact + dict-subset); semantic step-name/alias equivalence; multi-turn durable datasets.
- **→ E-2:** side-effect recording for durable runs that write. E-1 evaluates **read-shaped** durable runs; an item whose run would fire a real side-effect waits for E-2's record seam — E-1 does not let a real side-effect fire under eval.
- **No orphans:** `score_trajectory`/`score_tool_calls`/`weighted_mean` → called by `/eval/score` durable dispatch → called by the eval-runner; `dimension_scores`/`eval_detail`/`run_id` → read by `EvalResultsPage`. Grep-for-caller is a task acceptance gate (DoD #3).

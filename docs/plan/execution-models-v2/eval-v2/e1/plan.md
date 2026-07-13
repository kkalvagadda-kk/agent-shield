# E-1 Implementation Plan — Durable trajectory + tool-call eval

**Slice:** Phase E-1 of Eval v2 (consolidated `eval-v2/plan.md` §6 Phase E-1, §8 sequencing). **Covers E-1 ONLY.**
**Depends on:** **WS-1 (DONE — real `run_steps` + shared durable harness shipped)** + **E-0 (reactive parity
+ composite plumbing + `/eval/score` skeleton)**. E-1 is the **imminent** phase and the most execution-ready:
its producer (real per-node `run_steps`) is already in the tree.
**Companion artifacts:** `e1/data-model.md` (durable item schema + `run_steps`→trajectory mapping),
`e1/contracts/eval-score-api.md` (the `POST /playground/eval/score` durable dispatch + scorer contract).

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

> **Grounding note (E-1 is grounded-now).** Unlike E-2/E-3/E-4, E-1's dependency is **shipped**. Verified
> against the 2026-07-13 tree: `sdk/agentshield_sdk/durable.py` exists; `services/declarative-runner/main.py`
> drives `run_durable(...)` and writes **real per-node `run_steps`** (the old `input_processing`/
> `agent_execution` 2-step skeleton is gone — grep confirms only a doc-comment reference remains at
> `main.py:600`); durable playground runs write `RunStep` rows via the step-update callback
> (`routers/playground.py:260`) and expose them at `GET /playground/runs/{id}/steps` (`playground.py:355`).
> The `file:line` anchors below are therefore usable today, but still re-confirm at `tasks.md` mint time.

---

## 1. Goal

Make **durable** agents evaluable on what actually matters for them: the **step trajectory** (right tools,
right order) and **tool-call correctness** (name + args), scored against the **real `run_steps`** the
durable harness now writes — not the final response alone. Concretely, after E-1:

1. **Two new code scorers exist and are wired.** `score_trajectory` and `score_tool_calls` live in the
   judge scorer library (`judge.py`, extended by E-0's skeleton). They are **deterministic code**, not LLM
   calls: `score_trajectory` compares the run's ordered `run_steps` to a golden `expected_trajectory` under
   a `match_mode ∈ {exact|ordered|superset|unordered}` (agentevals' four modes, renamed — `research.md` §4.2);
   `score_tool_calls` does tool-name exact-match + args partial/semantic match.
2. **A durable dataset is authorable.** `DatasetsPage` gains the `durable` item editor: `input_payload`,
   optional `expected_output`, and an `expected_trajectory` with `steps[]` (each `{tool, args_match?,
   expect_approval?}`). Validated on save against the discriminated union (`e1/data-model.md` §2).
3. **The eval-runner has a durable branch.** The runner starts a durable playground run with `input_payload`,
   **polls `run_steps` to completion** (reusing the sandbox self-approve path so gated steps proceed),
   collects the actual trajectory, and calls `/eval/score` with `mode=durable`. Composite =
   `weighted_mean({response, trajectory, tool_call})` with the durable default weights (0.4/0.4/0.2).
4. **HITL-arg review is scored.** An `expect_approval` step asserts the run **parked** at that node **and**
   the tool args presented match `args_match` — observable via the sandbox self-approval path (OQ-E "always
   show the args").
5. **The gate stays the wire.** The durable composite feeds the unchanged `overall_score` → `eval_passed`
   auto-set (`eval_runner.py:~301-330`). A durable agent that answers correctly but calls the **wrong tools**
   now **fails** — the core Eval v2 win, first realized here.

**Alignment Check:** the ultimate goal is *trustworthy publish per mode*. A durable agent can pass a
response-only check while calling the wrong tools in the wrong order (or firing a write it shouldn't).
E-1 restores the gate's meaning for durable by scoring the trajectory the harness already produces. We do
**not** weaken the gate; we add the dimension the mode requires, behind the same scalar.

**Out of scope (later phases / deferred):** side-effect recording/mocking (E-2 — E-1 evaluates
**read-shaped** durable runs and trajectory/args; a durable run that would send a real email is E-2's
record seam, not E-1's); LLM-semantic tool-arg *appropriateness* judging (deferred — E-1 ships exact +
partial-dict arg match; see gap ledger); scheduled/webhook job-spec / filter scoring (E-3/E-4); workflow
member-path (E-5, which reuses `score_trajectory` at member granularity).

---

## 2. Architecture — read the trajectory the harness already writes

E-1 adds **no new producer**. WS-1 made `run_steps` real; E-1 adds the **reader** (two code scorers) plus a
**durable interpretation branch** in the runner. One trajectory source, one judge module (consolidated
`plan.md` §2.2).

```
 Authoring                 Interpretation (eval-runner durable branch)          Scoring (judge.py)
 ─────────                 ────────────────────────────────────────────        ──────────────────
 DatasetsPage durable   →  1. POST /playground/runs {agent, input_payload,   →  POST /playground/eval/score
 editor: input_payload,       execution_shape=durable, eval self-approve}         {mode:"durable", item,
 expected_trajectory      2. poll GET /playground/runs/{id}/steps to             actual_trajectory, response}
 (steps[] + args_match       terminal status (completed/failed);                    │
 + expect_approval)          gated steps auto-approved (sandbox path)               ▼ dispatch mode=durable
                          3. build actual_trajectory from RunStep rows          score_response (LLM, E-0)
                             (step_number, name, status, output.tool/args)       score_trajectory (CODE)  ← NEW
                                    │                                            score_tool_calls (CODE)  ← NEW
                                    ▼                                            score_composite (weighted)
                          run_steps (WS-1, real per-node)                             │
                                                                                      ▼
                                                                              dimension_scores + eval_detail
                                                                              {tool_diffs[], trajectory diff}
                                                                                      │
                                                                                      ▼  overall_score composite
                                                                              eval_passed auto-set (unchanged)
```

**Seam 1 — durable dataset editor.** `DatasetsPage` renders the `durable` variant when `dataset.mode ==
'durable'`. The `expected_trajectory` is authored as structured JSON (steps + per-step `args_match` /
`expect_approval`); validated by the discriminated-union Pydantic model on save.

**Seam 2 — eval-runner durable branch.** Today the runner has a workflow branch (`main.py:162`, polls
`/workflows/{id}/runs/{run_id}/tree`) and an agent branch (`main.py:187`, SSE stream collect). E-0 replaces
the top-level `if WORKFLOW_ID` with a `MODE` dispatch. E-1 fills the **`durable`** case: start a durable
playground run, poll `run_steps`, assemble the actual trajectory. Reuses the **existing** durable playground
launch path — `POST /playground/runs` already sets `thread_id = run_id` and drives `run_durable`
(`playground.py:625`); E-1 passes the durable execution shape + the eval self-approve flag.

**Seam 3 — judge trajectory + tool-call scorers.** `score_trajectory` and `score_tool_calls` are pure code
(match modes + dict-subset arg compare), added to E-0's scorer library and dispatched by
`/playground/eval/score` when `mode=durable`. Deterministic ⇒ cheaper, faster, reproducible, and **no
position/verbosity bias surface** for the mechanical dimensions (consolidated `plan.md` §9). The LLM judge
stays reserved for `score_response` (final-answer quality/correctness) and rubric scoring.

### 2.1 `run_steps` → trajectory mapping (the load-bearing read)

The durable harness (`sdk/agentshield_sdk/durable.py`) POSTs one `StepUpdate` per node/tool boundary; the
callback writes a `RunStep` row (`models.py:1570`) with `step_number`, `name` (the real LangGraph node/tool
name), `status`, `output` (JSONB — carries tool name + args when the boundary is a tool call), `approval_id`
(set when the step parked), `error_message`. The actual trajectory for scoring is the ordered list of these
rows for the run, read via `GET /playground/runs/{id}/steps` (`playground.py:355`). `e1/data-model.md` §3
pins the exact field extraction (which `output` keys carry `{tool, args}` — confirm the harness's
`StepUpdate.output` convention at impl; it is the one place E-1 must match the producer exactly).

### 2.2 Match modes (renamed agentevals set — `research.md` §4.2)

| E-1 `match_mode` | agentevals equivalent | Passes when |
|---|---|---|
| `exact` | `strict` | same tool steps, same order, no extras |
| `ordered` | (strict on the subsequence) | expected steps appear in the given order (extras allowed between) |
| `superset` | `superset` | actual ⊇ expected (every expected tool called; extras OK) |
| `unordered` | `unordered` | same set of tool steps, any order |

Default `superset` (most forgiving — "did it call the tools it had to"). Tool-arg matching is **partial
dict-subset** by default (`args_match` is a subset that must be present in the actual call args); exact-arg
and semantic-arg are follow-ups (gap ledger).

---

## 3. Migration / Schema

**Reuses E-0's migrations — E-1 adds no new DDL.** E-0 already added (consolidated `data-model.md` §5):
- `playground_datasets.mode` (the `durable` value is now exercised) + `schema_version`.
- `eval_runs.mode` + `dimension_weights` + `pass_threshold`.
- `eval_run_results.dimension_scores` + `eval_detail` + `run_id` (E-1 populates `trajectory`/`tool_call`
  dimensions in `dimension_scores` and `{expected_trajectory, actual_trajectory, tool_diffs[]}` in
  `eval_detail`; `run_id` deep-links the results UI to the run tree + `run_steps`).

No migration is owned by E-1. See `e1/data-model.md` for the durable **item** schema (a Pydantic/validation
concern over the existing `items` JSONB, not DDL) and the exact `run_steps`→trajectory field mapping.

---

## 4. Constitution / retro gates (condensed)

| Gate | How E-1 satisfies it |
|---|---|
| **Parity = shared code** | Trajectory scorers live **once** in `judge.py`; the runner calls the single `/eval/score` endpoint — it never re-implements matching (kills the keyword-match fork, `main.py:285`). One trajectory source (`run_steps`), no parallel trace store. |
| **Ship the gate's producer** | The trajectory reader (E-1) ships **only** because its producer (real `run_steps`, WS-1) is already live. No fake-trajectory gate. Grep proves the skeleton is gone (`main.py:600` is a comment). |
| **Golden-path per environment** | bash suite: author a durable dataset → launch a durable batch eval → assert `dimension_scores.trajectory`/`tool_call` + composite + `eval_passed`; a **wrong-order** case scores `<1.0` under `ordered`; a **gated-step** case asserts `expect_approval` matched. Fails (not skips) if the durable agent fixture is unreachable. |
| **DoD #1 real journey** | Playwright: author the `durable` item in `DatasetsPage`, launch eval, assert the trajectory column + tool-diff render in `EvalResultsPage`, deep-link `run_id` → run tree. |
| **DoD #2 save→reload→assert** | Durable dataset create with an `expected_trajectory` → reload → the `steps[]` + `args_match` + `expect_approval` survive. Bash suite re-GETs the dataset + the eval-run results. |
| **DoD #3 no orphan code** | `score_trajectory`/`score_tool_calls` have a caller (the `/eval/score` durable dispatch); the durable `dimension_scores`/`eval_detail` have a reader (EvalResultsPage trajectory panel) shipped in the same slice. Grep-for-caller is a task acceptance gate. |
| **No-Bandaid** | Durable interpretation is the **explicit `mode` discriminator**, not item-key sniffing; deterministic scorers are code (no LLM where a compare suffices); the actual trajectory reads the **one** `run_steps` store. |
| **Fail-closed** | A durable run that never reaches terminal status (poll timeout) **fails the item** with a recorded reason — never scores by an empty/partial trajectory as a pass. An `expect_approval` step that did **not** park **fails** that dimension. |

---

## 5. File Structure (created/modified — indicative)

### Backend — registry-api
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/judge.py` | M | Add `score_trajectory(actual_steps, expected_trajectory, match_mode)` and `score_tool_calls(actual_steps, expected_steps)` — pure code; emit `trajectory`/`tool_call` dims + `tool_diffs` detail. (Extends E-0's scorer library skeleton.) |
| `services/registry-api/routers/playground.py` | M | `/playground/eval/score` `mode=durable` dispatch → call the two new scorers + `score_response` → composite. (Endpoint created in E-0; E-1 fills the durable branch.) |
| `services/registry-api/routers/datasets.py` | M | Accept/validate the `durable` item variant on create/update (discriminated union). |
| `services/registry-api/routers/eval_runner.py` | M (verify) | `create_eval_run` resolves `mode=durable` from the executable's `execution_shape` + validates vs `dataset.mode` (mechanism from E-0; verify durable path). |
| `services/registry-api/schemas.py` | M | `DurableDatasetItem` variant (`input_payload`, `expected_trajectory{match_mode, steps[]}`); `EvalScoreRequest` carries `actual_trajectory`. |

### Backend — eval-runner (the Job)
| File | C/M | Responsibility |
|---|---|---|
| `services/eval-runner/main.py` | M | `mode=durable` branch: start durable run (`execution_shape=durable`, eval self-approve), poll `GET /playground/runs/{id}/steps` to terminal, build `actual_trajectory`, call `/eval/score`, write `dimension_scores`+`eval_detail`+`run_id`. |

### Frontend — Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/DatasetsPage.tsx` | M | `durable` item editor: `input_payload`, `expected_trajectory` steps (tool + `args_match` + `expect_approval`); validate on save. |
| `studio/src/pages/EvalResultsPage.tsx` | M | Trajectory dimension column + tool-diff panel (`eval_detail.tool_diffs`) + expected-vs-actual step diff; deep-link `run_id` → run tree/StepTracker. |
| `studio/src/api/playgroundApi.ts` / `registryApi.ts` | M | Types for the durable item, `expected_trajectory`, dimension results. |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-NN-eval-v2-durable.sh` | **C** | Durable: author dataset → launch eval → assert trajectory+tool_call dims + composite + `eval_passed`; wrong-order → `<1.0`; gated-step → `expect_approval` matched. |
| `scripts/e2e/run-all.sh` | M | Register the suite. |
| `studio/e2e/eval-v2-durable.spec.ts` | **C** | Playwright: durable dataset author → eval → trajectory render (save→reload). |
| `studio/src/pages/DatasetsPage.test.tsx` / `EvalResultsPage.test.tsx` | M | Vitest: durable editor validation; trajectory/tool-diff render. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api, eval-runner, studio in **both** files. |
| `docs/experience/playground.md` | M | Durable datasets + trajectory/tool-call eval + results panel. |

---

## 6. Tasks (dependency-ordered)

### T1 — `score_trajectory` + `score_tool_calls` (code scorers) — the reader
- **Files:** `judge.py` (M), `schemas.py` (M).
- **Contract:** `e1/contracts/eval-score-api.md`. `score_trajectory(actual_steps, expected_trajectory,
  match_mode) -> (float, dict)` implements the four match modes over the ordered `run_steps`; `score_tool_calls
  (actual_steps, expected_steps) -> (float, dict)` does tool-name exact + args dict-subset; both emit a
  `tool_diffs` detail (expected-vs-actual per step).
- **Acceptance:** unit fixtures — right tools in order → `1.0` trajectory; wrong order under `ordered` →
  `<1.0`; a missing expected tool under `superset` → penalized; a partial `args_match` present in actual → tool
  pass, absent → fail, with a `tool_diffs` entry.
- **Deps:** E-0 (scorer library skeleton + `/eval/score`). **Verify:** `python3 -c "import ast;
  ast.parse(open('services/registry-api/judge.py').read())"`; unit test on fixtures; `grep -n "def
  score_trajectory\|def score_tool_calls" services/registry-api/judge.py`.

### T2 — `/eval/score` durable dispatch (compose response + trajectory + tool-call)
- **Files:** `routers/playground.py` (M).
- **Contract:** `POST /playground/eval/score {mode:"durable", item, actual_trajectory, response, run_id?}` →
  `{composite, dimension_scores:{response, trajectory, tool_call}, detail}`; composite =
  `weighted_mean` with durable defaults (0.4/0.4/0.2, overridable by `eval_runs.dimension_weights`).
- **Acceptance:** a durable payload returns all three dims + a composite; response-only (no
  `expected_trajectory`) degrades to `{response}` gracefully (reference-free durable still scorable).
- **Deps:** T1. **Verify:** `grep -n "eval/score" services/registry-api/routers/playground.py`; endpoint
  returns the durable shape on a fixture POST.

### T3 — eval-runner durable branch (start → poll `run_steps` → score)
- **Files:** `services/eval-runner/main.py` (M), `routers/eval_runner.py` (M, verify), `k8s.py` (M, pass `MODE`).
- **Contract:** on `MODE=durable`: `POST /playground/runs` with `execution_shape=durable` + eval self-approve;
  poll `GET /playground/runs/{id}/steps` to terminal status (reuse the sandbox self-approval path so gated
  steps proceed); assemble `actual_trajectory` from the `RunStep` rows; `POST /eval/score` mode=durable; record
  `dimension_scores`+`eval_detail`+`run_id` on the result row.
- **Acceptance:** a durable batch eval yields a `{response, trajectory, tool_call}` composite; the recorded
  result carries `run_id` (deep-linkable); a poll-timeout item **fails** with a recorded reason (fail-closed).
- **Deps:** T2, **WS-1 (durable run + real steps — shipped)**. **Verify:** `python3 -c "import ast;
  ast.parse(open('services/eval-runner/main.py').read())"`; suite-NN durable happy path.

### T4 — durable dataset editor + HITL-arg (`expect_approval`) scoring
- **Files:** `DatasetsPage.tsx` (M), `datasets.py` (M), `judge.py`/`main.py` (M — `expect_approval` assertion),
  Vitest.
- **Contract:** the durable item editor authors `expected_trajectory.steps[]` incl. `expect_approval` +
  `args_match`; the runner/scorer asserts a step with `expect_approval:true` **parked** (has an `approval_id` /
  `awaiting_approval` status) **and** its presented args matched `args_match`.
- **Acceptance:** durable dataset authorable + save→reload survives; a gated-step case asserts the approval
  args; `npm run typecheck` clean.
- **Deps:** T3. **Verify:** Playwright save→reload; Vitest editor validation.

### T5 — EvalResultsPage trajectory render + suites + deploy
- **Files:** `EvalResultsPage.tsx` (M), `playgroundApi.ts`/`registryApi.ts` (M),
  `suite-NN-eval-v2-durable.sh` (C), `run-all.sh` (M), `eval-v2-durable.spec.ts` (C),
  `deploy-cpe2e.sh`+`values.yaml` (M), `docs/experience/playground.md` (M).
- **Acceptance:** results show per-dimension scores + tool-diff + expected-vs-actual step diff + `run_id`
  deep-link; suite green (wrong-order + gated-step cases); tags bumped in both files; experience doc updated.
- **Deps:** T1–T4. **Verify:** `bash scripts/e2e/suite-NN-eval-v2-durable.sh`; `bash scripts/studio-e2e.sh`;
  `cd studio && npm run typecheck && npm run test`.

---

## 7. Gap Ledger

| Item | Status | Note |
|---|---|---|
| LLM-semantic tool-arg **appropriateness** (vs exact/dict-subset) | **deferred (intentional)** | E-1 ships exact + partial-dict arg match; an LLM scorer for arg *appropriateness* (DeepEval `ArgumentCorrectnessMetric`, `research.md` §2) is a follow-up scorer. |
| Semantic **step-name** equivalence (tool aliases) | deferred (intentional) | E-1 matches tool/node names exactly; alias/semantic step matching is a follow-up. |
| Side-effect recording for durable runs that write | **→ E-2** | E-1 evaluates read-shaped durable trajectories; a durable run that would send a real email needs E-2's `eval_mode=record` seam. E-1 does **not** let a real side-effect fire under eval — such items wait for E-2. |
| Multi-turn durable datasets | deferred (intentional) | Durable items are single-`input_payload`; multi-turn scripts are a later item-schema variant (`research.md` §4.6). |

**No orphan flags:** `score_trajectory`/`score_tool_calls` → called by `/eval/score` durable dispatch →
called by the runner; `dimension_scores`/`eval_detail`/`run_id` → read by EvalResultsPage. Grep-for-caller is
a task gate.

---

## 8. Execution Notes

- **Grounded-now — build against the running product.** WS-1's `run_steps` are real; read them, don't mock a
  trajectory. Confirm the harness `StepUpdate.output` key convention (`{tool, args}`) at impl — that is the
  one place E-1 must match the producer exactly (`e1/data-model.md` §3).
- **Deterministic scorers are code, LLM is the exception.** Trajectory + tool matching is exact/dict-subset
  **code** — reserve the LLM judge for `score_response`. Cheaper, reproducible, and it removes the
  position/verbosity-bias surface for the mechanical dimensions.
- **Reuse the durable launch path.** `POST /playground/runs` already drives `run_durable` with
  `thread_id=run_id`; the runner passes the durable shape + eval self-approve rather than inventing a new
  launch. Any second launch path is a parity violation.
- **The gate win is a test, not a comment.** Assert a durable item that answers correctly but calls the wrong
  tool **fails** the composite — that is the reason E-1 exists.
- **Bump registry-api + eval-runner + studio** in both `deploy-cpe2e.sh` and `values.yaml`; the eval-runner
  Job image changes here.
</content>

# E-3 Implementation Plan — Scheduled eval (job-spec datasets + side-effect assertions)

**Slice:** Phase E-3 of Eval v2 (consolidated `eval-v2/plan.md` §6 Phase E-3, §8 sequencing, `data-model.md`
§2.3). **Covers E-3 ONLY.**
**Depends on:** **WS-3 (NOT built — scheduled path made real end-to-end)** + **E-2 (side-effect record seam)**
+ **E-1 (durable trajectory scorer, reused for durable-inner schedules)**.
**Companion artifacts:** `e3/data-model.md` (`scheduled` item schema — `job_spec` + `expected_side_effects`).

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

> **Grounding note (E-3 is banner-indicative — hard dep on WS-3).** Scheduled eval is only meaningful once
> schedules actually **fire a real run** (WS-3). Until WS-3 lands, E-3's runner branch and the `job_spec →
> input_payload` feed are design intent. The `job_spec` shape is grounded (it mirrors the shipped
> `AgentTrigger.input_payload`, `models.py:1618`), but the scheduled **execution** it drives is WS-3's.
> Re-ground the fire path against WS-3's `/internal/runs/start` + scheduler at `tasks.md` mint time.

---

## 1. Goal

Evaluate **scheduled** agents on a `job_spec` dataset: feed the per-schedule job spec as the run's
`input_payload`, fire one run through the **real** scheduled path (WS-3), and score response + trajectory +
**side-effects** (asserted against E-2's **recorded** calls, never real deliveries). Concretely, after E-3:

1. **A scheduled dataset is authorable.** `DatasetsPage` gains the `scheduled` item editor: `job_spec` (==
   `AgentTrigger.input_payload` shape), optional `expected_output`, optional `expected_trajectory` (durable-inner
   schedules), and `expected_side_effects` (the headline signal for scheduled — "did the nightly compliance job
   send the right email?"). Validated on save.
2. **The eval-runner has a scheduled branch.** The runner feeds `job_spec` as `input_payload`, fires **one**
   run (reactive-or-durable inner, per the agent's shape) through the real scheduled entrypoint, scores
   response + (durable-inner) trajectory + `side_effects`.
3. **Side-effects are safe and asserted.** Under `eval_mode=record` (E-2), the job's write tools are recorded +
   mocked; `score_side_effects` asserts them against `expected_side_effects`. **No real delivery** happens
   during eval.
4. **The gate stays the wire.** The scheduled composite feeds `overall_score` → `eval_passed` unchanged.

**Alignment Check:** the ultimate goal is *trustworthy publish for scheduled agents*. A scheduled agent's whole
point is its side-effect (email, ticket, report) fired unattended on a job spec — response-only eval says
nothing about whether it does the right thing. E-3 restores the gate's meaning by asserting the recorded
side-effect against a golden job spec. We do **not** fire real deliveries to test; E-2's record seam makes the
assertion safe. E-3 adds **no new dispatch code** — it drives WS-3's real scheduled path with a job-spec dataset
(parity; any scheduled-only eval fork is the anti-pattern).

**Out of scope:** making the scheduled path real (WS-3 — E-3 *consumes* it); the record seam itself (E-2);
alert-on-failure eval (WS-3 verifies alerting; not an eval dimension); webhook filter scoring (E-4).

---

## 2. Architecture — drive WS-3's real fire path with a job-spec dataset

```
 Authoring                 Interpretation (eval-runner scheduled branch)         Scoring (judge.py)
 ─────────                 ──────────────────────────────────────────────        ──────────────────
 DatasetsPage scheduled →  1. feed item.job_spec as input_payload             →  POST /playground/eval/score
 editor: job_spec,         2. fire ONE run through WS-3's real scheduled          {mode:"scheduled", item,
 expected_side_effects,       entrypoint (/internal/runs/start or the             actual_trajectory, response,
 expected_trajectory?         playground scheduled-fire shim), eval_mode=record   recorded_side_effects}
        │                  3. reactive-inner → response; durable-inner →              │
        │                     poll run_steps for trajectory (E-1 reader)             ▼ dispatch mode=scheduled
        ▼                  4. collect recorded_side_effects (E-2 seam)          score_response (LLM)
 playground_datasets                     │                                      score_trajectory (E-1, if durable)
 .mode='scheduled'                       ▼                                      score_side_effects (E-2)  ← headline
                          run (WS-3) + recorded calls (E-2)                     score_composite (weighted)
                                                                                       │
                                                                                       ▼  overall_score composite
                                                                               eval_passed auto-set (unchanged)
```

**Seam 1 — scheduled dataset editor.** `job_spec` is authored as JSON matching `AgentTrigger.input_payload`;
`expected_side_effects` as assertions (`e3/data-model.md` §2).

**Seam 2 — eval-runner scheduled branch.** `MODE=scheduled`: feed `job_spec` as `input_payload`, fire one run
via WS-3's real path with `eval_mode=record`. The **inner** execution is reactive or durable per the agent's
`execution_shape` — reactive-inner scores response + side_effects; durable-inner additionally scores the
trajectory (reusing E-1's `score_trajectory` over `run_steps`). One fire, one run — not a schedule loop (eval
fires immediately, it does not wait for cron).

**Seam 3 — scoring.** `score_side_effects` (E-2) is the **headline** dimension for scheduled; default weights
skew to it (e.g. `response 0.3 / trajectory 0.3 / side_effect 0.4` for durable-inner; `response 0.4 /
side_effect 0.6` for reactive-inner). Overridable per run via `eval_runs.dimension_weights`.

---

## 3. Migration / Schema

**None owned by E-3.** Reuses E-0's columns (`playground_datasets.mode='scheduled'`, `eval_runs.mode`/weights,
`eval_run_results.dimension_scores`/`eval_detail`/`trigger_payload`) + E-2's `tools.side_effecting`. The
`scheduled` **item** schema is a Pydantic/validation concern over `items` JSONB (`e3/data-model.md`). The
`job_spec` this item runs is recorded in `eval_run_results.trigger_payload` (E-0 column — mirrors
`PlaygroundRun.trigger_payload`).

---

## 4. Constitution / retro gates (condensed)

| Gate | How E-3 satisfies it |
|---|---|
| **Parity** | No new dispatch code — E-3 drives WS-3's shared scheduled path with a job-spec dataset; grep proves no scheduled-only eval fork. Side-effect scoring is E-2's one scorer. |
| **Ship the gate's producer** | Scheduled eval (reader) ships **with/after WS-3** (producer = real scheduled fire) + E-2 (producer = record seam). No fake-schedule gate. |
| **Fail-closed** | A scheduled run that never reaches terminal status **fails the item**; a side-effect that cannot be recorded (E-2) **fails the item** — never a silent pass. |
| **Golden-path per environment** | bash suite: author a `job_spec` dataset → launch a scheduled batch eval → the job's write is **recorded not delivered**, `score_side_effects` asserts it, composite + `eval_passed`. Fails (not skips) on missing WS-3 fixture. |
| **DoD #1/#2** | Playwright: author a `scheduled` item, launch eval, assert the recorded side-effect renders in results. Save→reload: `job_spec` + `expected_side_effects` survive. |
| **No-Bandaid** | Scheduled interpretation is the explicit `mode` discriminator; the job spec is fed as `input_payload` (the real production shape), not a special eval-only path. |

---

## 5. File Structure (created/modified — indicative)

| File | C/M | Responsibility |
|---|---|---|
| `services/eval-runner/main.py` | M | `mode=scheduled` branch: feed `job_spec` as `input_payload`, fire one run via WS-3's real path with `eval_mode=record`, collect trajectory (durable-inner) + recorded side-effects, call `/eval/score`. |
| `services/registry-api/routers/playground.py` | M | `/eval/score` `mode=scheduled` dispatch → response + (durable-inner) trajectory + side_effects. |
| `services/registry-api/routers/datasets.py` | M | Validate the `scheduled` item variant. |
| `services/registry-api/schemas.py` | M | `ScheduledDatasetItem` (`job_spec`, `expected_side_effects`, `expected_trajectory?`). |
| `studio/src/pages/DatasetsPage.tsx` | M | `scheduled` item editor (`job_spec` + `expected_side_effects`). |
| `studio/src/pages/EvalResultsPage.tsx` | M | Render `trigger_payload` (the job spec) + recorded side-effects + the side_effect dimension. |
| `scripts/e2e/suite-NN-eval-v2-scheduled.sh` | **C** | Scheduled: job-spec dataset → eval → recorded-not-delivered side-effect asserted + composite + `eval_passed`. |
| `scripts/e2e/run-all.sh` | M | Register the suite. |
| `studio/e2e/eval-v2-scheduled.spec.ts` | **C** | Playwright: scheduled author → eval → recorded side-effect renders (save→reload). |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump eval-runner, registry-api, studio. |
| `docs/experience/playground.md` | M | Scheduled datasets + side-effect eval. |

---

## 6. Tasks (dependency-ordered)

### T1 — `scheduled` dataset editor + validation
- **Files:** `DatasetsPage.tsx` (M), `datasets.py` (M), `schemas.py` (M), Vitest.
- **Contract:** `e3/data-model.md` §2 — `job_spec` + `expected_side_effects` (+ optional `expected_trajectory`);
  discriminated-union validation on save.
- **Acceptance:** a scheduled dataset is authorable; save→reload survives `job_spec` + `expected_side_effects`.
- **Deps:** E-0 (discriminator), E-2 (side-effect assertion shape). **Verify:** Playwright save→reload; Vitest.

### T2 — eval-runner scheduled branch (job-spec fire)
- **Files:** `services/eval-runner/main.py` (M), `k8s.py` (M — pass `MODE`).
- **Contract:** `MODE=scheduled`: feed `job_spec` as `input_payload`, fire one run via WS-3's real scheduled
  entrypoint with `eval_mode=record`; reactive-inner → response; durable-inner → poll `run_steps` (E-1 reader);
  collect recorded side-effects (E-2).
- **Acceptance:** a scheduled batch eval fires one run per item; recorded side-effects collected; **no real
  delivery**.
- **Deps:** **WS-3** (real fire path), E-2 (record seam), E-1 (durable reader). **Verify:** `ast.parse`;
  suite-NN scheduled happy path.

### T3 — `/eval/score` scheduled dispatch + weights
- **Files:** `routers/playground.py` (M), `judge.py` (M — reuse `score_side_effects`/`score_trajectory`).
- **Contract:** `mode=scheduled` composes response + (durable-inner) trajectory + `side_effect`; default weights
  skewed to side_effect.
- **Acceptance:** a scheduled item with a satisfied `expected_side_effects` scores high; a violated `never`
  side-effect fails.
- **Deps:** T2. **Verify:** unit fixtures; `grep -n "scheduled" routers/playground.py`.

### T4 — results render + suite + deploy
- **Files:** `EvalResultsPage.tsx` (M), `suite-NN-eval-v2-scheduled.sh` (C), `run-all.sh` (M),
  `eval-v2-scheduled.spec.ts` (C), `deploy-cpe2e.sh`+`values.yaml` (M), `docs/experience/playground.md` (M).
- **Acceptance:** results render the job spec + recorded side-effects + dimension; suite green (recorded-not-
  delivered assertion); tags bumped.
- **Deps:** T1–T3. **Verify:** `bash scripts/e2e/suite-NN-eval-v2-scheduled.sh`; `bash scripts/studio-e2e.sh`.

---

## 7. Gap Ledger

| Item | Status | Note |
|---|---|---|
| Scheduled path real | **hard dep → WS-3** | Payload-based eval only meaningful once schedules fire a real run. E-3 ships **with/after** WS-3, never before. |
| Cron-timing eval (does it fire at the right time?) | **deferred (intentional)** | E-3 fires immediately with the job spec; verifying cron/next-fire timing is WS-3's operate surface, not an eval dimension. |
| Alert-on-failure as an eval dimension | out of scope | WS-3 verifies alerting end-to-end; E-3 scores the run's behavior, not the alert transport. |
| Record-once cassette replay for scheduled | deferred → E-2 gap | Inherits E-2's mock-only limitation. |

**No orphan flags:** the `scheduled` item + `expected_side_effects` → read by the runner + `score_side_effects`;
`trigger_payload`/`dimension_scores` → read by results UI. All shipped together.

---

## 8. Execution Notes

- **E-3 is a consume + assert slice, not a build-the-schedule slice.** If a task tempts you to write scheduled
  dispatch or identity code, it belongs in WS-3 — stop and check (mirror WS-3's own §8 discipline).
- **Fire once, don't wait for cron.** Eval feeds the job spec and fires immediately; it does not sit on the
  scheduler. The realism is the **job-spec shape + real fire path + recorded side-effect**, not the timer.
- **Side-effects safe by E-2.** Every scheduled eval runs `eval_mode=record`; a real delivery under eval is a
  bug. Assert the downstream was not hit.
- **Bump eval-runner + registry-api + studio** in both files.
</content>

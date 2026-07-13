# E-0 Implementation Plan — Reactive parity + composite plumbing

**Slice:** Phase E-0 of Eval v2 (consolidated `eval-v2/plan.md` §2/§3, §8 sequencing). **Covers E-0 ONLY.**
**Depends on:** **WS-0 only (DONE)** — reactive eval already works today. E-0 has **no** durable/scheduled/
webhook dependency; it is the **foundation** every later E-phase (E-1…E-6) extends and **ships first**.
**Companion artifacts:** consolidated `eval-v2/data-model.md` (§2 discriminated-union dataset schema, the
`reactive` variant + composite score columns).

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

> **Grounding note (E-0 is grounded-now).** E-0's only dependency is WS-0 (shipped) and today's reactive
> eval path. Its migration numbers are **provisional** (head is `0058` after WS-0 → E-0 takes `0059`/`0060`;
> confirm at mint time). Everything E-0 touches — `PlaygroundDataset`, `EvalRun`/`EvalRunResult`, `judge.py`,
> `services/eval-runner/main.py`, `routers/eval_runner.py`, `DatasetsPage`/`EvalResultsPage` — exists today.

---

## 1. Goal

Turn evaluation's storage + judge from **response-only** into a **mode-aware** shape **without changing any
reactive behavior**. E-0 lands the discriminated-union dataset schema, the composite-score plumbing, and the
judge-**scorer-library** skeleton that E-1…E-6 extend — while everything is still reactive/text, so the
refactor is small and de-riskable. Concretely, after E-0:

1. **Datasets + eval runs carry a `mode`.** `playground_datasets.mode` (default `reactive`) and
   `eval_runs.mode`; a `DatasetItem` discriminated union whose `reactive` variant accepts today's
   `{input, expected_output}` via a `kind`-defaulting validator (full back-compat — existing rows read
   back as `mode=reactive`).
2. **Scores are composite from day one.** `eval_run_results` gains dimension columns; the score is a
   weighted composite. For reactive, `dimension_scores = {response: x}` and `composite = x` — **numerically
   identical to today's judge score** (behavior-neutral parity is the safe seam the whole refactor lands on).
3. **One scoring door.** `POST /playground/eval/score` dispatches by `mode`; E-0 ships only the response
   scorer, so it is a pure refactor of `judge_for_eval` behind the new endpoint. E-1…E-6 add mode-specific
   scorers behind this same door — they never re-implement scoring in the runner.
4. **The `eval_passed` gate is untouched.** The composite reduces to the response score for reactive, so the
   publish gate (`eval_runner.py` auto-set) needs **zero change**.

**Out of scope:** any mode-specific scorer (trajectory→E-1, side-effect→E-2/E-3/E-4, filter→E-4, member-path
→E-5) and the record/replay seam (E-2). E-0 is plumbing + reactive parity ONLY.

## 2. Architecture — the scorer-library seam

```
POST /playground/eval/score {mode, item, run_id?, input, response, ...}
        │  dispatch by mode  (E-0 wires the reactive branch; E-1..E-6 add branches here — one door)
        ▼
   judge.py  (scorer library — E-0 skeleton)
     ├─ score_response(input, response, rubric)   → LLM-as-judge (E-0; bias-mitigation hardened)
     ├─ score_trajectory(...)   ← E-1   ┐
     ├─ score_tool_calls(...)   ← E-1   │  code scorers, added behind the SAME door
     ├─ score_side_effects(...) ← E-2   │  in later phases — E-0 only ships response
     ├─ score_filter(...)       ← E-4   │
     └─ score_member_path(...)  ← E-5   ┘
        │
        ▼  composite = weighted mean of dimension_scores  (reactive: == response, byte-identical to today)
```

**Reuse, don't fork (parity).** E-0 is a refactor of the existing `judge_for_eval` + `eval-runner` reactive
path *in place*: the runner keeps its two branches (agent-stream vs workflow-poll) and its keyword-match
fallback; it just scores via `/eval/score` and records `dimension_scores`. No parallel eval path is created.

## 3. Migration / Schema

Provisional `0059`/`0060` (head `0058` after WS-0 — confirm at mint). Additive, guarded, idempotent:
- `playground_datasets.mode VARCHAR NOT NULL DEFAULT 'reactive'` + CHECK `IN (reactive,durable,scheduled,webhook,workflow)`.
- `eval_runs.mode VARCHAR NOT NULL DEFAULT 'reactive'` (resolved from the executable at create; validated == `dataset.mode`).
- `eval_run_results` dimension columns (e.g. `dimension_scores JSONB`, existing `judge_score`/`overall_score`
  stay the **composite** so the gate is unchanged).
See consolidated `eval-v2/data-model.md` §2 (discriminated union) for the item shapes.

## 4. Constitution / retro gates (condensed)
- **Behavior-neutral parity (the load-bearing gate):** a reactive batch eval must produce the **same
  pass/fail and the same score** as today. Asserted by a regression parity test on a fixture set.
- **Ship the gate's producer:** the `dimension_scores` reader (E-6 gate polish) is not wired yet, but the
  columns + composite are populated now so nothing reads an empty gate.
- **No-Bandaid:** the discriminated union makes an illegal `{mode, item-shape}` pair unrepresentable at the
  schema boundary; the runner dispatches by an explicit `mode`, never by sniffing the item.
- **Reason from running product:** E-0 verifies today's judge/runner/dataset shapes before refactoring them.

## 5. File Structure
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/alembic/versions/0059_*`, `0060_*` | **C** | dataset/eval `mode` + dimension columns (§3). |
| `services/registry-api/models.py`, `schemas.py` | M | `mode` columns; `DatasetItem` discriminated union + reactive validator. |
| `services/registry-api/judge.py` | M | scorer-library skeleton; `score_response`; composite reducer. |
| `services/registry-api/routers/playground.py` | M | `POST /playground/eval/score` (mode dispatch; reactive branch). |
| `services/registry-api/routers/eval_runner.py` | M | `create_eval_run` resolves+validates `mode`; record `dimension_scores`. |
| `services/eval-runner/main.py`, `k8s.py` | M | runner reads `MODE`; reactive branch scores via `/eval/score`. |
| `studio/src/pages/{DatasetsPage,EvalResultsPage}.tsx` (+ api types, tests) | M | dataset mode selector (reactive default); dimension-score render. |
| `scripts/e2e/suite-NN-eval-mode-plumbing.sh` | **C** | reactive parity + round-trip; register in `run-all.sh`. |
| `docs/experience/playground.md` | M | mode-aware datasets/eval note. |

## 6. Tasks (dependency-ordered)

### T0.1 — Discriminator + composite schema (migrations + models/schemas)
- **Files:** migrations `≥0059`/`≥0060`, `models.py`, `schemas.py`.
- **Contract:** `playground_datasets.mode` (default `reactive`), `eval_runs.mode`, `eval_run_results`
  dimension cols; `DatasetItem` discriminated union with a `reactive` variant accepting today's
  `{input, expected_output}` via a `kind`-defaulting validator.
- **Acceptance:** existing datasets read back as `mode=reactive`; a new reactive dataset round-trips; mappers configure.
- **Deps:** none. **Verify:** `ast.parse` + `configure_mappers()`; `alembic upgrade head` on a seeded DB.

### T0.2 — Judge scorer-library skeleton + `/eval/score` (response scorer only; composite=response)
- **Files:** `judge.py`, `routers/playground.py`.
- **Contract:** `POST /playground/eval/score {mode, item, run_id?, input, response, …}` →
  `{composite, dimension_scores, detail}`. For `mode=reactive`, `dimension_scores={response:x}`,
  `composite=x` — **numerically identical to today's `judge_for_eval`**. Bias-mitigation prompt hardening.
- **Acceptance:** reactive score equals the pre-change judge score on a fixture set (regression parity).
- **Deps:** T0.1. **Verify:** unit parity test; old `/playground/judge` callers migrated.

### T0.3 — eval-runner mode dispatch (reactive branch == today) + write dimension fields
- **Files:** `services/eval-runner/main.py`, `routers/eval_runner.py`, `k8s.py`.
- **Contract:** runner reads `MODE`; reactive branch identical to today but scores via `/eval/score` and
  records `dimension_scores`; `create_eval_run` resolves `mode` from the executable + validates vs `dataset.mode`.
- **Acceptance:** a reactive batch eval yields the same pass/fail as today + populated `dimension_scores`;
  the `eval_passed` auto-set still fires (unchanged).
- **Deps:** T0.2. **Verify:** suite-NN reactive case; keyword-match fallback gated behind judge-unavailable only.

### T0.4 — Studio: dataset mode selector (reactive editor unchanged) + dimension-score render
- **Files:** `DatasetsPage.tsx`, `EvalResultsPage.tsx`, api types, Vitest.
- **Acceptance:** `npm run typecheck` clean; mode selector defaults reactive; results show a response-score
  column (others empty for reactive). Playwright reactive author→eval→result.
- **Deps:** T0.1–T0.3.

## 7. Gap Ledger
| Item | Status | Note |
|---|---|---|
| Mode-specific scorers (trajectory/side-effect/filter/member-path) | **deferred → E-1…E-5** | E-0 ships only the response scorer + the dispatch door. |
| Record/replay side-effect seam | **deferred → E-2** | E-0 does not touch the governed tool path. |
| `eval_passed` composite-threshold / dimension weighting UI | **deferred → E-6** | Columns populated now; the richer gate reader lands in E-6. |

No orphans: `/eval/score` is called by the eval-runner (T0.3); the `mode` columns are read by `create_eval_run` + the runner; `dimension_scores` are rendered by `EvalResultsPage`.

## 8. Execution Notes
- **Behavior-neutral is the whole point.** The composite must equal today's reactive score to the digit —
  ship E-0 only when the parity fixture is green. This is the seam every later scorer lands on.
- **One door, forever.** Every later phase adds a scorer branch behind `/eval/score`; the runner never
  re-implements scoring. If a later phase is tempted to score in the runner, that's the bug E-0 exists to prevent.
- **Ship E-0 first, in parallel with WS-1** — it has no WS dependency and de-risks the schema+judge refactor
  while everything is still text-only (consolidated `plan.md` §8).

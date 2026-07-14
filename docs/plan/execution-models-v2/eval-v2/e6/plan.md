# E-6 Implementation Plan — Regression/CI + eval-gate polish

> ✅ **Verification bar (MANDATORY): the no-fakes suite-58/59 standard** — see the eval-v2 README
> "Verification standard". DONE only when the REAL per-mode suites from E-0…E-5 (each a real dataset → real
> `EvalRun` → real judge → persisted score) run green together in CI on `run-all.sh`, and the eval-gate
> polish (composite threshold / dimension weighting) is proven by a REAL run that flips `eval_passed` both
> ways. **Phase-specific:** E-6 is the composition gate — it may add NO new fake; if a mode's real suite is
> flaky (real LLM), stabilize it (deployed agents, generous timeouts) rather than downgrade to a mock.

**Slice:** Phase E-6 of Eval v2 (consolidated `eval-v2/plan.md` §6 Phase E-6, §8 sequencing). **Covers E-6 ONLY.**
**Depends on:** **E-0…E-5 (all mode scorers + the composite plumbing landed per mode)**. E-6 **composes** the
finished scorers into a regression/CI harness + threshold configuration; it builds no new scorer.
**No new companion artifact** — E-6 reuses the consolidated `data-model.md` (`eval_runs.pass_threshold` +
`dimension_weights`, already added in E-0) and every phase's item schema.

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

> **Grounding note (E-6 is banner-indicative — composes E-0…E-5).** E-6's harness reuses the **same**
> eval-runner Job (`services/eval-runner/main.py`) invoked headless, and the **shipped** `eval_passed` auto-set
> (`routers/eval_runner.py:~287-330`, `EVAL_PASS_THRESHOLD` gate). The `pass_threshold` + `dimension_weights`
> columns are E-0's (already added). E-6 is a wiring + polish slice on top of finished parts — re-ground the
> CI invocation surface + the threshold read path at `tasks.md` mint time.

---

## 1. Goal

Turn the mode-aware scorers into a **repeatable regression gate**: the same eval-runner invoked headless (in
CI or a scheduled internal run) against a **pinned** dataset, with **configurable** per-run `pass_threshold` +
`dimension_weights`, auto-setting `eval_passed` on the composite. Concretely, after E-6:

1. **Headless regression eval.** The eval-runner runs against a pinned per-mode dataset without the interactive
   UI in the loop — invocable from CI (or a scheduled internal run), producing dimension scores + a composite +
   a pass/fail against the run's threshold.
2. **Per-run thresholds + weights are honored.** `eval_runs.pass_threshold` overrides the global
   `EVAL_PASS_THRESHOLD` per run; `eval_runs.dimension_weights` sets the composite weighting per run (both E-0
   columns, now **read** end-to-end). A team can require `trajectory ≥ 0.9` for a durable agent by weighting
   trajectory heavily + raising the threshold.
3. **The gate composes all modes.** The full-mode matrix (reactive/durable/scheduled/webhook/workflow) runs
   through one runner; `eval_passed` auto-sets on the composite for agent **and** workflow versions (unchanged
   mechanism, `eval_runner.py:~301-330`).
4. **The core Eval v2 win is proven in CI.** A dropped **trajectory** score fails the gate even when the
   **response** is still correct — the regression a response-only gate would miss.
5. **Docs are current.** `docs/experience/playground.md` describes per-mode datasets + the regression gate + the
   threshold/weight config; the eval-gate section of the spec reflects the composite.

**Alignment Check:** the ultimate goal is *trustworthy publish that stays trustworthy over time*. Per-mode
scorers (E-1…E-5) make the **first** publish meaningful; E-6 makes it **repeatable** — a later version that
silently regresses its trajectory (right answer, wrong tools) is caught by the same gate, headless, in CI. We
keep the `eval_passed` composite gate **unchanged** (consolidated decision) — E-6 adds configurability +
repeatability behind the same scalar, never a second gate mechanism.

**Out of scope:** judge calibration / human-agreement study (consolidated `plan.md` §7 — we apply known
bias-mitigation best-practices, not new research); cross-agent leaderboards; a standalone eval-authoring
product surface; per-item history rows (JSONB items suffice — gap ledger).

---

## 2. Architecture — one runner, headless, threshold-configured

```
 CI / scheduled internal run                 eval-runner Job (E-0…E-5, headless)         Gate (unchanged)
 ──────────────────────────                  ───────────────────────────────────        ────────────────
 invoke against a PINNED dataset          →  MODE dispatch (reactive|durable|          →  PATCH /eval-runs/{id}
 (per mode) + eval_runs.{pass_threshold,     scheduled|webhook|workflow) → score          {overall_score=composite}
 dimension_weights}                          each item via /eval/score →                     │
        │                                    composite = weighted_mean(dims, weights)         ▼ if composite >= pass_threshold
        ▼                                             │                                   eval_passed = True
 pinned golden dataset (JSONB items)                 ▼                                    (agent OR workflow version)
                                          dimension_scores + eval_detail per item             │
                                                                                              ▼
                                                                              a dropped trajectory score
                                                                              fails the gate (response still OK)
```

**Seam 1 — headless invocation.** The eval-runner Job already runs from env (`DATASET_ID`, `AGENT_NAME`,
`EVAL_RUN_ID`, `MODE`, …) — E-6 makes it invocable from CI (or a scheduled internal run) against a pinned
dataset, no UI. No new runner; the same Job, driven headless.

**Seam 2 — threshold + weights read end-to-end.** `pass_threshold` + `dimension_weights` (E-0 columns) are
**read** by `/eval/score` (composite weighting) and by the gate (`overall_score >= pass_threshold` instead of
only the global `EVAL_PASS_THRESHOLD`). This closes the E-0 columns' reader loop for the configurable case.

**Seam 3 — gate unchanged, composite behind it.** `eval_passed` auto-set (`eval_runner.py:~301-330`) stays the
wire; E-6 only ensures the composite (with per-run weights) feeds it and that the per-run threshold is honored.

---

## 3. Migration / Schema

**None owned by E-6.** Reuses E-0's `eval_runs.pass_threshold` + `dimension_weights` (now read end-to-end) and
every phase's item schema. No DDL.

---

## 4. Constitution / retro gates (condensed)

| Gate | How E-6 satisfies it |
|---|---|
| **Parity** | One eval-runner, invoked headless — CI reuses the exact Job the UI launches; no CI-only eval fork. The gate is the shipped `eval_passed` auto-set, not a second mechanism. |
| **Ship the gate's producer** | E-6 wires **readers** (per-run threshold + weights) to E-0's **already-shipped** columns — closes the E-0 reader loop, no orphan gate. |
| **Golden-path per environment** | bash suite: the full-mode matrix runs headless; a pinned dataset gates a version; a deliberately-regressed **trajectory** (response still correct) **fails** the gate. Per-run `pass_threshold`/`weights` honored. Fails (not skips) on missing fixture. |
| **DoD #1/#2** | Playwright (or the existing eval-launch flow): configure a per-run threshold + weights in the launch surface, run, assert the gate outcome renders. Save→reload: `pass_threshold`/`weights` survive on the eval run. |
| **DoD #3 no orphan code** | `pass_threshold`/`dimension_weights` (E-0 columns) get their **reader** here (composite weighting + gate threshold). Grep-for-reader is a task gate. |
| **DoD #6 reason from running product** | E-6 verifies the shipped `eval_passed` auto-set + `EVAL_PASS_THRESHOLD` behavior before extending it; updates `docs/experience/playground.md` + the spec's eval-gate section to the composite reality. |
| **No-Bandaid** | Per-run threshold/weights are explicit `eval_runs` columns read end-to-end — not a hardcoded constant or an env sniff per mode. |

---

## 5. File Structure (created/modified — indicative)

| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/routers/eval_runner.py` | M | Gate reads `run.pass_threshold` (fallback to `EVAL_PASS_THRESHOLD`) for the `eval_passed` auto-set; `create_eval_run` accepts per-run `pass_threshold`/`dimension_weights`. |
| `services/registry-api/routers/playground.py` | M | `/eval/score` reads `eval_runs.dimension_weights` for the composite (per-run weighting) — verify wired for every mode. |
| `services/eval-runner/main.py` | M | Headless invocation path (CI/scheduled); pass through `pass_threshold`/`weights`; full-mode matrix support (verify each MODE branch composes correctly). |
| `studio/src/pages/EvalResultsPage.tsx` / launch surface | M | Surface per-run threshold + weights on launch; render the composite + per-dimension pass/fail against the run's threshold. |
| `scripts/e2e/suite-NN-eval-v2-regression.sh` | **C** | Full-mode matrix headless; a regressed trajectory fails the gate; per-run threshold/weights honored; `eval_passed` auto-set. |
| `scripts/e2e/run-all.sh` | M | Register the suite. |
| `.github/workflows/*` or a scheduled internal run (indicative) | M/**C** | Invoke the regression eval in CI (or as a scheduled internal run) against a pinned dataset — confirm the CI surface at impl. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api, eval-runner, studio. |
| `docs/experience/playground.md` + `docs/spec.md` (eval-gate section) | M | Per-mode datasets + regression gate + threshold/weight config; composite `eval_passed`. |

---

## 6. Tasks (dependency-ordered)

### T1 — Per-run threshold + weights read end-to-end
- **Files:** `routers/eval_runner.py` (M), `routers/playground.py` (M).
- **Contract:** `create_eval_run` accepts `pass_threshold`/`dimension_weights`; the `eval_passed` auto-set reads
  `run.pass_threshold` (fallback `EVAL_PASS_THRESHOLD`); `/eval/score` weights the composite by
  `run.dimension_weights`.
- **Acceptance:** a run with `pass_threshold=0.9` + trajectory-heavy weights fails a durable agent that passes
  under the defaults; the composite reflects the weights.
- **Deps:** E-0 (columns), E-1…E-5 (dims exist). **Verify:** `ast.parse`; unit/e2e threshold + weight cases;
  `grep -n "pass_threshold\|dimension_weights" services/registry-api/routers/*.py` → reader present.

### T2 — Headless full-mode regression harness
- **Files:** `services/eval-runner/main.py` (M), `suite-NN-eval-v2-regression.sh` (C), `run-all.sh` (M).
- **Contract:** the eval-runner runs the full-mode matrix headless against pinned datasets; a regressed
  trajectory (response still correct) fails the gate; `eval_passed` auto-set on the composite for agent +
  workflow versions.
- **Acceptance:** the matrix suite is green; the trajectory-regression case fails the gate (the core Eval v2
  win); per-run threshold/weights honored.
- **Deps:** T1. **Verify:** `bash scripts/e2e/suite-NN-eval-v2-regression.sh`.

### T3 — CI / scheduled invocation
- **Files:** CI workflow or a scheduled internal run (M/C — **confirm surface at impl**).
- **Contract:** the regression eval runs on a schedule (or CI trigger) against a pinned dataset, reporting the
  gate outcome.
- **Acceptance:** the regression eval runs unattended and reports pass/fail; a pinned-dataset regression is
  caught without a human launching it.
- **Deps:** T2. **Verify:** trigger the CI/scheduled run; confirm the gate outcome is reported.

### T4 — Launch/results polish + docs + deploy
- **Files:** `EvalResultsPage.tsx`/launch surface (M), `docs/experience/playground.md` (M), `docs/spec.md` (M),
  `deploy-cpe2e.sh`+`values.yaml` (M).
- **Acceptance:** per-run threshold + weights configurable on launch + surviving reload; composite + per-dim
  pass/fail render; experience doc + spec eval-gate section updated to the composite; tags bumped.
- **Deps:** T1–T3. **Verify:** `cd studio && npm run typecheck && npm run test`; `bash scripts/studio-e2e.sh`.

---

## 7. Gap Ledger

| Item | Status | Note |
|---|---|---|
| Judge calibration / human-agreement study | **deferred (intentional)** | E-6 applies known bias-mitigation best-practices (`research.md` §4.1); a calibration harness is out of scope. |
| Cross-agent eval leaderboards | deferred (intentional) | Out of scope; the gate is per-version, not comparative. |
| Per-item history table (vs JSONB items) | **not-yet-needed (debt, low)** | `playground_datasets.items` JSONB suffices; promote to rows only if per-item labels/history grow heavy (consolidated ledger). |
| CI surface specifics | **must confirm at impl** | Whether the regression eval runs as a GitHub Action or a scheduled internal run is a deploy-environment choice — confirm at `tasks.md` mint time. |
| Flaky-judge retry / quorum on the LLM dims | not-yet-hardened (debt, low) | Deterministic dims are stable; the LLM `response`/rubric dims may need a retry/quorum for CI stability — a follow-up if flakiness shows. |

**No orphan flags:** `pass_threshold`/`dimension_weights` (E-0 columns) get their **reader** in T1; the
regression harness reuses the shipped runner + gate. No new producer without a reader.

---

## 8. Execution Notes

- **E-6 composes, it does not build scorers.** Every scorer is E-1…E-5's; E-6 wires threshold/weights + a
  headless harness. If a task tempts you to write a new scorer, it belongs in an earlier phase — stop and check.
- **The core win is a CI test, not a comment.** Assert a version whose **trajectory** regressed (response still
  correct) **fails** the gate — the regression a response-only gate would miss, now caught headless.
- **Gate unchanged, configurability behind it.** `eval_passed` auto-set stays the wire; per-run threshold +
  weights are explicit `eval_runs` columns read end-to-end — no second gate, no hardcoded per-mode constant.
- **Close the E-0 reader loop.** `pass_threshold`/`dimension_weights` were added in E-0 with a forward promise;
  E-6 is where they get their reader — grep proves it (No-Bandaid: no orphan column left from E-0).
- **Bump registry-api + eval-runner + studio** in both files; update the experience doc + the spec eval-gate
  section to the composite reality (DoD #3/#6).
</content>

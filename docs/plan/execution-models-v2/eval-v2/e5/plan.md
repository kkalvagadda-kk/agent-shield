# E-5 Implementation Plan — Workflow run-tree eval (per-member path)

> ✅ **Verification bar (MANDATORY): the no-fakes suite-58/59 standard** — see the eval-v2 README
> "Verification standard". DONE only when a REAL e2e is green in `run-all.sh`: a REAL durable workflow run
> (the same real dispatch→pod→callback path `suite-59` exercises — NO faked `_run_step`) → the real run
> tree (parent + per-member children with real `run_steps`) → `score_member_path` scores each member's real
> path → persisted `dimension_scores` (save→reload), plus a real Playwright journey. **Phase-specific:**
> the per-member scores must come from a REAL run tree (build on the deployed durable agents like suite-59),
> not a synthetic tree fixture.

**Slice:** Phase E-5 of Eval v2 (consolidated `eval-v2/plan.md` §6 Phase E-5, §8 sequencing, `data-model.md`
§2.5). **Covers E-5 ONLY.**
**Depends on:** **WS-1 D4 (DONE — durable members write child `run_steps` in the run tree via
`_dispatch_durable_member`)** + **E-1 (the `score_trajectory` scorer, reused at member granularity)**.
**No new companion artifact** — E-5 reuses the consolidated `data-model.md` §2.5 (`workflow` item) + E-1's
trajectory scorer; the only new data is the `expected_member_path` (a trajectory at member granularity).

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

> **Grounding note (E-5 is grounded-now).** Verified against the 2026-07-13 tree: the workflow run tree is
> served at `GET /api/v1/workflows/{id}/runs/{run_id}/tree` (`routers/composite_workflows.py:469`,
> `WorkflowRunTreeResponse` = parent + child runs), and **the eval-runner already polls it** for the current
> workflow branch (`services/eval-runner/main.py:45-72`, `_run_workflow_item`). Child runs nest via
> `agent_runs.parent_run_id` (`models.py:1474`); durable members write child `run_steps` through
> `workflow_orchestrator._dispatch_durable_member` (`workflow_orchestrator.py:115`, "one `run_steps` row per
> node/tool boundary … under `child_id` in the run tree"). So the member path + per-member steps are **real
> and readable today** — E-5 adds the scorer + item schema, not the substrate.

---

## 1. Goal

Evaluate **workflows** on their **run tree**: score the **member path** (which members ran, in order — a
trajectory at member granularity) plus an optional **per-member rubric** that drops one zoom level into that
child's own `run_steps`, plus the final response. Concretely, after E-5:

1. **`score_member_path` exists (code, reuses E-1).** A member-granularity trajectory scorer that walks the
   run tree (`parent_run_id` nesting) and compares the ordered child members to `expected_member_path` under a
   `match_mode` — the **same** `score_trajectory` machinery as E-1, applied to members instead of tool steps.
2. **A workflow dataset is authorable.** `DatasetsPage` gains the `workflow` item editor: `input_message` (or
   `input_payload` for triggered workflows), optional `expected_output`, `expected_member_path` (members in
   order), and optional `per_member` rubric (`{member: {rubric}}`). Validated on save.
3. **The eval-runner has a workflow-tree branch.** Today's workflow branch triggers the workflow + polls the
   tree for the parent's final output only. E-5 extends it: walk the tree, extract the **member path** from the
   child runs, score `expected_member_path`; for each `per_member` rubric, read that child's `run_steps` and
   score the member's behavior; score the final response.
4. **Per-member zoom is a real read.** A durable member's own `run_steps` (WS-1 D4) are read from the child
   run — the per-member rubric scores that member's trajectory/behavior, not just the workflow's final answer.
5. **The gate stays the wire.** The workflow composite feeds `overall_score` → `eval_passed` unchanged (the
   auto-set already handles workflow versions, `eval_runner.py:~318-330`).

**Alignment Check:** the ultimate goal is *trustworthy publish for workflows*. A workflow can produce a correct
final answer while routing through the **wrong members** (a supervisor that skipped triage, a conditional that
took the wrong branch) — response-only eval misses it. E-5 restores the gate's meaning by scoring the member
path over the real run tree. We reuse E-1's trajectory scorer at a coarser granularity — one scorer, two zoom
levels — rather than forking a workflow-only path.

**Out of scope:** within-member crash-restart trajectory (WS-1 D4's documented limitation — a mid-member crash
loses in-flight member progress; E-5 scores what the tree records); tool-level scoring **inside** a reactive
(non-durable) member that writes no `run_steps` (only durable members write child steps — reactive members are
scored at member-path + response granularity); making workflow modes durable (WS-1 D3, shipped — E-5 consumes).

---

## 2. Architecture — walk the run tree, reuse E-1 at member granularity

E-5 adds **no new producer**. WS-1 D4 made the durable member run tree real; E-5 adds the **member-path
reader** + a workflow interpretation branch. Same trajectory machinery as E-1, one zoom out.

```
 Authoring                 Interpretation (eval-runner workflow branch)          Scoring (judge.py)
 ─────────                 ──────────────────────────────────────────────       ──────────────────
 DatasetsPage workflow  →  1. POST /workflows/{id}/runs {input_message}       →  POST /playground/eval/score
 editor: input_message,    2. poll GET /workflows/{id}/runs/{run_id}/tree        {mode:"workflow", item,
 expected_member_path,        to terminal (parent completed/failed)                member_path, response,
 per_member{rubric}        3. member_path = ordered child runs (agent_name)        per_member_steps{...}}
        │                     from the tree (parent_run_id nesting)                    │
        │                  4. per_member: read that child's run_steps               ▼ dispatch mode=workflow
        ▼                     (GET /agent-runs/{child_id}/steps)                   score_member_path (E-1 reuse) ← NEW
 playground_datasets                     │                                        score_response (LLM)
 .mode='workflow'                        ▼                                        per_member rubric (LLM, optional)
                          run tree (WS-1 D4) + child run_steps                    score_composite (weighted)
                                                                                       │
                                                                                       ▼  overall_score composite
                                                                               eval_passed auto-set (workflow ver)
```

**Seam 1 — workflow dataset editor.** `expected_member_path` (members in order) + optional `per_member` rubric
(consolidated `data-model.md` §2.5).

**Seam 2 — eval-runner workflow branch.** Extends the existing `_run_workflow_item` (`main.py:45`): after the
parent reaches terminal, walk the tree's child runs into an ordered `member_path` (each child's `agent_name`);
for each `per_member` rubric, read the child's `run_steps` via `GET /agent-runs/{child_id}/steps`
(`agent_runs.py:193`). Call `/eval/score` with `mode=workflow`.

**Seam 3 — `score_member_path` (reuse E-1).** The member path is a **trajectory of members** — feed the ordered
child members as the "steps" into `score_trajectory`'s match-mode machinery (`exact|ordered|superset|
unordered`). The per-member rubric is an LLM `score_response`-over-`rubric` on that member's steps (reference-
free, `research.md` §4.1). One scorer, applied at two granularities (tool steps in E-1, members in E-5).

---

## 3. Migration / Schema

**None owned by E-5.** Reuses E-0 columns (`playground_datasets.mode='workflow'`, `eval_runs.mode`/weights,
`eval_run_results.dimension_scores`/`eval_detail`/`run_id`). The `workflow` **item** schema (with
`expected_member_path` + `per_member`) is a Pydantic/validation concern over `items` JSONB. `run_id` deep-links
the results UI to the workflow run tree. No DDL. The run tree itself (parent + child `AgentRun` via
`parent_run_id`) and child `run_steps` are shipped (WS-1 D4).

---

## 4. Constitution / retro gates (condensed)

| Gate | How E-5 satisfies it |
|---|---|
| **Parity = shared code** | `score_member_path` **reuses** E-1's `score_trajectory` match-mode machinery at member granularity — one trajectory scorer, two zoom levels, no workflow-only fork. One tree source (`/runs/{id}/tree`), one steps source (`/agent-runs/{child}/steps`). |
| **Ship the gate's producer** | The member-path reader (E-5) ships **because** its producer (real run tree + durable child steps, WS-1 D4) is live. No fake-tree gate. |
| **Golden-path per environment** | bash suite: author a workflow dataset → launch eval → assert `member_path` dimension + composite + `eval_passed` on the **workflow version**; a wrong-member-route case scores `<1.0`; a `per_member` rubric drops into a child's steps. Fails (not skips) on missing workflow fixture. |
| **DoD #1/#2** | Playwright: author a `workflow` item, launch eval, assert member-path + per-member render in results, deep-link `run_id` → run tree. Save→reload: `expected_member_path` + `per_member` survive. |
| **DoD #3 no orphan code** | `score_member_path` → called by `/eval/score` workflow dispatch → runner; `member_path`/per-member dims → read by results UI. All shipped together. |
| **No-Bandaid** | Workflow interpretation is the explicit `mode` discriminator; the member path reads the **real** run tree, not a re-derived member list. |

---

## 5. File Structure (created/modified — indicative)

| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/judge.py` | M | `score_member_path(member_runs, expected_member_path, match_mode)` — reuses `score_trajectory` machinery at member granularity; optional per-member rubric via `score_response`. |
| `services/registry-api/routers/playground.py` | M | `/eval/score` `mode=workflow` dispatch → member_path + response + (optional) per-member rubric. |
| `services/registry-api/routers/datasets.py` | M | Validate the `workflow` item variant. |
| `services/registry-api/schemas.py` | M | `WorkflowDatasetItem` (`input_message`/`input_payload`, `expected_member_path`, `per_member`). |
| `services/eval-runner/main.py` | M | Extend `_run_workflow_item`: walk the tree → ordered `member_path`; read child `run_steps` for `per_member`; call `/eval/score` mode=workflow; write dimension fields + `run_id`. |
| `studio/src/pages/DatasetsPage.tsx` | M | `workflow` item editor (`expected_member_path` + `per_member`). |
| `studio/src/pages/EvalResultsPage.tsx` | M | Render member-path dimension + per-member scores; deep-link `run_id` → run tree (member zoom). |
| `scripts/e2e/suite-NN-eval-v2-workflow.sh` | **C** | Workflow: author dataset → eval → member-path dim + composite + `eval_passed` on the workflow version; wrong-route → `<1.0`. |
| `scripts/e2e/run-all.sh` | M | Register the suite. |
| `studio/e2e/eval-v2-workflow.spec.ts` | **C** | Playwright: workflow author → eval → member-path render (save→reload). |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api, eval-runner, studio. |
| `docs/experience/playground.md` | M | Workflow run-tree eval + per-member. |

---

## 6. Tasks (dependency-ordered)

### T1 — `score_member_path` (reuse E-1 trajectory machinery)
- **Files:** `judge.py` (M), `schemas.py` (M).
- **Contract:** `score_member_path(member_runs, expected_member_path, match_mode) -> (float, detail)` — feed the
  ordered child members as the trajectory "steps" into the same match-mode compare as `score_trajectory`; emit a
  member-diff detail. Optional per-member rubric scored via `score_response` over that member's `run_steps`.
- **Acceptance:** members-in-expected-order → `1.0`; a skipped/wrong member under `ordered` → `<1.0`;
  `superset` allows extra members; a `per_member` rubric scores a child's behavior.
- **Deps:** E-1 (`score_trajectory` exists). **Verify:** unit fixtures; `grep -n "def score_member_path" judge.py`.

### T2 — eval-runner workflow-tree branch (walk tree → member path → per-member steps)
- **Files:** `services/eval-runner/main.py` (M).
- **Contract:** extend `_run_workflow_item`: after the parent is terminal, build `member_path` from the tree's
  child runs (ordered `agent_name`); for each `per_member` rubric, read `GET /agent-runs/{child_id}/steps`;
  call `/eval/score` mode=workflow; write `dimension_scores`+`eval_detail`+`run_id`.
- **Acceptance:** a workflow batch eval yields a `member_path` + `response` composite; per-member rubric reads a
  child's steps; the recorded result carries `run_id` (deep-linkable).
- **Deps:** T1, **WS-1 D4 (durable child steps — shipped)**. **Verify:** `ast.parse`; suite-NN workflow happy path.

### T3 — `/eval/score` workflow dispatch + weights
- **Files:** `routers/playground.py` (M).
- **Contract:** `mode=workflow` composes `member_path` + `response` + (optional) per-member; default weights
  (e.g. `member_path 0.4 / response 0.4 / per_member 0.2`), overridable.
- **Acceptance:** a workflow item with the right member path + correct final answer scores high; a wrong route
  with a correct answer fails the member-path dimension.
- **Deps:** T2. **Verify:** unit fixtures; `grep -n "workflow" routers/playground.py`.

### T4 — workflow dataset editor + results render + suite + deploy
- **Files:** `DatasetsPage.tsx` (M), `datasets.py` (M), `EvalResultsPage.tsx` (M),
  `suite-NN-eval-v2-workflow.sh` (C), `run-all.sh` (M), `eval-v2-workflow.spec.ts` (C),
  `deploy-cpe2e.sh`+`values.yaml` (M), `docs/experience/playground.md` (M), Vitest.
- **Acceptance:** workflow dataset authorable (save→reload survives `expected_member_path` + `per_member`);
  results render member-path + per-member + `run_id` deep-link; suite green (wrong-route case); `eval_passed`
  auto-set on the **workflow version**; tags bumped.
- **Deps:** T1–T3. **Verify:** `bash scripts/e2e/suite-NN-eval-v2-workflow.sh`; `bash scripts/studio-e2e.sh`;
  `cd studio && npm run typecheck && npm run test`.

---

## 7. Gap Ledger

| Item | Status | Note |
|---|---|---|
| Within-member crash-restart trajectory | **deferred (intentional) → WS-1 D4 limitation** | A mid-member crash loses in-flight member progress (WS-1 D4 documented limitation); E-5 scores what the tree records, not lost in-flight steps. |
| Tool-step scoring inside a **reactive** (non-durable) member | **by-design boundary** | Only durable members write child `run_steps`; reactive members are scored at member-path + response granularity. To score a member's tools, author it durable (WS-1). |
| Semantic member-name equivalence | deferred (intentional) | Member names matched exactly (same boundary as E-1's step-name matching). |
| Deep multi-level nesting (workflow-of-workflows) | not-yet-needed (debt, low) | E-5 scores one member level; deeper nesting scores recursively only if member workflows land. |

**No orphan flags:** `score_member_path` → called by `/eval/score` workflow dispatch → runner;
`member_path`/per-member dims + `run_id` → read by EvalResultsPage. All shipped together.

---

## 8. Execution Notes

- **Grounded-now — read the real tree.** The run tree (`/runs/{id}/tree`) + child `run_steps`
  (`/agent-runs/{child}/steps`) are shipped; the eval-runner already polls the tree. E-5 extends the existing
  `_run_workflow_item`, it does not build a new workflow eval path.
- **One trajectory scorer, two zoom levels.** `score_member_path` reuses E-1's `score_trajectory` at member
  granularity — do not fork a workflow-only matcher. The per-member rubric is a reference-free `score_response`
  over the child's steps.
- **The gate win is a test.** Assert a workflow that answers correctly but routes through the wrong members
  **fails** the member-path dimension — the reason E-5 exists.
- **`eval_passed` already handles workflow versions** (`eval_runner.py:~318-330`) — E-5 needs no gate change,
  only meaningful member-path scores behind the composite.
- **Bump registry-api + eval-runner + studio** in both files.
</content>

# E-3 Tasks — Scheduled eval (job_spec datasets + side-effect assertions)

**Slice:** Eval v2 Phase E-3 (`e3/plan.md` + `e3/data-model.md`). **Covers E-3 ONLY.**
**Depends on:** **WS-3 (DONE)** + **E-2 (DONE, deployed)** + E-1 (trajectory scorer) + E-0 (discriminator/composite).

**Totals: 24 tasks — 19 implementation + 5 checkpoint (across 4 checkpoint phases CP1a–CP1d).**
Migration: **NO** (see §Re-grounding R3).
**Suite:** `scripts/e2e/suite-75-eval-v2-scheduled.sh` (IDs `T-S75-00x`), registered **after suite-74**.
**Image bumps (BOTH `scripts/deploy-cpe2e.sh` AND `charts/agentshield/values.yaml`):** registry-api
`0.2.184 → 0.2.185`, eval-runner `0.1.10 → 0.1.11`, studio `0.1.140 → 0.1.141`.
**No `sdk/agentshield_sdk/` change ⇒ NO declarative-runner bump** (stays `0.1.46`) — see §Re-grounding R6.

> **Alignment Check:** the goal is *trustworthy publish for scheduled agents*. A scheduled agent's whole
> point is the side-effect it fires unattended on a job spec; response-only eval says nothing about it.
> E-3 restores the gate's meaning by asserting the **recorded** side-effect against a golden job spec.
> E-3 adds **no new dispatch code** — it feeds the job spec through the shared run path under E-2's record
> seam. Any scheduled-only eval fork is the anti-pattern (parity grep `T-S75-000` enforces it).

---

## Re-grounding against the live tree (read before executing — several plan tasks DROPPED)

The plan carries the *design-stable / specifics-indicative* banner. Verified against the live tree
(2026-07-15, registry-api 0.2.184). What changed:

| # | Plan said | Code truth | Effect on tasks |
|---|---|---|---|
| **R1** | T1 "add the `ScheduledDatasetItem` schema" | **It already exists** — `schemas.py:1228`, in the `DatasetItem` discriminated union (`Tag("scheduled")`, `schemas.py:1283`) and exported (`:1990`). But it is **loosely typed**: `expected_trajectory: dict[str,Any]`, `expected_side_effects: list[dict[str,Any]]`, no `tool_mocks` — unlike `DurableDatasetItem` (`:1204`) which uses the structured `ExpectedTrajectory` (`:1179`) + `SideEffectAssertion` (`:1188`). | **"Add the schema" DROPPED.** Real delta = **tighten** the variant to the structured models (T001) so a malformed golden trajectory / assertion is rejected 422 at the door instead of key-sniffed at score time. |
| **R2** | T1 "validate the scheduled item in `datasets.py`" | `datasets.py` does **not** validate items itself — it persists `body.mode` (`:103`) and validation happens in `PlaygroundDatasetCreate._check_items` → `_validate_dataset_items` (`schemas.py:1293`), which is **already generic over the union**. | **`datasets.py` task DROPPED.** Tightening the variant (T001) automatically tightens validation. Proven by unit test (T003), not new code. |
| **R3** | §3 "no migration owned by E-3" | **Confirmed.** `eval_run_results` already has `dimension_scores`/`eval_detail`/`trigger_payload`/`matched`/`run_id` (`models.py:1521-1526`, E-0). `playground_runs.eval_mode` + CHECK (`models.py:1375`, E-2 migration 0063). Alembic head = **0063**. | **No migration task.** E-3 is validation + interpretation + scoring only. |
| **R4** | T2 "fire via `/internal/runs/start`; `k8s.py` (M — pass MODE)" | `k8s.py:163` **already** passes `MODE=mode` to the eval Job, and `eval_runner.py:187/215` already sets it to `dataset.mode`. **`PlaygroundRunCreate` already carries every field E-3 needs** — `input_payload`, `trigger_type`, `trigger_payload`, `eval_mode` (`schemas.py:1082-1091`). | **`k8s.py` task DROPPED.** MODE=scheduled reaches the runner for free once the launch guard passes (T002). |
| **R5** | T2 fire door = `/internal/runs/start` | **Architectural finding — see §D1 below.** That door creates a **production `AgentRun`** (`internal.py:449-462`, `context="production"`), requires a running deployment (`:370-380`), dispatches to the **`{agent}-production` pod** (`:103`,`:122`), and threads **no `eval_mode`** (`:104-110` omits it ⇒ defaults `"live"`; the reactive branch posts `{"message": message}` only). Driving eval through it would (a) **fire real side-effects** and (b) be **circular** with the publish gate (`deployments.py:560` requires `eval_passed` to deploy to production — you'd need a published prod pod to earn the eval that publishes it). | **Fire door = the sandbox playground door with scheduled job-spec semantics** (D1). The plan itself permits this (`plan.md` §2 seam 2: "`/internal/runs/start` **or the playground scheduled-fire shim**"). The real `/internal/runs/start` door is kept honest as a **live control** in the suite (T016). |
| **R6** | §8 "bump eval-runner + registry-api + studio" | **Confirmed** — E-3 touches no `sdk/agentshield_sdk/` file. | **No declarative-runner bump.** (If a task ever edits the SDK, the runner image pip-installs it — a stale runner made every E-1 trajectory score 0 — bump `0.1.46 → 0.1.47` in BOTH files.) |
| **R7** | T3 "`/eval/score` scheduled dispatch" | `playground.py:1104` hard-rejects: `if body.mode not in ("reactive","durable","workflow"): 501`. Scorers `score_side_effects`/`score_trajectory`/`score_response`/`weighted_mean` all shipped + reused as-is. | **Real, unchanged** (T005–T007). Reuse the E-2/E-1 scorers verbatim — no new scorer. |
| **R8** | — (not in the plan) | **The launch blocker.** `eval_runner.py:142-154` resolves `resolved_mode` from `Agent.execution_shape` **only** (`reactive`/`durable`) — it can never yield `scheduled`. The guard `:164` (`dataset.mode != "reactive" and resolved_mode != dataset.mode → 422`) therefore **rejects every scheduled dataset at launch**, before the runner ever starts. | **New load-bearing task T002.** Without it E-3 is unreachable end-to-end. |
| **R9** | T4 "results render" | `EvalResultsPage.tsx` already renders `side_effect` (`:59` `EVAL_DIMENSIONS`) + `SideEffectEvidence` recorded-not-delivered (`:654-687`). `trigger_payload` is rendered **nowhere** (grep: 0 hits). `DatasetsPage.tsx:334` already offers `<option value="scheduled">` but `:133` comments "Other modes create an empty dataset (their editors land later)" — **no scheduled editor**. | UI delta narrowed: **job-spec editor** (T012) + **render `trigger_payload`** (T013). The side-effect evidence panel is reused, not rebuilt. |

### D1 — Locked decision: the eval fires through the **sandbox** door, with scheduled job-spec semantics

**Not** through `/internal/runs/start`. Three reasons, in order of force:

1. **Circularity (fatal).** `deployments.py:560` gates production deploy on `eval_passed`; `/internal/runs/start`
   dispatches only to a `{agent}-production` pod. Requiring a published prod pod to run the eval that
   publishes it is a deadlock for every new scheduled agent. (`suite-71` only escapes it by hand-`PATCH`ing
   `eval_passed: True` before deploying — a fixture shortcut, not a user path.)
2. **Safety (fatal).** That door threads no `eval_mode`; E-2's seam is armed off the **persisted
   `PlaygroundRun.eval_mode`** (`playground.py:1521`, `approvals.py:137` on HITL resume). `AgentRun` has no
   such column. Driving eval through it would **deliver real side-effects** — the one thing E-3 forbids.
   Adding `agent_runs.eval_mode` to force it would be a bandaid serving a path that is already circular.
3. **Parity is preserved anyway.** Both doors converge on the **same** `dispatch_durable_run` →
   declarative-runner `/run`, where the job spec is resolved into the driving turn by the **same** shared
   line (`declarative-runner/main.py:668-671`: `input_payload.get("message") or json.dumps(input_payload)
   or DAEMON_KICKOFF`). E-3 feeds `job_spec` as `input_payload` + `trigger_type="schedule"` +
   `trigger_payload=job_spec` — the identical production shape. **The realism is the job-spec shape + the
   shared dispatch + the record seam, not the timer** (`plan.md` §8: "fire once, don't wait for cron").

**What this does NOT cover, and who does:** daemon-identity resolution on a trigger fire
(`resolve_principal`) and the trigger→`input_payload` pull (`internal.py:392-394`) are **WS-3's**, already
gated by `suite-71` (T-S71-001). E-3 does not re-prove them — but **T016 keeps the real door honest**: the
suite fires a REAL `/internal/runs/start` schedule run against a REAL armed trigger and asserts it still
**delivers live** (the plan's "no fake-schedule gate" bar), and that the eval's `job_spec` is the same shape
as that trigger's real `input_payload`. Recorded in the Gap Ledger.

---

## Summary

| Phase | Tasks | What it lands |
|---|---|---|
| **P1 — Contract: item + launch guard** | T001–T004 (4) | Tighten `ScheduledDatasetItem`; resolve `mode='scheduled'` from the schedule trigger; compatibility guard replaces equality. |
| **P2 — Scoring door** | T005–T008 (4) | `/eval/score` `mode=scheduled` branch reusing E-1/E-2 scorers; side-effect-skewed weights. |
| **[CP1a] Checkpoint — door + guard** | T009 (1) | A scheduled dataset **launches** (no 422) and the score door returns real dims (was 501). |
| **P3 — eval-runner scheduled branch** | T010–T011 (2) | `MODE=scheduled`: job_spec → `input_payload`, `eval_mode=record`, poll steps, score, record `trigger_payload`. |
| **P4 — Studio** | T012–T014 (3) | Job-spec editor; render the job spec in results; Vitest. |
| **[CP1b] Checkpoint — real scheduled eval** | T015 (1) | A REAL scheduled eval: record ⇒ **not delivered**, dims persisted, read back from the DB. |
| **P5 — Gate: suite-75 + journey** | T016–T021 (6) | `suite-75` (incl. live control + parity grep), register, Playwright, bumps, docs, gap ledger. |
| **[CP1c] Checkpoint — MVP gate** | T022 (1) | **MVP:** `suite-75` green in `run-all.sh` + Playwright journey green. |
| **[CP1d] Checkpoint — no-orphan + constitution** | T023–T024 (2) | Every new symbol has a live caller; both tag files agree; docs updated. |

**MVP scope line:** **MVP = through [CP1c]** — a real scheduled agent, a real job-spec dataset, a real
eval-runner Job, a real recorded-not-delivered side-effect, real persisted `dimension_scores` read back from
the DB, and a real browser journey. [CP1d] is the constitution sweep, not new capability.

---

## Phase 1 — Contract: the scheduled item + the launch guard

> **The launch guard (T002) is load-bearing:** without it `eval_runner.py:164` returns 422 for every
> scheduled dataset and nothing downstream is reachable. Do it first.

- [ ] [T001] [P] Tighten `ScheduledDatasetItem` to structured types — mirror `DurableDatasetItem`: `expected_trajectory: Optional[ExpectedTrajectory]`, `expected_side_effects: Optional[list[SideEffectAssertion]]`, add `tool_mocks: Optional[dict[str, Any]]`; keep `job_spec: Optional[dict[str, Any]]` (== `AgentTrigger.input_payload`) — `services/registry-api/schemas.py`
- [ ] [T002] Resolve `mode='scheduled'` from the agent's **schedule trigger** (not `execution_shape`) — add `_resolve_eval_mode(agent, workflow, db)` in `launch_eval_run`: workflow → `'workflow'`; else an enabled `AgentTrigger(trigger_type='schedule')` on the agent → `'scheduled'`; else `execution_shape`. Replaces the `execution_shape`-only read at `:148-154` — `services/registry-api/routers/eval_runner.py`
- [ ] [T003] Replace the mode **equality** guard with an explicit **compatibility** guard — `_assert_mode_compatible(dataset_mode, agent, workflow)`: `scheduled` requires the agent to have a schedule trigger (422 "arm a schedule trigger first"); `reactive`/`durable` require `execution_shape == dataset.mode` (today's rule, unchanged); `workflow` requires a workflow executable. Rationale in a docstring: an agent with BOTH manual and schedule triggers is legitimately evaluable **both** ways, so mode is not a pure function of the executable — the dataset declares intent, the executable must be compatible. No key-sniffing, no priority fallthrough — `services/registry-api/routers/eval_runner.py`
- [ ] [T004] [P] Unit-pin the contract: a valid scheduled item validates; a malformed `expected_trajectory` step (missing `tool`) and a bad `occurs` value are **rejected**; a `{mode:'scheduled', kind:'durable'}` pair is rejected; reactive/durable/workflow items are **unchanged** (E-0/E-1/E-5 regression pin) — `services/registry-api/tests/test_scheduled_dataset_item.py`

## Phase 2 — The scoring door (`mode=scheduled`)

> Reuse the shipped scorers **verbatim** — `score_response`, `score_trajectory`, `score_side_effects`,
> `weighted_mean`. E-3 writes **no new scorer** (No-Bandaid: one scorer per dimension, one door).

- [ ] [T005] Admit `scheduled` at the door — extend the allow-list at `:1104` to `("reactive","durable","workflow","scheduled")` and update the 501 message to name only the still-unwired modes (`webhook`) — `services/registry-api/routers/playground.py`
- [ ] [T006] Add the `mode=scheduled` branch — `response` (LLM, vs `expected_output`) + `trajectory`/`tool_call` (E-1 `score_trajectory` over `actual_trajectory`, **only when** the item carries `expected_trajectory` — durable-inner) + `side_effect` (E-2 `score_side_effects(recorded_side_effects, expected_side_effects)`, **only when** the item asserts them). Always surface `detail.recorded_side_effects` (E-2's always-surfaced contract) and set `detail.job_spec`. Present-dims-only reduction — an absent dimension is **never** scored 1.0 by default — `services/registry-api/routers/playground.py`
- [ ] [T007] Side-effect-skewed default weights — durable-inner `{response .3, trajectory .3, side_effect .4}`; reactive-inner `{response .4, side_effect .6}` (`e3/data-model.md` §3); inner shape inferred from the presence of `actual_trajectory`; overridable per run via `body.dimension_weights`. Reference-free degrade: no expecteds ⇒ `{response}` only — `services/registry-api/routers/playground.py`
- [ ] [T008] [P] Unit-pin the scheduled scorer: a satisfied `expected_side_effects` ⇒ `side_effect == 1.0`; a violated `occurs:'never'` ⇒ `0.0` and the composite drops below threshold; weights skew to `side_effect`; a reference-free item degrades to `{response}` — `services/registry-api/tests/test_scheduled_scorer.py`

## [CP1a] Checkpoint — the guard opens and the door scores

- [ ] [T009] **Checkpoint script** — `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0. Bump + deploy by **delegating to `scripts/deploy-cpe2e.sh`** (never bare helm/docker/kubectl), then `kubectl exec` into the registry-api pod and assert with **real httpx/jq**: (a) `POST /playground/datasets {mode:'scheduled'}` with a real `job_spec` item → **201**, and a malformed item → **422**; (b) launching that dataset against an agent with **no** schedule trigger → **422** naming the trigger; (c) against an agent **with** an armed schedule trigger → **201** and `EvalRun.mode == 'scheduled'`; (d) `POST /playground/eval/score {mode:'scheduled', …}` → **200** (was **501**) with `dimension_scores.side_effect` present and `composite` reflecting the skewed weights — `scripts/checkpoints/cp1a-e3-scheduled-door.sh`

## Phase 3 — eval-runner scheduled branch (job-spec fire)

> **Fire once, don't wait for cron.** The runner feeds the job spec and fires immediately. Reuse
> `_poll_durable`, `_project_trajectory`, `_project_recorded_side_effects`, `_fail_closed_record`,
> `_requires_recording` — all shipped by E-1/E-2 in this file.

- [ ] [T010] Add `_run_scheduled_item(client, item, idx)` — resolve the agent's **inner shape** once via `GET /agents/{AGENT_NAME}` (`execution_shape`); create the run through the sandbox door with the **production job-spec shape**: `{input_payload: job_spec, trigger_type: "schedule", trigger_payload: job_spec, execution_shape: <inner>, eval_mode: "record" if item asserts side effects else "live"}`; durable-inner → `_poll_durable` + `_project_trajectory`; reactive-inner → response only; drain `_project_recorded_side_effects`; **fail-closed** (never a silent pass) on run-create failure, non-terminal poll, empty durable trajectory, `_requires_recording` but nothing recorded, or door-unavailable; score via `_call_score_api_scheduled` (`mode="scheduled"`, sending `item`/`input`/`response`/`run_id`/`actual_trajectory`/`recorded_side_effects`) — `services/eval-runner/main.py`
- [ ] [T011] Dispatch + persist the job spec — in `run_eval()`'s loop add the `MODE == "scheduled" and not WORKFLOW_ID` branch (ahead of the durable branch, mirroring its shape) and **set `trigger_payload=job_spec` on the recorded result row** so `eval_run_results.trigger_payload` (E-0 column, read by T013) is not an orphan; `dimension_scores` + `eval_detail` + `run_id` recorded as the durable branch does — `services/eval-runner/main.py`

## Phase 4 — Studio: author the job spec, read the evidence

- [ ] [T012] Scheduled item editor — model it on `DurableItemEditor` (`:540`): a `job_spec` JSON textarea (labelled "fed to the run as `input_payload` — same shape as the schedule's job spec"), optional `expected_output`, optional expected-trajectory steps (durable-inner), and `expected_side_effects` rows (tool / `args_match` / `occurs` / `count`). Replace the `:133` "editors land later" path for `scheduled` so the mode option (`:334`) stops creating an empty dataset. Invalid JSON blocks save with an inline error — `studio/src/pages/DatasetsPage.tsx`
- [ ] [T013] Render the job spec in results — a "Job spec (fed as `input_payload`)" block reading `r.trigger_payload` inside the evidence panel, alongside the **reused** `SideEffectEvidence` recorded-not-delivered panel (`:654-687`) and the existing `side_effect` dimension chip (`:59`). Closes the `trigger_payload` orphan — `studio/src/pages/EvalResultsPage.tsx`
- [ ] [T014] [P] Vitest — scheduled editor renders only in `scheduled` mode, builds the item (job_spec + expected_side_effects), blocks invalid JSON; results render the job spec + recorded side effects; **no regression** to the durable/workflow editors — `studio/src/pages/DatasetsPage.test.tsx`, `studio/src/pages/EvalResultsPage.test.tsx`

## [CP1b] Checkpoint — a REAL scheduled eval, recorded not delivered

- [ ] [T015] **Checkpoint script** — `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0. Delegate the build+deploy to `scripts/deploy-cpe2e.sh`, then drive the REAL path (no fakes): create a real HTTP `/echo` **POST** tool (`side_effecting=true`) + a real **`agent_class=daemon`** + durable agent, **deploy a real sandbox pod**, arm a real schedule trigger, author a real `scheduled` dataset whose `job_spec` mirrors that trigger's `input_payload`, `POST /playground/eval-runs` → the REAL eval-runner Job (`MODE=scheduled`). Assert with real `kubectl`/httpx/jq: the Job reaches `Succeeded`; the write tool's real `run_steps` row carries the **mock sentinel** (no `/echo` reflection ⇒ **never delivered**); `run_steps.output.recorded_side_effects[]` persisted; `eval_run_results.dimension_scores.side_effect == 1.0` and `trigger_payload == job_spec` **re-read from the DB** — `scripts/checkpoints/cp1b-e3-scheduled-eval.sh`

## Phase 5 — The gate: suite-75 + the browser journey

> **NO-FAKES bar (`README.md` §Verification standard).** Real resources, real pods, the real runner Job,
> the real judge, real persisted rows re-read from the DB. **No** monkeypatch, **no** mocked httpx, **no**
> hand-built result rows, **no** `page.route`. **Fail — never skip — on an unreachable fixture.**
> **Fixture lessons (bake in):** the agent MUST be **`agent_class=daemon`** (a `user_delegated` agent with
> no live user ⇒ OPA `missing_user_identity` deny); use **HTTP tools** (`/echo`) — python-type tools crash
> the pod (`docs/bugs/python-tool-graph-build-kwargs.md`); `run_steps.output` is a **JSONB dict** — never
> text-coerce it (`docs/bugs/runstep-output-text-coerced-500.md`). Model: `suite-74` (eval side) +
> `suite-71` (scheduled side).

- [ ] [T016] Write `suite-75-eval-v2-scheduled.sh` — `#!/usr/bin/env bash`, `set -euo pipefail`, executable, exit 0, real `kubectl exec` + httpx/jq with explicit HTTP/JSON assertions:
  - `T-S75-000` — **PARITY grep** (repo source): no scheduled-only eval fork — `mode == "scheduled"` appears **only** at the score-door discriminator + the runner branch, never as a dispatch fork in `internal.py`/`durable_dispatch.py`/`workflow_orchestrator.py`; no second side-effect scorer besides `judge.score_side_effects`.
  - `T-S75-001` — a `scheduled` dataset with a real `job_spec` + `expected_side_effects` is authored via the REAL API (**201**) and **save→reload** returns `job_spec` + `expected_side_effects` intact; a malformed item → **422**.
  - `T-S75-002` — launch guard: the dataset against an agent with **no** schedule trigger → **422**; with an armed schedule trigger → **201**, `EvalRun.mode == 'scheduled'`, real Job created.
  - `T-S75-003` — **THE MVP GATE: recorded ⇒ NOT delivered.** The REAL eval-runner Job fires a REAL scheduled run of a REAL deployed daemon pod; the write tool's real `run_steps` row carries the mock sentinel (**no `/echo` reflection**) and `recorded_side_effects[]` persisted.
  - `T-S75-004` — the job spec IS the input: the eval's real `PlaygroundRun` has `trigger_type='schedule'`, `trigger_payload == job_spec`, `input_payload == job_spec`, `eval_mode='record'` — re-read from the DB.
  - `T-S75-005` — scorer: a satisfied assertion ⇒ `dimension_scores.side_effect == 1.0`, composite ≥ threshold, `eval_run_results.trigger_payload == job_spec` — **read back from the DB**.
  - `T-S75-006` — scorer: a violated `occurs:'never'` ⇒ `side_effect == 0.0` and the item does **not** pass.
  - `T-S75-007` — durable-inner trajectory: an item with `expected_trajectory` also scores `trajectory`/`tool_call` (E-1 reused); weights skew to `side_effect`.
  - `T-S75-008` — **fail-closed:** an item requiring a recording whose record-run recorded nothing is recorded **FAILED** (never a silent pass).
  - `T-S75-009` — **LIVE CONTROL — no fake-schedule gate** (`plan.md` verification bar): a **non-eval** scheduled run fired through the REAL `/internal/runs/start` door (real armed trigger, `run_by` sentinel, real production pod — the `suite-71` fixture pattern) still **DELIVERS for real** (the `/echo` reflection IS present, nothing recorded). Proves the record seam is armed only by the eval and the real scheduled door is untouched by E-3.
  — `scripts/e2e/suite-75-eval-v2-scheduled.sh`
- [ ] [T017] Register suite-75 **after suite-74** and `chmod +x` — `scripts/e2e/run-all.sh`
- [ ] [T018] Playwright journey (DoD #1/#2) — real login, author a `scheduled` dataset (job_spec + `expected_side_effects`) in the real UI, `waitForResponse` on the real POST, **reload the page** and assert the job spec + assertions survived, launch the eval, and assert the recorded side-effect + job spec render in results. **No `page.route`** — `studio/e2e/eval-v2-scheduled.spec.ts`
- [ ] [T019] [P] Bump tags in **BOTH** files (same commit): registry-api `0.2.185`, eval-runner `0.1.11`, studio `0.1.141`; update the `deploy-cpe2e.sh` comment header ("E-3: scheduled eval — job_spec datasets + side-effect assertions"). Leave declarative-runner at `0.1.46` (no SDK change — R6) — `scripts/deploy-cpe2e.sh`, `charts/agentshield/values.yaml`
- [ ] [T020] [P] Experience doc — scheduled datasets (job spec authoring), the scheduled eval branch, side-effect-skewed weights, recorded-not-delivered evidence, and the `scheduled`-mode launch guard (422 without a schedule trigger) — `docs/experience/playground.md`
- [ ] [T021] [P] Gap ledger — carry the plan's rows + D1's (below) into the canonical "Known gaps" header, each tagged **deferred (intentional)** vs **not-yet-wired (debt)** — `docs/testing/manual-ui-e2e-test-plan.md`

## [CP1c] Checkpoint — MVP gate

- [ ] [T022] **Checkpoint script (MVP)** — `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0. Delegate build+deploy to `scripts/deploy-cpe2e.sh`; run `bash scripts/e2e/suite-75-eval-v2-scheduled.sh` (all `T-S75-00x` pass, **0 skips**); assert suite-75 is registered in `run-all.sh`; run `bash scripts/studio-e2e.sh` for `eval-v2-scheduled.spec.ts`; assert `cd studio && npm run typecheck && npm run test` green and `python3 -c "import ast; ast.parse(...)"` on every changed Python file — `scripts/checkpoints/cp1c-e3-mvp.sh`

## [CP1d] Checkpoint — no-orphan + constitution sweep

- [ ] [T023] **No-orphan grep script** — `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0; **fail** if any new symbol has no live caller/reader: `_resolve_eval_mode`, `_assert_mode_compatible`, `_run_scheduled_item`, `_call_score_api_scheduled`, `ScheduledDatasetItem`'s `tool_mocks`, the scheduled editor component, and the `trigger_payload` render. Assert `eval_run_results.trigger_payload` has **both** a writer (eval-runner T011) and a reader (Studio T013) — `scripts/checkpoints/cp1d-e3-no-orphans.sh`
- [ ] [T024] **Constitution sweep script** — `#!/usr/bin/env bash`, `set -euo pipefail`, exit 0; assert the three tags are **identical** in `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` (the recurring "bumped one file only" failure), declarative-runner is untouched at `0.1.46` **iff** `git diff --name-only` shows no `sdk/agentshield_sdk/` change (**else fail loudly** and require the bump), no new Alembic version file was added (E-3 owns no migration — R3), and `docs/experience/playground.md` was modified — `scripts/checkpoints/cp1d-e3-constitution.sh`

---

## Gap Ledger (carried from `e3/plan.md` §7 + this mint's D1)

| Item | Status | Note |
|---|---|---|
| Eval fires through the **sandbox** door, not `/internal/runs/start` | **deferred (intentional)** — D1 | The real door is production-only + threads no `eval_mode`, and is **circular** with the `eval_passed` publish gate. E-3 drives the identical job-spec shape through the **shared** dispatch under the record seam; `T-S75-009` keeps the real door honest with a live-delivery control. Revisit only if evals ever need to run against published production agents (would need `agent_runs.eval_mode` + a non-circular deploy story). |
| Daemon identity on a trigger fire (`resolve_principal`) not re-proven by E-3 | **deferred (intentional)** | WS-3's surface, gated by `suite-71` T-S71-001. E-3 scores run behavior, not identity resolution. |
| Cron-timing eval (does it fire at the right time?) | **deferred (intentional)** | E-3 fires immediately with the job spec; next-fire timing is WS-3's operate surface, not an eval dimension. |
| Alert-on-failure as an eval dimension | out of scope | WS-3 verifies alerting end-to-end; E-3 scores the run's behavior, not the alert transport. |
| Record-once cassette replay for scheduled | **deferred → E-2 gap** | Inherits E-2's mock-only limitation. |
| Item `tool_mocks` not threaded to the seam | **not-yet-wired (debt)** — inherited from E-2 | T001 declares the field for contract parity with `DurableDatasetItem`; the seam still returns a type-default success sentinel. Same E-2 ledger row — E-3 adds no new debt. |

**No orphan flags:** the scheduled item + `expected_side_effects` → read by the runner (T010) +
`score_side_effects` (T006); `trigger_payload` → written by T011, read by T013; `dimension_scores` → read by
the existing results UI. All shipped together; [CP1d]/T023 greps it.

---

## Post-implementation gates (CLAUDE.md — state these explicitly when reporting done)

1. **E2E:** `suite-75-eval-v2-scheduled.sh` written, executable, **registered after suite-74** in
   `run-all.sh`, IDs `T-S75-00x`, happy path + error/edge (422 guard, violated assertion, fail-closed) — and
   the **no-fakes** bar met (real dataset, real pod, real Job, real judge, real DB read-back, live control).
2. **Image bumps:** registry-api `0.2.185`, eval-runner `0.1.11`, studio `0.1.141` in **BOTH**
   `scripts/deploy-cpe2e.sh` **and** `charts/agentshield/values.yaml`; build+deploy via
   `scripts/deploy-cpe2e.sh` (never bare helm/docker/kubectl). No declarative-runner bump (no SDK change).
3. **Experience docs:** `docs/experience/playground.md` updated (T020) — `playground.py`, `eval_runner.py`,
   `DatasetsPage.tsx`, `EvalResultsPage.tsx` are all covered files.
4. **Frontend tests:** Vitest green (`cd studio && npm run test`); Playwright green
   (`bash scripts/studio-e2e.sh`).
5. **Verification:** `cd studio && npm run typecheck`; `ast.parse` every changed Python file; ORM mappers
   configure; **no migration** (E-3 owns none — R3).
6. **DoD gate:** (a) T018 proves the real journey; (b) T018's reload + `T-S75-001`/`T-S75-005` prove
   save→reload→assert; (c) T023 proves no orphans; (d) the Gap Ledger (T021) records every deferral.

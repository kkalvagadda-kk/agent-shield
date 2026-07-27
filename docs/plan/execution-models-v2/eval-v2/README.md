# Eval v2 — per-phase plan index

Eval v2 turns evaluation from **response-only** into **mode-aware**. The consolidated design lives in
this directory's `plan.md` (+ `data-model.md`, `research.md`); those three are the **seed** and stay
authoritative for the cross-cutting architecture, the scorer-library approach, the sandbox side-effect
record/replay seam, and the sequencing decision (§8). This README indexes the **per-phase** plans that
bring each eval phase to per-workstream (WS-1…WS-6) parity.

Each `E-n` rides **with, or one beat behind, the workstream that makes its mode real** (consolidated
`plan.md` §8). Build eval **behind** its mode, mode by mode — never batch all eval work to the end.

> **STATUS — re-grounded against code 2026-07-27.** E-0…E-5 are **shipped**. E-6 is **20/26**, and its six
> open tasks are the same work as Slice 0 of [`docs/design/eval-ux-enrichment.md`](../../../design/eval-ux-enrichment.md).
> The live ledger — which dimensions each mode actually produces, what is stubbed, what is orphaned —
> is **[`docs/design/eval-state-of-play.md`](../../../design/eval-state-of-play.md)**. Read that first;
> this file is the per-phase map, not the status of record.

| Phase | Plan | Covers | Status |
|---|---|---|---|
| **E-0** | [`e0/plan.md`](e0/plan.md) | Reactive parity + composite plumbing (no behavior change) | ✅ **shipped** — 19/19 |
| **E-1** | [`e1/plan.md`](e1/plan.md) · [`e1/data-model.md`](e1/data-model.md) · [`e1/contracts/`](e1/contracts/) | Durable trajectory + tool-call eval | ✅ **shipped** — 20/20 |
| **E-2** | [`e2/plan.md`](e2/plan.md) · [`e2/data-model.md`](e2/data-model.md) | Side-effect record/replay seam (`eval_mode` through the governed tool path) | ✅ **shipped** — 20/20. Carve-out: item `tool_mocks` is **not** threaded to the seam. |
| **E-3** | [`e3/plan.md`](e3/plan.md) · [`e3/data-model.md`](e3/data-model.md) | Scheduled eval (job_spec datasets + side-effect assertions) | ✅ **shipped** — 24/24 |
| **E-4** | [`e4/plan.md`](e4/plan.md) · [`e4/data-model.md`](e4/data-model.md) | Webhook eval (filter match/miss + action + prompt-injection robustness) | ✅ **shipped** — 25/25 |
| **E-5** | [`e5/plan.md`](e5/plan.md) | Workflow run-tree eval (per-member path) | ✅ **shipped** — 12/12 |
| **E-6** | [`e6/plan.md`](e6/plan.md) | Regression/CI + eval-gate polish | ⏳ **20/26** — open: T006/T007 (UI threshold hardcodes), T020/T021 (launch surface), T022 (Playwright), T026 (doc honesty) |

Each phase plan still carries the ⚠️ *design-stable / specifics-indicative* banner. That banner was
written **before** the phases were built. For E-0…E-5 it no longer applies: the **code is the
specification**, and where a plan's `file:line` disagrees with `judge.py` or `routers/playground.py`,
the code wins and the plan is history. It still applies to E-6's six open tasks.

**E-0 is the foundation** (now in its own `e0/`, uniform with E-1…E-6). It ships **first** (no WS
dependency): the discriminated-union schema, the composite-score plumbing, and the judge-scorer-library
skeleton that every later phase extends. Its behavior-neutral parity requirement (composite == today's
reactive score) is the safe seam the whole refactor lands on. It gets its own `tasks.md` when minted.

## Finalized overall sequence (execution spine ⋈ eval phases)

Interleave of the workstreams and eval phases, **as built**. ✅ = shipped + deployed. Principle held:
each `E-n` landed with, or one beat behind, the WS that makes its mode real — never batched to the end
(consolidated `plan.md` §8; CLAUDE.md DoD #4).

| Order | Item | Depends on | Status |
|---|---|---|---|
| 1 | **WS-0** authoring + shape-aware dispatch | — | ✅ shipped |
| 2 | **WS-1** durable engine real & resumable | WS-0 | ✅ shipped |
| 3 | **E-0** reactive parity + composite plumbing | WS-0 | ✅ shipped |
| 4 | **E-1** durable trajectory + tool-call eval | WS-1 + E-0 | ✅ shipped |
| 5 | **WS-2** daemon identity + async approver routing | WS-1 | ✅ shipped |
| 6 | **E-2** side-effect record/replay seam | WS-1 + gov wrapper | ✅ shipped (minus `tool_mocks` threading) |
| 7 | **WS-3** scheduled e2e | WS-2 | ✅ shipped |
| 8 | **E-3** scheduled eval | WS-3 + E-2 | ✅ shipped |
| 9 | **WS-4** webhook client-id / HMAC (off-spine) | — | ✅ shipped |
| 10 | **E-4** webhook filter/action/injection eval | WS-4 + E-2 | ✅ shipped |
| 11 | **E-5** workflow run-tree eval | WS-1 D4 + E-1 | ✅ shipped |
| 12 | **WS-5** Kaniko in-browser build · **WS-6** operate parity | — | WS-6 shipped; WS-5 still open |
| 13 | **E-6** regression/CI + eval-gate polish | E-0…E-5 | ⏳ 20/26 — **the only open eval work** |

**The "one open call" is closed by history.** This section used to ask whether E-0→E-1 should precede
WS-2. They ran first, the durable capability became gate-able while eval was still cheap to change, and
the rest of the spine followed. Recorded rather than deleted — the reasoning is why the scorer library
exists as a library instead of a pile of branches in the runner.

**Read order for a reviewer:** consolidated `plan.md` (§2 scorer library, §3 schema, §8 sequencing) →
`data-model.md` (§2 discriminated union, §4 record seam) → the phase you're about to build. Every phase
plan carries the ⚠️ *design-stable / specifics-indicative* banner and a hard **depends-on** line; treat
`file:line`/migration numbers as indicative and re-ground at `tasks.md` mint time.

## Verification standard — MANDATORY for every phase (the suite-58/59 bar, no fakes)

The execution-models-v2 build proved (the hard way — 11 live-only bugs, see
`docs/bugs/durable-workflow-live-path.md`) that **a faked seam hides exactly the bugs that live in
it**: suites that monkeypatched `_run_step`/`resolve_edge_graph`, mocked `httpx`, or used "no-dispatch"
paths shipped green while the real dispatch→pod→callback→resume path was broken end to end. Eval is the
next place that trap will bite (a mocked judge / hand-crafted `eval_run_results` row proves nothing).
So **every E-phase's acceptance is a REAL, no-fakes e2e that matches how a user runs an eval** — the
same standard as `scripts/e2e/suite-58-workflow-live-run.sh` (creates its own agents, deploys real pods,
`POST /workflows/{id}/runs`, asserts the real terminal state) and `suite-59` (all four orchestrations +
HITL, real park→approve→advance). Concretely, each phase MUST include an e2e suite that:

1. **Creates its own resources up front** — a real `PlaygroundDataset` (of the phase's `mode`) with real
   items, via the real API. No hand-crafted DB rows, no in-memory fixture standing in for a dataset.
2. **Runs a REAL `EvalRun`** through the real path — `POST /playground/eval/...` → the real **eval-runner
   Job** (or the real scoring endpoint) → the real **judge** (`score_*` in `judge.py`) → real
   `eval_run_results`. NO mocked judge, NO faked runner, NO stubbed `_run_step`; if the mode dispatches a
   real agent/workflow (durable/scheduled/webhook), the suite drives that real dispatch (the exact class
   of path that hid the 11 bugs).
3. **Asserts the persisted, read-back outcome** — `dimension_scores` + `composite` written to the DB and
   re-read (save→reload), the `eval_passed` gate flips as designed, and — for side-effect modes — the
   side-effect was **recorded, not delivered** *and that recording is asserted from the real record seam*
   (E-2 record/replay is the ONLY thing mocked; the eval itself is never mocked).
4. **Proves the real user journey in the browser** — a Playwright spec against the deployed Studio that
   authors the dataset, launches the eval, and reads the score back (network `waitForResponse` +
   save→reload), per CLAUDE.md DoD #1/#2. Route-stubbing the eval API is NOT acceptable (a stubbed browser
   test is still a fake — it was a `page.route`'d spec that missed the mixed-content bug #7).
5. **Is registered in `run-all.sh` and named** (`T-SNN-00X`), replacing the `suite-NN` placeholders in the
   phase plans below with the concrete suite number at mint time.

**The parity gate (E-0) is the load-bearing one and must be a real run:** the reactive composite must equal
today's judge score to the digit *on a real eval run of a real dataset through the real runner+judge*, not
a unit fixture. A logic-only unit test may accompany it for speed, but it is NOT the gate — the real suite
is. Reinforces the `[[feedback_no_fakes_in_e2e]]` rule (create real resources; drive the real path; no
`_run_step` monkeypatch, no mocked judge, no faked result rows).

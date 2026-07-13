# Eval v2 — per-phase plan index

Eval v2 turns evaluation from **response-only** into **mode-aware**. The consolidated design lives in
this directory's `plan.md` (+ `data-model.md`, `research.md`); those three are the **seed** and stay
authoritative for the cross-cutting architecture, the scorer-library approach, the sandbox side-effect
record/replay seam, and the sequencing decision (§8). This README indexes the **per-phase** plans that
bring each eval phase to per-workstream (WS-1…WS-6) parity.

Each `E-n` rides **with, or one beat behind, the workstream that makes its mode real** (consolidated
`plan.md` §8). Build eval **behind** its mode, mode by mode — never batch all eval work to the end.

| Phase | Plan | Covers | Depends on | Grounding |
|---|---|---|---|---|
| **E-0** | *stays in the consolidated `plan.md` §6* | Reactive parity + composite plumbing (no behavior change) | WS-0 only | Foundation — implemented first; gets its own `tasks.md` at implement-time. |
| **E-1** | [`e1/plan.md`](e1/plan.md) · [`e1/data-model.md`](e1/data-model.md) · [`e1/contracts/`](e1/contracts/) | Durable trajectory + tool-call eval | **WS-1 (DONE)** + E-0 | **Grounded-now** against the shipped `run_steps` + durable harness. Most execution-ready. |
| **E-2** | [`e2/plan.md`](e2/plan.md) · [`e2/data-model.md`](e2/data-model.md) | Side-effect record/replay seam (`eval_mode` through the governed tool path) | WS-1 + governance wrapper | Banner-indicative (governance-wrapper seam not yet built). |
| **E-3** | [`e3/plan.md`](e3/plan.md) · [`e3/data-model.md`](e3/data-model.md) | Scheduled eval (job_spec datasets + side-effect assertions) | **WS-3 (not built)** + E-2 | Banner-indicative. |
| **E-4** | [`e4/plan.md`](e4/plan.md) · [`e4/data-model.md`](e4/data-model.md) | Webhook eval (filter match/miss + action + prompt-injection robustness) | **WS-4 (not built)** + E-2 | Banner-indicative. |
| **E-5** | [`e5/plan.md`](e5/plan.md) | Workflow run-tree eval (per-member path) | **WS-1 D4 (DONE)** + E-1 | **Grounded-now** against the shipped run tree + `_dispatch_durable_member`. |
| **E-6** | [`e6/plan.md`](e6/plan.md) | Regression/CI + eval-gate polish | E-0…E-5 | Banner-indicative (composes the finished scorers). |

**Why E-0 stays in the consolidated plan:** E-0 is the foundation — the discriminated-union schema, the
composite-score plumbing, and the judge-scorer-library skeleton that every later phase extends. It ships
**first** (no WS dependency) and its behavior-neutral parity requirement (composite == today's reactive
score) is the safe seam the whole refactor lands on. It gets its own `tasks.md` when minted; the per-phase
directories here begin at E-1 because that is where mode-specific scoring diverges.

**Read order for a reviewer:** consolidated `plan.md` (§2 scorer library, §3 schema, §8 sequencing) →
`data-model.md` (§2 discriminated union, §4 record seam) → the phase you're about to build. Every phase
plan carries the ⚠️ *design-stable / specifics-indicative* banner and a hard **depends-on** line; treat
`file:line`/migration numbers as indicative and re-ground at `tasks.md` mint time.
</content>
</invoke>

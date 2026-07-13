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
| **E-0** | [`e0/plan.md`](e0/plan.md) | Reactive parity + composite plumbing (no behavior change) | **WS-0 (DONE)** | **Grounded-now** — the foundation; implemented first; gets its own `tasks.md` at mint. |
| **E-1** | [`e1/plan.md`](e1/plan.md) · [`e1/data-model.md`](e1/data-model.md) · [`e1/contracts/`](e1/contracts/) | Durable trajectory + tool-call eval | **WS-1 (DONE)** + E-0 | **Grounded-now** against the shipped `run_steps` + durable harness. Most execution-ready. |
| **E-2** | [`e2/plan.md`](e2/plan.md) · [`e2/data-model.md`](e2/data-model.md) | Side-effect record/replay seam (`eval_mode` through the governed tool path) | WS-1 + governance wrapper | Banner-indicative (governance-wrapper seam not yet built). |
| **E-3** | [`e3/plan.md`](e3/plan.md) · [`e3/data-model.md`](e3/data-model.md) | Scheduled eval (job_spec datasets + side-effect assertions) | **WS-3 (not built)** + E-2 | Banner-indicative. |
| **E-4** | [`e4/plan.md`](e4/plan.md) · [`e4/data-model.md`](e4/data-model.md) | Webhook eval (filter match/miss + action + prompt-injection robustness) | **WS-4 (not built)** + E-2 | Banner-indicative. |
| **E-5** | [`e5/plan.md`](e5/plan.md) | Workflow run-tree eval (per-member path) | **WS-1 D4 (DONE)** + E-1 | **Grounded-now** against the shipped run tree + `_dispatch_durable_member`. |
| **E-6** | [`e6/plan.md`](e6/plan.md) | Regression/CI + eval-gate polish | E-0…E-5 | Banner-indicative (composes the finished scorers). |

**E-0 is the foundation** (now in its own `e0/`, uniform with E-1…E-6). It ships **first** (no WS
dependency): the discriminated-union schema, the composite-score plumbing, and the judge-scorer-library
skeleton that every later phase extends. Its behavior-neutral parity requirement (composite == today's
reactive score) is the safe seam the whole refactor lands on. It gets its own `tasks.md` when minted.

## Finalized overall sequence (execution spine ⋈ eval phases)

Authoritative interleave of the workstreams and eval phases. ✅ = shipped + deployed; ★ = the immediate
next unit; the rest are planned-ahead (banner). Principle: each `E-n` lands **with, or one beat behind, the
WS that makes its mode real** — never batched to the end (consolidated `plan.md` §8; CLAUDE.md DoD #4).

| Order | Item | Depends on | Status |
|---|---|---|---|
| 1 | **WS-0** authoring + shape-aware dispatch | — | ✅ shipped |
| 2 | **WS-1** durable engine real & resumable | WS-0 | ✅ shipped |
| 3 ★ | **E-0** reactive parity + composite plumbing | WS-0 | next (foundation, no WS dep) |
| 4 ★ | **E-1** durable trajectory + tool-call eval | WS-1 + E-0 | next (rides WS-1) |
| 5 | **WS-2** daemon identity + async approver routing | WS-1 | planned |
| 6 | **E-2** side-effect record/replay seam | WS-1 + gov wrapper | planned |
| 7 | **WS-3** scheduled e2e | WS-2 | planned |
| 8 | **E-3** scheduled eval | WS-3 + E-2 | planned (one beat behind WS-3) |
| 9 | **WS-4** webhook client-id / HMAC (off-spine) | — | planned |
| 10 | **E-4** webhook filter/action/injection eval | WS-4 + E-2 | planned (behind WS-4) |
| 11 | **E-5** workflow run-tree eval | WS-1 D4 (✅) + E-1 | planned (can slot alongside WS-2/3) |
| 12 | **WS-5** Kaniko in-browser build · **WS-6** operate parity | — | planned |
| 13 | **E-6** regression/CI + eval-gate polish | E-0…E-5 | last |

**The one open call (my lean: do E-0→E-1 first).** Slots 3–4 (E-0→E-1) can go *before* WS-2 — making the
durable capability just shipped actually trustworthy/gate-able, and cheapest while eval is still
reactive-only — **or** the spine can continue (WS-2→WS-3) with E-0/E-1 right after. Both honor the "ride
the WS" principle; it's a priority choice (evaluate-what's-shipped vs finish-the-cube-faster).

**Read order for a reviewer:** consolidated `plan.md` (§2 scorer library, §3 schema, §8 sequencing) →
`data-model.md` (§2 discriminated union, §4 record seam) → the phase you're about to build. Every phase
plan carries the ⚠️ *design-stable / specifics-indicative* banner and a hard **depends-on** line; treat
`file:line`/migration numbers as indicative and re-ground at `tasks.md` mint time.
</content>
</invoke>

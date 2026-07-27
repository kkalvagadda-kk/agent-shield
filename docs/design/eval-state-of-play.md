# Evaluation — State of Play

**Status:** LEDGER — the status of record for evaluation. **Verified against code 2026-07-27.**
**Author:** Kalyan + Claude

Three eval docs disagreed with the product and with each other. This file is the single answer to
*"what actually evaluates today, what is deferred, and what should we build next."* Every claim below
was read out of code, not out of a plan. Where a design doc contradicts this file, **check the code and
fix the doc** — that is how the drift started.

| Doc | What it is now |
|---|---|
| **this file** | Status of record. Start here. |
| `eval-ux-enrichment.md` | The **approved forward plan** (Slice 0 → Wave 1 → Wave 2). Still accurate. |
| `todo/agent-evaluation-capability.md` | Market research + prioritization frame from 2026-07-07. Its "current state" section is **history**; re-scored in place. |
| `plan/execution-models-v2/eval-v2/` | How the engine got built. E-0…E-5 are done; for those the **code is the spec**. |

---

## 1. What evaluates today

Eval is **mode-aware**, not response-only. One door — `POST /playground/eval/score` — dispatches on an
**explicit `mode` discriminator**, never by sniffing which keys an item happens to carry. Seven scorers
live in `services/registry-api/judge.py`; **six of the seven are pure code, no LLM.**

| Scorer | Kind | What it asserts |
|---|---|---|
| `score_response` | LLM judge | Output vs `expected_output`, or reference-free against a `rubric` |
| `score_trajectory` | deterministic | Ordered tool sequence vs a golden one, under 4 match modes |
| `score_tool_calls` | deterministic | Tool name + `args_match` dict-subset + HITL parking |
| `score_side_effects` | deterministic | Recorded writes vs assertions (`exactly` / `at_least` / `never`) |
| `score_filter` | deterministic | Webhook routing decision (match / miss) vs `AgentEvent.status` |
| `score_injection` | deterministic | `must_not_call` + `must_refuse` under adversarial input |
| `score_member_path` | deterministic | Which workflow members ran, in which order |

**Trajectory match modes** — `exact` (same calls, same order, no extras), `ordered` (expected appear
in-order, extras allowed between), `superset` (every expected call happened; order and extras free),
`unordered` (same set, any order; missing *and* extras both penalize). Default is `superset`, the most
forgiving; workflow member-path defaults to `ordered` on purpose, so a right answer reached by the
**wrong route** scores below 1.0.

### Which dimensions each mode produces

Read off `routers/playground.py`. The mode is the dataset's mode; you cannot mix.

| Mode | Dimensions | Default weights |
|---|---|---|
| `reactive` | `response` | composite **==** response (byte-identical to pre-Eval-v2 behaviour) |
| `workflow` | `member_path`, `response`, `per_member` | 0.4 / 0.4 / 0.2 |
| `durable` | `response`, `trajectory`, `tool_call`, `side_effect` | side-effect-skewed |
| `scheduled` | `response`, `side_effect` (+ `trajectory`/`tool_call` when durable-inner) | 0.4 / 0.6 |
| `webhook` | `filter`, `response`, `trajectory`, `tool_call`, `side_effect`, `injection` | per e4 |

### Three properties worth knowing before you extend it

**Present-dims-only reduction.** The reducer sums the weights of the dimensions that are actually
present. A weight naming an absent dimension cannot silently drag the composite down.

**Vetoes fire on exact facts, and weights cannot override them.** `filter_error` and
`injection_succeeded` force the composite to 0.0 (`playground.py:1610-1617`). They are facts, not
heuristics — which is also why a what-if re-score can read a stored veto as truth without re-running
policy.

**A correctly-rejected webhook event is not judged.** If the filter rightly dropped an event, nothing
ran, so there is no response to score — calling the judge there would burn a model call on empty text
and turn a judge outage into a *failed filter* item. Keyed off the mode discriminator plus the door's
explicit `matched` decision.

### The gate

`eval_passed` **is an evaluated result**, set automatically at `routers/eval_runner.py:594` when a
completed run's composite clears that run's **own** `pass_threshold` — per-run, not a platform-wide 0.7.

---

## 2. Deferred — 13 of the capability doc's 17 priorities

Nothing below exists in code. Verified by absence of any model, table, endpoint, or page.

| Priority | Capability | Note |
|---|---|---|
| **P0** | Custom evaluator definitions (1.2) | No evaluator model or table at all |
| **P1** | Evaluator library (1.3) | Follows 1.2 |
| **P1** | Version comparison (2.1) | Designed — `eval-ux-enrichment.md` Wave 1 Slice 2 |
| **P2** | Regression detection (2.2) | No score-drop alert anywhere |
| **P2** | Online / production eval (3.1) | No sampling of live traffic |
| **P2** | Human annotation queues (3.2) | Playground thumbs exist; no review queue |
| **P2** | Experiment history (2.3) | No eval-runs list page exists |
| **P3** | Production → dataset (3.3) | Endpoints exist but are **dark** (no caller) |
| **P3** | Red team (4.3) | — |
| **P3** | Dataset versioning (5.1) | Datasets are typed per mode, but not versioned |
| **P3** | CI/CD integration (5.2) | — |
| **P3** | Analytics dashboard (5.3) | — |
| **P3** | Multi-turn / conversation eval (5.4) | See §4 |

Partially shipped: **safety suite (4.1)** — `score_injection` and the veto are in, but there is no
pre-built adversarial dataset. **Governance regression (4.2)** — `expect_approval` is the assertion
primitive (fail-closed: a gate expected to park that did **not** park scores 0), but no suite wraps it.

---

## 3. Declared but inert — the dangerous middle

These read as shipped and are not. Each is worse than a missing feature, because a reader assumes
coverage that does not exist.

**`tool_mocks` — accepted, persisted, ignored.** Declared on the durable/scheduled/webhook item
schemas. The record seam never reads it; every intercepted call gets a type-default success sentinel.
The reason is architectural, not neglect: the seam runs **in the agent pod**, and the item never travels
there — only `eval_mode` rides the dispatch body. Wiring it means threading a per-tool mock map through
`dispatch_durable_run` → `DurableRunRequest` → `begin_eval_context`. Its only readers today are two unit
tests. *Consequence:* you can assert what the agent **called**, never how it behaves when a tool
**returns** a particular value.

**`adversarial_eval_passed` — a hard gate with no producer.** It blocks production deploy
(`deployments.py:641`, `agents.py:551`) and blocks publish for risky tools (`agents.py:551`). Its only
writers are the manual `PATCH` at `versions.py:120/205`. **No eval run can ever set it.** Do not
"fix" this by auto-flipping it from injection scores: it is monotonic, it blocks production, and
"ASR = 0 on the 2 probes this dataset happened to include" is not "adversarially safe". If built, it
needs an explicit `adversarial=true` run, ≥N probed items, ASR = 0 on all, and zero vetoes — its own
slice with its own review, never a side effect of another change.

**`_DatasetItemBase.weight`** — a declared field no scorer reads.

---

## 4. Multi-turn: the substrate exists, the eval does not

Worth stating precisely, because it looks further away than it is.

All five dataset modes take a **single input**. There is no multi-turn mode, no conversational scorer,
and no `multi_turn` reference anywhere in `judge.py` or the eval-runner. But the **runtime** already has
full conversations — `ConversationStore`, `thread_id`, transcripts that persist across turns. Eval
simply never consumes them.

`eval-v2/research.md` §4.6 surveyed the field and recorded the finding that decides the design: **better
single-turn does not predict better multi-turn** (MT-Bench, MINT). So this cannot be approximated by
scoring turns independently.

**It depends on `tool_mocks`.** A multi-turn path is only deterministic if the tools return fixed values
across turns; otherwise turn 3 varies because turn 2's tool answered differently. Build the mock
threading first — it is bounded plumbing with the route already written down — and multi-turn becomes
tractable. Attempted in the other order, it will be flaky and nobody will trust it.

---

## 5. Traces: fully populated, entirely unread

Live DB, today:

```
tot=447   with_trace=447   failed=201   failed_with_trace=201
```

**Every** eval result row, including every failure, carries a `langfuse_trace_id`. Zero nulls. The
per-row click-through already ships (`EvalResultsPage.tsx:484` renders `trace_url`).

What does not exist is anything that **reads span content** to explain a failure — "the tool returned
500", "retrieval came back empty", "it looped three times then gave up". The planned triage
(`eval-ux-enrichment.md` Wave 2) infers from `dimension_scores` and `eval_detail` only, so it can only
report what a dimension already encodes.

If trace-based triage gets built, build it as a **fixed vocabulary of failure modes** (tool error, empty
retrieval, loop, timeout) rather than free-form LLM summarization — the same reasoning that made the
existing scorers deterministic, and the same reason `eval-ux-enrichment.md` rejected embedding-based
clustering.

---

## 6. What to work on — in this order

### Now: `eval-ux-enrichment.md` **Slice 0** (~1.5 days)

**Slice 0 and E-6's six open tasks are the same work.** E-6 T006/T007 are the UI threshold hardcodes,
T020/T021 the launch surface, T022 the Playwright journey, T026 the doc pass (this file closes most of
T026). So this is not "new work vs finish the old thing" — starting Slice 0 **completes Eval v2**.

It also fixes a live correctness bug that a human approves releases on. All three verified today:

- `routers/admin.py:177-178` resolves each publish request's eval by **`agent_name` only**
  (`.order_by(agent_name, completed_at.desc()).distinct(agent_name)`), never filtering on
  `PublishRequest.source_version_id`. The queue can show a **different version's** score.
- `AdminPublishRequestsPage.tsx:167,169` renders that score against a hardcoded `>= 0.7` / `>= 0.4`,
  and `PublishRequestResponse` carries no threshold — so the page **structurally cannot** render a
  correct verdict. The fix needs a backend field, not a frontend edit.
- `DatasetsPage.tsx:1756` hardcodes `>= 0.7` for the run status dot.

`EvalResultsPage.tsx` has **zero** threshold literals — it was fixed in `7b3e3fc`. The guard that was
supposed to protect the class, `suite-80` `T-S80-000b`, greps **only that one already-fixed file**, so
it passes while both real offenders ship. Rewrite it to **discover** its scope
(`grep -rl "pass_threshold\|overall_score" studio/src/pages studio/src/components`) rather than name a
file — otherwise the fourth copy appears somewhere new and the guard stays green.

### Then: Wave 1 — the regression story (~1.5 weeks)

Eval-runs list page (the sidebar currently labels an item "Eval Runs" and points at `/playground`, which
has none), run-to-run diff, trend with the threshold as a reference line.

### Then, and only then: the two engine gaps

`tool_mocks` threading, then multi-turn. In that order, for the reason in §4.

**Not next:** custom evaluator authoring. It is P0 in the 2026-07-07 doc, but that ranking predates the
scorer library — seven dimensions now ship, which was the actual need behind it. Revisit after Wave 1.

---

## 7. Known gaps in this ledger

- **not-yet-wired (debt)** — no automated check keeps this file honest. It was written by reading code
  once. The `--audit`-style guard that would work is a grep for scorer names in `judge.py` against the
  table in §1.
- **deferred (intentional)** — per-item cost/latency for eval runs. Only workflow mode has upstream
  data (`agent_runs` carries `cost_usd`/`latency_ms`; `playground_runs` carries neither).

# E-2 Implementation Plan — Side-effect record/replay seam (`eval_mode` through the governed tool path)

> ✅ **Verification bar (MANDATORY): the no-fakes suite-58/59 standard** — see the eval-v2 README
> "Verification standard". DONE only when a REAL e2e is green in `run-all.sh`: a real run through the REAL
> governed tool path with `eval_mode` on → the high-risk tool call is **recorded, not delivered**, and the
> suite asserts that recording from the REAL record seam (this seam — the side-effect *delivery* — is the
> ONLY thing that may be stubbed; the run, the governance wrapper, the judge are all real). **Phase-specific:**
> prove a real tool call is intercepted+recorded AND that a non-eval run still delivers for real (no
> record-mode leak into production).

**Slice:** Phase E-2 of Eval v2 (consolidated `eval-v2/plan.md` §6 Phase E-2, §2 Seam 4, `data-model.md` §4).
**Covers E-2 ONLY.**
**Depends on:** **WS-1 (DONE — a real durable run carries the flag through the governed tool path)** + the
**tool-governance wrapper** (the OPA/HITL interception point every tool call already flows through) + **E-1**
(the durable branch that carries a real run to intercept). **Enables E-3 + E-4 side-effect assertions.**
**Companion artifacts:** `e2/data-model.md` (`Tool.side_effecting` + the recorded-call payload shape).

> ⚠️ **Plan status — design stable, specifics indicative.** The architecture, sequencing, and locked
> decisions (D1–D4, R1–R3, parity gates, gap ledger) here are **stable and reviewable now** — that is what
> writing ahead buys. The execution specifics — `file:line`, migration numbers, image tags, orphan-greps,
> exact task order — are **indicative against the 2026-07-12 tree** and WILL drift as the WS-0→ spine merges.
> **Re-ground every specific against live code when this slice is minted into its own `tasks.md`** (the
> just-in-time step). Never treat a `file:line` or migration number here as ground truth. (CLAUDE.md: design
> docs go stale — verify in code before relying.)

> **Grounding note — RESOLVED at impl (2026-07-15).** WS-1's durable path is shipped, so the flag had a real
> run to ride. The **governance-wrapper interception point** was the one open question, and it is now
> confirmed: `sdk/agentshield_sdk/graph_builder.py` `_wrap_tool_with_governance.governed_tool` **step 3** —
> the single `return await fn(**kwargs)` delivery edge that every declarative-runner **and** SDK tool call
> crosses. There is exactly **one** such point (no fork to collapse). The record/mock branch sits immediately
> before it, so OPA (step 1) and HITL (step 2) run unchanged and only the downstream delivery is substituted.
> The `eval_mode` flag is threaded there as an **explicit parameter** (run-create → `PlaygroundRun.eval_mode`
> → dispatch body → the runner's `begin_eval_context` → the `_current_eval_mode` ContextVar).

---

## 1. Goal

Make evaluating a side-effecting agent **safe**: a `eval_mode=record` seam at the tool-governance boundary
records `{tool, args}` and returns a mock/replay **instead of invoking** the real downstream — so evaluating a
scheduled/webhook (or write-shaped durable) agent never sends a real email, files a real JIRA, or moves real
money, while still running the **real governed code path** (playground-execution-modes.md Principle 2). This is
the **enabler** E-3/E-4 assert against. Concretely, after E-2:

1. **`Tool.side_effecting` exists.** A boolean on `Tool` (default inferred from HTTP method:
   `POST/PUT/PATCH/DELETE ⇒ true`, overridable). Classifies which tools must be intercepted under eval.
2. **`eval_mode` is threaded explicitly.** From the eval-runner → run-create → the tool-governance wrapper.
   **Not** a `context=='playground'` sniff — interactive sandbox chat (a human test-firing) may legitimately
   want real side-effects; only the **batch eval-runner** sets `eval_mode=record` (No-Bandaid, `data-model.md`
   §4).
3. **The record/mock seam works and fails closed.** In `eval_mode=record`, a `side_effecting` tool is
   intercepted: `{tool, args}` is **recorded** to the run's step output; a **mock/replay** response is returned;
   the real downstream is **not** invoked. Read-only tools pass through untouched. A tool that **cannot be
   classified** is **mocked (not invoked)** — fail-closed; it is never allowed through under eval.
4. **`score_side_effects` asserts recorded vs expected.** A code scorer compares the recorded calls to an
   item's `expected_side_effects` (`occurs ∈ {exactly|at_least|never}`, `count`, `args_match`) — E-3/E-4
   consume it; E-2 ships the scorer + a durable proof.
5. **Parity — one seam.** The governance wrapper is the **only** interception point; the runner sets a flag,
   it does **not** mock tools itself. Declarative-runner and SDK tool paths honor the same flag (one
   implementation, no fork).

**Alignment Check:** the ultimate goal is *evaluate on the real code path without real side-effects*. Mocking
in the runner (outside governance) would evaluate a **different** path than production — a bandaid. E-2 puts
the seam at the one boundary every tool call already crosses, engaged by an explicit flag, so the eval runs the
real OPA/HITL path and only the **downstream delivery** is substituted. We do **not** skip governance to avoid
side-effects; we intercept **after** governance, at the delivery edge.

**Out of scope (later / deferred):** VCR-style record-once cassette **replay store** (E-2 ships fixed mock +
record; keyed replay is a follow-up — gap ledger); scheduled job-spec interpretation (E-3); webhook filter
interpretation (E-4); PII tokenization policy beyond "assert args, don't render raw PII" (reuses OQ-3).

---

## 2. Architecture — intercept after governance, at the delivery edge

```
 eval-runner (E-1 durable branch)                 registry-api / runner                    judge.py
 ────────────────────────────────                 ─────────────────────                    ────────
 POST /playground/runs {..., eval_mode:"record"}  run-create stamps eval_mode on the run
        │                                                  │
        ▼ durable run drives the governed tool path        ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  tool-governance wrapper  (the ONE interception point)            │
   │   OPA check → HITL park (unchanged) → DELIVERY:                   │
   │     if eval_mode == "record" and tool.side_effecting:            │
   │        record {tool, args} → run step output                     │
   │        return mock/replay  (DO NOT invoke downstream)            │  ← fail-closed on unclassifiable
   │     else: invoke downstream as normal (read-only or non-eval)    │
   └──────────────────────────────────────────────────────────────────┘
        │ recorded calls land in run_steps.output.recorded_side_effects[]
        ▼
   /eval/score mode=durable (+ side_effect dim)  →  score_side_effects(recorded, expected_side_effects)  ← NEW
```

**Seam — `eval_mode` at the governance boundary.** Every tool call already flows through the OPA/HITL wrapper.
E-2 adds a **delivery branch** at the end of that wrapper: on `eval_mode=record` + `side_effecting`, record +
mock instead of invoking. Governance (OPA + HITL) runs **unchanged** — the eval exercises the real policy path;
only the final downstream call is substituted. The flag is an **explicit parameter** threaded run-create →
wrapper, per declarative-runner and SDK (parity — the SDK-side governed tool call honors the same flag,
consolidated `plan.md` §5 SDK row).

**`score_side_effects` (code).** Reads the recorded calls, asserts each `expected_side_effects` entry:
`occurs=exactly|at_least|never` with `count` and `args_match` (dict-subset). Deterministic, no LLM.

**Mock strategies (`data-model.md` §2, `research.md` §4.3):**
1. **Mock** — return a fixed stub (item's optional `tool_mocks`, else a type-default success). Deterministic;
   best for CI regression.
2. **Record-once / replay (cassette)** — **deferred** (gap ledger). E-2 ships mock + record only.

---

## 3. Migration / Schema

**One additive migration** (`e2/data-model.md` §1; number indicative — after E-0's two migrations and the
spine's; head was `0057` on 2026-07-12, so E-2 first is **≥ E-0's last + 1**; confirm at impl):

- `≥00NN` — `tools.side_effecting BOOLEAN NOT NULL DEFAULT (inferred from method)`. Guarded/idempotent;
  backfill from the HTTP method (`POST/PUT/PATCH/DELETE ⇒ true`, else false; overridable).

Recorded calls live in the existing `run_steps.output` JSONB (`recorded_side_effects[]`) and surface in
`eval_run_results.eval_detail` (E-0's column) — **no new result column**. `eval_mode` is a **request/run
field**, not necessarily a persisted column (thread it; persist on the run only if the resume path needs it —
confirm at impl).

---

## 4. Constitution / retro gates (condensed)

| Gate | How E-2 satisfies it |
|---|---|
| **No-Bandaid** | The seam is an **explicit `eval_mode` param** threaded to the governance wrapper, **not** a `context=='playground'` sniff. One flag, no priority fallthrough. |
| **Fail-closed governance** | An unclassifiable side-effecting tool is **mocked (not invoked)** under eval, never allowed through. An eval that **cannot record** a side-effect **fails the item**, never silently passes (retro #4). Asserted by a test. |
| **Parity = shared code** | The governance wrapper is the **only** interception point; the runner sets a flag, it does not mock. Declarative-runner + SDK honor the same flag — grep proves no runner-side mock fork. |
| **Ship the gate's producer** | `score_side_effects` (reader) ships with the record seam (producer) — the recorded calls are produced by the wrapper and read by the scorer in the same slice. |
| **Golden-path per environment** | bash suite: a durable agent whose trajectory includes a `side_effecting` write, evaluated under `eval_mode=record`, performs **no** real HTTP write (assert the downstream was not hit), the call is recorded, and `score_side_effects` asserts it. An unclassified write tool is mocked. Fails (not skips) on missing fixture. |
| **DoD #3 no orphan code** | `Tool.side_effecting` is **read** by the wrapper; `recorded_side_effects` is **read** by `score_side_effects` + the results UI; all shipped together. Grep-for-caller is a task gate. |

---

## 5. File Structure (created/modified — indicative)

### Backend — registry-api + governance
| File | C/M | Responsibility |
|---|---|---|
| `services/registry-api/alembic/versions/00NN_tools_side_effecting.py` | **C** | `tools.side_effecting` (default from method). |
| `services/registry-api/models.py` | M | `Tool.side_effecting`. |
| `services/registry-api/schemas.py` | M | `EvalScoreRequest.recorded_side_effects`; `SideEffectAssertion` (`occurs`/`count`/`args_match`); thread `eval_mode` on run-create. |
| tool-governance wrapper (e.g. `tool_governance.py` / the OPA/HITL wrap — **locate at impl**) | M | Delivery branch: `eval_mode=record` + `side_effecting` ⇒ record + mock, don't invoke; fail-closed on unclassifiable. |
| `services/registry-api/routers/playground.py` | M | Thread `eval_mode` into run-create; `/eval/score` adds the `side_effect` dim (calls `score_side_effects`). |
| `services/registry-api/judge.py` | M | `score_side_effects(recorded_calls, expected_side_effects)` — code scorer. |

### SDK
| File | C/M | Responsibility |
|---|---|---|
| `sdk/agentshield_sdk/` tool wrapper (the governed tool call) | M | Honor `eval_mode=record` in the SDK-side governed path (parity with declarative). |

### Backend — eval-runner
| File | C/M | Responsibility |
|---|---|---|
| `services/eval-runner/main.py` | M | Pass `eval_mode=record` on run-create for modes that assert side-effects (durable-with-writes / scheduled / webhook); collect `recorded_side_effects` for `/eval/score`. |

### Frontend — Studio
| File | C/M | Responsibility |
|---|---|---|
| `studio/src/pages/EvalResultsPage.tsx` | M | Render recorded side-effects ("the email that would have been sent", args tokenized for PII) + the side-effect dimension. |

### Tests + infra
| File | C/M | Responsibility |
|---|---|---|
| `scripts/e2e/suite-NN-eval-v2-side-effects.sh` | **C** | Durable-with-write under `eval_mode=record`: no real downstream call; recorded + asserted; unclassifiable tool mocked (fail-closed). |
| `scripts/e2e/run-all.sh` | M | Register the suite. |
| `scripts/deploy-cpe2e.sh` + `charts/agentshield/values.yaml` | M | Bump registry-api, eval-runner, studio (SDK rebuild note). |
| `docs/experience/playground.md` | M | Side-effect record/mock under eval. |

---

## 6. Tasks (dependency-ordered)

### T1 — `Tool.side_effecting` (migration + model + backfill)
- **Files:** migration `00NN` (C), `models.py` (M), `schemas.py` (M).
- **Contract:** `e2/data-model.md` §1 — boolean default inferred from HTTP method; overridable; guarded/idempotent.
- **Acceptance:** upgrade round-trips; existing HTTP `POST/PUT/PATCH/DELETE` tools backfill to `true`, `GET` to
  `false`; mapper configures.
- **Deps:** E-0 migrations landed. **Verify:** `ast.parse` + `configure_mappers()`; migration up/down/up.

### T2 — Thread `eval_mode` run-create → governance wrapper
- **Files:** `routers/playground.py` (M), `schemas.py` (M), the governance wrapper (M — **locate at impl**),
  SDK tool wrapper (M).
- **Contract:** `eval_mode ∈ {live, record}` (default `live`) threaded run-create → wrapper; only the batch
  eval-runner sets `record`. Explicit param end-to-end, no `context` sniff.
- **Acceptance:** a run created with `eval_mode=record` carries the flag to the wrapper (proven by a log/assert
  at the boundary); interactive sandbox chat stays `live`.
- **Deps:** T1. **Verify:** `grep -rn "eval_mode" services/registry-api services/eval-runner sdk` → threaded, no
  `context ==` sniff introduced.

### T3 — Record/mock delivery branch (the seam) — fail-closed
- **Files:** the governance wrapper (M), SDK tool wrapper (M).
- **Contract:** on `eval_mode=record` + `tool.side_effecting`: record `{tool, args}` to `run_steps.output.
  recorded_side_effects[]`, return a mock (item `tool_mocks` or type-default), **do not invoke** downstream.
  Read-only tools pass through. Unclassifiable side-effecting tool ⇒ **mock (not invoke)** — fail-closed.
- **Acceptance:** a durable run with a write tool under `record` performs **no** real HTTP write (assert the
  downstream mock endpoint was never hit); the call is recorded; an unclassified write tool is mocked.
- **Deps:** T2. **Verify:** `ast.parse`; suite-NN no-real-write assertion + fail-closed case; `grep` proves a
  single interception point (no runner-side mock).

### T4 — `score_side_effects` + `/eval/score` side-effect dim
- **Files:** `judge.py` (M), `routers/playground.py` (M).
- **Contract:** `score_side_effects(recorded, expected_side_effects)` — per assertion `occurs=exactly|at_least|
  never`, `count`, `args_match` (dict-subset); code, no LLM. `/eval/score` adds the `side_effect` dim.
- **Acceptance:** a recorded call matching `expected_side_effects` scores `1.0`; a `never` violated scores
  `0.0`; a missing required call fails.
- **Deps:** T3. **Verify:** unit fixtures; `grep -n "def score_side_effects" judge.py`.

### T5 — eval-runner + results render + suite + deploy
- **Files:** `services/eval-runner/main.py` (M), `EvalResultsPage.tsx` (M),
  `suite-NN-eval-v2-side-effects.sh` (C), `run-all.sh` (M), `deploy-cpe2e.sh`+`values.yaml` (M),
  `docs/experience/playground.md` (M).
- **Acceptance:** the runner sets `eval_mode=record` for side-effect-asserting items + posts recorded calls to
  `/eval/score`; results render recorded side-effects (PII-tokenized) + the dimension; suite green; tags bumped.
- **Deps:** T1–T4. **Verify:** `bash scripts/e2e/suite-NN-eval-v2-side-effects.sh`; `cd studio && npm run
  typecheck && npm run test`.

---

## 7. Gap Ledger

> **Status 2026-07-15 — re-grounded against the shipped code.** Phases 1–5 landed. Rows that were
> "confirm at impl" are resolved and folded into the design body (§2); the live rows below are the honest
> remainder. See `e2/tasks.md` for the per-task ledger.

| Item | Status | Note |
|---|---|---|
| Record-once cassette **replay** store (vs fixed mock) | **deferred (intentional)** | E-2 ships fixed mock + record only: the seam returns a type-default `{"status":"ok","id":"mock-<uuid>"}` sentinel and records `{tool,args,mocked_response,would_have_invoked}`. VCR-style keyed replay (`{tool,args-hash}` → the real recorded response) is the follow-up (`research.md` §4.3). The record→mock proof is the E-2 scope; nothing in E-2 reads a cassette. |
| Item **`tool_mocks` not threaded to the seam** | **not-yet-wired (debt, low)** | `DurableDatasetItem.tool_mocks` is accepted + persisted at the door (`schemas.py`), but the seam does **not** read it — every intercepted call gets the type-default sentinel regardless. The plan's T008 contract said "item `tool_mocks` else a type-default"; only the type-default half shipped, because the seam runs **in the agent pod** and the item never travels there (only `eval_mode` rides the dispatch body). Wiring it means threading the per-tool mock map through `dispatch_durable_run` → `DurableRunRequest` → `begin_eval_context`. Harmless today (an agent that branches on a write's response body would need it); tracked so it never reads as shipped. `schemas.py:1222` points here. |
| A **violated** side-effect assertion does not by itself fail the item | **by-design (noted, sharp edge)** | `side_effect` is one weighted dimension (default `0.2`), so a recorded-but-wrong call (or a violated `never`) scores the dim `0.0` yet can still land the composite ≈`0.83` — above the `0.7` pass threshold — when response/trajectory are perfect. Same property E-1's `tool_call` dim has; the dimension score + `eval_detail` are the evidence. The case the eval **cannot verify** (a required call recorded **nowhere**) is fail-closed **hard at the eval-runner** instead of relying on this arithmetic, since `dimension_weights` is per-run overridable. If "any violated side effect ⇒ item fails" is wanted, that is a new policy decision (a gate, not a weight) — not silently assumed here. |
| SDK/custom-container agent path parity | **not-yet-wired (debt) if the suite uses a declarative agent** | The seam is one shared function in `sdk/agentshield_sdk/graph_builder.py`, so an SDK-container agent honors the identical flag — but it is bundled into each agent image at build time, so an SDK-container agent must be **rebuilt** on the new SDK to get it. suite-74 drives a **declarative** agent (declarative-runner image, rebuilt at `0.1.46`). Tracked so SDK-container parity never reads as proven. |
| `_execute_durable_run` sets no OPA `_current_user_context` | **noted (pre-existing, out of E-2 scope)** | E-2 adds `_current_eval_mode` only; the durable path's empty OPA user context is a separate pre-existing gap, neither introduced nor fixed here. |

### Resolved (folded into the design body — no longer gaps)

- ~~Locating the single governance interception point~~ — **RESOLVED.** It is
  `sdk/agentshield_sdk/graph_builder.py` `_wrap_tool_with_governance.governed_tool` **step 3**, the single
  `return await fn(**kwargs)` delivery edge every declarative-runner **and** SDK tool call crosses. There was
  exactly **one**; no collapse was needed and no runner-side mock fork exists (T020 grep-gated). §2 documents it.
- ~~PII tokenization of recorded args~~ — **RESOLVED (policy inherited from OQ-3, now implemented).** Recorded
  args are asserted **by value** server-side (`score_side_effects` dict-subset over the raw args) and
  **tokenized for display** by `studio/src/lib/piiTokenize.ts` (email/SSN/card/phone → `‹email›`…), consumed by
  `EvalResultsPage`'s recorded-side-effect panel. Raw PII is never rendered to the reviewer.
- ~~`eval_mode` may not need persisting~~ — **RESOLVED: it does.** A durable run parks at HITL and **resumes**
  via a separate dispatch, which re-drives the graph and re-crosses the delivery edge — so `eval_mode` is a
  column (`playground_runs.eval_mode`, migration `0063`) read back on the resume dispatch.

**No orphan flags:** `Tool.side_effecting` → read by `governed_tool` (`_should_record`); `eval_mode` → read by
the runner's `begin_eval_context` → the delivery branch; `recorded_side_effects` → written by the durable
harness, read by `score_side_effects` + the results UI; `score_side_effects` → called by `/eval/score`. All
shipped together (T020 grep gate).

---

## 8. Execution Notes

- **Intercept after governance, not instead of it.** The eval must run the real OPA/HITL path; only the final
  downstream delivery is substituted. Putting the mock before governance would evaluate a different path — the
  bandaid E-2 exists to avoid.
- **Fail-closed is a test.** Assert an unclassifiable write tool is **mocked (not invoked)** and an
  un-recordable side-effect **fails the item** — never a silent pass (retro #4).
- **One seam, explicit flag.** Locate the single tool-governance delivery point; thread `eval_mode` as an
  explicit param; make the SDK path honor the identical flag. No runner-side mocking, no `context` sniff.
- **Bump registry-api + eval-runner + studio** in both files; SDK ships via pip into agent images (rebuild
  note — no separate SDK tag).
</content>

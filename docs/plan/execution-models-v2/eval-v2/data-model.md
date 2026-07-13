# Eval v2 — Data Model: per-mode dataset schema + trajectory/side-effect result store

**Companion to** `plan.md` (the phased plan) and `research.md` (industry survey). **Docs only.**

> ⚠️ **Plan status — design stable, specifics indicative.** The discriminated-union item
> schemas, the discriminator column, and the result-store shape below are **stable and reviewable
> now**. The exact column names, migration numbers, `file:line` anchors, and CHECK-constraint spellings
> are **indicative against the 2026-07-12 / execution-models-v2 tree** and WILL drift as WS-0…WS-6 land.
> **Re-ground every specific against live code** (`services/registry-api/models.py`,
> `schemas.py`, `alembic/versions/`) when this is minted into a `tasks.md`. Migration numbers here are
> placeholders — head was `0057`, WS-0 takes `0058`, so Eval v2's first migration is **≥ `0059`**;
> confirm at impl (`ls alembic/versions/ | tail`).

---

## 0. What exists today (code-truth, verified)

| Object | Today | File |
|---|---|---|
| `PlaygroundDataset.items` | `JSONB`, `list[Any]` — **schema-free at the DB**. UI authors one JSON object per line, expects `{input, expected_output}` (text). | `models.py:1362`, `DatasetsPage.tsx:251` |
| `EvalRun` | Has `agent_name`, `agent_version_id`, `workflow_id/_version_id`, `dataset_id`, counts, `overall_score`. **No `mode`.** | `models.py:1384` |
| `EvalRunResult` | `input_message`, `expected_output`, `response`, `judge_score`, `judge_reasoning`, `passed` — **all text/scalar**. No trajectory/tool/side-effect fields. | `models.py:1432` |
| `RunStep` | Polymorphic `run_id` → `agent_runs.id` **or** `playground_runs.id`; `step_number`, `name`, `status`, `output JSONB`, `approval_id`. **This is the trajectory substrate.** | `models.py:1554` |
| `AgentTrigger` | `filter_conditions JSONB` (webhook), `input_payload JSONB` (schedule job spec). | `models.py:1602` |
| `AgentEvent` | `status ∈ {matched, filtered, rejected}`, `filter_reason`, `payload`, `run_id`. **The filter-decision substrate.** | `models.py:1662` |
| `PlaygroundRun` | Already carries `execution_shape`, `trigger_type`, `trigger_payload`, `input_payload`. | `models.py:1285` |

**Consequence:** because `items` is free-form JSONB, the per-mode item schema is primarily a
**validation + interpretation** concern, not a storage migration. The migration work is small and
lives in three places: a **dataset discriminator**, an **eval-run mode**, and a **richer result row**.

---

## 1. Discriminator: where "mode" lives

Per OQ-C (playground-execution-modes.md §11): **no unified dataset shape**; batch eval interprets
items **by the agent's mode**. Two candidate discriminators — we use **both, with a clear owner**:

- **`playground_datasets.mode`** (NEW column) — the authoring discriminator. Declares which item
  schema this dataset's rows follow, so the authoring UI can validate on save and render the right
  editor. Domain: `reactive | durable | scheduled | webhook | workflow`. Default `reactive`
  (back-compat: every existing dataset is reactive text).
- **`eval_runs.mode`** (NEW column) — the **interpretation** discriminator, resolved at eval-launch
  from the **agent/workflow under evaluation** (its `execution_shape` + `trigger`), not from the
  dataset. This is the authoritative "how the runner reads each item." It is validated against the
  dataset's `mode` at launch (mismatch → 422), so you can't run a `webhook` dataset against a
  `reactive` agent.

> **Why both, not one:** the dataset column makes authoring self-describing and lets us reject a
> malformed row at save time (fail at the door). The eval-run column records what actually happened
> and drives the runner's branch — it is derived from the executable, honoring OQ-C's "interpret by
> the agent's mode." They must agree; the launch check enforces it. (No-Bandaid: an explicit,
> checked discriminator beats sniffing item keys at runtime.)

`mode` is **not** a new axis — it is a projection of the existing execution cube
(`execution_shape` × `trigger` × `agent_class`) onto the four eval-relevant families plus `workflow`:

| Eval `mode` | Comes from | Evaluate = (playground-execution-modes.md) |
|---|---|---|
| `reactive` | `execution_shape=reactive`, `trigger=manual/api` | response correctness |
| `durable` | `execution_shape=durable`, `trigger=manual/api` | trajectory + tool-call correctness + HITL-arg review |
| `scheduled` | `trigger=schedule` | job-spec run → side-effect verification |
| `webhook` | `trigger=webhook` | filter match/miss + action correctness + injection robustness |
| `workflow` | executable is a `CompositeWorkflow` | run-tree / per-member eval (one extra zoom) |

---

## 2. The discriminated-union item schema

One TypeScript/Pydantic **discriminated union** keyed by `kind` (mirrors `mode`; carried per-item so a
reader never has to guess). Every variant shares a small **common envelope**; the mode-specific fields
hang off it. Reference-free items simply omit `expected_*` and supply a `rubric` instead (see §2.6).

### 2.0 Common envelope (all kinds)
```jsonc
{
  "kind": "reactive|durable|scheduled|webhook|workflow",  // discriminator (== dataset.mode)
  "id": "opt-stable-item-id",           // optional; for stable per-item diffing across runs
  "notes": "why this case exists",      // optional author note
  "rubric": "string criteria, optional",// reference-free scoring criteria (see §2.6)
  "weight": 1.0,                          // optional; weight in the dataset aggregate
  "tags": ["regression","edge"]          // optional; slice dashboards by tag
}
```

### 2.1 `reactive` (response correctness) — **back-compat with today**
```jsonc
{
  "kind": "reactive",
  "input_message": "What's the status of order 123?",
  "expected_output": "Order 123 shipped on the 30th"   // optional if rubric present
}
```
> Today's `{input, expected_output}` rows upgrade losslessly: `input` → `input_message`; missing
> `kind` defaults to `reactive`. A **compat shim** in the runner reads either key (see plan T-C).

### 2.2 `durable` (trajectory + tool-call + HITL-arg review)
```jsonc
{
  "kind": "durable",
  "input_payload": { "contract_url": "s3://demo/acme.pdf" },
  "expected_output": "…",                       // optional (final-answer check)
  "expected_trajectory": {                       // optional; scored against real run_steps (WS-1)
    "match_mode": "superset|ordered|exact|unordered",   // see research.md §Trajectory
    "steps": [
      { "tool": "parse_document" },
      { "tool": "extract_clauses" },
      { "tool": "jira_create",
        "args_match": { "project": "LEG" },      // partial/semantic arg assertion
        "expect_approval": true }                 // HITL-arg review: this step SHOULD gate
    ]
  }
}
```
- **Trajectory** is scored against the `run_steps` the durable run actually writes — which only become
  **real per-node steps in WS-1** (before WS-1 the declarative-runner writes a 2-step skeleton, so
  trajectory scoring is meaningless — see plan §Sequencing).
- **HITL-arg review** (`expect_approval`): asserts the step parked for approval **and** the tool args
  presented match `args_match` — the sandbox self-approval path (OQ-E "always show the args") is where
  this is observable.

### 2.3 `scheduled` (job-spec dataset + side-effect verification)
```jsonc
{
  "kind": "scheduled",
  "job_spec": { "region": "us-east-1", "lookback_days": 7 },  // == AgentTrigger.input_payload shape
  "expected_output": "…",                                       // optional
  "expected_trajectory": { … },                                 // optional (durable-inner schedule)
  "expected_side_effects": [                                     // optional; asserted via tool-recording
    { "tool": "send_email",
      "args_match": { "to": "compliance@acme.com" },
      "occurs": "exactly|at_least|never",
      "count": 1 }
  ]
}
```
- `job_spec` is fed as the run's `input_payload` — identical shape to `AgentTrigger.input_payload`
  (the production per-schedule job spec). Payload-based eval only makes sense once the scheduled path
  is real — **WS-3**.
- **Side-effects** are asserted against **recorded tool calls** (§4 sandbox side-effect handling), not
  by letting the agent actually send email.

### 2.4 `webhook` (filter match/miss + action correctness + injection robustness)
```jsonc
{
  "kind": "webhook",
  "trigger_payload": { "event_type": "payment.fail", "amount": 12000 },
  "expected_match": true,                    // filter SHOULD match this event
  "expected_filter_reason": null,            // when expected_match=false, the reason substring
  "expected_output": "…",                    // only meaningful when expected_match=true
  "expected_trajectory": { … },              // optional (durable-inner webhook)
  "expected_side_effects": [ … ],            // optional
  "injection_probe": {                        // optional untrusted-input robustness case
    "must_not_call": ["wire_transfer", "delete_record"],   // tools the payload tries to coerce
    "must_refuse": true                                     // agent should refuse the injected instruction
  }
}
```
- **Filter match/miss** is the first-class signal (playground-execution-modes.md §7: "filtered events
  are logged, not silently dropped"). Scored against the `AgentEvent.status` (`matched`/`filtered`) +
  `filter_reason` the **real** filter logic produces — **WS-4**.
- **`injection_probe`** encodes untrusted-input cases (the payload is attacker-controlled): assert the
  agent did **not** call a forbidden tool and/or refused. See research.md §Prompt-injection.

### 2.5 `workflow` (run-tree / per-member eval)
```jsonc
{
  "kind": "workflow",
  "input_message": "…",                       // or input_payload for triggered workflows
  "expected_output": "…",
  "expected_member_path": ["intake","triage","resolver"],  // members expected to run, in order
  "per_member": {                              // optional per-member rubric/expectation
    "triage": { "rubric": "correctly routed to billing" }
  }
}
```
- A workflow produces a **run tree** (parent `AgentRun` → child `AgentRun`s via `parent_run_id`,
  already in `models.py:1509`). `expected_member_path` is a **trajectory at the member granularity**;
  per-member rubric drops one zoom level into that child's own steps.

### 2.6 Reference-free vs reference-based (all kinds)
- **Reference-based:** item supplies `expected_output` / `expected_trajectory` / `expected_side_effects`;
  judge scores actual-vs-expected.
- **Reference-free:** item omits expecteds and supplies a **`rubric`** string (or relies on a
  dataset-level default rubric); the judge scores the run against the rubric alone (e.g. "did it
  resolve the ticket without leaking PII?"). This is the only viable mode for many durable/agentic
  cases where a golden final answer doesn't exist but good behavior is describable. See research.md
  §LLM-as-judge (reference-free / rubric / G-Eval).

---

## 3. Schema changes (tables & columns)

### 3.1 `playground_datasets` — add discriminator (migration ≥ `0059`)
| Column | Type | Notes |
|---|---|---|
| `mode` | `String(16)` NOT NULL, `server_default 'reactive'`, CHECK `∈ {reactive,durable,scheduled,webhook,workflow}` | authoring discriminator; back-compat default |
| `schema_version` | `SmallInt` NOT NULL, `server_default 1` | lets item schema evolve without a data migration |

`items` stays `JSONB`. Validation of each row against the `mode` variant happens in the API layer
(`PlaygroundDatasetCreate/Update` gains a discriminated-union validator) — **not** a DB constraint
(JSONB shape checks belong in Pydantic).

### 3.2 `eval_runs` — record the interpretation mode + thresholds
| Column | Type | Notes |
|---|---|---|
| `mode` | `String(16)` NOT NULL, `server_default 'reactive'`, same CHECK | resolved from the executable at launch; must match dataset.mode |
| `dimension_weights` | `JSONB` nullable | optional per-dimension weights (response/trajectory/tool/side-effect/filter) for the composite score |
| `pass_threshold` | `Numeric(4,3)` nullable | overrides the global `EVAL_PASS_THRESHOLD=0.7` per run |

### 3.3 `eval_run_results` — the trajectory/side-effect/filter store
Rather than a dozen sparse columns, add **two JSONB payloads plus the composite scalars** the
dashboard already reads. Keep `judge_score` as the **composite** (so existing readers and the
`eval_passed` auto-promote in `eval_runner.py:299` keep working unchanged).

| Column | Type | Notes |
|---|---|---|
| `dimension_scores` | `JSONB` nullable | `{response:0.9, trajectory:1.0, tool_call:0.8, side_effect:1.0, filter:1.0}` — per-dimension 0–1 |
| `eval_detail` | `JSONB` nullable | the evidence: `{expected_trajectory, actual_trajectory, tool_diffs[], recorded_side_effects[], matched, filter_reason, injection_result}` |
| `trigger_payload` | `JSONB` nullable | the event/job payload this item ran (scheduled/webhook) — mirrors `PlaygroundRun.trigger_payload` |
| `matched` | `Boolean` nullable | webhook: did the filter match? (fast column for pass/fail + dashboards) |
| `run_id` | `UUID` nullable (soft FK) | the `playground_runs.id` this item produced — lets the results UI deep-link to the run tree + `run_steps` |

> `judge_score` (composite) is `weighted_mean(dimension_scores, dimension_weights)`; `passed` =
> `judge_score >= eval_runs.pass_threshold`. The **auto-set `eval_passed`** wire
> (`eval_runner.py:299-331`) is **unchanged** — it reads the composite `overall_score`.

### 3.4 No new dataset table
We deliberately do **not** introduce `eval_datasets` / per-item rows. `playground_datasets.items`
JSONB + a discriminator is sufficient and avoids a write-path migration. If per-item run history or
labels grow heavy later, promoting items to their own table is a clean follow-up (gap ledger).

---

## 4. Sandbox side-effect handling (tool mocking / recording)

The central risk: **evaluating a scheduled/webhook agent must not send a real email, file a real
JIRA, or move real money.** Yet the eval is only meaningful if it runs the *real* governed code path
(playground-execution-modes.md Principle 2: "Evaluate on the real code path… No mocked shortcuts").
Resolution — a **record/mock seam at the tool-governance boundary**, engaged only in eval context:

- **Governance wrapper is the seam.** Every tool call already flows through the OPA/HITL governance
  wrapper. In `context=playground` **with `eval_mode=record`**, a side-effecting tool (HTTP tools with
  a write method, or tools flagged `side_effecting=true`) is **intercepted**: the call's `{tool, args}`
  is **recorded** to the run's step output, and a **canned/mocked response** is returned instead of
  actually invoking the downstream. Read-only tools pass through untouched.
- **Two record strategies** (research.md §Side-effecting eval):
  1. **Mock** — return a fixed stub (from the dataset item's optional `tool_mocks`, or a type-default
     success). Deterministic; best for CI regression.
  2. **Record-once / replay (cassette)** — first eval run against a real sandbox downstream records
     responses into a cassette keyed by `{tool,args-hash}`; subsequent runs replay. VCR-style.
- **Assertion** — `expected_side_effects` is checked against the **recorded** calls, not real
  deliveries. "The email that would have been sent" is asserted by its args, tokenized for PII
  (OQ-3 — raw PII never shown to the reviewer).
- **Explicitness (No-Bandaid):** the seam is an **explicit `eval_mode` parameter** threaded from the
  eval-runner → run-create → governance wrapper, **not** a `context=='playground'` sniff. Interactive
  playground chat (a human test-firing) may legitimately want real side-effects in sandbox; only the
  **batch eval-runner** sets `eval_mode=record`. One flag, explicit, no priority fallthrough.

Marking a tool `side_effecting`: add `Tool.side_effecting: bool` (default inferred from HTTP method —
`POST/PUT/PATCH/DELETE` ⇒ true — overridable). This is a small, orthogonal column; listed in the plan
as a dependency for scheduled/webhook eval, not a blocker for reactive/durable.

---

## 5. Migration ordering (indicative)

1. `≥0059` — `playground_datasets.mode` + `schema_version`; `eval_runs.mode` + `dimension_weights` +
   `pass_threshold`; backfill all existing datasets/runs to `mode='reactive'`. Idempotent, guarded,
   data-preserving.
2. `≥0060` — `eval_run_results` add `dimension_scores`, `eval_detail`, `trigger_payload`, `matched`,
   `run_id`. All nullable; no backfill needed (old rows read as response-only).
3. `≥0061` — `tools.side_effecting` (default from method). Only needed for the scheduled/webhook slice.

Each migration re-grounds its `down_revision` against `ls alembic/versions/ | tail` at impl time —
WS-0…WS-6 are consuming numbers concurrently.

---

## 6. Back-compat & orphan-avoidance notes

- **Every existing dataset** is a valid `reactive` dataset after migration 1 (default). The eval-runner
  compat shim reads `item["input"]` or `item["input_message"]`. No data rewrite.
- **`judge_score` stays the composite** so `EvalRunResult.judge_score`, the dashboard, and the
  `eval_passed` auto-promote need **no change** — new dimensions are additive.
- **No orphan columns:** `dimension_scores`/`eval_detail`/`matched`/`run_id` are all read by the
  Eval v2 results UI (plan T-UI) and written by the upgraded runner (plan T-R*) in the **same slice**;
  `tools.side_effecting` is read by the governance record seam. Grep-for-caller is a plan acceptance gate.

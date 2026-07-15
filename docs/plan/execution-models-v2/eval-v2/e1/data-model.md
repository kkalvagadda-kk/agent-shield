# E-1 Data Model — durable item schema + `run_steps`→trajectory mapping

**Companion to** `e1/plan.md`. **Docs only.** This is the E-1 slice of the consolidated `eval-v2/data-model.md`
(§2.2 durable item, §3.3 result store) plus the **`run_steps`→trajectory extraction** that E-1 owns because
its producer (WS-1) is shipped.

> ⚠️ **Plan status — design stable, specifics indicative.** The durable item schema and the trajectory
> mapping below are **stable and reviewable now**. Exact column/field names, migration numbers, and
> `file:line` anchors are indicative against the 2026-07-13 tree — re-ground against
> `services/registry-api/models.py`, `schemas.py`, `sdk/agentshield_sdk/durable.py`, and
> `routers/playground.py` at `tasks.md` mint time.

---

## 0. What exists today (code-truth, verified 2026-07-13)

| Object | Today | File |
|---|---|---|
| `RunStep` | `run_id` (polymorphic → `agent_runs.id` **or** `playground_runs.id`, no DB FK), `step_number`, `name` (real LangGraph node/tool name), `status ∈ {pending,running,completed,failed,awaiting_approval,cancelled}`, `output JSONB`, `approval_id` FK, `error_message`, `started_at`, `completed_at`. **This is the trajectory substrate.** | `models.py:1570` |
| durable playground run | `POST /playground/runs` sets `thread_id = run_id` and drives `run_durable` from the shared harness. | `routers/playground.py:625` |
| step-update callback | `POST /playground/runs/{id}/step-update` writes/updates a `RunStep` per node/tool boundary. | `routers/playground.py:260` |
| step read | `GET /playground/runs/{id}/steps` → ordered `list[RunStepResponse]`. | `routers/playground.py:355` |
| shared harness | `StepEmitter` POSTs one `StepUpdate` per boundary; `run_durable` drives `astream_events`; `interrupt()`→`awaiting_approval`. | `sdk/agentshield_sdk/durable.py` |

**Consequence:** the durable **item** schema is a validation/interpretation concern over the existing `items`
JSONB (no storage migration); the only E-1-specific data work is (a) validating the `durable` variant and
(b) mapping `RunStep` rows into an `actual_trajectory` the scorer compares.

---

## 1. `durable` dataset item (discriminated-union variant, `kind:"durable"`)

Shares the common envelope from the consolidated `data-model.md` §2.0 (`kind`, `id?`, `notes?`, `rubric?`,
`weight?`, `tags?`).

```jsonc
{
  "kind": "durable",
  "input_payload": { "contract_url": "s3://demo/acme.pdf" },   // fed to POST /playground/runs
  "expected_output": "…",                                       // optional final-answer check (reference-based)
  "expected_trajectory": {                                       // optional; scored vs real run_steps
    "match_mode": "superset",                                    // exact | ordered | superset | unordered (default superset)
    "steps": [
      { "tool": "parse_document" },
      { "tool": "extract_clauses" },
      { "tool": "jira_create",
        "args_match": { "project": "LEG" },                     // partial dict-subset assertion on the call args
        "expect_approval": true }                                // HITL-arg review: this step SHOULD park
    ]
  }
}
```

- **Reference-free durable** is legal: omit `expected_trajectory` + `expected_output`, supply `rubric` — the
  composite degrades to `{response}` scored against the rubric (many agentic runs have no golden path,
  `research.md` §4.1 G-Eval / AspectCritic).
- `match_mode` semantics: see `e1/plan.md` §2.2 (renamed agentevals set — `research.md` §4.2).
- `args_match` is a **subset** that must be present in the actual tool-call args (dict-subset). Absent-in-actual
  ⇒ that tool's arg dimension fails, with a `tool_diffs` entry.

---

## 2. `EvalScoreRequest` (durable) — what the runner sends `/eval/score`

```jsonc
{
  "mode": "durable",
  "item": { …the durable item above… },
  "input": "<stringified input_payload>",       // for score_response context
  "response": "<final answer text>",             // for score_response
  "run_id": "<playground_runs.id>",              // soft link; also stored on the result row
  "actual_trajectory": [                          // built from RunStep rows (§3)
    { "step_number": 1, "name": "parse_document", "status": "completed",
      "tool": "parse_document", "args": { … } },
    { "step_number": 2, "name": "extract_clauses", "status": "completed",
      "tool": "extract_clauses", "args": { … } },
    { "step_number": 3, "name": "jira_create", "status": "awaiting_approval",
      "tool": "jira_create", "args": { "project": "LEG", … }, "approval_id": "…" }
  ]
}
```

Response (composite shape shared with all modes):
```jsonc
{
  "composite": 0.84,
  "dimension_scores": { "response": 0.9, "trajectory": 1.0, "tool_call": 0.7 },
  "detail": {
    "expected_trajectory": { … },
    "actual_trajectory": [ … ],
    "tool_diffs": [ { "step": "jira_create", "expected_args": {"project":"LEG"},
                      "actual_args": {"project":"LEG","summary":"…"}, "arg_match": true } ],
    "approvals": [ { "step": "jira_create", "expected": true, "parked": true, "args_matched": true } ]
  }
}
```

---

## 3. `run_steps` → `actual_trajectory` mapping (E-1 owns this — match the producer)

The runner reads `GET /playground/runs/{id}/steps` and projects each `RunStepResponse` into a trajectory
entry:

| Trajectory field | Source `RunStep` field | Notes |
|---|---|---|
| `step_number` | `step_number` | ordering key |
| `name` | `name` | the real LangGraph node/tool name (WS-1 removed the `input_processing`/`agent_execution` skeleton) |
| `status` | `status` | `completed`/`failed`/`awaiting_approval` — the last drives `expect_approval` scoring |
| `tool` | `output.tool` **(confirm key at impl)** | the tool name when the boundary is a tool call; node-only boundaries have no tool |
| `args` | `output.args` **(confirm key at impl)** | the tool-call args for `args_match` |
| `approval_id` | `approval_id` | present ⇒ the step parked (HITL-arg review) |

> **The one place E-1 must match the producer exactly:** which `RunStep.output` keys carry `{tool, args}`.
> The harness `StepEmitter`/`StepUpdate.output` convention (`sdk/agentshield_sdk/durable.py`) is the source
> of truth — read it at impl and mirror the key names in the projection. If tool/args are not yet emitted
> into `output`, that is a **small producer addition in the harness** (add `{tool, args}` to the tool-boundary
> `StepUpdate.output`), listed as a T1 pre-req, not a new store.

**One entry per LOGICAL tool call (collapse — E-1 fix).** A single tool call can span MULTIPLE
`run_steps`: on a HITL park the harness emits a `running` boundary (`on_tool_start`, no `approval_id`)
and then a SEPARATE `awaiting_approval` boundary (next step number, carrying the `approval_id`) because
the interrupt fires before `on_tool_end` (`sdk/agentshield_sdk/durable.py:_drive`). The projection
therefore **collapses** a call's consecutive same-tool rows into ONE entry
(`eval-runner/main.py:_collapse_tool_calls`): an entry folds into the immediately-preceding one iff both
carry the same non-null `tool` AND the previous entry's status is in-flight (`running`/`pending`); the
fold advances to the later boundary's status and keeps a **sticky** `approval_id`. Without this the park
evidence lands on a different entry than the tool's first `running` boundary, and `score_tool_calls`
greedy-matches an `expect_approval` step to the un-parked `running` entry → `parked:false` for a real
park. Distinct completed calls of the same tool are NOT merged (a `completed` boundary is terminal).

**Scoring reads (no LLM):**
- `score_trajectory`: compare the ordered `[name where tool present]` (or `[tool]`) list to
  `expected_trajectory.steps[].tool` under `match_mode`.
- `score_tool_calls`: per matched step, `args_match ⊆ actual.args` (dict-subset).
- `expect_approval`: the matched step's `status == 'awaiting_approval'` (or non-null `approval_id`) **and**
  its `args` satisfied `args_match`.

---

## 4. Schema changes

**None owned by E-1.** All columns come from E-0 (`playground_datasets.mode`, `eval_runs.mode`/
`dimension_weights`/`pass_threshold`, `eval_run_results.dimension_scores`/`eval_detail`/`run_id`). E-1
**populates** the durable slice of those columns and adds the `DurableDatasetItem` Pydantic variant +
validation — no DDL.

## 5. Back-compat & orphan-avoidance

- Durable datasets are **new** (no legacy rows to migrate); existing reactive datasets are untouched.
- `dimension_scores.trajectory`/`tool_call` + `eval_detail.tool_diffs`/`approvals` are **read** by the
  EvalResultsPage trajectory panel (E-1 T5) in the same slice — no orphan columns.
- If the harness does not yet emit `{tool, args}` into `RunStep.output`, adding it is a producer change in
  `durable.py` (shipped with E-1 T1), not a new table.
</content>

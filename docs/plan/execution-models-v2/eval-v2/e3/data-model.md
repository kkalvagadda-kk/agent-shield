# E-3 Data Model — `scheduled` item schema (job-spec + side-effect assertions)

**Companion to** `e3/plan.md`. **Docs only.** E-3 slice of the consolidated `eval-v2/data-model.md` §2.3.

> ⚠️ **Plan status — design stable, specifics indicative.** The `scheduled` item schema is **stable and
> reviewable now**. The scheduled **fire path** it drives is WS-3's (not built) — banner-indicative. The
> `job_spec` shape is grounded against the shipped `AgentTrigger.input_payload` (`models.py:1618`). Re-ground
> the fire entrypoint against WS-3 at `tasks.md` mint time.

---

## 0. What exists today (code-truth)

| Object | Today | File |
|---|---|---|
| `AgentTrigger.input_payload` | `JSONB` — the per-schedule job spec fed to a scheduled run (production shape E-3's `job_spec` mirrors). | `models.py:1618` |
| `PlaygroundRun.trigger_payload` | already carries the trigger/job payload a run ran with. | `models.py:1301` |
| `eval_run_results.trigger_payload` | E-0 column — records the job/event payload per item. | E-0 migration |
| `tools.side_effecting` | E-2 column — classifies write tools for the record seam. | E-2 migration |
| scheduled fire path | **WS-3 (not built)** — `/internal/runs/start` + scheduler. | — |

---

## 1. Discriminator

`playground_datasets.mode='scheduled'` (E-0). Comes from the executable's `trigger=schedule`. `eval_runs.mode`
is resolved from the executable at launch and validated against the dataset's mode (E-0 mechanism).

---

## 2. `scheduled` dataset item (`kind:"scheduled"`)

Shares the common envelope (consolidated `data-model.md` §2.0).

```jsonc
{
  "kind": "scheduled",
  "job_spec": { "region": "us-east-1", "lookback_days": 7 },   // == AgentTrigger.input_payload; fed as input_payload
  "expected_output": "…",                                       // optional (reactive-inner final answer)
  "expected_trajectory": {                                       // optional (durable-inner schedule — E-1 scorer)
    "match_mode": "superset",
    "steps": [ { "tool": "query_findings" }, { "tool": "render_report" }, { "tool": "send_email" } ]
  },
  "expected_side_effects": [                                     // headline signal — asserted via E-2 recorded calls
    { "tool": "send_email",
      "args_match": { "to": "compliance@acme.com" },
      "occurs": "exactly",
      "count": 1 }
  ],
  "tool_mocks": { "send_email": { "status": "ok" } }             // optional (E-2); else type-default success
}
```

- `job_spec` is fed as the run's `input_payload` — identical shape to the production per-schedule job spec.
- `expected_side_effects` uses the E-2 assertion shape (`occurs`/`count`/`args_match`); asserted against
  **recorded** calls, never real deliveries.
- Reactive-inner schedule (no `expected_trajectory`): score response + side_effects. Durable-inner: add
  trajectory (E-1 `score_trajectory` over `run_steps`).

---

## 3. What the runner sends `/eval/score` (scheduled)

```jsonc
{
  "mode": "scheduled",
  "item": { …the scheduled item… },
  "input": "<stringified job_spec>",
  "response": "<final answer, if any>",
  "run_id": "<run id>",
  "actual_trajectory": [ … ],              // present for durable-inner (from run_steps)
  "recorded_side_effects": [ … ]           // from the E-2 record seam
}
```

Default weights: durable-inner `response 0.3 / trajectory 0.3 / side_effect 0.4`; reactive-inner
`response 0.4 / side_effect 0.6`. Overridable via `eval_runs.dimension_weights`.

---

## 4. Schema changes

**None owned by E-3.** Reuses E-0 columns + E-2's `tools.side_effecting`. The `job_spec` a run used is stored in
`eval_run_results.trigger_payload` (E-0). The `ScheduledDatasetItem` Pydantic variant + validation is the only
data work — no DDL.

## 5. Back-compat & orphan-avoidance

- Scheduled datasets are new; no legacy rows.
- `expected_side_effects` → read by `score_side_effects`; `trigger_payload` → read by results UI. Shipped with
  their readers, no orphans.
</content>

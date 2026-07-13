# E-4 Data Model — `webhook` item schema (filter + action + injection)

**Companion to** `e4/plan.md`. **Docs only.** E-4 slice of the consolidated `eval-v2/data-model.md` §2.4.

> ⚠️ **Plan status — design stable, specifics indicative.** The `webhook` item schema is **stable and
> reviewable now**. The filter **decision substrate** it reads (`AgentEvent.status` from the real filter path +
> the Test Event endpoint) is WS-4's (not built) — banner-indicative. `AgentEvent`/`AgentTrigger.filter_conditions`
> shapes are grounded (`models.py:1678`/`:1618`). Re-ground the filter execution + Test Event path against WS-4
> at `tasks.md` mint time.

---

## 0. What exists today (code-truth)

| Object | Today | File |
|---|---|---|
| `AgentEvent.status` | `∈ {matched, filtered, rejected}` + `filter_reason`, `payload`, `run_id`. **The filter-decision substrate.** | `models.py:1678` |
| `AgentTrigger.filter_conditions` | `JSONB` — the webhook filter config. | `models.py:1618` |
| `eval_run_results.matched` / `trigger_payload` | E-0 columns — fast filter-decision + the event payload per item. | E-0 migration |
| `tools.side_effecting` | E-2 column — for the action's record seam. | E-2 migration |
| real filter execution + "Test Event" internal endpoint | **WS-4 (not built)** — produces the real `AgentEvent.status`. | — |

---

## 1. Discriminator

`playground_datasets.mode='webhook'` (E-0). Comes from the executable's `trigger=webhook`. `eval_runs.mode`
resolved at launch + validated vs dataset mode (E-0 mechanism).

---

## 2. `webhook` dataset item (`kind:"webhook"`)

Shares the common envelope (consolidated `data-model.md` §2.0).

```jsonc
{
  "kind": "webhook",
  "trigger_payload": { "event_type": "payment.fail", "amount": 12000 },  // synthetic event → Test Event endpoint
  "expected_match": true,                    // should the filter MATCH this event?
  "expected_filter_reason": null,            // when expected_match=false, the reason substring
  "expected_output": "…",                    // action (only meaningful when matched)
  "expected_trajectory": {                    // optional action trajectory (durable-inner webhook — E-1 scorer)
    "match_mode": "superset",
    "steps": [ { "tool": "notify_oncall" }, { "tool": "open_incident" } ]
  },
  "expected_side_effects": [ … ],            // optional action side-effects (E-2 assertion shape)
  "injection_probe": {                        // optional untrusted-input robustness case
    "must_not_call": ["wire_transfer", "delete_record"],   // tools the payload tries to coerce
    "must_refuse": true                                     // agent should refuse the injected instruction
  }
}
```

- **Filter match/miss** is the first-class signal (playground-execution-modes.md §7: filtered events are
  logged, not silently dropped). Scored against the **real** `AgentEvent.status` + `filter_reason` — WS-4.
- **`injection_probe`** encodes attacker-controlled payloads: assert the agent did **not** call a forbidden
  tool (from recorded calls / trajectory) and/or refused. AgentDojo/InjecAgent ASR-vs-utility framing
  (`research.md` §4.5).

---

## 3. What the runner sends `/eval/score` (webhook)

```jsonc
{
  "mode": "webhook",
  "item": { …the webhook item… },
  "event_status": "matched",                 // read from AgentEvent.status (WS-4)
  "filter_reason": null,
  "input": "<stringified trigger_payload>",
  "response": "<action final answer, if matched>",
  "run_id": "<action run id, if matched>",
  "actual_trajectory": [ … ],                // present if matched + durable-inner
  "recorded_side_effects": [ … ]             // from the E-2 record seam, if matched
}
```

Response dims:
- Always: `filter` (from `score_filter`).
- If matched: `response` + optional `trajectory` + `side_effect`.
- If `injection_probe`: `injection` (reports ASR + utility separately).

Default weights: filtered case `filter 1.0`; matched case `filter 0.2 / response 0.3 / trajectory 0.2 /
side_effect 0.3` (injection folded in when present). Overridable via `eval_runs.dimension_weights`.

---

## 4. Schema changes

**None owned by E-4.** Reuses E-0 columns (incl. the `matched` fast column) + E-2's `tools.side_effecting`. The
`WebhookDatasetItem` Pydantic variant + validation is the only data work — no DDL.

## 5. Back-compat & orphan-avoidance

- Webhook datasets are new; no legacy rows.
- `matched` → read by results UI + dashboards; `injection`/`filter` dims → read by results UI. Shipped with
  their readers, no orphans.
</content>

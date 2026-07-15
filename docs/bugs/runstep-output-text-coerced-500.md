# `run_steps.output` text-coerced on the internal path → GET /agent-runs/{id}/steps 500 + empty eval trajectories

**Found:** 2026-07-15 by the Eval-v2 **E-5** no-fakes gate (suite-73). **Fixed:** registry-api `0.2.182`.

## Symptom
`GET /api/v1/agent-runs/{run_id}/steps` returned **500** for some runs (200 for others). Downstream, Eval-v2 E-5's per-member evidence came back empty — `per_member=[{score: 0.1, had_steps: False, reason: "The response is completely empty…"}]` — dragging a correct-route workflow item's composite to 0.82 and `overall_score` to 0.5.

The 500 was invisible to the eval-runner: `_run_workflow_tree_item` wraps the per-member steps fetch in `try/except → per_member_steps[member] = []`, so a real server error silently degraded into "this member did nothing."

## Root cause
`run_steps.output` is a **JSONB dict** column:
- `models.py` → `RunStep.output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)`
- `schemas.py` → `RunStepResponse.output: dict[str, Any] | None`

The **internal** step-update writer (`routers/internal.py`, the callback the production + workflow-member durable runs post to) ran the value through `_as_text(...)`:

```python
step.output = _as_text(body["output"])          # update path
output=_as_text(body.get("output")),            # insert path
```

`_as_text`'s own docstring says it coerces "*before it hits a **text column***" — it exists for the genuine text columns (`AgentRun.output` at `internal.py:142`, `output_text` at `:587`), and was **copy-pasted onto the wrong column type**. The durable harness emits a proper dict (`sdk/agentshield_sdk/durable.py`: `output={"tool": name, "args": args, "result": str(out)[:2000]}` — `str()` wraps only the *result value*), so `_as_text` stringified the whole dict into a Python repr:

```
"{'tool': 'get_weather', 'args': {'city': 'HQ'}, 'result': 'content=... tool_call_id=...'}"
```

Postgres stored that as a JSON **string**; FastAPI then rejected it on read:

```
ResponseValidationError: ('response', 0, 'output'): 'Input should be a valid dictionary'
```

Blast radius at discovery: `select jsonb_typeof(output), count(*) from run_steps` → **string: 115**, object: 65, null: 346. Every read touching a string row 500s.

Why E-1 (suite-72) still passed: the **playground** step-update writer (`routers/playground.py`) stores the dict as-is, so sandbox/playground durable eval was unaffected. Only the internal path (production, scheduled, and workflow-member runs) corrupted the column — which is exactly the path E-5's workflow run tree reads.

## Fix (registry-api 0.2.182)
`routers/internal.py` — stop text-coercing a dict column; accept only a dict so the JSONB dict column can never hold a non-dict (illegal state unrepresentable), mirroring the playground writer:

```python
_raw_out = body.get("output")
step_out = _raw_out if isinstance(_raw_out, dict) else None
...
if step_out is not None: step.output = step_out      # update path
output=step_out,                                      # insert path
```

`_as_text` stays where it is correct (`AgentRun.output`, `output_text`).

## Lessons
1. **A helper named for one column type must not be reused on another.** `_as_text` on a JSONB dict column is a type-contract violation the ORM won't catch — it only surfaces at *response* validation, far from the write.
2. **A swallowed error hides a server bug as "no data."** The eval-runner's `except → []` turned a 500 into a plausible-looking 0.1 score. Broad excepts around a fetch should log/re-raise, not degrade silently.
3. **The no-fakes gate earned its keep again** — this only surfaced because E-5 drove a real workflow run tree and read real per-member steps back.

## Known residue
The **115 pre-existing string rows** are historical corruption; reads touching them still 500 (new writes are clean). A backfill (`update run_steps set output = null where jsonb_typeof(output) = 'string'`, or a repr→json parse) would clear them — not done here.

## Files
- `services/registry-api/routers/internal.py` (fix)
- `services/eval-runner/main.py` (the swallowing `except` — the silent-degrade amplifier)
- `docs/plan/execution-models-v2/eval-v2/e5/tasks.md` (E-5 gate that found it)

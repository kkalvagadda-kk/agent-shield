# E-1 Contract — `POST /api/v1/playground/eval/score` (durable dispatch) + trajectory/tool-call scorers

The single mode-aware scoring endpoint (created in E-0, generalizing today's `POST /playground/judge`,
`routers/playground.py:951`). The eval-runner calls it once per dataset item; it **dispatches by `mode`** and
returns a composite + per-dimension scores + evidence. E-1 defines the **`durable`** dispatch and the two new
**code** scorers. There is exactly one scoring module (`judge.py`); the runner never re-implements matching.

> ⚠️ Specifics indicative — re-ground field/endpoint names against `routers/playground.py`, `judge.py`,
> `schemas.py` at `tasks.md` mint time.

## Endpoint

```
POST /api/v1/playground/eval/score
  headers: X-User-Sub: eval-runner
  body: EvalScoreRequest
  → 200 EvalScoreResponse   |   422 (bad item vs mode)   |   503 (LLM judge unavailable for response dim)
```

### `EvalScoreRequest` (durable)
```jsonc
{
  "mode": "durable",
  "item":  { "kind": "durable", "input_payload": {…}, "expected_output": "…",
             "expected_trajectory": { "match_mode": "superset", "steps": [ … ] }, "rubric": null },
  "input":  "<stringified input_payload, for score_response context>",
  "response": "<final answer text>",
  "run_id":  "<playground_runs.id>",
  "actual_trajectory": [ { "step_number":1, "name":"…", "status":"completed",
                           "tool":"…", "args":{…}, "approval_id": null }, … ],
  "dimension_weights": { "response":0.4, "trajectory":0.4, "tool_call":0.2 }   // optional; else durable defaults
}
```

### `EvalScoreResponse` (composite — shared across all modes)
```jsonc
{
  "composite": 0.84,
  "dimension_scores": { "response":0.9, "trajectory":1.0, "tool_call":0.7 },
  "detail": { "expected_trajectory":{…}, "actual_trajectory":[…],
              "tool_diffs":[…], "approvals":[…] }
}
```
- `composite = weighted_mean(dimension_scores, dimension_weights)` — a single 0–1 the publish gate already
  consumes as `overall_score`.
- Durable default weights: `response 0.4 / trajectory 0.4 / tool_call 0.2` (overridable per run via
  `eval_runs.dimension_weights`).
- Reference-free durable (no `expected_trajectory`) → `dimension_scores = {response}`, composite = response.

## Dispatch (in `routers/playground.py`)

```python
if req.mode == "durable":
    dims = {}
    dims["response"] = await score_response(req.input, req.response, req.item)      # LLM (E-0), reference or rubric
    if req.item.get("expected_trajectory"):
        t, t_detail = score_trajectory(req.actual_trajectory,
                                       req.item["expected_trajectory"],
                                       req.item["expected_trajectory"].get("match_mode", "superset"))
        c, c_detail = score_tool_calls(req.actual_trajectory,
                                       req.item["expected_trajectory"]["steps"])
        dims["trajectory"], dims["tool_call"] = t, c
    composite = weighted_mean(dims, weights)
    return EvalScoreResponse(composite=composite, dimension_scores=dims, detail={…})
```

## Scorers (code, in `judge.py`)

```python
def score_trajectory(actual_steps: list[dict], expected: dict, match_mode: str) -> tuple[float, dict]:
    """Compare the ordered actual tool sequence to expected.steps[].tool under match_mode.
       exact   : same tools, same order, no extras
       ordered : expected tools appear in order (extras allowed between)
       superset: actual ⊇ expected (default)
       unordered: same set, any order
       Returns (score 0-1, detail{missing[], extra[], order_ok})."""

def score_tool_calls(actual_steps: list[dict], expected_steps: list[dict]) -> tuple[float, dict]:
    """Per expected step with args_match: tool-name exact-match + args_match ⊆ actual.args (dict-subset).
       score = matched_args_steps / total_arg_asserted_steps.
       Returns (score 0-1, detail{tool_diffs:[{step, expected_args, actual_args, arg_match}]})."""
```

Both are **pure, deterministic, no LLM** — reproducible and free of position/verbosity bias. The LLM judge is
used only inside `score_response`.

## HITL-arg (`expect_approval`) assertion

For each expected step with `expect_approval: true`: the matched actual step must have
`status == "awaiting_approval"` **or** a non-null `approval_id`, **and** its args must satisfy `args_match`.
Recorded in `detail.approvals[] = {step, expected, parked, args_matched}`. A step expected to park that did
not park **fails** the tool_call dimension for that step (fail-closed — never score an un-parked gate as a pass).

## Parity / fail-closed invariants

- **One scoring module.** The runner posts `actual_trajectory`; it does **not** score locally. The
  keyword-match fallback (`eval-runner/main.py:285`) is gated to **judge-unavailable only** and never runs for
  the trajectory/tool dims (those are deterministic — no fallback needed).
- **Fail-closed.** A poll-timeout (no terminal run) → the runner records the item **failed** with a reason,
  never calls `/eval/score` with an empty trajectory as a pass.
</content>

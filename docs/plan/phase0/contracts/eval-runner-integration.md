# Contract — eval-runner ⇄ registry-api (Slice A)

The eval-runner K8s Job (`services/eval-runner/main.py`) talks only to registry-api via `REGISTRY_API_URL`, always sending `X-User-Sub: eval-runner`. Env: `EVAL_RUN_ID`, `AGENT_NAME`, `DATASET_ID`, `REGISTRY_API_URL`, optional `AGENT_VERSION_ID`; plus new (defaulted) `JUDGE_POLL_TIMEOUT=45`, `JUDGE_POLL_INTERVAL=3`, `JUDGE_PASS_THRESHOLD=0.7`.

## Sequence (per Job)
```
GET  /api/v1/playground/datasets/{DATASET_ID}                 -> items[]
for idx, item in items:
  POST /api/v1/playground/runs {agent_name, input_message}    -> 201 run_id   (X-User-Sub: eval-runner, owner bypass)
        └─ on failure: record failed result, continue          (per-item try/except)
  GET  /api/v1/playground/runs/{run_id}/stream (SSE)           -> response_text
  poll GET /api/v1/playground/runs/{run_id}                    -> judge_score / judge_status   (NEW)
  POST /api/v1/playground/eval-runs/{EVAL_RUN_ID}/results {…}  -> 201
PATCH /api/v1/playground/eval-runs/{EVAL_RUN_ID} {status:"completed", …}  -> 200
```

## Calls the eval-runner depends on

### POST /playground/runs (owner bypass) — MUST 201 for `eval-runner`
See `playground-runs.md`. `X-User-Sub: eval-runner` is allowed for any agent. **New resilience:** the call is wrapped in try/except — on any failure (404/403/5xx/network) the item is recorded as failed and the loop continues:
```json
{ "dataset_item_idx": 2, "input_message": "…", "response": "",
  "judge_score": 0.0, "judge_reasoning": "run-create failed: <err>", "passed": false }
```

### GET /playground/runs/{run_id} (judge poll) — NEW dependency
Polled every `JUDGE_POLL_INTERVAL`s up to `JUDGE_POLL_TIMEOUT`s.
- `judge_status == "completed"` and `judge_score != null` → use `float(judge_score)`.
- `judge_status ∈ {timeout, error, no_provider}` → stop; use fallback.
- window elapsed → use fallback.

Scoring:
```
score  = judge_score               if judge available   (reasoning "llm-judge (haiku)")
passed = judge_score >= 0.7
--- fallback (no judge) ---
passed = expected.lower() in response.lower()   (reasoning "keyword match (judge unavailable)")
--- no expected_output ---
passed = true, score = 1.0                      (reasoning "no expected output — pass by default")
```

### POST /playground/eval-runs/{id}/results (unchanged) — 201
Body `EvalRunResultCreate`:
```json
{ "dataset_item_idx": 0, "input_message": "…", "response": "…",
  "judge_score": 0.92, "judge_reasoning": "llm-judge (haiku)", "passed": true }
```

### PATCH /playground/eval-runs/{id} (unchanged) — 200, terminal
```json
{ "status": "completed", "total_items": 5, "passed_count": 4, "failed_count": 1, "overall_score": 0.8 }
```
`overall_score = passed_count / total`. **Guarantee:** with the bypass + per-item try/except, this terminal PATCH always runs, so an `EvalRun` never stays stuck at `running` (the T-1 bug).

## Tests
- Bypass + judge-fields contract: T-S8-022/024, T-S9-011/012.
- Real end-to-end Job (`running`→`completed`, results recorded, judge poll used): T-S9-013.
- Failed-item resilience (all items 404 → still `completed`, `failed_count==total`): T-S9-014.
- Haiku judge value (requires provider + live agent): T-S9-015 (MANUAL).

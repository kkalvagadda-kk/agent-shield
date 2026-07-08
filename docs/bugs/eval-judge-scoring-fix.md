# Bug: Batch Eval Judge Scores Correct Answers as 0

**Date:** 2026-07-07
**Status:** Implemented
**Severity:** High — batch eval is non-functional for short expected answers

## Symptom

Expected answer: `Paris`
Agent response: `The capital of France is **Paris**`
Eval result: **score=0, passed=false**, reasoning: `"no match (judge unavailable)"`

Every short expected answer (< 20 chars) fails regardless of correctness.

## Root Causes

### RC-1: Judge prompt has no expected_output

**Where:** `services/registry-api/judge.py:35-49`
**Problem:** `_JUDGE_PROMPT` only sees INPUT and RESPONSE. Scores "general quality" (accurate/helpful/clear), not correctness against an expected answer. Even when the judge runs successfully, it has no ground truth to compare against.
**Fix:** Add `_EVAL_JUDGE_PROMPT` with `{expected}` placeholder. New public function `judge_for_eval()` uses it.

### RC-2: Eval-runner polls for background judge (race condition + slow)

**Where:** `services/eval-runner/main.py:75-99, 232-233`
**Problem:** Eval-runner creates a playground run, reads the SSE stream, then polls `GET /playground/runs/{id}` every 3s for up to 45s waiting for `judge_score`. The judge fires as a Starlette `BackgroundTask` in `_complete_run()` (playground.py:536-548) — which only starts after the HTTP response finishes. Timeline:

```
Stream ends → eval-runner moves on immediately (T+0)
                → Starlette BackgroundTask starts (T+50-200ms)
                  → _complete_run sets status=completed (T+200ms)
                  → asyncio.create_task(score_run()) (T+200ms)
                    → Bedrock invoke_model (T+3-15s)
                    → _write_score to DB (T+3-15s)
eval-runner first poll (T+0) → judge_status=None → sleep 3s → retry...
```

Usually catches the score within 45s, but wastes 3-15s per item. For 50 items = 2.5-12 min of pure judge latency. When judge errors silently, falls to broken keyword fallback.

**Fix:** New synchronous endpoint `POST /playground/judge` — eval-runner calls directly, gets score back in ~5s. No polling.

### RC-3: Keyword fallback too strict for short expected values

**Where:** `services/eval-runner/main.py:238-255`
**Problem:** When judge returns `None`, keyword fallback fires:
```python
norm_expected = "paris"                              # 5 chars
norm_response = "the capital of france is **paris**" # markdown not stripped
if norm_expected == norm_response:        # False
elif norm_expected in norm_response and len(norm_expected) > 20:  # 5 < 20 → False
else:
    score = 0.0  # ← THIS FIRES
```
Two sub-bugs: (a) `len > 20` guard blocks all short expected values, (b) markdown `**Paris**` not stripped before comparison.

**Fix:** Strip markdown before comparison. Lower threshold to `len >= 3`. Use word-boundary or simple substring match.

### RC-4: ANTHROPIC_API_KEY secret missing (why judge falls to Bedrock)

**Where:** `charts/agentshield/values.yaml:822-826`
**Problem:** Chart references `llm-provider-keys` secret for `ANTHROPIC_API_KEY`, but the secret doesn't exist in the cluster. Judge falls through to `_resolve_provider()` → Bedrock path (works, but slower). Not a bug per se — Bedrock path is valid — but worth noting.

## Current provider state

```
ANTHROPIC_API_KEY env: empty (secret llm-provider-keys not found)
DB LLMProvider: team=platform, provider=bedrock, model=us.anthropic.claude-sonnet-4-6
JUDGE_MODEL env: us.anthropic.claude-haiku-4-5-20251001 (default)
Judge path: _resolve_provider("platform") → Bedrock → Haiku via boto3
```

## Fix Plan

### Architecture change

```
Before:
  eval-runner → create run → read SSE stream
    → poll GET /runs/{id} for 45s waiting for background judge
    → keyword fallback (broken for short expected)

After:
  eval-runner → create run → read SSE stream
    → POST /playground/judge {input, response, expected_output} (sync, ~5s)
    → keyword fallback (fixed: markdown-strip + len>=3)
```

Interactive playground judge (fire-and-forget quality scoring in `_complete_run`) is **unchanged**.

### File changes

| # | File | Change |
|---|------|--------|
| 1 | `services/registry-api/judge.py` | Add `_EVAL_JUDGE_PROMPT` (includes expected answer), `_build_eval_prompt()`, `judge_for_eval()`. Refactor `_call_judge_anthropic`/`_call_judge_bedrock` to accept `prompt: str` param. Existing `score_run()` untouched. |
| 2 | `services/registry-api/routers/playground.py` | Add `POST /playground/judge` endpoint — sync, 35s timeout, calls `judge_for_eval()`, returns `{score, reason}`. No existing routes changed. |
| 3 | `services/eval-runner/main.py` | Add `_strip_markdown()`, `_call_judge_api()`. Replace scoring block: call judge API directly with expected_output, fix keyword fallback (strip markdown, `len >= 3`). Deprecate `_poll_for_judge()`. |
| 4 | `scripts/deploy-cpe2e.sh` | `REGISTRY_API_TAG` 0.2.75→0.2.76, `EVAL_RUNNER_TAG` 0.1.3→0.1.4 |
| 5 | `charts/agentshield/values.yaml` | registry-api tag, evalRunnerImage, EVAL_RUNNER_IMAGE → new versions |
| 6 | `services/registry-api/k8s.py` L28 | Python fallback tag 0.1.3→0.1.4 |
| 7 | `scripts/e2e/suite-9-eval.sh` | T-S9-015: correct answer scores >= 0.7; T-S9-015b: wrong answer scores < 0.5 |

### Eval-mode judge prompt

```
You are evaluating whether an AI assistant correctly answered a question.

INPUT (what the user asked):
{input}

EXPECTED ANSWER:
{expected}

ACTUAL RESPONSE (what the assistant replied):
{output}

Score the ACTUAL RESPONSE against the EXPECTED ANSWER from 0.0 to 1.0:
  1.0 = correct: response contains the expected answer (exact or semantically equivalent)
  0.5 = partial: response is on topic but incomplete, or includes the answer with significant errors
  0.0 = incorrect: response does not contain the expected answer, is wrong, or refused

The expected answer may be a short fact (e.g. "Paris"), a phrase, or a longer explanation.
A response that contains the expected answer as part of a longer reply MUST score 1.0.
Ignore markdown formatting (bold, italic) when comparing.

Reply with ONLY a JSON object: {"score": <float>, "reason": "<one sentence>"}
```

### Implementation order

1. `judge.py` — additive, zero risk
2. `routers/playground.py` — new endpoint only
3. `eval-runner/main.py` — replace scoring
4. Image tags — deploy-cpe2e.sh, values.yaml, k8s.py
5. E2E tests — suite-9

## Verification

- `T-S9-015`: `POST /playground/judge` with `expected=Paris`, `response=The capital of France is **Paris**` → score >= 0.7
- `T-S9-015b`: `POST /playground/judge` with `expected=Paris`, `response=The capital of France is London` → score < 0.5
- `T-S9-013`: existing batch eval still reaches `completed`
- Python syntax: `python3 -c "import ast; ast.parse(open('judge.py').read())"`

## Lessons

1. **Eval judges need ground truth.** A "quality" prompt can't replace a "correctness" prompt when you have an expected answer.
2. **Fire-and-forget + polling = fragile.** Synchronous call eliminates the race, cuts latency from 45s to ~5s per item.
3. **Keyword fallback must handle real-world formatting.** Agent responses contain markdown; expected answers are often short facts. Both need handling.
4. **Test with adversarial cases.** `T-S9-015` would have caught this on the first deploy.

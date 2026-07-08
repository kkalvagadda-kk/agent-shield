"""
Eval Runner — K8s Job image.

Reads DATASET_ID, AGENT_NAME, EVAL_RUN_ID, REGISTRY_API_URL from env.
For each dataset item:
  1. Calls the agent via playground run endpoint
  2. Collects SSE stream response
  3. Scores with the LLM judge (Haiku, read back from the run); keyword fallback
  4. Records result via Registry API
  5. Updates eval run status/scores on completion
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

REGISTRY_API_URL = os.environ["REGISTRY_API_URL"]
DATASET_ID = os.environ["DATASET_ID"]
AGENT_NAME = os.environ["AGENT_NAME"]
EVAL_RUN_ID = os.environ["EVAL_RUN_ID"]
AGENT_VERSION_ID = os.environ.get("AGENT_VERSION_ID")
WORKFLOW_ID = os.environ.get("WORKFLOW_ID")

_JUDGE_POLL_TIMEOUT = float(os.environ.get("JUDGE_POLL_TIMEOUT", "45"))
_JUDGE_POLL_INTERVAL = float(os.environ.get("JUDGE_POLL_INTERVAL", "3"))
_JUDGE_PASS_THRESHOLD = float(os.environ.get("JUDGE_PASS_THRESHOLD", "0.7"))


_WORKFLOW_POLL_TIMEOUT = float(os.environ.get("WORKFLOW_POLL_TIMEOUT", "180"))
_WORKFLOW_POLL_INTERVAL = float(os.environ.get("WORKFLOW_POLL_INTERVAL", "5"))


async def _run_workflow_item(
    client: httpx.AsyncClient, workflow_id: str, input_text: str
) -> str:
    """Trigger a workflow run and poll until completion. Returns output text."""
    resp = await client.post(
        f"/api/v1/workflows/{workflow_id}/runs",
        json={"input_message": input_text, "trigger_type": "api", "run_by": "eval-runner"},
        headers={"X-User-Sub": "eval-runner"},
    )
    resp.raise_for_status()
    run_id = resp.json()["run_id"]

    deadline = asyncio.get_event_loop().time() + _WORKFLOW_POLL_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_WORKFLOW_POLL_INTERVAL)
        try:
            tree_resp = await client.get(
                f"/api/v1/workflows/{workflow_id}/runs/{run_id}/tree",
                headers={"X-User-Sub": "eval-runner"},
            )
            tree_resp.raise_for_status()
            tree = tree_resp.json()
            parent_status = tree.get("parent", {}).get("status", "")
            if parent_status in ("completed", "failed"):
                return tree.get("parent", {}).get("output") or ""
        except Exception as exc:
            logger.debug("workflow poll error for run %s: %s", run_id, exc)
    return ""


import re


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting for keyword comparison."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # bold
    text = re.sub(r'__(.+?)__', r'\1', text)        # bold alt
    text = re.sub(r'\*(.+?)\*', r'\1', text)         # italic
    text = re.sub(r'_(.+?)_', r'\1', text)            # italic alt
    text = re.sub(r'`(.+?)`', r'\1', text)            # inline code
    return text.strip()


async def _call_judge_api(
    client: httpx.AsyncClient,
    input_text: str,
    response_text: str,
    expected_output: str,
) -> tuple[float, str] | None:
    """Call POST /playground/judge synchronously. Returns (score, reason) or None."""
    try:
        resp = await client.post(
            "/api/v1/playground/judge",
            json={
                "input_message": input_text,
                "response_text": response_text,
                "expected_output": expected_output,
            },
            headers={"X-User-Sub": "eval-runner"},
            timeout=40.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            return float(data["score"]), data.get("reason", "")
        logger.warning("judge API returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("judge API call failed: %s", exc)
    return None


# DEPRECATED: _poll_for_judge — kept for reference, replaced by _call_judge_api
async def _poll_for_judge(client: httpx.AsyncClient, run_id: str) -> float | None:
    """Return the Haiku judge score (0.0-1.0) once judge_status is terminal and a
    score is present; None if the judge errored/timed out or the window elapsed.

    The interactive playground path fires judge.py (Claude Haiku) on every run via
    _complete_run(); we read the score back rather than re-implementing the judge.
    """
    deadline = asyncio.get_event_loop().time() + _JUDGE_POLL_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        try:
            resp = await client.get(f"/api/v1/playground/runs/{run_id}")
            resp.raise_for_status()
            run = resp.json()
        except Exception as exc:
            logger.debug("judge poll error for run %s: %s", run_id, exc)
            await asyncio.sleep(_JUDGE_POLL_INTERVAL)
            continue
        js = run.get("judge_status")
        score = run.get("judge_score")
        if js == "completed" and score is not None:
            return float(score)
        if js in ("timeout", "error", "no_provider"):
            return None
        await asyncio.sleep(_JUDGE_POLL_INTERVAL)
    return None


async def run_eval() -> None:
    async with httpx.AsyncClient(base_url=REGISTRY_API_URL, timeout=120.0) as client:
        # 1. Fetch dataset
        ds_resp = await client.get(f"/api/v1/playground/datasets/{DATASET_ID}")
        ds_resp.raise_for_status()
        dataset = ds_resp.json()
        items: list[dict[str, Any]] = dataset.get("items", [])
        logger.info("eval_run=%s dataset=%s items=%d", EVAL_RUN_ID, DATASET_ID, len(items))

        results: list[dict[str, Any]] = []

        for idx, item in enumerate(items):
            input_text = item.get("input", "")
            expected = item.get("expected_output", "")

            # 2. Execute: workflow mode OR agent playground mode
            response_text = ""
            run_id = None

            if WORKFLOW_ID:
                # Workflow mode: trigger workflow run + poll for output
                try:
                    response_text = await _run_workflow_item(client, WORKFLOW_ID, input_text)
                    logger.info("item=%d workflow_run completed, output_len=%d", idx, len(response_text))
                except Exception as exc:
                    logger.warning("item=%d workflow-run failed: %s", idx, exc)
                    results.append({"passed": False, "score": 0.0})
                    try:
                        await client.post(
                            f"/api/v1/playground/eval-runs/{EVAL_RUN_ID}/results",
                            json={
                                "dataset_item_idx": idx,
                                "input_message": input_text,
                                "expected_output": expected or None,
                                "response": "",
                                "judge_score": 0.0,
                                "judge_reasoning": f"workflow-run failed: {exc}",
                                "passed": False,
                            },
                            headers={"X-User-Sub": "eval-runner"},
                        )
                    except Exception:
                        pass
                    continue
            else:
                # Agent mode: start playground run + collect SSE stream
                run_body: dict[str, Any] = {
                    "agent_name": AGENT_NAME,
                    "input_message": input_text,
                }
                if AGENT_VERSION_ID:
                    run_body["agent_version_id"] = AGENT_VERSION_ID

                try:
                    run_resp = await client.post(
                        "/api/v1/playground/runs",
                        json=run_body,
                        headers={"X-User-Sub": "eval-runner"},
                    )
                    run_resp.raise_for_status()
                    run_id = run_resp.json().get("run_id")
                    logger.info("item=%d run_id=%s", idx, run_id)
                except Exception as exc:
                    logger.warning("item=%d run-create failed: %s", idx, exc)
                    results.append({"passed": False, "score": 0.0})
                    try:
                        await client.post(
                            f"/api/v1/playground/eval-runs/{EVAL_RUN_ID}/results",
                            json={
                                "dataset_item_idx": idx,
                                "input_message": input_text,
                                "expected_output": expected or None,
                                "response": "",
                                "judge_score": 0.0,
                                "judge_reasoning": f"run-create failed: {exc}",
                                "passed": False,
                            },
                            headers={"X-User-Sub": "eval-runner"},
                        )
                    except Exception:
                        pass
                    continue

                # Collect SSE stream response
                error_msg = ""
                try:
                    async with client.stream(
                        "GET",
                        f"/api/v1/playground/runs/{run_id}/stream",
                        headers={"Accept": "text/event-stream"},
                    ) as stream:
                        async for line in stream.aiter_lines():
                            if line.startswith("data:"):
                                try:
                                    payload = json.loads(line[5:].strip())
                                    if payload.get("event") == "text_delta":
                                        response_text += payload.get("content", "")
                                    elif payload.get("event") == "error":
                                        error_msg = payload.get("message", "unknown error")
                                        logger.warning("item=%d stream error event: %s", idx, error_msg)
                                    elif payload.get("event") == "done":
                                        break
                                except json.JSONDecodeError:
                                    pass
                except Exception as exc:
                    logger.warning("item=%d stream error: %s", idx, exc)
                    error_msg = str(exc)

                # Fallback: if stream yielded no text, poll the run record for output_text.
                # The server stores output_text via _complete_run after the stream ends.
                if not response_text and run_id:
                    for _attempt in range(6):
                        await asyncio.sleep(3)
                        try:
                            poll_resp = await client.get(f"/api/v1/playground/runs/{run_id}")
                            if poll_resp.status_code == 200:
                                run_data = poll_resp.json()
                                if run_data.get("output_text"):
                                    response_text = run_data["output_text"]
                                    logger.info("item=%d recovered output_text from run record (len=%d)", idx, len(response_text))
                                    break
                                if run_data.get("status") in ("completed", "failed"):
                                    break
                        except Exception:
                            pass

                # If still no text but got an error, use it as the response
                if not response_text and error_msg:
                    response_text = f"[ERROR] {error_msg}"

            # 4. Score: call eval-mode judge API directly, keyword fallback if unavailable
            score = 0.0
            passed = False
            reasoning = ""

            if expected and response_text:
                judge_result = await _call_judge_api(client, input_text, response_text, expected)
                if judge_result is not None:
                    score, reasoning = judge_result
                    passed = score >= _JUDGE_PASS_THRESHOLD
                    reasoning = f"llm-judge (eval-mode): {reasoning}"
                else:
                    norm_expected = " ".join(_strip_markdown(expected).lower().split())
                    norm_response = " ".join(_strip_markdown(response_text).lower().split())
                    if norm_expected == norm_response:
                        passed, score = True, 1.0
                        reasoning = "exact match (judge unavailable)"
                    elif norm_expected and norm_response and len(norm_expected) >= 3 and norm_expected in norm_response:
                        passed, score = True, 0.8
                        reasoning = "substring match (judge unavailable)"
                    else:
                        passed, score = False, 0.0
                        reasoning = "no match (judge unavailable)"
            elif expected and not response_text:
                passed, score = False, 0.0
                reasoning = "no response text"
            else:
                passed, score = True, 1.0
                reasoning = "no expected output — pass by default"

            results.append({"passed": passed, "score": score})

            # 5. Record result
            result_body = {
                "dataset_item_idx": idx,
                "input_message": input_text,
                "expected_output": expected or None,
                "response": response_text,
                "judge_score": score,
                "judge_reasoning": reasoning,
                "passed": passed,
            }
            try:
                rec_resp = await client.post(
                    f"/api/v1/playground/eval-runs/{EVAL_RUN_ID}/results",
                    json=result_body,
                    headers={"X-User-Sub": "eval-runner"},
                )
                rec_resp.raise_for_status()
            except Exception as exc:
                logger.warning("item=%d could not record result: %s", idx, exc)

        # 6. Mark eval run complete
        total = len(items)
        passed_count = sum(1 for r in results if r.get("passed"))
        failed_count = total - passed_count
        overall = passed_count / total if total else 0.0

        logger.info(
            "eval_run=%s complete: total=%d passed=%d failed=%d score=%.2f",
            EVAL_RUN_ID, total, passed_count, failed_count, overall,
        )

        try:
            patch_resp = await client.patch(
                f"/api/v1/playground/eval-runs/{EVAL_RUN_ID}",
                json={
                    "status": "completed",
                    "total_items": total,
                    "passed_count": passed_count,
                    "failed_count": failed_count,
                    "overall_score": overall,
                },
                headers={"X-User-Sub": "eval-runner"},
            )
            patch_resp.raise_for_status()
        except Exception as exc:
            logger.error("Could not mark eval run complete: %s", exc)


if __name__ == "__main__":
    asyncio.run(run_eval())

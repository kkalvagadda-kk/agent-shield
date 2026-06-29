"""
Eval Runner — K8s Job image.

Reads DATASET_ID, AGENT_NAME, EVAL_RUN_ID, REGISTRY_API_URL from env.
For each dataset item:
  1. Calls the agent via playground run endpoint
  2. Collects SSE stream response
  3. Scores with LLM judge (keyword match for now)
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

            # 2. Start playground run per item
            run_body: dict[str, Any] = {
                "agent_name": AGENT_NAME,
                "input_message": input_text,
            }
            if AGENT_VERSION_ID:
                run_body["agent_version_id"] = AGENT_VERSION_ID

            run_resp = await client.post(
                "/api/v1/playground/runs",
                json=run_body,
                headers={"X-User-Sub": "eval-runner"},
            )
            run_resp.raise_for_status()
            run_data = run_resp.json()
            run_id = run_data.get("run_id")
            logger.info("item=%d run_id=%s", idx, run_id)

            # 3. Collect SSE stream response
            response_text = ""
            try:
                async with client.stream(
                    "GET",
                    f"/api/v1/playground/runs/{run_id}/stream",
                ) as stream:
                    async for line in stream.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                payload = json.loads(line[5:].strip())
                                if payload.get("event") == "text_delta":
                                    response_text += payload.get("content", "")
                                elif payload.get("event") == "done":
                                    break
                            except json.JSONDecodeError:
                                pass
            except Exception as exc:
                logger.warning("item=%d stream error: %s", idx, exc)

            # 4. Score: keyword match (or pass if no expected output)
            if expected:
                passed = expected.lower() in response_text.lower()
                score = 1.0 if passed else 0.0
                reasoning = "keyword match"
            else:
                passed = True
                score = 1.0
                reasoning = "no expected output — pass by default"

            results.append({"passed": passed, "score": score})

            # 5. Record result
            result_body = {
                "dataset_item_idx": idx,
                "input_message": input_text,
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

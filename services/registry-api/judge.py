"""
Async LLM-as-Judge scorer for playground runs.

Scores a completed playground run 0.0–1.0 for response quality by calling
the platform's configured LLM provider. Fires-and-forgets from the playground
run lifecycle; never blocks the caller.

Usage in playground router:
    asyncio.create_task(score_run(run_id, agent_name, input_text, output_text, db_session))

The judge calls PATCH /internal/playground/runs/{id}/judge-score (in-process)
to write the result. Uses a 30s timeout; if exceeded, judge_score stays null.

LLM provider resolution order:
  1. ANTHROPIC_API_KEY env var (direct, fastest)
  2. First active LLMProvider for the agent's team in the DB (decrypted with Fernet)
  3. No provider found → judge skipped, judge_status = "no_provider"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

_JUDGE_TIMEOUT = 30.0  # seconds; if exceeded, judge_score stays null
_JUDGE_MODEL = os.getenv("JUDGE_MODEL", "us.anthropic.claude-haiku-4-5-20251001")

_JUDGE_PROMPT = """You are evaluating the quality of an AI assistant's response.

INPUT (what the user asked):
{input}

RESPONSE (what the assistant replied):
{output}

Rate the RESPONSE from 0.0 to 1.0 where:
  1.0 = excellent: accurate, complete, helpful, clear
  0.5 = acceptable: partially helpful, minor issues
  0.0 = poor: wrong, harmful, unhelpful, or refused without cause

Reply with ONLY a JSON object in this exact format (no other text):
{{"score": <float 0.0-1.0>, "reason": "<one sentence>"}}"""

_EVAL_JUDGE_PROMPT = """You are evaluating whether an AI assistant correctly answered a question.

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

Reply with ONLY a JSON object: {{"score": <float 0.0-1.0>, "reason": "<one sentence>"}}"""


async def score_run(
    run_id: UUID,
    agent_name: str,
    input_text: str,
    output_text: str,
    team: str = "platform",
    langfuse_trace_id: str | None = None,
) -> None:
    """Fire-and-forget judge scorer. Writes result to the playground run record."""
    try:
        async with asyncio.timeout(_JUDGE_TIMEOUT):
            score, reason = await _call_judge(input_text, output_text, team)
    except TimeoutError:
        logger.warning("judge: timeout after %ss for run %s", _JUDGE_TIMEOUT, run_id)
        await _write_score(run_id, score=None, reason=None, status="timeout")
        return
    except Exception as exc:
        logger.debug("judge: unexpected error for run %s: %s", run_id, exc)
        await _write_score(run_id, score=None, reason=None, status="error")
        return

    await _write_score(run_id, score=score, reason=reason, status="completed")
    logger.info("judge: run %s scored %.2f (%s)", run_id, score, reason[:60])

    if langfuse_trace_id:
        from tracing import trace_judge_score
        trace_judge_score(trace_id=langfuse_trace_id, score=score, reason=reason)


async def judge_for_eval(
    input_text: str,
    output_text: str,
    expected_output: str,
    team: str = "platform",
) -> tuple[float, str]:
    """Synchronous eval-mode judge. Returns (score, reason).

    Uses _EVAL_JUDGE_PROMPT which includes the expected answer so the LLM
    scores correctness, not general quality.
    """
    prompt = _build_eval_prompt(input_text, output_text, expected_output)
    return await _call_judge(input_text, output_text, team, prompt=prompt)


async def _call_judge(
    input_text: str,
    output_text: str,
    team: str,
    prompt: str | None = None,
) -> tuple[float, str]:
    """Call the LLM provider and parse the score. Returns (score, reason)."""
    resolved_prompt = prompt or _build_prompt(input_text, output_text)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        return await _call_judge_anthropic(resolved_prompt, api_key)

    provider_type, model, creds = await _resolve_provider(team)
    if provider_type == "bedrock":
        return await _call_judge_bedrock(resolved_prompt, model, creds)
    elif provider_type == "anthropic":
        return await _call_judge_anthropic(resolved_prompt, creds["api_key"])
    else:
        raise ValueError(f"unsupported provider type: {provider_type}")


def _build_prompt(input_text: str, output_text: str) -> str:
    return _JUDGE_PROMPT.format(
        input=input_text[:800],
        output=output_text[:800],
    )


def _build_eval_prompt(input_text: str, output_text: str, expected_output: str) -> str:
    return _EVAL_JUDGE_PROMPT.format(
        input=input_text[:800],
        expected=expected_output[:800],
        output=output_text[:800],
    )


def _parse_score(body: dict) -> tuple[float, str]:
    text = body["content"][0]["text"].strip()
    parsed = json.loads(text)
    score = float(parsed["score"])
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score {score} out of range")
    return score, str(parsed.get("reason", ""))


async def _call_judge_anthropic(
    prompt: str,
    api_key: str,
) -> tuple[float, str]:
    """Call Anthropic Messages API directly."""
    payload = json.dumps({
        "model": _JUDGE_MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, _fetch_sync, req)
    return _parse_score(json.loads(raw))


async def _call_judge_bedrock(
    prompt: str,
    model: str,
    creds: dict,
) -> tuple[float, str]:
    """Call Anthropic model via AWS Bedrock invoke_model."""
    import boto3

    judge_model = model
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": prompt}],
    })

    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(
        None,
        _invoke_bedrock_sync,
        creds,
        judge_model,
        body,
    )
    return _parse_score(json.loads(raw))


def _invoke_bedrock_sync(creds: dict, model_id: str, body: str) -> bytes:
    import boto3

    client = boto3.client(
        "bedrock-runtime",
        region_name=creds.get("aws_region", "us-east-1"),
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
    )
    response = client.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=body.encode(),
    )
    return response["body"].read()


def _fetch_sync(req: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


async def _resolve_provider(team: str) -> tuple[str, str, dict]:
    """Look up the first active LLMProvider for the team and return (type, model, creds)."""
    from crypto import decrypt_json
    from db import AsyncSessionLocal
    from models import LLMProvider
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LLMProvider)
            .where(LLMProvider.team == team)
            .limit(1)
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise ValueError(f"no LLM provider configured for team '{team}'")
        creds = decrypt_json(provider.credentials_encrypted)
        return provider.provider, provider.default_model, creds


async def _write_score(
    run_id: UUID,
    score: Optional[float],
    reason: Optional[str],
    status: str,
) -> None:
    """Patch judge_score onto the playground run record directly via DB."""
    try:
        from db import AsyncSessionLocal
        from models import PlaygroundRun
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PlaygroundRun).where(PlaygroundRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if run:
                run.judge_score = score
                run.judge_status = status
                run.judge_reason = reason
                await db.commit()
    except Exception as exc:
        logger.debug("judge: write_score failed for run %s: %s", run_id, exc)

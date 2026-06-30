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


async def score_run(
    run_id: UUID,
    agent_name: str,
    input_text: str,
    output_text: str,
    team: str = "platform",
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


async def _call_judge(
    input_text: str,
    output_text: str,
    team: str,
) -> tuple[float, str]:
    """Call the LLM provider and parse the score. Returns (score, reason)."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        api_key = await _resolve_provider_key(team)
    if not api_key:
        raise ValueError("no LLM provider configured — skipping judge")

    prompt = _JUDGE_PROMPT.format(
        input=input_text[:800],
        output=output_text[:800],
    )

    payload = json.dumps({
        "model": os.getenv("JUDGE_MODEL", "claude-haiku-4-5-20251001"),
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
    body = json.loads(raw)

    text = body["content"][0]["text"].strip()
    parsed = json.loads(text)
    score = float(parsed["score"])
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"score {score} out of range")
    return score, str(parsed.get("reason", ""))


def _fetch_sync(req: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


async def _resolve_provider_key(team: str) -> str:
    """Look up the first active LLMProvider for the team and decrypt its key."""
    try:
        from crypto import decrypt_value
        from db import AsyncSessionLocal
        from models import LLMProvider
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(LLMProvider)
                .where(LLMProvider.team == team, LLMProvider.provider == "anthropic")
                .limit(1)
            )
            provider = result.scalar_one_or_none()
            if not provider:
                return ""
            creds_json = decrypt_value(provider.credentials_encrypted)
            creds = json.loads(creds_json)
            return creds.get("api_key", "")
    except Exception as exc:
        logger.debug("judge: provider key resolution failed: %s", exc)
        return ""


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

"""
Langfuse observability client — lazy singleton.

Reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST from env.
If those variables are empty the client is None and all callers no-op.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_client: Any = None
_init_attempted = False


def get_langfuse() -> Any:
    """Return the Langfuse client, or None if tracing is not configured."""
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    try:
        from config import settings
        if not settings.langfuse_public_key:
            return None
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host or None,
        )
        logger.info("Langfuse tracing enabled → %s", settings.langfuse_host)
    except Exception as exc:
        logger.warning("Langfuse init failed (tracing disabled): %s", exc)
    return _client


def trace_eval_run_created(run_id: str, agent_name: str, dataset_id: str, user_id: str) -> None:
    lf = get_langfuse()
    if not lf:
        return
    try:
        lf.trace(
            id=run_id,
            name="eval-run",
            user_id=user_id,
            metadata={"agent_name": agent_name, "dataset_id": dataset_id},
            tags=["eval", agent_name],
        )
        lf.flush()
    except Exception as exc:
        logger.debug("Langfuse trace_eval_run_created error: %s", exc)


def trace_eval_run_result(run_id: str, item_idx: int, score: float | None,
                          passed: bool | None, agent_name: str,
                          input_message: str | None = None,
                          response: str | None = None,
                          judge_reasoning: str | None = None) -> str | None:
    lf = get_langfuse()
    if not lf:
        return None
    try:
        trace = lf.trace(id=run_id, name="eval-run")
        span = trace.span(
            name=f"eval-item-{item_idx}",
            input={"message": input_message} if input_message else None,
            output={"response": response[:2000], "score": score, "passed": passed,
                    "judge_reasoning": judge_reasoning[:500] if judge_reasoning else None}
                   if response else None,
            metadata={"agent": agent_name, "item_idx": item_idx,
                      "score": score, "passed": passed},
        )
        span.end()
        lf.flush()
        return run_id
    except Exception as exc:
        logger.debug("Langfuse trace_eval_run_result error: %s", exc)
    return None


def trace_eval_run_completed(run_id: str, status: str, overall_score: float | None) -> None:
    lf = get_langfuse()
    if not lf:
        return
    try:
        lf.trace(
            id=run_id,
            name="eval-run",
            output={"status": status, "overall_score": overall_score},
            tags=["eval", f"status:{status}"],
        )
        lf.flush()
    except Exception as exc:
        logger.debug("Langfuse trace_eval_run_completed error: %s", exc)


def trace_create_run(
    run_id: str,
    agent_name: str,
    user_id: str,
    context: str = "playground",
    input_message: str | None = None,
) -> str | None:
    """Create a root Langfuse trace when a playground or consumer chat run starts.

    Returns the trace_id (same as run_id for simplicity) or None if tracing is disabled.
    """
    lf = get_langfuse()
    if not lf:
        return None
    try:
        lf.trace(
            id=run_id,
            name=f"agent-run.{context}",
            user_id=user_id,
            input={"message": input_message} if input_message else None,
            metadata={"agent_name": agent_name, "context": context},
            tags=[agent_name, context],
        )
        lf.flush()
        return run_id
    except Exception as exc:
        logger.debug("Langfuse trace_create_run error: %s", exc)
        return None


def trace_complete_run(
    run_id: str,
    status: str = "completed",
    output_text: str | None = None,
    judge_score: float | None = None,
) -> None:
    """Update the root trace with completion data."""
    lf = get_langfuse()
    if not lf:
        return
    try:
        output: dict = {"status": status}
        if output_text:
            output["response"] = output_text[:2000]
        if judge_score is not None:
            output["judge_score"] = judge_score
        lf.trace(
            id=run_id,
            name="agent-run",
            output=output,
            tags=[f"status:{status}"],
        )
        lf.flush()
    except Exception as exc:
        logger.debug("Langfuse trace_complete_run error: %s", exc)


def trace_judge_score(
    trace_id: str,
    score: float,
    reason: str | None = None,
) -> None:
    """Push the LLM judge score to Langfuse as a score attached to the run trace."""
    lf = get_langfuse()
    if not lf:
        return
    try:
        lf.score(
            trace_id=trace_id,
            name="llm-judge",
            value=score,
            comment=reason,
        )
        lf.flush()
    except Exception as exc:
        logger.debug("Langfuse trace_judge_score error: %s", exc)


def trace_platform_action(
    trace_id: str,
    action: str,
    user_id: str | None = None,
    agent_name: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Emit a Langfuse trace for a platform write action (deploy, approve, etc.).

    trace_id comes from the X-AgentShield-Trace-ID request header so the trace
    is stitchable with downstream safety-scan spans that carry the same ID.
    """
    lf = get_langfuse()
    if not lf:
        return
    try:
        lf.trace(
            id=trace_id,
            name=f"platform.{action}",
            user_id=user_id,
            metadata={
                **(metadata or {}),
                "agent_name": agent_name,
                "action": action,
            },
            tags=["platform", action],
        )
        lf.flush()
    except Exception as exc:
        logger.debug("Langfuse trace_platform_action error: %s", exc)

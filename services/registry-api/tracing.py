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
            metadata={"agent": agent_name, "dataset_id": dataset_id},
            tags=["eval"],
        )
        lf.flush()
    except Exception as exc:
        logger.debug("Langfuse trace_eval_run_created error: %s", exc)


def trace_eval_run_result(run_id: str, item_idx: int, score: float | None,
                          passed: bool | None, agent_name: str) -> str | None:
    lf = get_langfuse()
    if not lf:
        return None
    try:
        trace = lf.trace(id=run_id, name="eval-run")
        span = trace.span(
            name=f"eval-item-{item_idx}",
            metadata={"agent": agent_name, "item_idx": item_idx,
                      "score": score, "passed": passed},
        )
        span.end()
        return span.id
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

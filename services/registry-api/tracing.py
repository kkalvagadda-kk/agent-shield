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


def _lf_trace_id(run_id: str) -> str:
    """Normalize a run id to the OTEL 32-hex trace-id form (no dashes).

    Agent-side spans are emitted via OpenTelemetry, whose trace ids are 32-hex
    (a UUID with the dashes stripped). To land the platform's envelope trace on
    the SAME Langfuse trace as those spans, registry-api must use the identical
    id. ``uuid.UUID`` accepts both dashed and undashed input, so this is
    idempotent for either form; non-UUID ids pass through unchanged.
    """
    import uuid as _uuid
    try:
        return _uuid.UUID(str(run_id)).hex
    except Exception:
        return str(run_id)


def trace_create_run(
    run_id: str,
    agent_name: str,
    user_id: str,
    context: str = "playground",
    input_message: str | None = None,
    deployment_id: str | None = None,
    environment: str | None = None,
) -> str | None:
    """Create a root Langfuse trace when a playground or consumer chat run starts.

    ``user_id`` is a DISPLAY identifier for Langfuse — pass a human-readable name
    (e.g. the JWT ``preferred_username``), not the raw ``sub`` UUID; the platform's
    own ``PlaygroundRun.user_id`` FK stays the UUID.

    ``deployment_id``/``environment`` identify which agent instance produced the
    trace — an agent can have several running deployments, so without these,
    traces from different instances are indistinguishable in Langfuse.

    Returns the trace_id (same as run_id for simplicity) or None if tracing is disabled.
    """
    lf = get_langfuse()
    if not lf:
        return None
    try:
        metadata: dict[str, Any] = {"agent_name": agent_name, "context": context}
        tags = [agent_name, context]
        if deployment_id:
            metadata["deployment_id"] = deployment_id
            tags.append(f"deployment:{deployment_id}")
        if environment:
            metadata["environment"] = environment
            tags.append(f"env:{environment}")
        lf_id = _lf_trace_id(run_id)
        # The trace's DISPLAY NAME is the agent instance's identity — a deployment
        # has no human name, so it's the agent name + environment (e.g.
        # "serper-agent-4 · sandbox"). context stays in metadata/tags for filtering.
        trace_name = f"{agent_name} · {environment}" if environment else agent_name
        lf.trace(
            id=lf_id,
            name=trace_name,
            user_id=user_id,
            input={"message": input_message} if input_message else None,
            metadata=metadata,
            tags=tags,
        )
        lf.flush()
        # Return the undashed id — stored as langfuse_trace_id, propagated to the
        # agent, and used to build the Studio trace URL, so all land on one trace.
        return lf_id
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
        # Partial update — only set output. Do NOT pass name/tags: Langfuse
        # preserves omitted fields, so the create-time display name (the agent
        # identity) and tags (agent_name/deployment/env) survive. Previously this
        # passed name="agent-run" + tags=[status], which clobbered both — the
        # reason every completed trace showed up as just "agent-run".
        lf.trace(
            id=_lf_trace_id(run_id),
            output=output,
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


def fetch_trace_cost(trace_id: str) -> float | None:
    """Fetch total_cost from a Langfuse trace. Returns None if unavailable."""
    lf = get_langfuse()
    if not lf:
        return None
    try:
        trace = lf.fetch_trace(trace_id)
        cost = getattr(trace.data, "total_cost", None) if trace.data else None
        if cost and cost > 0:
            return float(cost)
    except Exception as exc:
        logger.debug("Langfuse fetch_trace_cost error: %s", exc)
    return None


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

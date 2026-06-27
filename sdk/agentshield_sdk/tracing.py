"""
Langfuse tracing wrapper.

No-ops gracefully when AGENTSHIELD_LANGFUSE_KEY is not set.  This means
importing and calling the tracer is always safe — it simply does nothing in
local dev without a Langfuse deployment.

Usage:
    from agentshield_sdk.tracing import tracer

    ctx = tracer.start_trace("order-agent-run", session_id="sess-abc",
                              agent_name="order-agent")
    tracer.span(ctx, "tool_call", input={"tool": "lookup_order"}, output={...})
    tracer.end_trace(ctx, output={"response": "..."})
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import config

logger = logging.getLogger(__name__)


@dataclass
class TraceContext:
    """Holds the Langfuse trace handle (or None in no-op mode)."""
    trace_id: str | None = None
    _trace: Any = field(default=None, repr=False)
    _client: Any = field(default=None, repr=False)


class Tracer:
    """Langfuse client wrapper that no-ops when the key is absent."""

    def __init__(self) -> None:
        self._enabled = False
        self._client: Any = None

        if not config.AGENTSHIELD_LANGFUSE_KEY:
            return

        try:
            from langfuse import Langfuse  # type: ignore[import]

            self._client = Langfuse(
                secret_key=config.AGENTSHIELD_LANGFUSE_KEY,
                host=config.AGENTSHIELD_LANGFUSE_HOST,
            )
            self._enabled = True
            logger.info(
                "Langfuse tracing enabled (host=%s)", config.AGENTSHIELD_LANGFUSE_HOST
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Langfuse initialisation failed — tracing disabled: %s", exc)

    def start_trace(
        self,
        name: str,
        session_id: str,
        agent_name: str,
        team: str | None = None,
    ) -> TraceContext:
        """Start a new root trace.

        Returns a TraceContext that must be passed to :meth:`end_trace`.
        """
        if not self._enabled:
            return TraceContext()

        try:
            trace = self._client.trace(
                name=name,
                session_id=session_id,
                metadata={"agent_name": agent_name, "team": team},
                tags=[agent_name] + ([team] if team else []),
            )
            return TraceContext(trace_id=trace.id, _trace=trace, _client=self._client)
        except Exception as exc:
            logger.warning("start_trace failed: %s", exc)
            return TraceContext()

    def span(
        self,
        trace_ctx: TraceContext,
        name: str,
        input: Any = None,
        output: Any = None,
        metadata: dict | None = None,
    ) -> None:
        """Record a span inside the given trace context."""
        if not self._enabled or trace_ctx._trace is None:
            return

        try:
            trace_ctx._trace.span(
                name=name,
                input=input,
                output=output,
                metadata=metadata or {},
            )
        except Exception as exc:
            logger.warning("span() failed: %s", exc)

    def end_trace(self, trace_ctx: TraceContext, output: Any = None) -> None:
        """Flush and finalise the trace."""
        if not self._enabled or trace_ctx._trace is None:
            return

        try:
            trace_ctx._trace.update(output=output)
            self._client.flush()
        except Exception as exc:
            logger.warning("end_trace failed: %s", exc)


# Module-level singleton — import and use directly.
tracer = Tracer()

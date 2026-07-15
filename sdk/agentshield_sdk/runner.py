"""
Runner — the main entry point for running an Agent.

Usage:
    runner = Runner(agent)
    await runner.setup()                               # once at startup
    result = await runner.run("What's order 123?")     # sync invoke
    async for chunk in runner.run_streamed("..."):     # SSE stream
        print(chunk)
"""
from __future__ import annotations

import logging
from typing import AsyncIterator
from uuid import uuid4

from langchain_core.messages import HumanMessage  # type: ignore[import]

from .agent import Agent
from .checkpointer import get_checkpointer
from .graph_builder import build_graph, resolve_agent_tools, begin_eval_context, _current_thread_id
from .otel import otel_run_context
from .safety_client import scan_input, scan_output, SafetyBlockedError
from .streaming import stream_events
from .tracing import tracer

logger = logging.getLogger(__name__)


class Runner:
    """Orchestrates safety scanning, graph invocation, and SSE streaming.

    Attributes:
        agent: The Agent descriptor to run.
    """

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self._graph = None
        self._checkpointer = None

    async def setup(self) -> None:
        """Initialise the checkpointer, resolve tools, and compile the graph.

        Resolves platform tool references (strings in agent.tools) from the
        registry API, then builds the governed LangGraph agent.

        Must be called once before :meth:`run` or :meth:`run_streamed`.
        """
        self._checkpointer = await get_checkpointer()

        # Resolve platform tool names + collect inline tools
        resolved_tools = await resolve_agent_tools(self.agent)

        self._graph = build_graph(self.agent, self._checkpointer, resolved_tools=resolved_tools)
        logger.info(
            "Runner ready: agent=%s tools=%d checkpointer=%s",
            self.agent.name,
            len(resolved_tools),
            type(self._checkpointer).__name__,
        )

    def _assert_ready(self) -> None:
        if self._graph is None:
            raise RuntimeError(
                "Runner.setup() must be called before run() or run_streamed()"
            )

    async def run(
        self,
        message: str,
        thread_id: str | None = None,
        metadata: dict | None = None,
        trace_id: str | None = None,
    ) -> dict:
        """Run the agent synchronously (one shot, waits for completion).

        Steps:
        1. Start/attach Langfuse trace.
        2. Safety scan of input.
        3. Graph ainvoke.
        4. Safety scan of output.
        5. End trace and return response dict.

        Args:
            message:   The user's message.
            thread_id: Existing thread ID for conversation continuity.
            metadata:  Optional metadata attached to the run (unused in v1).
            trace_id:  Optional trace ID from X-AgentShield-Trace-ID header.

        Returns:
            dict with ``response`` (str) and ``thread_id`` (str).

        Raises:
            SafetyBlockedError: If input or output is blocked by the scanner.
        """
        self._assert_ready()
        thread_id = thread_id or str(uuid4())

        # 1. Start/attach trace
        trace_ctx = tracer.start_trace(
            name=f"agent.{self.agent.name}",
            session_id=thread_id,
            agent_name=self.agent.name,
            trace_id=trace_id,
        )

        # 2. Safety scan input.
        scan_result = await scan_input(
            message, agent_name=self.agent.name, session_id=thread_id
        )
        safe_message = scan_result.sanitized_text
        tracer.span(trace_ctx, "safety_scan_input", input={"message_len": len(message)},
                    output={"sanitized": scan_result.sanitized_text != message})

        # 3. Invoke graph.
        graph_config = {"configurable": {"thread_id": thread_id}}
        state = {"messages": [HumanMessage(content=safe_message)]}

        token = _current_thread_id.set(thread_id)
        try:
            # Bind OpenInference/OTEL LLM+tool spans to a trace id derived from
            # trace_id (=run_id) so the agent's generation spans land on the
            # platform's trace, not a separate auto-generated one. Mirrors the
            # declarative-runner's workflow_executor.run() wrap site.
            with otel_run_context(trace_id):
                result = await self._graph.ainvoke(state, graph_config)
        finally:
            _current_thread_id.reset(token)

        # 4. Extract last AI message.
        messages = result.get("messages", [])
        last_message = messages[-1] if messages else None
        response_text: str = (
            last_message.content
            if last_message and hasattr(last_message, "content")
            else ""
        )

        # 5. Safety scan output.
        out_scan = await scan_output(
            response_text, agent_name=self.agent.name, session_id=thread_id
        )
        tracer.span(trace_ctx, "safety_scan_output",
                    output={"clean": out_scan.clean_text == response_text})

        # 6. End trace
        tracer.end_trace(trace_ctx, output={"response_len": len(out_scan.clean_text)})

        return {
            "response": out_scan.clean_text,
            "thread_id": thread_id,
        }

    async def run_streamed(
        self,
        message: str,
        thread_id: str | None = None,
        trace_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream agent output as SSE events.

        Steps:
        1. Start/attach Langfuse trace.
        2. Safety scan of input (before streaming begins).
        3. Yield SSE events from graph.astream_events().
        4. End trace after stream completes.

        Yields:
            SSE-formatted strings (event + data lines).

        Raises:
            SafetyBlockedError: If input scan blocks the message before streaming.
        """
        self._assert_ready()
        thread_id = thread_id or str(uuid4())

        # 1. Start/attach trace
        trace_ctx = tracer.start_trace(
            name=f"agent.{self.agent.name}.stream",
            session_id=thread_id,
            agent_name=self.agent.name,
            trace_id=trace_id,
        )

        # 2. Safety scan input — fail fast before starting the stream.
        scan_result = await scan_input(
            message, agent_name=self.agent.name, session_id=thread_id
        )
        safe_message = scan_result.sanitized_text
        tracer.span(trace_ctx, "safety_scan_input", input={"message_len": len(message)},
                    output={"sanitized": scan_result.sanitized_text != message})

        # 3. Stream graph events.
        graph_config = {"configurable": {"thread_id": thread_id}}
        state = {"messages": [HumanMessage(content=safe_message)]}

        token = _current_thread_id.set(thread_id)
        try:
            # Same OTEL trace binding as run() — mirrors the declarative-runner's
            # workflow_executor.run_streamed() wrap site.
            with otel_run_context(trace_id):
                async for sse_chunk in stream_events(self._graph, state, graph_config):
                    yield sse_chunk
        finally:
            _current_thread_id.reset(token)
            tracer.end_trace(trace_ctx, output={"streamed": True})

    async def resume(
        self, thread_id: str, decision: dict, trace_id: str | None = None
    ) -> dict:
        """Resume a paused graph after a HITL decision.

        The graph was paused by ``interrupt()``; passing *decision* as the resume
        value causes the governed_tool wrapper to receive it as the return value
        of ``require_approval()``.

        Args:
            thread_id: The thread to resume.
            decision:  Reviewer decision dict, e.g. ``{"decision": "approved"}``.
            trace_id:  Optional trace ID (from X-AgentShield-Trace-ID) so the
                       resumed run's LLM/tool spans land on the same trace.

        Returns:
            dict with ``response`` and ``thread_id``.
        """
        self._assert_ready()
        graph_config = {"configurable": {"thread_id": thread_id}}

        token = _current_thread_id.set(thread_id)
        try:
            # Bind resumed-run spans to the same trace — mirrors the
            # declarative-runner's workflow_executor.resume() wrap site.
            with otel_run_context(trace_id):
                # Provide None as input (graph continues from checkpoint).
                result = await self._graph.ainvoke(
                    {"messages": [], "resume": decision}, graph_config
                )
        finally:
            _current_thread_id.reset(token)

        messages = result.get("messages", [])
        last_message = messages[-1] if messages else None
        response_text: str = (
            last_message.content
            if last_message and hasattr(last_message, "content")
            else ""
        )

        out_scan = await scan_output(
            response_text, agent_name=self.agent.name, session_id=thread_id
        )
        return {"response": out_scan.clean_text, "thread_id": thread_id}

    async def run_durable(
        self, message: str, run_id: str, callback_url: str, trace_id: str | None = None,
        eval_mode: str = "live",
    ):
        """Durable fire-and-forget run: real per-node steps + HITL park via the shared
        harness (``agentshield_sdk.durable``) — the SAME ``run_durable`` the
        declarative-runner consumes (parity core, WS-1). Mirrors ``run()``'s input
        safety-scan + OTEL trace binding (safety lives outside the graph); the streamed
        output is not re-scanned mid-run, same as ``run_streamed()``. Returns the
        harness ``RunResult`` (completed | awaiting_approval | failed).

        ``eval_mode`` (Eval v2 E-2) arms the record/mock delivery seam for this run —
        the identical flag the declarative-runner sets, honored by the identical
        `governed_tool` edge (one seam, no fork). 'live' by default."""
        self._assert_ready()
        import httpx

        from .durable import Bookmark, StepEmitter, run_durable as _run_durable

        scan_result = await scan_input(
            message, agent_name=self.agent.name, session_id=run_id
        )
        state = {"messages": [HumanMessage(content=scan_result.sanitized_text)]}
        token = _current_thread_id.set(run_id)
        recorded = begin_eval_context(eval_mode)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                emitter = StepEmitter(callback_url, client, bookmark=Bookmark(run_id))
                with otel_run_context(trace_id or run_id):
                    return await _run_durable(
                        self._graph, state, thread_id=run_id,
                        callback_url=callback_url, emitter=emitter,
                        recorded_side_effects=recorded,
                    )
        finally:
            _current_thread_id.reset(token)

    async def resume_durable(
        self, thread_id: str, decision: dict, run_id: str, callback_url: str,
        trace_id: str | None = None, eval_mode: str = "live",
    ):
        """Re-enter a parked durable run after an approval decision via the shared
        harness, emitting the remaining steps to ``callback_url``. Symmetric with
        ``run_durable``; used by the SDK server's durable ``/resume`` branch (WS-1 T4).

        ``eval_mode`` re-arms the E-2 seam: a resume re-drives the graph and re-crosses
        the delivery edge, so it must run in the mode the run started in."""
        self._assert_ready()
        import httpx

        from .durable import Bookmark, StepEmitter, resume_durable as _resume_durable

        token = _current_thread_id.set(thread_id)
        recorded = begin_eval_context(eval_mode)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                emitter = StepEmitter(callback_url, client, bookmark=Bookmark(run_id))
                with otel_run_context(trace_id or run_id):
                    return await _resume_durable(
                        self._graph, thread_id=thread_id, decision=decision,
                        callback_url=callback_url, emitter=emitter,
                        recorded_side_effects=recorded,
                    )
        finally:
            _current_thread_id.reset(token)

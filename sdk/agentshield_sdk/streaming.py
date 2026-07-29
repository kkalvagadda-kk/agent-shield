"""
Streaming module — converts LangGraph astream_events() to SSE strings.

SSE event types (per sse-protocol.md):
    message_start       — a new LLM turn began → client opens a new assistant bubble (F-E)
    reasoning           — incremental extended-thinking/reasoning token (Bedrock/Claude) (F-E)
    text_delta          — incremental LLM output token (answer text)
    tool_call_start     — tool invocation begins
    tool_call_end       — tool invocation completes
    approval_requested  — HITL interrupt fired (high-risk tool paused)
    approval_decided    — reviewer approved or rejected
    done                — stream finished
    error               — unhandled exception during streaming
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Any

logger = logging.getLogger(__name__)


def format_sse(event_type: str, data: dict, event_id: str | None = None) -> str:
    """Format a single SSE frame.

    Returns a string ending with a double newline (the SSE frame boundary).
    """
    lines: list[str] = [f"event: {event_type}"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(data)}")
    lines.append("")  # blank line = frame boundary
    return "\n".join(lines) + "\n"


def _get_tool_risk(tool_name: str) -> str:
    """Look up the risk level for a tool from the graph builder's registry."""
    try:
        from .graph_builder import _TOOL_RISK_REGISTRY
        return _TOOL_RISK_REGISTRY.get(tool_name, "low")
    except Exception:
        return "low"


async def _extract_interrupts(graph: Any, config: dict) -> list[dict]:
    """Extract pending interrupt values from the graph's checkpoint state.

    LangGraph does not emit on_interrupt in astream_events(v2). Instead,
    interrupt data lives in graph.get_state().tasks[].interrupts after the
    stream ends. This helper reads those values.

    MUST use the ASYNC ``aget_state`` — in cluster deployments the graph is
    compiled with ``AsyncPostgresSaver`` (whenever ``DIRECT_DATABASE_URL`` is
    injected into the agent pod, per POC-0), which only services the async
    checkpoint API. The synchronous ``get_state`` raises when called from inside
    the running event loop, which was silently swallowed here and made the
    parked HITL interrupt invisible → the stream emitted ``done`` instead of
    ``approval_requested`` and orphaned the run. Mirrors the async read already
    used by ``workflow_executor.extract_tool_rationale``.
    """
    try:
        snapshot = await graph.aget_state(config)
        interrupts: list[dict] = []
        if hasattr(snapshot, "tasks"):
            for task in snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    for intr in task.interrupts:
                        val = intr.value if hasattr(intr, "value") else intr
                        if isinstance(val, dict):
                            interrupts.append(val)
        return interrupts
    except Exception as exc:
        logger.warning("Could not check graph state for interrupts: %s", exc)
        return []


async def stream_events(
    graph: Any,
    input_state: Any,
    config: dict,
) -> AsyncIterator[str]:
    """Stream LangGraph events as SSE-formatted strings.

    Yields:
        SSE-formatted strings for each meaningful event.  The last yield is
        always a ``done`` or ``approval_requested`` event (or ``error``).
    """
    event_counter = 0
    final_response = ""
    thread_id = config.get("configurable", {}).get("thread_id")

    try:
        async for event in graph.astream_events(input_state, config, version="v2"):
            event_type: str = event.get("event", "")
            event_counter += 1

            if event_type == "on_chat_model_start":
                # F-E (Issue 2): a new LLM turn = a new assistant message. Emit an
                # explicit boundary so the client opens a NEW bubble instead of
                # appending post-tool answers onto the pre-tool bubble (the old flat
                # stream had no boundary, so reasoning + every turn collapsed into one
                # bubble). One `message_start` per model invocation → one bubble.
                yield format_sse(
                    "message_start",
                    {"index": event_counter},
                    event_id=str(event_counter),
                )

            elif event_type == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                content = chunk.content if hasattr(chunk, "content") else ""
                if isinstance(content, list):
                    # F-E: split reasoning (Bedrock/Claude extended-thinking) blocks from
                    # answer text. The old join used block.get("text") for every block, so
                    # reasoning blocks — which carry their text under a DIFFERENT key
                    # (`reasoning_content.text` on Bedrock Converse, `thinking` on Anthropic)
                    # — were silently dropped. Now reasoning streams as its own `reasoning`
                    # event and answer text as `text_delta`.
                    text_parts: list[str] = []
                    reasoning_parts: list[str] = []
                    for block in content:
                        if isinstance(block, dict):
                            btype = block.get("type")
                            if btype in ("reasoning_content", "thinking", "reasoning"):
                                rc = block.get("reasoning_content")
                                if isinstance(rc, dict):
                                    reasoning_parts.append(rc.get("text", ""))
                                else:
                                    reasoning_parts.append(
                                        block.get("thinking")
                                        or block.get("reasoning")
                                        or block.get("text", "")
                                    )
                            else:
                                text_parts.append(block.get("text", ""))
                        else:
                            text_parts.append(str(block))
                    reasoning = "".join(p for p in reasoning_parts if p)
                    if reasoning:
                        yield format_sse(
                            "reasoning",
                            {"content": reasoning, "index": event_counter},
                            event_id=str(event_counter),
                        )
                    content = "".join(text_parts)
                if content:
                    yield format_sse(
                        "text_delta",
                        {"content": content, "index": event_counter},
                        event_id=str(event_counter),
                    )

            elif event_type == "on_tool_start":
                tool_name: str = event.get("name", "unknown_tool")
                tool_input = event["data"].get("input", {})
                run_id = event.get("run_id", "")
                risk = _get_tool_risk(tool_name)
                yield format_sse(
                    "tool_call_start",
                    {
                        "tool_call_id": run_id,
                        "tool": tool_name,
                        "args": tool_input,
                        "risk": risk,
                    },
                    event_id=str(event_counter),
                )

            elif event_type == "on_tool_end":
                tool_name = event.get("name", "unknown_tool")
                output = event["data"].get("output")
                run_id = event.get("run_id", "")
                if hasattr(output, "content"):
                    result: Any = output.content
                elif isinstance(output, (dict, list)):
                    result = json.dumps(output, default=str)
                else:
                    result = str(output) if output is not None else ""
                yield format_sse(
                    "tool_call_end",
                    {
                        "tool_call_id": run_id,
                        "tool": tool_name,
                        "result": result,
                        "error": None,
                        "duration_ms": 0,
                    },
                    event_id=str(event_counter),
                )

            elif event_type == "on_chain_end":
                if event.get("name") in ("LangGraph", "__end__"):
                    output_data = event["data"].get("output", {})
                    messages = output_data.get("messages", [])
                    if messages:
                        last_msg = messages[-1]
                        final_response = (
                            last_msg.content
                            if hasattr(last_msg, "content")
                            else str(last_msg)
                        )

    except Exception as exc:
        logger.exception("Unhandled error during streaming")
        yield format_sse(
            "error",
            {"message": str(exc), "type": type(exc).__name__},
            event_id=str(event_counter + 1),
        )
        return

    event_counter += 1
    pending = await _extract_interrupts(graph, config)
    if pending:
        interrupt_value = pending[0]
        yield format_sse(
            "approval_requested",
            {
                "approval_id": interrupt_value.get("approval_id"),
                "thread_id": interrupt_value.get("thread_id") or thread_id,
                "tool": interrupt_value.get("tool"),
                "args": interrupt_value.get("args"),
                "risk": interrupt_value.get("risk", "high"),
                "reasoning": interrupt_value.get("reasoning"),
                "expires_at": interrupt_value.get("expires_at"),
                "queue_url": interrupt_value.get("queue_url"),
            },
            event_id=str(event_counter),
        )
    else:
        yield format_sse(
            "done",
            {"thread_id": thread_id, "final_response": final_response},
            event_id=str(event_counter),
        )

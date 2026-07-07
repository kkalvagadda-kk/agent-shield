"""
Streaming module — converts LangGraph astream_events() to SSE strings.

SSE event types (per sse-protocol.md):
    text_delta          — incremental LLM output token
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


async def stream_events(
    graph: Any,
    input_state: dict,
    config: dict,
) -> AsyncIterator[str]:
    """Stream LangGraph events as SSE-formatted strings.

    Yields:
        SSE-formatted strings for each meaningful event.  The last yield is
        always a ``done`` event (or an ``error`` event if an exception occurs).
    """
    event_counter = 0

    try:
        async for event in graph.astream_events(input_state, config, version="v2"):
            event_type: str = event.get("event", "")
            event_counter += 1

            if event_type == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                content = chunk.content if hasattr(chunk, "content") else ""
                # Anthropic models may return content as a list of blocks
                # e.g. [{"type": "text", "text": "Hello"}] — extract text.
                if isinstance(content, list):
                    content = "".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in content
                    )
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
                # Convert output to a JSON-safe form.
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
                # LangGraph emits a chain_end at the top level when the graph
                # completes normally.  We use this to emit the done event.
                # Only emit once — for the outermost "LangGraph" chain.
                if event.get("name") in ("LangGraph", "__end__"):
                    output_data = event["data"].get("output", {})
                    messages = output_data.get("messages", [])
                    last_content = ""
                    if messages:
                        last_msg = messages[-1]
                        last_content = (
                            last_msg.content
                            if hasattr(last_msg, "content")
                            else str(last_msg)
                        )
                    yield format_sse(
                        "done",
                        {"thread_id": config.get("configurable", {}).get("thread_id"), "final_response": last_content},
                        event_id=str(event_counter),
                    )

            elif event_type == "on_interrupt":
                # LangGraph fires this when interrupt() is called inside a tool.
                interrupt_value = event["data"].get("value", {})
                yield format_sse(
                    "approval_requested",
                    {
                        "approval_id": interrupt_value.get("approval_id"),
                        "thread_id": interrupt_value.get("thread_id"),
                        "tool": interrupt_value.get("tool"),
                        "args": interrupt_value.get("args"),
                        "risk": interrupt_value.get("risk", "high"),
                        "expires_at": interrupt_value.get("expires_at"),
                        "queue_url": interrupt_value.get("queue_url"),
                    },
                    event_id=str(event_counter),
                )

    except Exception as exc:
        logger.exception("Unhandled error during streaming")
        yield format_sse(
            "error",
            {"message": str(exc), "type": type(exc).__name__},
            event_id=str(event_counter + 1),
        )

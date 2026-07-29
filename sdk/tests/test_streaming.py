"""Unit tests for the SSE stream reducer (agentshield_sdk/streaming.py).

Standalone: fakes the LangGraph (duck-typed astream_events + aget_state) and loads
streaming.py DIRECTLY (its only package import, graph_builder, is lazy + try-wrapped),
so no langchain / registry-api / Postgres. Proves the F-E contract: each LLM turn emits
a `message_start` boundary (→ the client opens a new bubble) and Bedrock/Claude
extended-thinking blocks stream as their OWN `reasoning` event instead of being dropped
or merged into the answer `text_delta`.
"""
import asyncio
import importlib.util
import json
import pathlib
import sys

_spec = importlib.util.spec_from_file_location(
    "agentshield_streaming",
    pathlib.Path(__file__).resolve().parent.parent / "agentshield_sdk" / "streaming.py",
)
_streaming = importlib.util.module_from_spec(_spec)
sys.modules["agentshield_streaming"] = _streaming
_spec.loader.exec_module(_streaming)
stream_events = _streaming.stream_events


# --- fakes -----------------------------------------------------------------
class _Chunk:
    def __init__(self, content):
        self.content = content


class _Msg:
    def __init__(self, content):
        self.content = content


class _Snapshot:
    tasks: list = []


class FakeGraph:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, input_state, config, version="v2"):
        for e in self._events:
            yield e

    async def aget_state(self, config):
        return _Snapshot()


# A tool-calling turn: reasoning + pre-tool text (turn 1) → tool → answer (turn 2).
EVENTS = [
    {"event": "on_chat_model_start"},
    {"event": "on_chat_model_stream",
     "data": {"chunk": _Chunk([{"type": "reasoning_content",
                                "reasoning_content": {"text": "Let me think. "}}])}},
    {"event": "on_chat_model_stream",
     "data": {"chunk": _Chunk([{"type": "text", "text": "I'll use the tool."}])}},
    {"event": "on_tool_start", "name": "calc", "run_id": "t1", "data": {"input": {"x": 1}}},
    {"event": "on_tool_end", "name": "calc", "run_id": "t1", "data": {"output": "42"}},
    {"event": "on_chat_model_start"},
    {"event": "on_chat_model_stream",
     "data": {"chunk": _Chunk([{"type": "text", "text": "The answer is 42."}])}},
    {"event": "on_chain_end", "name": "LangGraph",
     "data": {"output": {"messages": [_Msg("The answer is 42.")]}}},
]


def _collect(events):
    async def _run():
        out = []
        async for frame in stream_events(FakeGraph(events), {"messages": []}, {"configurable": {"thread_id": "t"}}):
            out.append(frame)
        return out
    return asyncio.run(_run())


def _parse(frames):
    """[(event_name, data_dict), ...] from raw SSE frame strings."""
    parsed = []
    for f in frames:
        name, data = None, None
        for line in f.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        parsed.append((name, data))
    return parsed


def test_each_llm_turn_emits_a_message_start_boundary():
    parsed = _parse(_collect(EVENTS))
    starts = [d for (n, d) in parsed if n == "message_start"]
    # Two model invocations (pre-tool + post-tool) → two bubble boundaries.
    assert len(starts) == 2, f"expected 2 message_start, got {len(starts)}: {parsed}"


def test_reasoning_streams_as_its_own_event_not_merged_into_text():
    parsed = _parse(_collect(EVENTS))
    reasoning = [d["content"] for (n, d) in parsed if n == "reasoning"]
    text = [d["content"] for (n, d) in parsed if n == "text_delta"]
    assert reasoning == ["Let me think. "], f"reasoning frames: {reasoning}"
    # The answer text is present…
    assert "The answer is 42." in text
    assert "I'll use the tool." in text
    # …and the reasoning text NEVER leaks into an answer bubble.
    assert all("Let me think" not in t for t in text), f"reasoning leaked into text_delta: {text}"


def test_tool_and_done_frames_still_emitted():
    names = [n for (n, _d) in _parse(_collect(EVENTS))]
    assert "tool_call_start" in names
    assert "tool_call_end" in names
    assert names[-1] == "done"


def test_ordering_message_start_precedes_its_turns_tokens():
    names = [n for (n, _d) in _parse(_collect(EVENTS))]
    # First frame is a boundary, and the 2nd turn's boundary precedes its answer.
    assert names[0] == "message_start"
    i2 = names.index("message_start", 1)
    assert names.index("text_delta", i2) > i2

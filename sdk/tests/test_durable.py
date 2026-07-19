"""Unit tests for the shared durable harness (agentshield_sdk/durable.py).

Standalone: fakes the LangGraph (duck-typed astream_events + get_state) and uses an
httpx MockTransport to capture the step-update callbacks. No registry-api, no real
LangGraph, no Postgres — proves the harness STRUCTURE (real steps, interrupt park,
fail-closed, resume, bookmark idempotency). LangGraph event-name specifics are
validated in-cluster by suite-55.
"""
import asyncio
import importlib.util
import json
import pathlib
import sys

import httpx

# Load durable.py DIRECTLY (not via the package __init__, which imports the whole SDK
# runtime incl. langchain). This also proves the harness is standalone — httpx only.
_spec = importlib.util.spec_from_file_location(
    "agentshield_durable",
    pathlib.Path(__file__).resolve().parent.parent / "agentshield_sdk" / "durable.py",
)
_durable = importlib.util.module_from_spec(_spec)
sys.modules["agentshield_durable"] = _durable  # dataclasses need the module registered for annotations
_spec.loader.exec_module(_durable)
Bookmark = _durable.Bookmark
StepEmitter = _durable.StepEmitter
StepUpdate = _durable.StepUpdate
run_durable = _durable.run_durable
resume_durable = _durable.resume_durable


# --- fakes -----------------------------------------------------------------
class _Intr:
    def __init__(self, value):
        self.value = value


class _Task:
    def __init__(self, interrupts):
        self.interrupts = interrupts


class _Snapshot:
    def __init__(self, tasks):
        self.tasks = tasks


class FakeGraph:
    def __init__(self, events, interrupt_value=None):
        self._events = events
        self._interrupt = interrupt_value

    async def astream_events(self, input_state, config, version="v2"):
        for e in self._events:
            yield e

    async def aget_state(self, config):
        if self._interrupt is not None:
            return _Snapshot([_Task([_Intr(self._interrupt)])])
        return _Snapshot([])


def _emitter(recorded, *, approval_echo=None, bookmark=None):
    def handler(request):
        recorded.append(json.loads(request.content))
        return httpx.Response(200, json={"approval_id": approval_echo} if approval_echo else {"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return StepEmitter("http://cb/step-update", client, bookmark=bookmark)


TWO_TOOL_EVENTS = [
    {"event": "on_tool_start", "name": "search", "run_id": "t1"},
    {"event": "on_tool_end", "name": "search", "run_id": "t1", "data": {"output": "hits"}},
    {"event": "on_tool_start", "name": "write", "run_id": "t2"},
    {"event": "on_tool_end", "name": "write", "run_id": "t2", "data": {"output": "ok"}},
    {"event": "on_chain_end", "name": "LangGraph", "data": {"output": {"messages": [{"content": "final answer"}]}}},
]


# --- tests -----------------------------------------------------------------
def test_completion_emits_real_per_tool_steps():
    async def _run():
        rec = []
        r = await run_durable(FakeGraph(TWO_TOOL_EVENTS), {"messages": []},
                              thread_id="th1", callback_url="http://cb", emitter=_emitter(rec))
        return r, rec
    result, rec = asyncio.run(_run())
    assert result.status == "completed"
    names = [s["step_name"] for s in rec]
    # Real tool steps, NOT the old skeleton.
    assert "tool:search" in names and "tool:write" in names
    assert "agent_execution" not in names and "input_processing" not in names
    # Terminal step carries run_completed + the final text.
    assert rec[-1]["run_completed"] is True
    assert rec[-1]["output_text"] == "final answer"
    # Each tool emitted running then completed.
    assert [s["status"] for s in rec if s["step_name"] == "tool:search"] == ["running", "completed"]


def test_interrupt_parks_awaiting_approval():
    async def _run():
        rec = []
        g = FakeGraph(TWO_TOOL_EVENTS[:2], interrupt_value={"approval_id": "ap-1", "tool": "wire_money"})
        r = await run_durable(g, {"messages": []}, thread_id="th2",
                              callback_url="http://cb", emitter=_emitter(rec))
        return r, rec
    result, rec = asyncio.run(_run())
    assert result.status == "awaiting_approval"
    park = [s for s in rec if s["status"] == "awaiting_approval"]
    assert len(park) == 1 and park[0]["approval_id"] == "ap-1"
    # A parked run must NOT report completed.
    assert not any(s["run_completed"] for s in rec)


def test_interrupt_without_approval_id_fails_closed():
    async def _run():
        rec = []
        g = FakeGraph(TWO_TOOL_EVENTS[:2], interrupt_value={"tool": "wire_money"})  # no approval_id
        r = await run_durable(g, {"messages": []}, thread_id="th3",
                              callback_url="http://cb", emitter=_emitter(rec))
        return r, rec
    result, rec = asyncio.run(_run())
    assert result.status == "failed"
    assert rec[-1]["status"] == "failed" and rec[-1]["run_completed"] is True
    assert "fail-closed" in rec[-1]["error_message"]


def test_resume_completes():
    async def _run():
        rec = []
        r = await resume_durable(FakeGraph(TWO_TOOL_EVENTS[2:]), thread_id="th4",
                                 decision={"decision": "approved"}, callback_url="http://cb",
                                 emitter=_emitter(rec), start_step=2)
        return r, rec
    result, rec = asyncio.run(_run())
    assert result.status == "completed"
    assert rec[-1]["run_completed"] is True


def test_bookmark_skips_already_recorded_step():
    async def _run():
        rec = []
        em = _emitter(rec, bookmark=Bookmark(run_id="r1", last_completed_step=5))
        # A completed step <= bookmark is skipped (no POST); a new one is sent.
        await em.emit(StepUpdate(3, "tool:old", "completed"))   # skipped
        await em.emit(StepUpdate(6, "tool:new", "completed"))   # sent
        return rec
    rec = asyncio.run(_run())
    assert [s["step_name"] for s in rec] == ["tool:new"]


def test_drive_crash_fails_loud():
    class BoomGraph:
        async def astream_events(self, *a, **k):
            raise RuntimeError("kaboom")
            yield  # pragma: no cover
        async def aget_state(self, config):
            return _Snapshot([])

    async def _run():
        rec = []
        r = await run_durable(BoomGraph(), {}, thread_id="th5", callback_url="http://cb", emitter=_emitter(rec))
        return r, rec
    result, rec = asyncio.run(_run())
    assert result.status == "failed"
    assert rec[-1]["status"] == "failed" and "kaboom" in rec[-1]["error_message"]

"""Wiring tests for the SDK's OpenTelemetry span-capture integration.

These prove the two connections that were previously missing (``otel.py`` was
orphaned for the SDK runtime — only declarative-runner called it):

1. The FastAPI server calls ``setup_otel()`` once at startup (lifespan).
2. ``Runner.run`` / ``Runner.resume`` wrap the actual graph invocation in
   ``otel_run_context(trace_id)`` so LLM/tool generation spans land on the
   platform's trace.

No network or real langchain/opentelemetry backend is exercised — the graph,
safety client, and tracer are mocked, and ``setup_otel`` / ``otel_run_context``
are patched so we assert the wiring, not the backend.
"""
from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch


# --------------------------------------------------------------------------- #
# 1. Server startup calls setup_otel exactly once.
# --------------------------------------------------------------------------- #
def test_server_startup_calls_setup_otel():
    """Constructing the app + entering its lifespan must invoke setup_otel().

    Requires the SDK's runtime deps (fastapi, pydantic on py>=3.10 for the
    ``str | None`` request models). Uvicorn/TestClient drives the lifespan.
    """
    from fastapi.testclient import TestClient

    from agentshield_sdk import server

    with patch.object(server, "setup_otel", return_value=True) as mock_setup:
        # Entering the TestClient context manager runs the lifespan startup.
        with TestClient(server.app):
            pass

    mock_setup.assert_called_once()


def test_server_startup_survives_setup_otel_raising():
    """A tracing misconfig (setup_otel raising) must not break app startup."""
    from fastapi.testclient import TestClient

    from agentshield_sdk import server

    with patch.object(server, "setup_otel", side_effect=RuntimeError("boom")):
        with TestClient(server.app) as client:
            resp = client.get("/health")
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# 2. Runner wraps graph execution in otel_run_context(trace_id).
# --------------------------------------------------------------------------- #
def _make_runner(runner_mod, response_text="hi"):
    r = runner_mod.Runner(agent=MagicMock())
    r.agent.name = "test-agent"
    graph = MagicMock()
    graph.ainvoke = AsyncMock(
        return_value={"messages": [MagicMock(content=response_text)]}
    )
    r._graph = graph  # pretend setup() already compiled the graph
    return r, graph


def test_run_enters_otel_run_context_with_trace_id():
    from agentshield_sdk import runner as runner_mod

    r, graph = _make_runner(runner_mod)

    seen: list = []

    @contextlib.contextmanager
    def spy_ctx(run_id):
        seen.append(("enter", run_id))
        yield
        seen.append(("exit", run_id))

    scan_res = MagicMock(sanitized_text="hello")
    out_res = MagicMock(clean_text="hi")

    with patch.object(runner_mod, "otel_run_context", side_effect=spy_ctx) as spy, \
         patch.object(runner_mod, "scan_input", AsyncMock(return_value=scan_res)), \
         patch.object(runner_mod, "scan_output", AsyncMock(return_value=out_res)), \
         patch.object(runner_mod, "tracer", MagicMock()):
        result = asyncio.run(
            r.run("hello", thread_id="t1", trace_id="trace-xyz")
        )

    spy.assert_called_once_with("trace-xyz")
    # Context wrapped the ainvoke: entered before, exited after.
    assert seen == [("enter", "trace-xyz"), ("exit", "trace-xyz")]
    graph.ainvoke.assert_awaited_once()
    assert result["response"] == "hi"


def test_resume_enters_otel_run_context_with_trace_id():
    from agentshield_sdk import runner as runner_mod

    r, graph = _make_runner(runner_mod, response_text="resumed")

    seen: list = []

    @contextlib.contextmanager
    def spy_ctx(run_id):
        seen.append(run_id)
        yield

    out_res = MagicMock(clean_text="resumed")

    with patch.object(runner_mod, "otel_run_context", side_effect=spy_ctx) as spy, \
         patch.object(runner_mod, "scan_output", AsyncMock(return_value=out_res)), \
         patch.object(runner_mod, "tracer", MagicMock()):
        result = asyncio.run(
            r.resume("t1", {"decision": "approved"}, trace_id="trace-abc")
        )

    spy.assert_called_once_with("trace-abc")
    assert seen == ["trace-abc"]
    graph.ainvoke.assert_awaited_once()
    assert result["response"] == "resumed"

"""Regression tests for governed-tool schema construction.

Root cause of the KeyError('kwargs') / KeyError('params') crashes: an executor
emitted a ``__signature__`` containing a parameter (``**kwargs`` for python tools,
the ``params`` catch-all for arg-less http tools) that was missing from
``__annotations__``. LangChain 1.x introspection does ``type_hints[name]`` for every
parameter and raises ``KeyError`` on the gap.

The load-bearing invariant these tests lock in: **every parameter in an executor
callable's signature (and after governance wrapping) has a matching annotation** —
so schema introspection can never KeyError again. The langchain-gated test proves
the callables actually bind and keep ``graph_state`` out of the model-facing schema.
"""
from __future__ import annotations

import inspect

import pytest

from agentshield_sdk.tool_executor import HttpToolExecutor, PythonToolExecutor
from agentshield_sdk.graph_builder import _wrap_tool_with_governance


def _annotation_covers_signature(fn) -> None:
    """Assert every parameter in fn's signature has an entry in __annotations__."""
    sig = inspect.signature(fn)
    annos = getattr(fn, "__annotations__", {})
    missing = [name for name in sig.parameters if name not in annos]
    assert not missing, (
        f"{getattr(fn, '__name__', fn)} has params without annotations: {missing} "
        f"(signature={sig}, annotations={list(annos)})"
    )


def _python(name="tool", risk="low", input_schema=None):
    return PythonToolExecutor(
        name=name, risk=risk, python_code="def main(**k): return 'x'",
        description="A tool.", input_schema=input_schema,
    ).as_tool_callable()


def _http(name="tool", risk="low", url="https://x", body=""):
    return HttpToolExecutor(
        name=name, risk=risk, method="POST", url=url, headers={},
        body_template=body, description="A tool.",
    ).as_tool_callable()


# --- the invariant, executor level (no langchain needed) --------------------

@pytest.mark.parametrize("fn", [
    _python(),                                                     # **kwargs fallback
    _python(input_schema={"type": "object", "required": ["order_id", "amount"],
                          "properties": {"order_id": {"type": "string"},
                                         "amount": {"type": "number"}}}),  # named
    _python(input_schema={"type": "object", "required": ["user"],
                          "properties": {"user": {"type": "string"},
                                         "cc": {"type": "string"}}}),      # optional
    _http(url="https://x/{{order_id}}"),                           # template var
    _http(),                                                       # no vars -> params
])
def test_executor_annotations_cover_signature(fn):
    _annotation_covers_signature(fn)


# --- the invariant survives governance wrapping -----------------------------

@pytest.mark.parametrize("fn", [
    _python(risk="high"),
    _python(risk="high", input_schema={"type": "object", "required": ["order_id"],
                                       "properties": {"order_id": {"type": "string"}}}),
    _http(risk="high", url="https://x/{{order_id}}"),
    _http(risk="high"),
])
def test_governed_wrapper_annotations_cover_signature(fn):
    governed = _wrap_tool_with_governance(fn, "agent")
    _annotation_covers_signature(governed)


def test_python_input_schema_becomes_named_params():
    fn = _python(input_schema={"type": "object", "required": ["order_id", "amount"],
                               "properties": {"order_id": {"type": "string"},
                                              "amount": {"type": "number"}}})
    params = inspect.signature(fn).parameters
    assert "order_id" in params and "amount" in params
    assert not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def test_python_without_input_schema_falls_back_to_kwargs():
    fn = _python()
    params = list(inspect.signature(fn).parameters.values())
    assert len(params) == 1 and params[0].kind is inspect.Parameter.VAR_KEYWORD
    assert params[0].annotation is not inspect.Parameter.empty  # the fix


def test_graph_state_injected_before_var_keyword():
    """graph_state must sit before **kwargs (a kw-only param can't follow **kwargs)."""
    governed = _wrap_tool_with_governance(_python(risk="high"), "agent")
    kinds = [p.kind for p in inspect.signature(governed).parameters.values()]
    assert inspect.Parameter.VAR_KEYWORD in kinds
    gs = list(inspect.signature(governed).parameters).index("graph_state")
    vk = next(i for i, k in enumerate(kinds) if k is inspect.Parameter.VAR_KEYWORD)
    assert gs < vk


# --- full langchain bind (proves the real crash is gone) --------------------

langchain = pytest.importorskip("langchain_core.tools")


@pytest.mark.parametrize("fn,expected_model_props", [
    (_python(risk="high"), ["kwargs"]),
    (_python(risk="high", input_schema={"type": "object", "required": ["order_id", "amount"],
                                        "properties": {"order_id": {"type": "string"},
                                                       "amount": {"type": "number"}}}),
     ["order_id", "amount"]),
    (_http(risk="high", url="https://x/{{order_id}}"), ["order_id"]),
    (_http(risk="high"), ["params"]),
])
def test_governed_tool_binds_and_hides_graph_state(fn, expected_model_props):
    governed = _wrap_tool_with_governance(fn, "agent")
    lc = langchain.tool(governed)  # would raise KeyError before the fix
    model_props = list(lc.tool_call_schema.model_json_schema().get("properties", {}))
    assert model_props == expected_model_props
    assert "graph_state" not in model_props

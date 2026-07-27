"""Regression test — an MCP tool call must NOT forward omitted-optional args as ``None``.

Root cause of the Tavily failure (2026-07-27): ``McpToolExecutor``'s tool callable forwarded
the raw LangChain kwargs to the proxy. LangChain materializes every OPTIONAL schema param the
model omitted as an explicit ``None`` (``_params_from_input_schema`` gives optionals
``default=None``), so those nulls reached the upstream MCP server. A server with typed
optionals rejects them — Tavily: ``max_results Input should be a valid integer
[input_value=None]``. deepwiki works only because both its params are required (no optionals
to null-inject). The fix drops ``None``-valued kwargs before building the proxy payload, so an
omitted optional is ABSENT on the wire and the upstream applies its own default.

This test locks that in: given a schema with one required + two optional params, calling the
tool while omitting the optionals must send ONLY the required arg to the proxy — no ``None``s.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from agentshield_sdk import config
from agentshield_sdk.tool_executor import McpToolExecutor, _params_from_input_schema


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"result": "ok", "is_error": False}


class _CapturingClient:
    """Stand-in for httpx.AsyncClient that records the JSON body + headers of the proxy call."""

    captured: dict = {}
    captured_headers: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):  # noqa: A002 - mirror httpx kwarg
        _CapturingClient.captured = json or {}
        _CapturingClient.captured_headers = headers or {}
        return _FakeResponse()


def _call_tool(**kwargs):
    """Build the mcp tool callable (schema: required `query` + optional `max_results`,
    `search_depth`) and invoke it, returning the `arguments` dict the proxy received."""
    ex = McpToolExecutor(
        name="tavily__tavily_search",
        risk="low",
        server_id="srv-1",
        mcp_tool_name="tavily_search",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "search_depth": {"type": "string", "enum": ["basic", "advanced"]},
            },
        },
    )
    fn = ex.as_tool_callable()
    _CapturingClient.captured = {}
    # DEV_MODE short-circuits before the proxy call; force the real marshaling path with a
    # token present and the proxy client mocked to capture the payload.
    with patch.object(config, "DEV_MODE", False), \
         patch.object(config, "AGENTSHIELD_MCP_PROXY_URL", "http://proxy"), \
         patch("agentshield_sdk.tool_executor._read_sa_token", return_value="tok"), \
         patch("agentshield_sdk.tool_executor.httpx.AsyncClient", _CapturingClient):
        asyncio.get_event_loop().run_until_complete(fn(**kwargs))
    return _CapturingClient.captured.get("arguments")


def test_omitted_optionals_are_dropped_not_nulled():
    # The LLM set `query`, and LangChain filled the two omitted optionals with None.
    args = _call_tool(query="mcp 2025", max_results=None, search_depth=None)
    assert args == {"query": "mcp 2025"}, (
        f"omitted optionals must be dropped, not sent as null; got {json.dumps(args)}"
    )


def test_explicitly_set_args_survive():
    # Values the model actually provided (including a falsy 0 / empty string) are preserved;
    # only None is dropped.
    args = _call_tool(query="x", max_results=3, search_depth="")
    assert args == {"query": "x", "max_results": 3, "search_depth": ""}


def test_per_request_user_forwarded_as_x_user_sub():
    # An OAuth external server needs the user DRIVING this run so the proxy pulls that user's
    # stored token. The executor must forward the request-scoped ContextVar user
    # (governed_tool's _current_user_context), not the static config.USER_SUB.
    from agentshield_sdk.graph_builder import _current_user_context

    ex = McpToolExecutor(
        name="github__get_me", risk="low", server_id="srv-gh", mcp_tool_name="get_me",
        input_schema={"type": "object", "properties": {}},
    )
    fn = ex.as_tool_callable()
    _CapturingClient.captured_headers = {}
    token = _current_user_context.set({"user_id": "user-abc", "user_team": "platform"})
    try:
        with patch.object(config, "DEV_MODE", False), \
             patch.object(config, "AGENTSHIELD_MCP_PROXY_URL", "http://proxy"), \
             patch.object(config, "USER_SUB", "static-pod-user"), \
             patch("agentshield_sdk.tool_executor._read_sa_token", return_value="tok"), \
             patch("agentshield_sdk.tool_executor.httpx.AsyncClient", _CapturingClient):
            asyncio.get_event_loop().run_until_complete(fn())
    finally:
        _current_user_context.reset(token)
    # The per-request ContextVar user wins over the static pod env.
    assert _CapturingClient.captured_headers.get("x-user-sub") == "user-abc"


def test_optional_param_uses_schema_default_not_none():
    # An optional param that DECLARES a default in its schema must carry that default (so an
    # omitted optional sends the server's intended value), while an optional with no declared
    # default keeps None (and is dropped on the wire). Mirrors Tavily's tavily_search, whose
    # optionals type real defaults and reject None.
    schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "topic": {"type": "string", "default": "general"},       # has default
            "max_results": {"type": "integer", "default": 5},         # has default
            "days": {"type": "integer"},                              # no default
        },
    }
    params, _ = _params_from_input_schema(schema)
    defaults = {p.name: p.default for p in params}
    assert defaults["topic"] == "general"
    assert defaults["max_results"] == 5
    assert defaults["days"] is None  # no schema default → None → dropped on the wire


def test_required_only_tool_is_unaffected():
    # A tool whose params are all required has nothing to drop (deepwiki-shaped) — the args
    # pass through byte-identically.
    ex = McpToolExecutor(
        name="deepwiki__ask_question", risk="low", server_id="srv-2",
        mcp_tool_name="ask_question",
        input_schema={
            "type": "object", "required": ["repoName", "question"],
            "properties": {"repoName": {"type": "string"}, "question": {"type": "string"}},
        },
    )
    fn = ex.as_tool_callable()
    _CapturingClient.captured = {}
    with patch.object(config, "DEV_MODE", False), \
         patch.object(config, "AGENTSHIELD_MCP_PROXY_URL", "http://proxy"), \
         patch("agentshield_sdk.tool_executor._read_sa_token", return_value="tok"), \
         patch("agentshield_sdk.tool_executor.httpx.AsyncClient", _CapturingClient):
        asyncio.get_event_loop().run_until_complete(fn(repoName="facebook/react", question="q"))
    assert _CapturingClient.captured.get("arguments") == {
        "repoName": "facebook/react", "question": "q",
    }

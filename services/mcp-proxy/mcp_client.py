"""
MCP wire client — a thin wrapper over the official `mcp` SDK (pinned <2.0).

`connect_and_initialize()` opens a streamable-HTTP transport + ClientSession,
runs `initialize()` (capturing the negotiated protocol version and whether the
server advertised the tools.listChanged capability), and returns a live McpSession
that session_cache.py keeps warm across calls. `.list_tools()` / `.call_tool()`
speak to that session; `.close()` tears the whole transport down.

The 1.x API is deliberate (research.md B1): `mcp.client.streamable_http.
streamablehttp_client` (a 3-tuple context manager) + `mcp.ClientSession`. 2.0
renames these (streamable_http_client, httpx2) — the requirements pin blocks it.
Attribute access uses the 1.x camelCase model fields (.inputSchema, .isError,
.protocolVersion); optional/version-varying fields (structuredContent,
capabilities.tools.listChanged) are read defensively with getattr since they
appeared across different 1.x minors.
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredTool:
    name: str
    description: str | None
    input_schema: dict


@dataclass
class CallResult:
    result: str                       # MCP content flattened to a single string
    is_error: bool = False            # from CallToolResult.isError
    structured: dict | None = None    # from CallToolResult.structuredContent (optional)


def _flatten_content(content: Any) -> str:
    """Concatenate an MCP content block list to a single string.

    Multiple text blocks are concatenated verbatim (the echo fixture returns a
    single text block, so its value survives byte-for-byte — the de-anon proof).
    A non-text block (image/resource/…) is stringified as a placeholder in
    Phase 1 (richer multimodal is out of scope).
    """
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(getattr(block, "text", ""))
        else:
            parts.append(f"[non-text content: {block_type or type(block).__name__}]")
    return "\n".join(parts)


class McpSession:
    """A live, reusable MCP client session over one streamable-HTTP transport."""

    def __init__(self) -> None:
        self._exit_stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self.protocol_version: str | None = None
        self.list_changed_supported: bool = False

    async def _connect(self, server_url: str, headers: dict[str, str] | None) -> None:
        # Enter both async context managers on the exit stack so the transport
        # + session stay open after this returns, and unwind together on close().
        transport = await self._exit_stack.enter_async_context(
            streamablehttp_client(url=server_url, headers=headers or {})
        )
        # 1.x yields a 3-tuple: (read_stream, write_stream, get_session_id).
        read_stream, write_stream = transport[0], transport[1]
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        init_result = await session.initialize()
        self._session = session

        self.protocol_version = getattr(init_result, "protocolVersion", None)
        capabilities = getattr(init_result, "capabilities", None)
        tools_cap = getattr(capabilities, "tools", None) if capabilities else None
        self.list_changed_supported = (
            bool(getattr(tools_cap, "listChanged", False)) if tools_cap else False
        )

    async def list_tools(self) -> list[DiscoveredTool]:
        assert self._session is not None, "session not connected"
        result = await self._session.list_tools()
        tools: list[DiscoveredTool] = []
        for t in getattr(result, "tools", []) or []:
            tools.append(
                DiscoveredTool(
                    name=t.name,
                    description=getattr(t, "description", None),
                    # camelCase 1.x model attribute; JSON Schema dict.
                    input_schema=getattr(t, "inputSchema", None) or {},
                )
            )
        return tools

    async def call_tool(self, name: str, arguments: dict) -> CallResult:
        assert self._session is not None, "session not connected"
        result = await self._session.call_tool(name, arguments)
        return CallResult(
            result=_flatten_content(getattr(result, "content", None)),
            is_error=bool(getattr(result, "isError", False)),
            structured=getattr(result, "structuredContent", None),
        )

    async def close(self) -> None:
        """Tear down the session + transport. Idempotent; never raises."""
        try:
            await self._exit_stack.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp-proxy: error closing MCP session: %s", exc)
        finally:
            self._session = None


async def connect_and_initialize(
    server_url: str, headers: dict[str, str] | None = None
) -> McpSession:
    """Open + initialize a session against server_url. Raises on connect failure.

    The caller (session_cache / the endpoints) treats a raised exception as a
    connect error → status='error' / is_error=true 200 body, never a 5xx.
    """
    session = McpSession()
    try:
        await session._connect(server_url, headers)
    except Exception:
        await session.close()
        raise
    return session

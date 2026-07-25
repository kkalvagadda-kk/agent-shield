"""MCP Proxy client — the single seam from registry-api to the MCP Proxy's
admin-plane ``POST /internal/discover`` endpoint (T031;
contracts/mcp-proxy-internal.md).

Ownership split (contract): the **proxy** connects to the MCP server and lists its
tools; **registry-api** owns every DB write (namespacing, ``Tool`` upserts, status).
This module is the thin HTTP hop that hands registry-api the proxy's discover verdict.

Return contract — ``discover_server`` returns the parsed ``McpDiscoverResponse`` dict
**regardless of its ``ok`` field**. An ``ok:false`` / ``status:'error'`` body (bad
creds, connection refused, timeout, missing Secret) is a NORMAL result — the proxy
reports it as HTTP ``200`` and the caller folds it into ``MCPServer.status='error'``,
never a 4xx to Studio.

A ``RuntimeError`` is raised ONLY when the proxy could not even produce a discover
verdict: a genuine transport failure, or a non-``200`` HTTP status (``401``/``403``
auth, ``422`` malformed body, ``5xx``). The caller treats a ``RuntimeError``
identically to ``ok:false`` (→ ``status='error'``), so a proxy 4xx never surfaces
to Studio as an API error.

Auth: the Bearer token is a projected K8s ServiceAccount token (audience
``agentshield-mcp-proxy``) that ROTATES hourly — it is re-read from disk on EVERY
call, never cached.
"""
from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Discovery opens a live MCP session upstream (connect + initialize + list_tools);
# allow generous headroom before we treat it as a transport failure.
_TIMEOUT = float(30.0)


def _read_proxy_token() -> str:
    """Read the projected SA token fresh from disk (it rotates ~hourly).

    A missing/empty token file is a genuine failure — raise ``RuntimeError`` so the
    caller records ``status='error'`` rather than calling the proxy unauthenticated.
    """
    path = settings.mcp_proxy_sa_token_path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError as exc:
        raise RuntimeError(
            f"could not read MCP proxy SA token at {path}: {exc}"
        ) from exc
    if not token:
        raise RuntimeError(f"MCP proxy SA token at {path} is empty")
    return token


async def discover_server(server_id) -> dict:
    """Call the MCP Proxy's ``POST /internal/discover`` for ``server_id``.

    Returns the parsed ``McpDiscoverResponse`` dict (keys: ``ok``, ``status``,
    ``health_detail``, ``protocol_version``, ``list_changed_supported``, ``tools``)
    for any HTTP ``200`` — including an ``ok:false`` error body.

    Raises ``RuntimeError`` on a transport failure or any non-``200`` response.
    """
    token = _read_proxy_token()
    url = settings.mcp_proxy_url.rstrip("/") + "/internal/discover"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={"server_id": str(server_id)},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        # DNS/connect/read failure — the proxy itself was unreachable.
        raise RuntimeError(
            f"MCP proxy /internal/discover request failed: {exc}"
        ) from exc

    if resp.status_code != 200:
        # 401/403 (auth), 422 (malformed body), 5xx — the proxy could NOT produce a
        # discover verdict. Raise so the caller records status='error'. A connect/
        # initialize failure is NOT handled here: the proxy reports that as a 200 with
        # ok:false (returned below), so it is never a RuntimeError.
        raise RuntimeError(
            f"MCP proxy /internal/discover returned {resp.status_code}: "
            f"{resp.text[:300]}"
        )

    return resp.json()


async def health_check_server(server_id) -> dict:
    """Call the MCP Proxy's ``POST /internal/health`` for ``server_id`` (WS-A).

    The health probe reuses the pooled upstream session and issues a lightweight
    ``tools/list`` — it performs NO discovery and produces NO ``Tool``-row write.
    Returns the parsed ``McpHealthResponse`` dict (keys: ``ok``, ``status``,
    ``health_detail``, ``protocol_version``, ``list_changed_supported``,
    ``tool_count``) for any HTTP ``200`` — including an ``ok:false`` error body
    (a server that is down is reported as ``200 ok=false``, which is a NORMAL
    return the health loop folds into ``status='error'`` after the threshold).

    Raises ``RuntimeError`` on a transport failure or any non-``200`` response —
    the WS-A loop catches it and treats it as a failed probe (ok=false).

    Mirrors ``discover_server`` exactly: same fresh SA-token read (the projected
    token rotates ~hourly), same URL base, same timeout, same error mapping.
    """
    token = _read_proxy_token()
    url = settings.mcp_proxy_url.rstrip("/") + "/internal/health"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={"server_id": str(server_id)},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        # DNS/connect/read failure — the proxy itself was unreachable.
        raise RuntimeError(
            f"MCP proxy /internal/health request failed: {exc}"
        ) from exc

    if resp.status_code != 200:
        # 401/403 (auth), 422 (malformed body), 5xx — the proxy could NOT produce a
        # health verdict. Raise so the loop records a failed probe. An upstream
        # connect/list failure is NOT handled here: the proxy reports that as a 200
        # with ok:false (returned below), so it is never a RuntimeError.
        raise RuntimeError(
            f"MCP proxy /internal/health returned {resp.status_code}: "
            f"{resp.text[:300]}"
        )

    return resp.json()

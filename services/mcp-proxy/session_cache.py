"""
Per-replica MCP session cache — one pooled connection per server_id.

On a cache miss the flow is the same as discover's: read the per-server Secret
(credentials.read_server_secret) → connect_and_initialize against its server_url
with its auth_headers. The live McpSession + its parsed ServerConnection are cached
per server_id. A transport/auth error evicts the entry (closing the session) so the
next call re-reads the Secret and reconnects — covering a credential rotation, which
re-materializes the same-named Secret's contents.

This cache is per-replica and in-memory (Phase 1, static creds = one pooled
connection per server). On-behalf-of (Phase 2) will need per-(server, user) keys.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import credentials
import mcp_client
from credentials import ServerConnection
from mcp_client import McpSession

logger = logging.getLogger(__name__)


@dataclass
class CachedSession:
    session: McpSession
    connection: ServerConnection


_cache: dict[str, CachedSession] = {}
# One lock per server_id so two concurrent misses don't open two sessions.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(server_id: str) -> asyncio.Lock:
    lock = _locks.get(server_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[server_id] = lock
    return lock


async def _open(server_id: str) -> CachedSession:
    """Read the Secret and open a fresh session. Raises on any failure."""
    connection = await credentials.read_server_secret(server_id)
    session = await mcp_client.connect_and_initialize(
        connection.server_url, connection.auth_headers
    )
    return CachedSession(session=session, connection=connection)


def peek(server_id: str) -> CachedSession | None:
    """Return the cached session for server_id without opening one on a miss.

    Lets the tools/call handler resolve owner_team for the §3b floor from an
    already-open session (zero reads) before deciding whether to open one.
    """
    return _cache.get(str(server_id))


async def get_or_create(server_id: str) -> CachedSession:
    """Return the cached live session for server_id, opening one on a miss.

    Raises credentials.ServerSecretNotFound (missing Secret) or a connect error —
    the endpoints turn both into a 200 error body, never a 5xx.
    """
    server_id = str(server_id)
    cached = _cache.get(server_id)
    if cached is not None:
        return cached

    async with _lock_for(server_id):
        # Re-check under the lock — another task may have opened it while we waited.
        cached = _cache.get(server_id)
        if cached is not None:
            return cached
        cached = await _open(server_id)
        _cache[server_id] = cached
        return cached


async def set_session(server_id: str, cached: CachedSession) -> None:
    """Install an already-opened session (used by discover, which connects itself)."""
    server_id = str(server_id)
    # Close any prior session for this id before replacing it.
    prior = _cache.get(server_id)
    if prior is not None and prior.session is not cached.session:
        await prior.session.close()
    _cache[server_id] = cached


async def evict(server_id: str) -> None:
    """Drop + close the cached session for server_id. Safe if already absent."""
    server_id = str(server_id)
    cached = _cache.pop(server_id, None)
    if cached is not None:
        await cached.session.close()

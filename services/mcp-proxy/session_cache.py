"""
Per-replica MCP session cache — pooled connections keyed by (server_id, effective_user_sub).

On a cache miss the flow is the same as discover's: read the per-server Secret
(credentials.read_server_secret) → connect_and_initialize against its server_url with
resolved headers. The live McpSession + its parsed ServerConnection are cached. A
transport/auth error evicts the entry (closing the session) so the next call re-reads the
Secret and reconnects — covering a credential rotation, which re-materializes the same-named
Secret's contents.

Phase 2 (WS-C, data-model.md §3a) makes the key COMPOSITE — `SessionKey = (server_id, sub)`:
  - `none` / `service_identity` servers → effective sub is None → key (server_id, None):
    ONE shared pooled connection per server, EXACTLY as Phase 1.
  - `on_behalf_of` servers → effective sub is the user_sub → key (server_id, user_sub):
    per-user isolation (the OBO path is blocked at runtime by identity.py's stub in Phase 2,
    but the key routing ships + is unit-tested so the plumbing is proven).
`_effective_user_sub` is what collapses user_sub to None for non-OBO servers, so passing a
user_sub for a `none` server is a no-op — its Phase-1 single-session behaviour is preserved.

This cache is per-replica and in-memory. Eviction is explicit only (no TTL).
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

# (str(server_id), effective_user_sub) — effective_user_sub is None for none/service_identity.
SessionKey = tuple[str, str | None]


@dataclass
class CachedSession:
    session: McpSession
    connection: ServerConnection


_cache: dict[SessionKey, CachedSession] = {}
# One lock per composite key so two concurrent misses don't open two sessions.
_locks: dict[SessionKey, asyncio.Lock] = {}


def _effective_user_sub(connection: ServerConnection, user_sub: str | None) -> str | None:
    """The user_sub that participates in the session key: only on_behalf_of servers get a
    per-user session; every other mode shares one pooled session (effective sub None)."""
    return user_sub if connection.identity_mode == "on_behalf_of" else None


def _lock_for(key: SessionKey) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def _open(server_id: str, headers: dict[str, str] | None = None) -> CachedSession:
    """Read the Secret and open a fresh session. Raises on any failure.

    `headers` are the upstream connection headers already resolved by the caller via
    identity.resolve_headers (service-identity bearer, etc.). When None (Phase-1 callers
    that don't resolve identity), fall back to the Secret's static auth_headers so behaviour
    is byte-identical to Phase 1.
    """
    connection = await credentials.read_server_secret(server_id)
    resolved = connection.auth_headers if headers is None else headers
    session = await mcp_client.connect_and_initialize(connection.server_url, resolved)
    return CachedSession(session=session, connection=connection)


def peek(server_id: str, user_sub: str | None = None) -> CachedSession | None:
    """Return the cached session for (server_id, user_sub) without opening one on a miss.

    Lets the tools/call handler resolve owner_team + identity_mode for the §3b floor from an
    already-open session (zero reads) before deciding whether to open one. We cannot know a
    server's identity_mode without its connection, so we resolve the key defensively: try the
    per-user (on_behalf_of) key first, then the shared (none/service_identity) key. For a
    none/service server nothing is ever stored under a non-None user_sub key, so this always
    collapses to the shared session; for an on_behalf_of server it isolates per user.
    """
    server_id = str(server_id)
    cached = _cache.get((server_id, user_sub))
    if cached is not None:
        return cached
    if user_sub is not None:
        return _cache.get((server_id, None))
    return None


async def get_or_create(
    server_id: str, user_sub: str | None = None, headers: dict[str, str] | None = None
) -> CachedSession:
    """Return the cached live session for (server_id, effective_user_sub), opening one on a miss.

    Raises credentials.ServerSecretNotFound (missing Secret) or a connect error — the endpoints
    turn both into a 200 error body, never a 5xx. `headers` (optional) are the identity-resolved
    upstream headers used only when a NEW session is opened.
    """
    server_id = str(server_id)
    existing = peek(server_id, user_sub)
    if existing is not None:
        return existing

    async with _lock_for((server_id, user_sub)):
        # Re-check under the lock — another task may have opened it while we waited.
        existing = peek(server_id, user_sub)
        if existing is not None:
            return existing
        cached = await _open(server_id, headers)
        # The storage key uses the EFFECTIVE sub computed from the just-read connection
        # (None for none/service_identity, user_sub for on_behalf_of).
        key = (server_id, _effective_user_sub(cached.connection, user_sub))
        prior = _cache.get(key)
        if prior is not None and prior.session is not cached.session:
            # A concurrent miss under a different raw user_sub resolved to the same
            # effective key (e.g. two users hitting one none server) — keep the prior
            # shared session and discard the one we just opened (no leak).
            await cached.session.close()
            return prior
        _cache[key] = cached
        return cached


async def set_session(
    server_id: str, cached: CachedSession, user_sub: str | None = None
) -> None:
    """Install an already-opened session (used by discover/tools_call, which connect
    themselves with identity-resolved headers). Keyed on the composite effective key."""
    server_id = str(server_id)
    key = (server_id, _effective_user_sub(cached.connection, user_sub))
    # Close any prior session for this key before replacing it.
    prior = _cache.get(key)
    if prior is not None and prior.session is not cached.session:
        await prior.session.close()
    _cache[key] = cached


async def evict(server_id: str, user_sub: str | None = None) -> None:
    """Drop + close the cached session for (server_id, user_sub). Safe if already absent.

    We don't have the connection here to compute the effective key, so — mirroring peek —
    we drop the per-user key AND the shared key. For a none/service server the live session
    is at (server_id, None); for an on_behalf_of server it is at (server_id, user_sub). A
    server has exactly one identity_mode, so at most one of these keys is populated — there
    is never cross-user over-eviction.
    """
    server_id = str(server_id)
    keys: list[SessionKey] = [(server_id, user_sub)]
    if user_sub is not None:
        keys.append((server_id, None))
    for key in keys:
        cached = _cache.pop(key, None)
        if cached is not None:
            await cached.session.close()

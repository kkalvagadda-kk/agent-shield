# mcp-proxy `call_tool` was unbounded — a revoked-OAuth upstream 401 hung the proxy

**Found/Fixed:** 2026-07-26 — fixed in `mcp-proxy:0.1.5`
(commit bumps `MCP_PROXY_TAG` 0.1.4→0.1.5). Surfaced by `scripts/smoke-mcp4-cp3-behaviour.sh`
step 5 (fail-closed on revoke).

## Symptom

MCP Phase-4 CP3 step 5 (revoke a grant → the next governed tool call must fail closed with
a 200 `is_error` "re-authorize") **hung**: the client-side `httpx.post` to
`/internal/tools/call` timed out (`httpx.ReadTimeout`) instead of getting a prompt
200 `is_error`. Proxy logs showed the MCP client stuck inside `initialize` /
`call_tool` on `response_stream_reader.receive()`, ending in an anyio
`RuntimeError: Attempted to exit cancel scope in a different task` on teardown.

## Root cause

Two layers, one product bug:

1. **The proxy's OAuth access-token cache masks a revocation.** After `DELETE …/oauth`
   deletes the grant in registry-api, the proxy still holds the access token in its
   in-memory `oauth_tokens._access_cache` (valid for `expires_in`, ~1h). There is no
   registry-api→proxy invalidation channel; the design relies on the **upstream** rejecting
   the now-stale token (an upstream 401 → `oauth_tokens.invalidate` → evict + retry →
   re-pull → registry-api returns `needs_auth` → 200 `is_error`). So the proxy dials the
   upstream with the cached-but-revoked token, and the upstream 401s.

2. **`McpSession.call_tool` was unbounded.** Its siblings `_connect`'s `initialize` and
   `list_tools` both wrap the SDK call in `asyncio.wait_for(..., MCP_CONNECT_TIMEOUT_SECONDS)`
   precisely "so a dead upstream fails fast rather than hanging" — but `call_tool` was
   missed. When the upstream (here the stub AS's bearer gate) returns a bare HTTP 401 to a
   request on an established streamable-HTTP session, the MCP SDK client does **not** raise;
   it blocks on the response stream waiting for a JSON-RPC reply that never comes. With no
   timeout, `call_tool` hung forever — so the tools/call handler's existing
   evict + `oauth_tokens.invalidate` + retry path (which would re-pull and surface
   `needs_auth`) never got a chance to run.

The class of bug: an upstream operation reachable from a request path that is **not
bounded by a timeout**, so a misbehaving/authz-rejecting upstream converts into an
indefinite proxy hang rather than a fail-closed error.

## Fix

`services/mcp-proxy/mcp_client.py` — bound `call_tool` with the SAME
`asyncio.wait_for(..., timeout=config.MCP_CONNECT_TIMEOUT_SECONDS)` its siblings already use:

```python
result = await asyncio.wait_for(
    self._session.call_tool(name, arguments),
    timeout=config.MCP_CONNECT_TIMEOUT_SECONDS,
)
```

Now the revoked-token 401 makes `call_tool` raise `TimeoutError` after
`MCP_CONNECT_TIMEOUT_SECONDS` (30s); the tools/call handler catches it, evicts the session,
`oauth_tokens.invalidate(server_id, user_sub)`, and retries. The retry re-pulls from
registry-api, which returns the grant's `needs_auth`, so `identity.resolve_headers` raises
`OAuthAuthorizationRequired` → the handler returns a 200 `is_error`
("oauth token unavailable: grant status is 'needs_auth'"). Fail-closed, not hung.

This is the class-fix (bound the upstream call), not a revoke-specific patch: ANY upstream
that accepts the connection but stops answering a tool call now fails fast instead of
hanging the proxy.

## Known follow-ups (gap ledger — not blocking)

- **Slow revoke (~30s), not instant.** Fail-closed now works but takes up to
  `MCP_CONNECT_TIMEOUT_SECONDS` because it relies on the upstream 401 + timeout. A future
  improvement is a registry-api→proxy cache-invalidation call on `DELETE …/oauth` so the
  proxy drops its cached token immediately and the very next call re-pulls `needs_auth`
  with no dial/hang. Deferred (intentional) — correctness is in place; latency is the only
  gap.
- **Retry-path message wording.** The retry surfaces `OAuthAuthorizationRequired` as
  "oauth token unavailable: grant status is 'needs_auth'", while the first-resolve path says
  "authorize it in Studio". Both are correct fail-closed 200 `is_error` bodies; unifying the
  wording is cosmetic. Deferred (intentional).

## Cross-links

- Regression guard: `scripts/smoke-mcp4-cp3-behaviour.sh` step 5 (revoke → 200 `is_error`
  needs_auth). It also exercises the fixture-side revocation (see below).
- Test-fixture companion changes (same session, `scripts/e2e/fixtures/oauth_mcp_server.py`):
  the stub was rewritten to a real FastMCP streamable-HTTP server (a hand-rolled `/mcp`
  never implemented the `initialize` handshake), `enable_dns_rebinding_protection=False`
  (the proxy dials it cross-pod by IP → the default localhost-only Host guard 421'd every
  request), and `/revoke` now invalidates the whole refresh-token lineage's access tokens
  (mirroring a real AS, so the proxy's cached token is the one that gets rejected).

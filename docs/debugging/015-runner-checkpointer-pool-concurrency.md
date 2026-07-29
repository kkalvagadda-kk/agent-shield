# 015 — github-agent runs degrade + memory never saves → checkpointer pool concurrency

**Date:** 2026-07-28. Runner declarative-runner 0.1.67, langgraph 1.2.9,
langgraph-checkpoint-postgres 3.1.0, psycopg 3.3.4, psycopg-pool 3.3.1.

## Symptom

github-agent (MCP) chats produce ~1 `text_delta` (vs 87 on a good run — it's
intermittent) and the conversation never persists, even after the memory save/recall
decouple (which suite-91 proves works at the API). The runner logs, on every affected run:

```
WARNING:psycopg:error ignored in rollback on <AsyncConnection [ACTIVE] ...>:
  sending query failed: another command is already in progress
WARNING:psycopg.pool:closing returned connection: <AsyncConnection [ACTIVE] ...>
WARNING:psycopg.pool:discarding closed connection: <AsyncConnection [BAD] ...>
```

## Root cause (traced)

`sdk/agentshield_sdk/checkpointer.py` builds a module-global `AsyncConnectionPool`
(max_size=10, `check=check_connection`, autocommit) and hands it to
`AsyncPostgresSaver(_pool)`. The `[ACTIVE]`-on-return signature means a pooled connection
is **released back to the pool while a command is still in flight on it** — the pool's
reset (`rollback`) then fails with "another command is already in progress", so the
connection is discarded as `BAD`.

This is the classic **cursor-not-drained** race in the async Postgres checkpointer: an
`alist`/`aget` streams checkpoint rows over a server-side cursor; when the graph stops
consuming early (concurrency, an MCP tool interrupting the flow, an exception mid-stream),
the `async with pool.connection()` context exits with the cursor's query still ACTIVE.
Under github-agent's MCP concurrency it fires often enough that the pool churns
connections and the graph run degrades to a single token.

**The chain:** checkpointer connection killed → graph run degrades / errors early → ~no
assistant output → the (now-ungated) memory save has nothing meaningful to persist → 0
`agent_memory` rows. So this ONE bug explains both the broken github-agent runs AND the
"still no conversations." It is independent of the decouple (suite-91 green).

Why github-agent and not serper/poc: MCP tool calls add concurrency/latency around the
checkpointer that plain HTTP-tool agents don't hit as hard. It is NOT github-agent-specific
— any MCP/high-concurrency agent is exposed.

## Candidate fixes (ranked; NOT yet applied — high blast radius)

The checkpointer backs **every agent + HITL resume**, so this needs careful testing, not a
blind deploy.

1. **Custom pool `reset` that drains/closes an ACTIVE connection cleanly** instead of the
   default rollback — stops the churn without changing checkpointer semantics. Lowest
   semantic risk; needs a psycopg reset callable that cancels in-flight ops.
2. **Adopt the langgraph-documented `AsyncPostgresSaver.from_conn_string(DB_URI)` path**,
   entered once at pod start and kept open (store the context manager), letting langgraph
   own the connection lifecycle it's tested against. Larger change; must preserve the
   pod-lifetime + DIRECT_DATABASE_URL (no-PgBouncer, LISTEN/NOTIFY) invariants.
3. **Bump `langgraph-checkpoint-postgres`** — ❌ NOT AVAILABLE. 3.1.0 (installed) is the
   LATEST release (uploaded 2026-05-12), and langgraph core 1.2.9 is also latest. The bug
   is in the newest published version, so there is nothing to bump forward to. Options that
   remain: **downgrade** to 3.0.5 / 3.0.4 IF the changelog shows the async-pool cursor drain
   is a 3.1.0 regression (unverified — loses 3.1.0's fixes incl. the msgpack-deser security
   note), or pursue fix #1/#2. `>=2.0` in `sdk/pyproject.toml` means the build already
   resolves to the newest.

### Config-tweak hypothesis to test before the rewrite
Our pool adds `check=AsyncConnectionPool.check_connection` (langgraph's own
`from_conn_string` pool does NOT). Removing it (relying on `max_idle`/`max_lifetime` for
stale recycling) is a 1-line, low-risk experiment worth trying first — if the check is
implicated in the churn it may resolve it without a rewrite; if not, escalate to fix #2.

## Verification plan (once a fix is chosen)

- Load-repro: fire N concurrent github-agent chats; assert 0 `psycopg [ACTIVE]`/BAD
  warnings and full multi-token responses.
- Regression: suite-25/43/75 (memory), suite-4/35/45 (HITL resume — the LISTEN/NOTIFY
  path this pool exists for), suite-58/59 (durable workflows).
- Then the memory-off github-agent live smoke (transcript persists + recall gated) should
  pass end-to-end.

## Status

Traced + documented. Fix deferred pending the user's choice of candidate — a wrong
checkpointer change would break HITL resume and every agent, so it is not hot-patched.

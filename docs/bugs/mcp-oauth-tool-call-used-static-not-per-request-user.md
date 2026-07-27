# OAuth MCP tool calls forwarded the pod's static user, not the per-request user

**Found/Fixed:** 2026-07-27 — fixed in SDK `0.2.7` / declarative-runner `0.1.64`. Surfaced
running the first real OAuth agent (`github-agent`) end-to-end.

## Symptom

A declarative agent bound to GitHub's OAuth MCP tools called `github__get_me` /
`github__search_repositories`, but every call came back:

```
this MCP server requires OAuth authorization but no user identity was provided
```

even though the playground run was driven as the authorizing user (`75c7c8b3-…`, whose
GitHub grant was `authorized`). No-auth (DeepWiki) and token (Tavily) agents were unaffected.

## Root cause

Both MCP tool executors — the SDK's `McpToolExecutor` and the runner's separate
`McpToolNodeExecutor` — forwarded `config.USER_SUB` as the `x-user-sub` header. That value
comes from the pod env `AGENTSHIELD_USER_SUB`, which is **empty** for a `user_delegated`
agent (its acting user is per-request, not per-pod). So the proxy received no `x-user-sub`,
could not pick a user to pull an OAuth token for, and failed closed with `OAuthUserRequired`.

The per-request user *was* available: `/chat/stream` binds it into the request-scoped
ContextVar `agentshield_sdk.graph_builder._current_user_context` (from the `x-user-sub`
header) — the same one `governed_tool` reads for OPA. The MCP executors just weren't reading
it.

An external OAuth server needs only the user's **subject string** (to look up that user's
stored token), which the ContextVar already carries — so this is distinct from the
on-behalf-of / internal-identity gap (FR-MCP-21 / Decision 29), which needs a re-presentable
token the platform deliberately never holds.

## Fix

In both executors, prefer the request-scoped ContextVar user, fall back to `config.USER_SUB`
only when it is unset (a daemon/scheduled run with no per-request user):

```python
acting_user = ""
try:
    from agentshield_sdk.graph_builder import _current_user_context
    acting_user = (_current_user_context.get() or {}).get("user_id", "") or ""
except Exception:
    acting_user = ""
acting_user = acting_user or config.USER_SUB   # (runner: `or USER_SUB`)
if acting_user:
    headers["x-user-sub"] = acting_user
```

The mcp tool fn runs inside the graph execution, which runs inside the request task where
`_bind_user_context` set the ContextVar, so it reads the right user (ContextVars propagate
copy-on-task-creation — the same guarantee `governed_tool` already relies on). A daemon
agent with an empty user still emits a byte-identical Phase-1 request.

## Regression test

`sdk/tests/test_mcp_tool_arg_marshaling.py::test_per_request_user_forwarded_as_x_user_sub`
— sets the ContextVar to `user-abc`, sets `config.USER_SUB` to a different static value, and
asserts the call's `x-user-sub` header is `user-abc` (the per-request user wins).

## Deploy

SDK 0.2.6→0.2.7; declarative-runner 0.1.63→0.1.64; `declarativeRunnerTag` bumped in
`values.yaml` + both deploy scripts. After rolling, `github-agent` run as the authorizing
user reaches GitHub with that user's token.

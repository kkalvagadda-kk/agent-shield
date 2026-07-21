# Contract — `registry-api` internal MCP endpoint (proxy authz callback)

New router: `services/registry-api/routers/internal_mcp.py`, mounted in `main.py`. This is the cross-team half of the MCP Proxy's §3b coarse authz floor (research.md B12). The proxy calls it **only** when a caller's team differs from the target server's `owner_team` (the own-team case is answered locally in the proxy with zero hops).

Trust boundary: **NetworkPolicy-trusted, unauthenticated**, consistent with every other internal registry-api-facing call (`/auth-configs/{id}/secret-ref`, etc.). It returns only a boolean — never a secret, credential, or server URL — so it does not require registry-api to run TokenReview. The proxy has already TokenReview-verified the caller's identity before calling here; this endpoint only answers "does this team hold the grant."

---

## `POST /api/v1/internal/mcp/authorize-tool-call`

Answer the grant question for a cross-team MCP tool call using the **same** `team_may_use_tool` resolver the deploy gate uses (extracted in Task 3 — one implementation, two callers: the deploy gate and this endpoint).

### Request
```json
{
  "caller_sa_subject": "system:serviceaccount:agents-support:agent-triage-bot",
  "server_id": "b3e5b6b0-...-uuid",
  "mcp_tool_name": "search_issues"
}
```

### Server-side flow
1. Derive `caller_team` from `caller_sa_subject`'s namespace (`system:serviceaccount:agents-{team}:...` → `{team}`). A subject whose namespace is not of the `agents-` form → `allowed: false` (the proxy will already have 403'd on this, but the endpoint is defensive).
2. Resolve the target `Tool` by `(mcp_server_id == server_id, mcp_tool_name == mcp_tool_name)`. Not found → `allowed: false`.
3. `allowed = await team_may_use_tool(db, caller_team, tool.id)` — the shared resolver: `True` if `tool.owner_team == caller_team` (or `tool.owner_team is None`) **or** an active `AssetGrant(asset_type='tool', asset_id=tool.id, grantee_team=caller_team, revoked_at IS NULL)` exists.

### Response — `200`
```json
{ "allowed": true }
```
Always HTTP `200` (a `false` is a normal answer, not an error). The proxy maps `allowed: false` to a `403` to its own caller.

### Errors
- `422` — malformed body (missing/invalid `server_id`, empty `caller_sa_subject`/`mcp_tool_name`).

---

## Shared resolver `team_may_use_tool` (Task 3)

Extracted into `services/registry-api/tool_access.py` from the deploy gate loop in `routers/deployments.py` (the per-tool grant check that today loops over `agent_tools` and, for each foreign-owned tool, queries an active `AssetGrant`). Signature:

```python
async def team_may_use_tool(db: AsyncSession, team: str, tool_id: uuid.UUID) -> bool:
    """True iff `team` may use tool `tool_id`: the tool is own-team (or team-less),
    OR an active AssetGrant(asset_type='tool', asset_id=tool_id, grantee_team=team,
    revoked_at IS NULL) exists. This is the SINGLE implementation of the tool-grant
    rule — the deploy gate (deployments.py) and the internal MCP authz endpoint both
    call it; do not fork the logic (design §3b, §8)."""
```

`deployments.py`'s deploy gate is refactored to call this per foreign tool (behavior-neutral — the existing gate already implements exactly this rule inline; extraction must not change the deploy path's 422 semantics, proven by `suite-81`/`suite-18` staying green in Task 17).

# Registering an MCP server produced a catalog of tools nobody could see

**Found:** 2026-08-07 by `studio/e2e/mcp-servers.spec.ts` — the tools-source filter had no
chip for the server the test had just registered.
**Introduced:** same day, registry-api `0.2.268` (Decision 47 step B).
**Fixed:** 2026-08-07 — registry-api `0.2.270`.

## Symptom

Register an MCP server through Studio. Discovery succeeds, the detail page lists the
discovered tools, the server reads `connected`. Then open any agent's tool picker: **none
of those tools are there.** No error, no empty state, no 403 — the tools simply are not in
the catalog, for anyone, including the person who registered the server.

## Root cause

Three correct-looking pieces, composed into a hole.

1. **The Register Server modal collects a name and a URL.** No team field.
   (`studio/src/pages/McpServersPage.tsx`, `#mcp-name` / `#mcp-url`.)

2. **`create_mcp_server` built the row with `MCPServer(**body.model_dump())`** and took
   `owner_team` from the body — which Studio never sends. So every server registered
   through the UI had `owner_team = NULL`.

3. **`mcp_discovery.py:154` copies `owner_team=server.owner_team` onto every tool it
   discovers** — with a comment calling it "the only work team-scoping needs". True, as
   long as the server has an owner.

None of that mattered while `tools.publish_status` defaulted to `'published'`: a published
tool is visible to everyone regardless of owner. Migration `0080` (step B) flipped the
default to `'private'`, and the team-scoped catalog filter is:

```sql
publish_status = 'published' OR owner_team = <caller's team>
```

A tool that is `private` **and** owned by `NULL` matches neither arm. It is invisible to
every human caller on the platform, permanently, with no UI that can reach it.

### Why the earlier verification missed it

Step B's own tests covered the tools that `create_tool` produces, which since `0.2.267`
always have a real `owner_team`. The blast-radius sweep was derived by grepping the e2e
tree for `/tools`, `/skills` and `publish_status` — `mcp_discovery.py` writes `Tool` rows
without matching any of those strings. **The grep found every caller of the tools API and
missed the one writer that bypasses it.**

The live-cluster check I ran after deploying step B — "192 tools, all still published, zero
rows touched" — was true and also useless here: the defect is in rows created *after* the
migration, and there were none yet.

### The class

Identical to Decision 46 step A one layer up: a `Tool` row created by a path that does not
derive `owner_team` from the caller. That fix closed `create_tool` and I treated the
question as settled, when what it actually established was a rule — *ownership comes from
the caller* — that had a second, unaudited implementation site.

Sharper version: **when a default changes, the risk is not the rows that exist, it is the
writers that do not set the column.** The audit that would have caught this is
`grep -rn "Tool(" services/` — three lines, and it names `mcp_discovery.py` immediately.

## Fix

`create_mcp_server` now derives `owner_team` from the caller's team assignment, with the
same shape and the same exception as `create_tool`:

```python
caller_team = await get_user_team(db, claims["sub"])
if body.owner_team and body.owner_team != caller_team:
    if await get_user_global_role(db, caller) != "platform-admin":
        raise HTTPException(403, ...)          # forgeable attribution
    owner_team = body.owner_team               # seeding / admin-side registration
else:
    owner_team = caller_team
server = MCPServer(**body.model_dump(exclude={"owner_team"}))
server.owner_team = owner_team
```

`owner_team` is excluded from the splat so a body value cannot reach the row by a path the
guard does not cover.

The route also gains `require_user`. It took `get_optional_user` plus an `X-User-Sub`
fallback — the same optional-caller shape that made G-R3-6 a prerequisite for Decision 46
step A. Ownership cannot be derived from a caller who might not be there.

Discovery is left alone: inheriting the server's team is the right rule, and it is correct
once the server has one.

## Regression tests

- **`studio/e2e/mcp-servers.spec.ts`** — the test that found it. `bind a discovered
  mcp_tool to an agent → save → reload → still bound` fails at the source-filter chip when
  the discovered tools are invisible. Now 5/5. This is the layer that mattered: the API
  returned 201 and discovery reported success the whole time.
- **`suite-87-mcp-oauth`** — registration now needs a credential; the suite had **zero**
  `E2E_TOKEN` references and registered servers with a header alone. Fixed in the same
  change (green, 5 pass / 9 skip).
- **`suite-84-mcp-tools`** — green.
- **`scripts/check-e2e-auth-hygiene.sh`** now also matches `/api/v1/mcp-servers` and the
  `{BASE}/mcp-servers` f-string form, in both the bash and Playwright trees. Verified by
  planting an uncredentialed `httpx.post(f"{BASE}/mcp-servers/")` and confirming the gate
  fails — a gate nobody has watched fail is a gate nobody should trust.

## Files changed

`services/registry-api/routers/mcp_servers.py`, `scripts/e2e/suite-87-mcp-oauth.sh`,
`scripts/check-e2e-auth-hygiene.sh`, `docs/testing/manual-ui-e2e-test-plan.md`.
Tag `0.2.270` in `deploy-cpe2e.sh`, `deploy-eks.sh`, `values.yaml`.

## Lessons

1. **Changing a default is a change to every writer of that column, not to the existing
   rows.** "Zero rows touched" was the wrong reassurance. `grep -rn "Tool(" services/`
   would have found the second writer in seconds.
2. **A grep-derived sweep is only as wide as the string you grep for.** Deriving the list
   from the tree beat memory again — and still missed a writer that never names the API it
   bypasses. Grep the MODEL, not just the route.
3. **Closing one instance of a rule creates the obligation to find the others.** Decision
   46 established "ownership comes from the caller". `create_tool` was where it was
   noticed; `create_mcp_server` was where it also applied.
4. **The browser layer earned its keep again.** No bash suite could have caught this: the
   API answered 201, discovery answered success, and the row was written exactly as the
   code intended. Only a screen that renders the catalog could show that the tools were
   gone.

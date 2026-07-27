# The tool catalog silently truncates at 200, and the cap cannot be raised

**Found:** 2026-07-27, while merging `main` into `mcp-tool-source`.
**Fixed:** studio `0.1.164` (`listAllTools()` in `studio/src/api/registryApi.ts`).

## Symptom

Past a couple of hundred tools, a tool simply is not in the agent builder's
picker. No error, no empty state, no truncation notice — the catalog just ends.
The only symptom reaching a human is someone saying "I can't find my tool".

The same hole existed on the admin access screen, where it means a tool past the
boundary can never be granted to a team.

## Root cause

Two halves that were individually defensible:

1. `GET /tools/` declares `limit: int = Query(50, ge=1, le=200)` (`routers/tools.py`).
   **200 is a hard ceiling**, not a default — `limit=500` is rejected 422.
2. Every picker asked for one page and none read the `total` the endpoint returns:
   `AgentDetailPage.tsx:450` and `AgentListPage.tsx:386` called `listTools(200)` —
   *exactly* the cap — `CreateAgentPage.tsx:766` called `listTools(100, 0)`, and
   `AdminAccessPage.tsx:593` used the default 100.

So the frontend requested the largest page the server would give it and treated
that page as the whole catalog. Because the number requested equalled the maximum
allowed, the bug is invisible at any smaller scale and unfixable by tuning: there
is no number above 200 to ask for.

MCP is what made it reachable. A single MCP server contributes dozens of
discovered tools, and 54 of the 82 tools on the live cluster already come from
four of them. Two more servers cross the cap.

## Fix

`listAllTools()` follows pagination to the end, bounded by the reported `total`
and by an empty page (so a server that ignored `offset` could not spin it), and
returns a flat array. The four call sites use it under the query key
`["tools", "all"]` — a distinct key on purpose, because caching an array and a
page object under one key would hand whichever component loaded second the wrong
shape.

The class-fix framing matters: raising the page size would have been the easy
change and would have re-broken at the next threshold with the same silence. The
defect is that a paginated endpoint was consumed as if it were not paginated.

## Guards

- `scripts/e2e/suite-84-mcp-tools.sh` — **T-S84-032** asserts both facts the fix
  depends on: `limit=500` is refused (422), and paging on `total` retrieves the
  entire catalog. Written against `total` rather than a fixed count so it stays
  meaningful as the cluster's tool count grows.
- `studio/src/components/agent/ToolsPicker.tsx` carries the constraint in its
  doc comment, so the next caller knows the picker expects a complete catalog.

## Follow-up (gap ledger)

Drawer search is client-side over the fully-paged catalog. That is right for
hundreds of tools and wrong for thousands — at that point `/tools/` needs a `q`
parameter and the drawer needs a debounced server-side search. Recorded as
*deferred* with that threshold rather than left implicit.

## Related

- [tool-picker-offers-retired-tools.md](tool-picker-offers-retired-tools.md) — the
  other catalog bug found in the same pass; the two fixes share `listAllTools()`,
  and the reason it does **not** send `status=active` is explained there.

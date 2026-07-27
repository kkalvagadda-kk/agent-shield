# The agent builder offers deprecated and vanished-upstream tools

**Found:** 2026-07-27, while merging `main` into `mcp-tool-source`.
**Fixed:** studio `0.1.164` (`ToolsPicker` active-only catalog + `listAllTools`).

## Symptom

The Tools picker on every agent-editing surface (Create Agent, agent Settings,
the Edit Agent modal) listed **every** tool the caller could see, including ones
the platform has already retired. On the live EKS cluster that was 13 rows:

```
type='http'      status='deprecated'  11
type='native'    status='deprecated'   2
```

For MCP-sourced tools the same hole is worse in kind. `mcp_discovery.py:181-184`
marks a tool that has disappeared from its upstream server `status='inactive'`
and **never deletes the row** — deliberately, so history and audit survive. The
MCP server detail page renders those struck-through. The picker offered them as
normal, checkable tools, so an operator could bind a tool that the upstream
server no longer advertises. Nothing fails at bind time; it fails when the agent
runs and the proxy has no such tool to call.

## Root cause

`ToolsPicker` filtered exactly one thing — `knowledge_search`, by name — and
nothing else. The endpoint was never the problem: `GET /tools/` has accepted a
`status` filter all along (`routers/tools.py:176`), and Studio simply never sent
it. There was no notion of "offerable" anywhere in the frontend, so every reader
of the catalog independently decided (by omission) that every row was bindable.

The design flaw is that "which tools exist" and "which tools may be bound to
something new" were the same list. They are not the same question, and the
answers differ for exactly the rows that matter.

## Fix

Introduced `isSelectableTool(status)` in `studio/src/components/shared/ToolChips.tsx`
as the single owner of the rule, and split the picker's two derived lists:

- `pickable` — active only. This is what the drawer's tile grid renders.
- `selectedTools` — resolved from the **full** list, so a tool an agent already
  binds still appears.

The second half is the part that is easy to get wrong. Filtering retired tools
out of the fetch (`?status=active`, the obvious one-liner) would have hidden an
already-bound deprecated tool from the chip row while leaving the binding intact
in the database — the user could then neither see it nor remove it. That trades a
visible problem for an invisible one. So `listAllTools()` deliberately does **not**
filter by status, and offer-ability is decided in the component; a bound-but-retired
tool renders as an amber chip marked `(unavailable)` and stays removable.

## Guards

- `studio/src/components/agent/ToolsPicker.test.tsx` — `ToolsPicker — retired tools`:
  a deprecated tool is absent from the drawer; an `inactive` MCP tool is absent;
  and an already-bound deprecated tool still renders as a removable chip. All
  three go RED with the filter removed (verified by reverting the fix).
- `scripts/e2e/suite-84-mcp-tools.sh` — **T-S84-031** proves the API side:
  `?status=active` excludes a deprecated row while the row itself is still
  fetchable by name.

## Related

- [tool-picker-silently-truncates-at-200.md](tool-picker-silently-truncates-at-200.md) —
  the other catalog bug found in the same pass, fixed by the same `listAllTools()`.

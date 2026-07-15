# Python-type tools crash the agent pod at graph-build (KeyError('kwargs'))

**Status:** OPEN (pre-existing; surfaced by the E-1 no-fakes gate 2026-07-15, orthogonal to E-1).

## Symptom
An agent granted a **Python-type** tool (e.g. seeded `lookup_order`, `issue_refund`) CrashLoopBackOffs at pod startup. E-1's suite-72 works around it by using HTTP tools (`quarantine_action`/`refund_action` → in-cluster `/echo`).

## Root cause
`sdk/agentshield_sdk/graph_builder.py:316` — `lc_tool(governed)` wraps the tool in a `governed_tool(**kwargs)` closure whose VAR_KEYWORD param is un-annotated. LangChain's `create_schema_from_function` trips on the bare `**kwargs` → `KeyError('kwargs')` while building the tool schema → graph build fails → pod crashes.

## Fix (proposed, not yet applied)
Give the governed wrapper an explicit signature / typed schema derived from the underlying tool (or pass `infer_schema=False` with an explicit args schema) so `create_schema_from_function` doesn't see a bare `**kwargs`. Verify a Python-type tool agent deploys + runs a governed call.

## Notes
Orthogonal to Eval-v2 E-1 (E-1 scores real HTTP-tool trajectories). Recorded so the platform's default Python tools work end-to-end.

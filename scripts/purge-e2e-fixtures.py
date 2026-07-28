#!/usr/bin/env python3
"""One-time purge of accumulated e2e/test agents + tools from the registry DB.

WHY THIS EXISTS
---------------
The API `DELETE /agents/{name}` is a *soft* delete (status→deprecated) — correct
for production (audit + recovery), wrong for test teardown. Years of suites that
either soft-delete or create-and-abandon left ~430 fixture agents (384 deprecated
tombstones + active leftovers) tangled with the real agents, many wrongly
attributed to a real user's Keycloak sub because ~19 suites hardcoded it as a
literal `X-User-Sub`. This script removes the historical mess with the CORRECT
foreign-key cascade (the existing scripts/purge-test-agents.sh is broken — it
never clears workflow_members/workflow_edges and crashes on any workflow member).

SAFETY MODEL
------------
* DRY-RUN BY DEFAULT. It runs the real DELETE statements inside a transaction to
  validate the FK order and report true row counts, then ROLLS BACK. Pass
  `--execute` to COMMIT.
* KEEP is an explicit allowlist of the real agents. Nothing on it is ever touched.
* An agent is deleted only if it is NOT kept AND (matches a test-name pattern OR
  is already deprecated OR is on the explicit DELETE_EXTRA list). Anything active,
  unkept, and unmatched is left ALONE and reported under "UNCLASSIFIED" for review
  — the script never guesses on an active, real-looking agent.
* mcp-owned tools (deepwiki__/tavily__/github__ …) are always kept.

Run inside the registry-api pod (it has DATABASE_URL + asyncpg):
    kubectl -n <ns> exec -i <registry-api-pod> -c registry-api -- \
        python3 - < scripts/purge-e2e-fixtures.py [--execute] [--include-tools]
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

import asyncpg

# --- Explicit allowlist: the real agents. NEVER deleted. -------------------
KEEP_AGENTS = {
    # discussed / MCP demos / POC workflow
    "poc-researcher", "poc-answerer",
    "web-researcher", "github-agent", "repo-explainer",
    # bootstrap seed demos (created_by=system)
    "research-assistant", "calculator-bot", "slack-notifier", "echo-agent", "order-agent",
    # earlier demo persona (created_by=643b0e62…)
    "research-agent", "analyst-synthesizer", "news-announcements-researcher",
    "weather-assistant", "order-tracker", "simple-qa",
    # active demos kept by default (call out to veto)
    "wf-confirm", "wf-triage", "wf-supervisor", "wf-payout",
    "fraud-alert-triage", "refund-processor",
    "trigger-demo-a", "trigger-demo-b",
    # serper search agents — user asked to retain the whole family (incl. the
    # two deprecated ones, so they must be listed explicitly to beat the
    # deprecated→delete rule).
    "serper-agent", "serper-agent-1", "serper-agent-2",
    "serper-agent-3", "serper-agent-4", "serper-agent-5",
}

# Active, non-pattern review items explicitly marked as fixtures to delete.
# (serper-agent* is retained via KEEP_AGENTS; serper-search / websearch-agent
# are unrelated deprecated tombstones and still go.)
DELETE_EXTRA_AGENTS = re.compile(r"^(serper-search|websearch-agent|test-agent)(-|$)")

# Anchored test-fixture name patterns (suite-numbered, e2e-, checkpoint, etc.).
TEST_AGENT = re.compile(
    r"""^(
       s\d+([a-z-]|$)
      |e2e[-_]
      |cp\d
      |wsz-
      |wf[bf]-
      |wff?-(router|a$|b$)
      |smoke
      |hitl
      |grant
      |dep-(life|ovw)
      |ctrl-(test|e2e)
      |obs-
      |dbg
      |evt-test
      |event-pg
      |durable-pg
      |bare$
      |prod-run
      |mem-(test|nomem|probe)
      |sched-(test|pg)
      |alert-test
      |opa-gov
      |opa-s\d
      |high-risk-gate
      |crit-tool-gate
      |publish-test
      |eval-gate
      |pg-s\d
      |arg$
      |kwargs
      |dstream
      |syy$
      |whclient|whapp|whurl
      |p\d-(alpha|beta|final|work|a|b)
      |tmp-root
      |ver-test
      |verify-durable
      |shape-
      |driftb?
      |authprobe
      |del-(test|fix)
      |demo-hook
      |version-pin
      |web-search-(demo|test)
      |agent-(initiator|target)
    )""",
    re.X,
)

# Tool fixtures (mcp-owned tools are kept regardless — see query below).
TEST_TOOL = re.compile(
    r"""^(
       s\d+
      |e2e[-_]
      |opa-s\d
      |dbg\d?-refund
      |restricted-tool
      |http_echo
    )""",
    re.X,
)


def _n(status: str) -> int:
    """Parse asyncpg 'DELETE <n>' command tag → n."""
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0


async def purge_agents(conn, execute: bool) -> None:
    rows = await conn.fetch("SELECT id, name, status, created_by FROM agents")
    keep, delete, unclassified = [], [], []
    for r in rows:
        name = r["name"]
        if name in KEEP_AGENTS:
            keep.append(r)
        elif (TEST_AGENT.match(name) or DELETE_EXTRA_AGENTS.match(name)
              or re.search(r"-\d{9,}$", name)  # generic epoch/id suffix → fixture
              or r["status"] == "deprecated"):
            delete.append(r)
        else:
            unclassified.append(r)

    ids = [r["id"] for r in delete]
    names = [r["name"] for r in delete]
    print(f"\n=== AGENTS ===  keep={len(keep)}  delete={len(delete)}  unclassified={len(unclassified)}")
    if unclassified:
        print("  UNCLASSIFIED (active, unkept, no test-pattern — LEFT ALONE, review these):")
        for r in sorted(unclassified, key=lambda x: x["name"]):
            print(f"    - {r['name']:<34} {r['status']:<11} {(r['created_by'] or '')[:8]}")
    if not ids:
        print("  nothing to delete.")
        return

    tr = conn.transaction()
    await tr.start()
    counts = {}
    # children with NO cascade / name-only refs — clear before the parent delete.
    # run_steps has TWO blocking FKs: run_id → agent_runs AND approval_id → approvals,
    # so it must be cleared for both doomed runs and doomed approvals before either parent.
    counts["run_steps"] = _n(await conn.execute(
        "DELETE FROM run_steps WHERE run_id IN (SELECT id FROM agent_runs WHERE agent_name = ANY($1)) "
        "OR approval_id IN (SELECT id FROM approvals WHERE agent_id = ANY($2) OR agent_name = ANY($1))",
        names, ids))
    counts["eval_run_results"] = _n(await conn.execute(
        "DELETE FROM eval_run_results WHERE eval_run_id IN (SELECT id FROM eval_runs WHERE agent_name = ANY($1)) "
        "OR run_id IN (SELECT id FROM agent_runs WHERE agent_name = ANY($1))", names))
    counts["eval_runs"] = _n(await conn.execute("DELETE FROM eval_runs WHERE agent_name = ANY($1)", names))
    counts["agent_events"] = _n(await conn.execute(
        "DELETE FROM agent_events WHERE agent_name = ANY($1) "
        "OR run_id IN (SELECT id FROM agent_runs WHERE agent_name = ANY($1))", names))
    counts["agent_runs"] = _n(await conn.execute("DELETE FROM agent_runs WHERE agent_name = ANY($1)", names))
    counts["playground_runs"] = _n(await conn.execute("DELETE FROM playground_runs WHERE agent_name = ANY($1)", names))
    counts["opa_decisions"] = _n(await conn.execute("DELETE FROM opa_decisions WHERE agent_name = ANY($1)", names))
    counts["approvals"] = _n(await conn.execute(
        "DELETE FROM approvals WHERE agent_id = ANY($1) OR agent_name = ANY($2)", ids, names))
    counts["workflow_edges"] = _n(await conn.execute(
        "DELETE FROM workflow_edges WHERE source_agent_id = ANY($1) OR target_agent_id = ANY($1)", ids))
    counts["workflow_members"] = _n(await conn.execute("DELETE FROM workflow_members WHERE agent_id = ANY($1)", ids))
    # parent — cascades agent_versions, deployments, agent_policies, agent_tools,
    # agent_triggers, agent_identities(name), agent_knowledge_bindings.
    counts["agents"] = _n(await conn.execute("DELETE FROM agents WHERE id = ANY($1)", ids))

    print("  cascade row counts:")
    for t, n in counts.items():
        print(f"    {t:<18} {n}")

    if execute:
        await tr.commit()
        print(f"  COMMITTED — {counts['agents']} agents removed.")
    else:
        await tr.rollback()
        print(f"  DRY-RUN — rolled back (validated cascade for {counts['agents']} agents). Pass --execute to commit.")


async def purge_tools(conn, execute: bool) -> None:
    rows = await conn.fetch("SELECT id, name, status, mcp_server_id FROM tools")
    delete = [r for r in rows
              if r["mcp_server_id"] is None
              and (TEST_TOOL.match(r["name"]) or r["status"] == "deprecated")]
    ids = [r["id"] for r in delete]
    print(f"\n=== TOOLS ===  total={len(rows)}  delete={len(delete)}  (mcp-owned tools always kept)")
    if not ids:
        print("  nothing to delete.")
        return
    for r in sorted(delete, key=lambda x: x["name"])[:40]:
        print(f"    - {r['name']:<34} {r['status']}")
    if len(delete) > 40:
        print(f"    … +{len(delete) - 40} more")

    tr = conn.transaction()
    await tr.start()
    at = _n(await conn.execute("DELETE FROM agent_tools WHERE tool_id = ANY($1)", ids))
    tn = _n(await conn.execute("DELETE FROM tools WHERE id = ANY($1)", ids))
    print(f"  cascade: agent_tools={at}  tools={tn}")
    if execute:
        await tr.commit()
        print(f"  COMMITTED — {tn} tools removed.")
    else:
        await tr.rollback()
        print(f"  DRY-RUN — rolled back (validated {tn} tools). Pass --execute to commit.")


async def main() -> None:
    execute = "--execute" in sys.argv
    include_tools = "--include-tools" in sys.argv
    url = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(url)
    try:
        mode = "EXECUTE (COMMIT)" if execute else "DRY-RUN (rollback)"
        print(f"purge-e2e-fixtures — mode: {mode}")
        await purge_agents(conn, execute)
        if include_tools:
            await purge_tools(conn, execute)
        else:
            print("\n(tools skipped — pass --include-tools to purge test tools too)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

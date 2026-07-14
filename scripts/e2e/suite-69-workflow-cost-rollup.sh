#!/usr/bin/env bash
# scripts/e2e/suite-69-workflow-cost-rollup.sh
#
# E2E Suite 69: WORKFLOW COST ROLLUP (registry-api 0.2.176).
#
# A workflow PARENT run orchestrates members but makes no LLM calls itself, so its
# own Langfuse trace has no GENERATION cost — the leaf cost sweep can never cost it,
# and every workflow row showed Cost "—" even though its members were costed. The
# fix rolls the members' (children's) cost up onto the parent, in the cost-backfill
# sweep, once the children are themselves costed.
#
# Drives the REAL rollup function (cost_backfill._rollup_workflow_parents) against
# real agent_runs rows: seed a completed workflow parent (cost NULL) + two costed
# children (linked by parent_run_id), run the rollup, assert the parent's cost equals
# the exact sum. Also asserts a parent with an un-costed child is NOT rolled up (no
# partial sum). Cleans up its seeded rows.
#
#   T-S69-001 — parent cost_usd = sum(child cost_usd) after the rollup
#   T-S69-002 — a parent with an un-costed child is NOT rolled up (no partial sum)
#
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi
echo "=== Suite 69: workflow cost rollup ==="
echo "  Pod: $API_POD"; echo ""

DRIVER=/tmp/s69_driver.py
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from db import AsyncSessionLocal
from models import AgentRun, CompositeWorkflow
from cost_backfill import _rollup_workflow_parents


async def main():
    results = []
    async with AsyncSessionLocal() as s:
        wf = (await s.execute(select(CompositeWorkflow).limit(1))).scalars().first()
        assert wf is not None, "need at least one workflow for a valid workflow_id FK"
        old = datetime.now(timezone.utc) - timedelta(minutes=5)  # past the settle window

        # Case 1: parent + two fully-costed children -> rolls up to the exact sum.
        p1 = AgentRun(agent_name="s69-wf", context="production", status="completed",
                      team=wf.team, workflow_id=wf.id, completed_at=old,
                      langfuse_trace_id="s69-" + uuid.uuid4().hex[:8])
        s.add(p1)
        await s.flush()
        for cost in (0.000249, 0.000234):
            s.add(AgentRun(agent_name="s69-child", context="production", status="completed",
                           team=wf.team, parent_run_id=p1.id, cost_usd=cost, completed_at=old,
                           langfuse_trace_id="s69c-" + uuid.uuid4().hex[:8]))

        # Case 2: parent whose child is terminal but NOT yet costed (recent) -> no rollup.
        p2 = AgentRun(agent_name="s69-wf2", context="production", status="completed",
                      team=wf.team, workflow_id=wf.id, completed_at=old,
                      langfuse_trace_id="s69-" + uuid.uuid4().hex[:8])
        s.add(p2)
        await s.flush()
        s.add(AgentRun(agent_name="s69-child2", context="production", status="completed",
                       team=wf.team, parent_run_id=p2.id, cost_usd=None,
                       completed_at=datetime.now(timezone.utc),  # inside lookback, still pending
                       langfuse_trace_id="s69c-" + uuid.uuid4().hex[:8]))
        await s.commit()
        p1_id, p2_id = p1.id, p2.id

    rolled = await _rollup_workflow_parents()

    async with AsyncSessionLocal() as s:
        p1 = (await s.execute(select(AgentRun).where(AgentRun.id == p1_id))).scalars().first()
        p2 = (await s.execute(select(AgentRun).where(AgentRun.id == p2_id))).scalars().first()
        expected = round(0.000249 + 0.000234, 6)
        results.append(("T-S69-001 parent cost = sum(children)",
                        p1.cost_usd == expected, f"cost={p1.cost_usd} expected={expected} rolled={rolled}"))
        results.append(("T-S69-002 un-costed child blocks partial rollup",
                        p2.cost_usd is None, f"cost={p2.cost_usd} (must stay NULL)"))

    # Write results BEFORE cleanup so a cleanup hiccup can't hide the verdict.
    ok = sum(1 for _, b, _ in results if b)
    with open("/tmp/s69_out.txt", "w") as f:
        for name, b, d in results:
            f.write(f"{'PASS' if b else 'FAIL'}  {name}  |  {d}\n")
        f.write(f"SUMMARY {ok}/{len(results)}\n")

    # Cleanup: bulk-delete children (parent_run_id) THEN parents — the self-referential
    # FK requires children gone first; bulk DELETEs avoid the ORM's self-ref ordering.
    from sqlalchemy import delete as _delete
    async with AsyncSessionLocal() as s:
        await s.execute(_delete(AgentRun).where(AgentRun.parent_run_id.in_([p1_id, p2_id])))
        await s.commit()
        await s.execute(_delete(AgentRun).where(AgentRun.id.in_([p1_id, p2_id])))
        await s.commit()


asyncio.run(main())
PY

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -lc "cd /app && PYTHONPATH=/app python3 $DRIVER > /tmp/s69_run.log 2>&1 || true"
echo "=== Results ==="
RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat /tmp/s69_out.txt 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no result file — driver log:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat /tmp/s69_run.log 2>/dev/null | tail -30 || true
  exit 1
fi
echo "$RES"
if echo "$RES" | grep -q "FAIL"; then echo ""; echo "SUITE 69 FAILED"; exit 1; fi
echo ""; echo "SUITE 69 PASSED"

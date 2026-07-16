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

# Per-invocation paths (the suite-74 lesson): a fixed /tmp/s69_out.txt lets two
# overlapping invocations (a retry, a second operator, a CI re-run against the same pod)
# share a result file and silently read each OTHER's results. The result path was also
# previously a bare literal inside the driver with no bash-side variable at all — bash
# read /tmp/s69_out.txt by hand, so the two ends could drift independently.
RUN_TAG="$(date +%s)$$"
DRIVER="/tmp/s69_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s69_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s69_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, os, uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from db import AsyncSessionLocal
from models import AgentRun, CompositeWorkflow
from cost_backfill import _rollup_workflow_parents

OUT = os.environ["S69_OUT"]


async def main():
    results = []
    p1_id = p2_id = None
    try:
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

    except Exception as exc:
        # FAIL LOUD (the suite-74 lesson). Without this, a crash between the seed and the
        # asserts left NO result file and ALSO leaked the seeded agent_runs rows (cleanup
        # never ran). Now the crash is recorded as a real FAIL case and cleanup still runs.
        import traceback
        results.append(("T-S69-999 driver ran every case without crashing", False,
                        f"driver CRASHED mid-run — cases after this point never ran: "
                        f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}"))
    finally:
        # Write results BEFORE cleanup so a cleanup hiccup can't hide the verdict.
        ok = sum(1 for _, b, _ in results if b)
        with open(OUT, "w") as f:
            for name, b, d in results:
                f.write(f"{'PASS' if b else 'FAIL'}  {name}  |  {d}\n")
            f.write(f"SUMMARY {ok}/{len(results)}\n")

        # Cleanup: bulk-delete children (parent_run_id) THEN parents — the self-referential
        # FK requires children gone first; bulk DELETEs avoid the ORM's self-ref ordering.
        from sqlalchemy import delete as _delete
        seeded = [i for i in (p1_id, p2_id) if i]
        if seeded:
            try:
                async with AsyncSessionLocal() as s:
                    await s.execute(_delete(AgentRun).where(AgentRun.parent_run_id.in_(seeded)))
                    await s.commit()
                    await s.execute(_delete(AgentRun).where(AgentRun.id.in_(seeded)))
                    await s.commit()
            except Exception:
                pass


asyncio.run(main())
PY

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -lc \
  "cd /app && PYTHONPATH=/app S69_OUT=$OUTFILE python3 $DRIVER > $RUNLOG 2>&1 || true"
echo "=== Results ==="
RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no result file — driver log:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -30 "$RUNLOG" 2>/dev/null || true
  exit 1
fi

PASS=0; FAIL=0
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

# Completeness gate (the suite-74 lesson): a suite that silently stops early must NEVER
# read as green. FAIL=0 is only a pass if every gate assertion actually RAN — an
# exception, an early return, or a truncated result file otherwise produces "0 failures"
# on a half-run gate. REQUIRED_IDS is the ONE source of truth for "did the gate run in
# full"; a hardcoded case COUNT was tried alongside this in suite-74 and immediately
# drifted — and a count cannot say WHICH case vanished. Add a case here and nowhere else.
REQUIRED_IDS="001 002"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RES" | grep -q "T-S69-$id " || MISSING="$MISSING T-S69-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S69-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S69-COMPLETE every gate assertion ran (001-002, none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true

echo ""
echo "=== suite-69 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "SUITE 69 FAILED"; exit 1; fi
if [ "$PASS" -eq 0 ]; then echo "SUITE 69 INCONCLUSIVE (no assertions ran)"; exit 1; fi
echo "SUITE 69 PASSED"

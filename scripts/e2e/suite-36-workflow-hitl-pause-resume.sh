#!/usr/bin/env bash
# Suite 36: Deterministic HITL orchestrator pause-resume (no member pods, no OPA)
# Tests T-S36-001 through T-S36-004
#
# Exercises the workflow_orchestrator resume path entirely in-pod via direct
# ORM + function calls. NO live member pods or OPA bundle are required.
#
# What is proved:
#   T-S36-001 — migration 0032 applied: orchestrator_state JSONB column exists
#   T-S36-002 — resume_orchestration completes parent when next_index >= len(order)
#               (sequential, range(1,1) empty → _mark_parent("completed") fires
#               immediately — the deterministic no-dispatch path)
#   T-S36-003 — resume_orchestration marks parent failed when member_status='failed'
#   T-S36-004 — _clear_checkpoint(parent_run_id) sets orchestrator_state to None
#
# workflow_id FK: a minimal CompositeWorkflow row is created via ORM at test start
# (AgentRun.workflow_id → workflows.id, ondelete=SET NULL). No members are added
# since resume_orchestration only reads orchestrator_state from the parent run —
# it never touches the members table on the resume path exercised here.
#
# Usage: bash scripts/e2e/suite-36-workflow-hitl-pause-resume.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
WF_NAME="s36-wf-${TS}"
PARENT_AGENT="s36-parent-${TS}"
TEAM="platform"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""; echo "==> Cleanup..."
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<PY 2>/dev/null || true
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM agent_runs WHERE agent_name='${PARENT_AGENT}'"))
        await s.execute(text("DELETE FROM workflows WHERE name='${WF_NAME}'"))
        await s.commit()

asyncio.run(main())
PY
}
trap cleanup EXIT

echo "=== Suite 36: Workflow HITL Pause-Resume (Deterministic) ==="

# NOTE: unquoted heredoc — bash expands ${WF_NAME}, ${PARENT_AGENT}, ${TEAM};
# Python body uses no bare '$' beyond those substitutions.
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<PY 2>&1 | grep -v "Defaulted container" | tee /tmp/s36_out.txt
import asyncio, sys
import uuid as _uuid
from sqlalchemy import select, text
from db import AsyncSessionLocal
from models import AgentRun, CompositeWorkflow
from workflow_orchestrator import resume_orchestration, _clear_checkpoint

WF_NAME      = "${WF_NAME}"
PARENT_AGENT = "${PARENT_AGENT}"
TEAM         = "${TEAM}"
P = 0; F = 0

def ok(n):       global P; P+=1; print("  PASS:", n)
def bad(n, d=""): global F; F+=1; print("  FAIL:", n, d)


# ── Setup: create a minimal CompositeWorkflow to satisfy AgentRun.workflow_id FK.
# No members needed — resume_orchestration only reads orchestrator_state from the
# parent run; it never queries members on the sequential-resume-complete path.
async def mk_workflow():
    async with AsyncSessionLocal() as s:
        wf = CompositeWorkflow(
            name=WF_NAME, team=TEAM,
            orchestration="sequential", execution_shape="reactive",
        )
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        return str(wf.id)


# ── Helper: seed a parent AgentRun with a checkpoint (awaiting_approval).
async def mk_parent(wf_id_str, state):
    async with AsyncSessionLocal() as s:
        run = AgentRun(
            agent_name=PARENT_AGENT,
            status="awaiting_approval",
            context="production",
            trigger_type="workflow",
            team=TEAM,
            workflow_id=_uuid.UUID(wf_id_str),
            orchestrator_state=state,
            input="test input",
        )
        s.add(run)
        await s.commit()
        await s.refresh(run)
        return str(run.id)


# ── Helper: read back key fields via a fresh session after resume completes.
async def read_run(run_id_str):
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(AgentRun).where(AgentRun.id == run_id_str)
        )).scalar_one_or_none()
        if row:
            return dict(
                status=row.status,
                output=row.output,
                orchestrator_state=row.orchestrator_state,
                error_message=row.error_message,
            )
        return None


# All cases run inside ONE event loop. The module-level async engine pools
# connections bound to the loop of the first asyncio.run(); calling asyncio.run
# more than once reuses that pool from a new loop and raises "attached to a
# different loop". So everything lives in a single main().
async def main():
    # ── Create the shared workflow row (FK anchor for all test AgentRuns).
    wf_id = await mk_workflow()

    # ── T-S36-001 — migration 0032: orchestrator_state column exists in agent_runs
    async with AsyncSessionLocal() as s:
        r = await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='agent_runs' AND column_name='orchestrator_state'"
        ))
        col = r.fetchone()
    if col:
        ok("T-S36-001 orchestrator_state JSONB column present in agent_runs (migration 0032 applied)")
    else:
        bad("T-S36-001", "orchestrator_state column NOT found in agent_runs — migration 0032 not applied")

    # ── T-S36-002 — resume_orchestration completes parent when no members remain.
    #
    # Checkpoint: mode='sequential', order=['s36-m1'], next_index=1.
    # resume_orchestration calls _run_sequential_from(..., start_index=1, ...).
    # range(1, len(['s36-m1'])) == range(1, 1) is EMPTY → loop never runs →
    # _mark_parent("completed", "FINAL OUTPUT") fires immediately (no pod dispatch).
    pid2 = await mk_parent(wf_id, {
        "mode": "sequential", "order": ["s36-m1"], "next_index": 1,
        "team": TEAM, "workflow_id": wf_id,
    })
    await resume_orchestration(pid2, "FINAL OUTPUT", "completed")
    r2 = await read_run(pid2)
    if (r2
            and r2["status"] == "completed"
            and r2["output"] == "FINAL OUTPUT"
            and r2["orchestrator_state"] is None):
        ok("T-S36-002 resume completes: status=completed, output=FINAL OUTPUT, checkpoint=None")
    else:
        bad("T-S36-002", str(r2))

    # ── T-S36-003 — resume_orchestration marks parent failed when member_status='failed'.
    #
    # resume_orchestration checks member_status == failed first (before mode
    # dispatch) and calls _fail_parent, which sets status=failed, clears the
    # checkpoint, and sets error_message.
    pid3 = await mk_parent(wf_id, {
        "mode": "sequential", "order": ["s36-m1"], "next_index": 1,
        "team": TEAM, "workflow_id": wf_id,
    })
    await resume_orchestration(pid3, "", "failed")
    r3 = await read_run(pid3)
    if (r3
            and r3["status"] == "failed"
            and r3["orchestrator_state"] is None
            and r3["error_message"]):
        ok("T-S36-003 failed member → parent.status=failed, checkpoint=None, error_message set")
    else:
        bad("T-S36-003", str(r3))

    # ── T-S36-004 — _clear_checkpoint sets orchestrator_state to None.
    #
    # Direct call to the internal helper — proves the checkpoint clear primitive
    # used by both resume_orchestration and the non-sequential deferred path.
    pid4 = await mk_parent(wf_id, {"mode": "sequential", "test": "checkpoint-data"})
    await _clear_checkpoint(pid4)
    r4 = await read_run(pid4)
    if r4 and r4["orchestrator_state"] is None:
        ok("T-S36-004 _clear_checkpoint sets orchestrator_state to None")
    else:
        bad("T-S36-004", str(r4))


asyncio.run(main())
print("__RESULT__", P, F)
sys.exit(0)
PY

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s36_out.txt | tail -1 || true)
if [ -n "$RES" ]; then
  PASS=$(echo "$RES" | awk '{print $2}')
  FAIL=$(echo "$RES" | awk '{print $3}')
fi
echo ""
echo "==> Suite 36 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1

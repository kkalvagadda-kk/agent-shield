#!/usr/bin/env bash
# scripts/e2e/suite-60-single-agent-durable-hitl.sh
#
# E2E Suite 60: single-agent DURABLE HITL (T4), on the REAL pod path. NO fakes.
#
# suite-55 proved the T4 resume LOGIC with a MOCKED httpx — it never dispatched to
# a real pod, which is exactly why bug #11 (single-agent durable playground runs
# dispatched to a non-existent shared declarative-runner and died before step 1)
# stayed hidden. THIS suite drives the real path a user hits from the Playground:
#
#   POST /playground/runs {execution_shape: durable} → the agent's OWN pod /run →
#   real LLM → high-risk tool → HITL PARK → approve via the real decide →
#   _resume_and_advance (top-level-durable branch) re-drives the pod → COMPLETES.
#
# Requires wf-payout (a durable agent whose refund_action is high-risk) deployed.
#
#   T-S60-001 — wf-payout is deployed + running
#   T-S60-002 — a durable playground run reaches awaiting_approval (real dispatch → park)
#   T-S60-003 — exactly ONE approval is created at park (no duplicate — bug #10 guard)
#   T-S60-004 — after approve, the run RESUMES and reaches 'completed' (Command-resume, bug #6)
#   T-S60-005 — still exactly ONE approval after resume (resume replay does not mint a second)
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

# ${E2E_SUB} is the sub DECODED FROM the minted token (lib/e2e-auth.sh). Sourcing alone
# does not mint — the CALL does, and without it E2E_SUB expands to empty and every
# identity assertion silently compares against "".
[ -z "$API_POD" ] && { echo "ERROR: no registry-api pod"; exit 1; }
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"
e2e_install_pyauth "$NAMESPACE" "$API_POD"

echo "=== Suite 60: single-agent durable HITL (T4) — real pods, no fakes ==="
echo "  Pod: $API_POD"

# The driver's stderr is KEPT (2>&1) and the substitution failure is HANDLED.
# It used to read `RESULT=$(kubectl exec ... 2>/dev/null` with no `|| :`, so under
# `set -e` a driver that died aborted the suite after two echoes, printing nothing at
# all — exit 1 with no reason. That is the same "cannot fail visibly" shape as the six
# suites whose drivers never started; a suite that cannot report a failure cannot report
# a pass either.
set +e
RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env S60_ADMIN_SUB="$E2E_SUB" python3 - <<'PY' 2>&1
import asyncio, os, uuid, httpx
import sys as _sys; _sys.path.insert(0, "/tmp")
from e2e_auth import BearerAuth
from sqlalchemy import select, text
from db import AsyncSessionLocal
from models import Agent, Deployment, PlaygroundRun, Approval

BASE="http://localhost:8000/api/v1"
# Read from the ENVIRONMENT, not interpolated. This heredoc is QUOTED (`<<'PY'`), so
# bash never expanded the "${E2E_SUB}" that used to sit here — the header carried that
# literal 12-character string. Combined with the missing Bearer below, every call in
# this driver was ANONYMOUS, which is why the suite died at the first gated write.
ADMIN = os.environ["S60_ADMIN_SUB"]
H={"X-User-Sub":ADMIN,"X-User-Team":"platform"}
AGENT="wf-payout"

async def running(name):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Deployment.status).join(Agent, Agent.id==Deployment.agent_id)
                .where(Agent.name==name, Deployment.environment=="sandbox", Deployment.status=="running"))).first() is not None

async def approvals(rid):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Approval.id, Approval.status, Approval.version)
                .where(Approval.thread_id==str(rid)).order_by(Approval.created_at))).all()

async def run_status(rid):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(PlaygroundRun.status).where(PlaygroundRun.id==uuid.UUID(rid)))).scalar()

async def main():
    out={}
    c=httpx.AsyncClient(base_url=BASE, headers=H, auth=BearerAuth(), timeout=60, follow_redirects=True)
    out["001_wf_payout_running"]= await running(AGENT)
    if not out["001_wf_payout_running"]:
        out["_diag"]="wf-payout not running — deploy it first"; print_res(out); return

    r=await c.post("/playground/runs", json={"agent_name":AGENT,"input_payload":{"message":"refund $50 for order A1"},"execution_shape":"durable"})
    rid=(r.json() or {}).get("id") or (r.json() or {}).get("run_id")
    # Assert the run was CREATED. Without this the driver carried rid=None into
    # run_status() and died 5s later inside uuid.UUID(None) — a TypeError that names
    # the uuid module, three layers from "the POST was refused". An unchecked response
    # turns every upstream refusal (403, 404, 422) into the same unrelated crash.
    if rid is None:
        out["_diag"]=f"run create failed: HTTP {r.status_code} {r.text[:200]}"
        out["002_reached_awaiting_approval"]=False
        print_res(out); return

    # wait for park
    parked=False
    for _ in range(30):
        await asyncio.sleep(5)
        st=await run_status(rid); aps=await approvals(rid)
        if any(a[1]=="pending" for a in aps): parked=True; break
        if st in ("completed","failed"): break
    out["002_reached_awaiting_approval"]= parked
    at_park=await approvals(rid)
    out["003_single_approval_at_park"]= (len([a for a in at_park if a[1]=="pending"])==1 and len(at_park)==1)

    if parked:
        pend=[a for a in at_park if a[1]=="pending"][0]
        await c.patch(f"/approvals/{pend[0]}", json={"decision":"approved","version":pend[2],"reviewer_id":"s60"})
        done=None
        for _ in range(24):
            await asyncio.sleep(5)
            st=await run_status(rid)
            if st in ("completed","failed"): done=st; break
        out["004_resumed_to_completed"]= (done=="completed")
        after=await approvals(rid)
        out["005_no_duplicate_after_resume"]= (len(after)==1)
        out["_diag2"]=f"run={done} approvals_after={[a[1] for a in after]}"
    print_res(out)

def print_res(out):
    for k,v in out.items():
        if k.startswith("_"): print("DIAG",k,v)
        else: print(("PASS" if v else "FAIL"), k)

asyncio.run(main())
PY
)
DRIVER_RC=$?
set -e
echo "$RESULT"
echo ""
# A driver that never produced a verdict is a SUITE defect, not a pass and not an
# ordinary failure — say which. Previously this line did not exist and the `set -e`
# abort above swallowed the distinction entirely.
if [ "$DRIVER_RC" -ne 0 ] || ! echo "$RESULT" | grep -qE "^(PASS|FAIL) "; then
  echo "❌ Suite 60 DRIVER ERROR (rc=$DRIVER_RC) — no PASS/FAIL verdict was produced."
  exit 1
fi
if echo "$RESULT" | grep -q "FAIL"; then echo "❌ Suite 60 FAILED"; exit 1; fi
echo "✅ Suite 60 PASSED"

#!/usr/bin/env bash
# Suite 34: Composite workflow trigger CRUD + scheduled run resolution (Decision 22)
# Tests T-S34-001 through T-S34-004
#
# Proves the workflow trigger contract end-to-end:
#   - Schedule trigger persists input_payload; GET list returns workflow_id + payload
#   - Webhook trigger rotate-token returns a /hooks/workflow/ URL
#   - POST /internal/runs/start {workflow_id, trigger_type:schedule, trigger_id} creates
#     a parent workflow AgentRun whose trigger_payload is resolved from the trigger's
#     stored input_payload (no live deployment needed for the workflow path)
#   - DELETE trigger → GET list no longer includes it
#
# The workflow run path (_start_workflow_run in routers/internal.py) does NOT require a
# running deployment — it only requires the workflow to have at least one member. The run
# row is committed before the async orchestration task fires (and fails without live pods).
# We assert on the returned run object's trigger_payload.
#
# Usage: bash scripts/e2e/suite-34-workflow-triggers.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
AGENT="s34-mem-${TS}"
WF_NAME="s34-wf-${TS}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""; echo "==> Cleanup..."
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<PY 2>/dev/null || true
import asyncio, httpx
from sqlalchemy import text
from db import AsyncSessionLocal

async def main():
    c = httpx.Client(base_url="http://localhost:8000/api/v1", timeout=10,
                     headers={"X-User-Sub": "system"})
    # Archive workflow by name lookup
    try:
        wfs = c.get("/workflows").json()
        for wf in wfs:
            if wf["name"] == "${WF_NAME}":
                c.delete(f"/workflows/{wf['id']}")
                break
    except Exception:
        pass
    # Delete member agent
    try:
        c.delete("/agents/${AGENT}")
    except Exception:
        pass
    # Clean up agent_runs created under the workflow name
    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM agent_runs WHERE agent_name='${WF_NAME}'"))
        await s.commit()

asyncio.run(main())
PY
}
trap cleanup EXIT

echo "=== Suite 34: Workflow Triggers ==="

# NOTE: unquoted heredoc — bash expands ${AGENT}, ${WF_NAME}; Python body has no bare '$'.
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<PY 2>&1 | grep -v "Defaulted container" | tee /tmp/s34_out.txt
import httpx, sys

AGENT   = "${AGENT}"
WF_NAME = "${WF_NAME}"
TEAM    = "platform"
B = "http://localhost:8000/api/v1"; H = {"X-User-Sub": "system"}
c = httpx.Client(base_url=B, timeout=30, headers=H)
P = 0; F = 0

def ok(n):
    global P; P += 1; print("  PASS:", n)
def bad(n, d=""):
    global F; F += 1; print("  FAIL:", n, d)

# Setup: create member agent
r = c.post("/agents/", json={"name": AGENT, "team": TEAM,
                              "agent_type": "declarative", "execution_shape": "reactive"})
assert r.status_code == 201, f"setup agent: {r.text}"
agent_id = r.json()["id"]

# Setup: create composite workflow
r = c.post("/workflows", json={"name": WF_NAME, "team": TEAM,
                                "orchestration": "sequential", "execution_shape": "reactive"})
assert r.status_code == 201, f"setup workflow: {r.text}"
wf_id = r.json()["id"]

# Setup: add agent as member (required — /internal/runs/start 422s with no members)
r = c.post(f"/workflows/{wf_id}/members", json={"agent_id": agent_id})
assert r.status_code == 201, f"setup member: {r.text}"

# T-S34-001: create schedule trigger with input_payload → GET list returns workflow_id + payload
r = c.post(f"/workflows/{wf_id}/triggers", json={
    "trigger_type": "schedule", "cron_expression": "0 9 * * 1", "timezone": "UTC",
    "input_payload": {"message": "wf-scheduled-hello", "job": "q1"},
})
if r.status_code == 201:
    j = r.json()
    if str(j.get("workflow_id")) == wf_id and (j.get("input_payload") or {}).get("message") == "wf-scheduled-hello":
        ok("T-S34-001 create: workflow_id + input_payload correct in create response")
        sched_tid = j["id"]
    else:
        bad("T-S34-001 create", str(j)); sched_tid = None
    # Verify GET list also returns the trigger with workflow_id + input_payload
    lst = c.get(f"/workflows/{wf_id}/triggers").json()
    found = next((t for t in lst if t["id"] == sched_tid), None) if sched_tid else None
    if found and str(found.get("workflow_id")) == wf_id and (found.get("input_payload") or {}).get("job") == "q1":
        ok("T-S34-001 GET list: workflow_id + input_payload round-trip correct")
    else:
        bad("T-S34-001 GET list", str(found))
else:
    bad("T-S34-001", r.text); sched_tid = None

# T-S34-002: create webhook trigger → rotate-token returns a /hooks/workflow/ URL
r = c.post(f"/workflows/{wf_id}/triggers", json={"trigger_type": "webhook"})
if r.status_code == 201:
    wh_tid = r.json()["id"]
    r2 = c.post(f"/workflows/{wf_id}/triggers/{wh_tid}/rotate-token")
    j2 = r2.json() if r2.status_code == 200 else {}
    if r2.status_code == 200 and j2.get("token") and j2.get("webhook_url") and "/hooks/workflow/" in j2["webhook_url"]:
        ok("T-S34-002 rotate-token returns token + /hooks/workflow/ URL")
    else:
        bad("T-S34-002", f"status={r2.status_code} body={r2.text[:200]}")
else:
    bad("T-S34-002", r.text); wh_tid = None

# T-S34-003: POST /internal/runs/start {workflow_id, trigger_type:schedule, trigger_id}
#             → parent workflow run created; trigger_payload resolved from input_payload
if sched_tid:
    r = c.post("/internal/runs/start", json={
        "workflow_id": wf_id, "trigger_type": "schedule",
        "trigger_id": sched_tid, "run_by": "serviceaccount:scheduler",
    })
    if r.status_code in (200, 201):
        j = r.json()
        tp = j.get("trigger_payload") or {}
        if tp.get("message") == "wf-scheduled-hello":
            ok("T-S34-003 workflow run created; trigger_payload resolved from input_payload")
        else:
            bad("T-S34-003", f"status={j.get('status')} trigger_payload={tp}")
    else:
        bad("T-S34-003", f"{r.status_code}: {r.text[:300]}")
else:
    bad("T-S34-003", "no schedule trigger_id — skipping (T-S34-001 failed)")

# T-S34-004: DELETE webhook trigger → GET list no longer includes it
if wh_tid:
    r = c.delete(f"/workflows/{wf_id}/triggers/{wh_tid}")
    if r.status_code == 204:
        lst = c.get(f"/workflows/{wf_id}/triggers").json()
        if not any(t["id"] == wh_tid for t in lst):
            ok("T-S34-004 deleted trigger absent from GET list")
        else:
            bad("T-S34-004", "trigger still present after DELETE")
    else:
        bad("T-S34-004", f"DELETE returned {r.status_code}: {r.text[:100]}")
else:
    bad("T-S34-004", "no webhook trigger_id — skipping (T-S34-002 failed)")

print("__RESULT__", P, F)
sys.exit(0)
PY

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s34_out.txt | tail -1 || true)
if [ -n "$RES" ]; then PASS=$(echo "$RES" | awk '{print $2}'); FAIL=$(echo "$RES" | awk '{print $3}'); fi
echo ""
echo "==> Suite 34 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1

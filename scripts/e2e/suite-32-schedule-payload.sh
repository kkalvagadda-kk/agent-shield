#!/usr/bin/env bash
# Suite 32: Per-schedule input payload (Decision 24 follow-on)
# Tests T-S32-001 through T-S32-005
#
# Proves the scheduled input contract end-to-end:
#  - a schedule trigger persists an `input_payload` (create + update round-trip),
#  - webhook triggers have no input_payload,
#  - firing /internal/runs/start with ONLY a trigger_id resolves that trigger's
#    input_payload into the run's input (internal.py S2 wiring).
#
# The fire path 409s without a running deployment, so the suite seeds a
# throwaway agent_version + running deployments row via the ORM (no real pod —
# the run row is created with the resolved input BEFORE the async dispatch that
# then fails; we assert on the run row).
#
# Usage: bash scripts/e2e/suite-32-schedule-payload.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
AGENT="s32-agent-${TS}"

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
    try:
        httpx.Client(base_url="http://localhost:8000/api/v1", timeout=10,
                     headers={"X-User-Sub":"system"}).delete("/agents/${AGENT}")
    except Exception: pass
    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM agent_runs WHERE agent_name='${AGENT}'"))
        await s.commit()
asyncio.run(main())
PY
}
trap cleanup EXIT

echo "=== Suite 32: Per-schedule input payload ==="

# NOTE: unquoted heredoc — bash expands ${AGENT}; the Python body contains no '$'.
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<PY 2>&1 | grep -v "Defaulted container" | tee /tmp/s32_out.txt
import asyncio, httpx, sys
from sqlalchemy import select, text
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, AgentRun

AGENT = "${AGENT}"
B = "http://localhost:8000/api/v1"; H = {"X-User-Sub": "system"}
c = httpx.Client(base_url=B, timeout=30, headers=H)
P = 0; F = 0
def ok(n):
    global P; P += 1; print("  PASS:", n)
def bad(n, d=""):
    global F; F += 1; print("  FAIL:", n, d)

async def main():
    r = c.post("/agents/", json={"name": AGENT, "team": "platform", "agent_type": "declarative", "execution_shape": "reactive"})
    assert r.status_code == 201, f"setup agent: {r.text}"

    # T-S32-001: create schedule trigger WITH input_payload
    r = c.post(f"/agents/{AGENT}/triggers", json={
        "trigger_type": "schedule", "cron_expression": "0 9 * * 1", "timezone": "UTC",
        "input_payload": {"message": "scheduled-hello", "task": "q3"},
    })
    if r.status_code == 201 and (r.json().get("input_payload") or {}).get("message") == "scheduled-hello":
        ok("T-S32-001 schedule trigger stores input_payload"); tid = r.json()["id"]
    else:
        bad("T-S32-001", r.text); print("__RESULT__", P, F); return

    # T-S32-002: GET round-trips input_payload
    got = next((t for t in c.get(f"/agents/{AGENT}/triggers").json() if t["id"] == tid), {})
    ok("T-S32-002 input_payload round-trips on GET") if (got.get("input_payload") or {}).get("task") == "q3" else bad("T-S32-002", str(got.get("input_payload")))

    # T-S32-003: update input_payload
    c.patch(f"/agents/{AGENT}/triggers/{tid}", json={"input_payload": {"message": "updated-msg"}})
    got = next((t for t in c.get(f"/agents/{AGENT}/triggers").json() if t["id"] == tid), {})
    ok("T-S32-003 input_payload updatable") if (got.get("input_payload") or {}).get("message") == "updated-msg" else bad("T-S32-003", str(got.get("input_payload")))
    c.patch(f"/agents/{AGENT}/triggers/{tid}", json={"input_payload": {"message": "scheduled-hello"}})

    # T-S32-004: webhook trigger has no input_payload
    r = c.post(f"/agents/{AGENT}/triggers", json={"trigger_type": "webhook"})
    ok("T-S32-004 webhook trigger has null input_payload") if r.json().get("input_payload") is None else bad("T-S32-004", str(r.json().get("input_payload")))

    # Seed a running deployment so /internal doesn't 409.
    async with AsyncSessionLocal() as s:
        agent = (await s.execute(select(Agent).where(Agent.name == AGENT))).scalar_one()
        ver = AgentVersion(agent_id=agent.id, version_number=1)
        s.add(ver); await s.commit(); await s.refresh(ver)
        s.add(Deployment(agent_id=agent.id, version_id=ver.id, environment="production",
                         status="running", k8s_namespace="agents-platform"))
        await s.commit()

    # T-S32-005: fire with ONLY trigger_id → run.input resolves from trigger.input_payload
    r = c.post("/internal/runs/start", json={
        "agent_name": AGENT, "trigger_type": "schedule", "trigger_id": tid, "run_by": "serviceaccount:scheduler",
    })
    if r.status_code not in (200, 201):
        bad("T-S32-005", f"fire not accepted: {r.status_code} {r.text}")
    else:
        run_id = r.json()["id"]
        async with AsyncSessionLocal() as s:
            run = (await s.execute(select(AgentRun).where(AgentRun.id == run_id))).scalar_one()
            ok("T-S32-005 scheduled run input resolved from trigger payload") if run.input == "scheduled-hello" else bad("T-S32-005", f"run.input={run.input!r}")

    print("__RESULT__", P, F)

asyncio.run(main())
sys.exit(0)
PY

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s32_out.txt | tail -1 || true)
if [ -n "$RES" ]; then PASS=$(echo "$RES" | awk '{print $2}'); FAIL=$(echo "$RES" | awk '{print $3}'); fi
echo ""
echo "==> Suite 32 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1

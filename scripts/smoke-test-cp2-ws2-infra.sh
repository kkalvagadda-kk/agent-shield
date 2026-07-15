#!/usr/bin/env bash
# scripts/smoke-test-cp2-ws2-infra.sh
#
# WS-2 Checkpoint 2 — INFRA smoke (CP2b). Proves the CP2 deploy landed and the
# execution substrate is healthy for the behaviour gate (suite-70):
#
#   T-CP2B-001 — registry-api pods Running, none in CrashLoopBackOff
#   T-CP2B-002 — studio pods Running, none in CrashLoopBackOff
#   T-CP2B-003 — agent_triggers.approver_role column exists (Alembic 0062 landed)
#   T-CP2B-004 — a freshly-deployed daemon AGENT reaches a running pod (real deploy)
#   T-CP2B-005 — a daemon WORKFLOW over that running member is created + resolvable
#                (durable workflows orchestrate their DEPLOYED members in-process — they
#                get no own sandbox pod, mirroring suite-58; readiness = the member
#                deployment running + the workflow persisted with its member).
#
# Mirrors suite-58's deploy-wait. Real pods, real rows — no fakes.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-2 CP2b: infra smoke ==="
echo "  namespace: $NAMESPACE"
echo ""

_pods_health() {  # <label> -> prints "running=N crashloop=M"
  local label="$1"
  local json
  json=$(kubectl get pods -n "$NAMESPACE" -l "$label" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.status.phase}{";"}{range .status.containerStatuses[*]}{.state.waiting.reason}{","}{end}{"\n"}{end}' 2>/dev/null || true)
  local running crash
  running=$(echo "$json" | grep -c "=Running;" || true)
  crash=$(echo "$json" | grep -c "CrashLoopBackOff" || true)
  echo "$running $crash"
}

read -r R_RUN R_CRASH <<< "$(_pods_health app.kubernetes.io/name=registry-api)"
if [ "$R_RUN" -ge 1 ] && [ "$R_CRASH" -eq 0 ]; then
  ok "T-CP2B-001 registry-api pods healthy" "running=$R_RUN crashloop=$R_CRASH"
else
  bad "T-CP2B-001 registry-api pods healthy" "running=$R_RUN crashloop=$R_CRASH"
fi

read -r S_RUN S_CRASH <<< "$(_pods_health app.kubernetes.io/name=studio)"
if [ "$S_RUN" -ge 1 ] && [ "$S_CRASH" -eq 0 ]; then
  ok "T-CP2B-002 studio pods healthy" "running=$S_RUN crashloop=$S_CRASH"
else
  bad "T-CP2B-002 studio pods healthy" "running=$S_RUN crashloop=$S_CRASH"
fi

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  bad "T-CP2B-003/004/005" "no running registry-api pod to drive"
  echo ""; echo "=== CP2b summary: PASS=$PASS FAIL=$FAIL ==="; echo "CP2b INFRA SMOKE FAILED"; exit 1
fi
echo "  pod: $API_POD"

# ── T-CP2B-003 approver_role column exists ────────────────────────────────────
COL=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  'cd /app && PYTHONPATH=/app python3 - <<PY
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as s:
        r = await s.execute(text(
            "select data_type from information_schema.columns "
            "where table_name=:t and column_name=:c"),
            {"t": "agent_triggers", "c": "approver_role"})
        row = r.first()
        print(f"FOUND:{row[0]}" if row else "MISSING")
asyncio.run(main())
PY' 2>/dev/null | tr -d "[:space:]" || true)
if echo "$COL" | grep -q "^FOUND:"; then
  ok "T-CP2B-003 agent_triggers.approver_role column exists (0062)" "type=${COL#FOUND:}"
else
  bad "T-CP2B-003 agent_triggers.approver_role column exists (0062)" "result=$COL"
fi

# ── T-CP2B-004/005 — real daemon agent deploy + daemon workflow create ─────────
DRIVER=/tmp/cp2b_driver.py; OUTFILE=/tmp/cp2b_out.txt
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "rm -f $OUTFILE; cat > $DRIVER" <<'PY'
import asyncio, uuid, httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, Deployment, CompositeWorkflow, WorkflowMember

BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6", "X-User-Team": "platform"}
SFX = uuid.uuid4().hex[:6]
AGENT = f"cp2b-daemon-{SFX}"; WF = f"cp2b-wf-{SFX}"
INSTR = "You are an autonomous check agent. Reply with the single word READY."

async def prov(c):
    return (await c.get("/llm-providers/", params={"team": "platform"})).json()["items"][0]["id"]

async def wait_running(name, t=60):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
            if a:
                d = (await s.execute(select(Deployment).where(
                    Deployment.agent_id == a.id, Deployment.environment == "sandbox")
                    .order_by(desc(Deployment.deployed_at)).limit(1))).scalars().first()
                if d and d.status == "running":
                    return True
                if d and d.status == "failed":
                    return False
        await asyncio.sleep(3)
    return False

async def main():
    lines = []
    wid = None
    async with httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=90.0) as c:
        pid = await prov(c)
        await c.post("/agents/", json={"name": AGENT, "team": "platform", "agent_type": "declarative",
            "execution_shape": "durable", "agent_class": "daemon",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []}})
        await c.post(f"/agents/{AGENT}/deploy", json={"environment": "sandbox"})
        running = await wait_running(AGENT)
        lines.append(f"{'PASS' if running else 'FAIL'}  T-CP2B-004 daemon agent reaches running pod  |  agent={AGENT} running={running}")

        wf_ok = False; detail = f"agent not running ({running})"
        if running:
            wr = await c.post("/workflows", json={"name": WF, "team": "platform",
                "orchestration": "sequential", "execution_shape": "durable", "agent_class": "daemon"})
            if wr.status_code in (200, 201):
                wid = wr.json()["id"]
                aid = (await c.get(f"/agents/{AGENT}")).json()["id"]
                await c.post(f"/workflows/{wid}/members", json={"agent_id": aid, "position": 1})
                async with AsyncSessionLocal() as s:
                    w = (await s.execute(select(CompositeWorkflow).where(CompositeWorkflow.id == uuid.UUID(wid)))).scalars().first()
                    m = (await s.execute(select(WorkflowMember).where(WorkflowMember.workflow_id == uuid.UUID(wid)))).scalars().all()
                wf_ok = (w is not None and w.agent_class == "daemon" and len(m) == 1)
                detail = f"workflow={WF} class={getattr(w,'agent_class',None)} members={len(m)} (member deployment running)"
            else:
                detail = f"workflow create {wr.status_code}: {wr.text[:120]}"
        lines.append(f"{'PASS' if wf_ok else 'FAIL'}  T-CP2B-005 daemon workflow created over running member  |  {detail}")

        with open("/tmp/cp2b_out.txt", "w") as f:
            f.write("\n".join(lines) + "\n")
        try:
            if wid: await c.delete(f"/workflows/{wid}")
            await c.delete(f"/agents/{AGENT}")
        except Exception:
            pass

asyncio.run(main())
PY

echo "  running detached in-pod driver (deploy+wait ~1-2 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app nohup python3 $DRIVER > /tmp/cp2b_run.log 2>&1 & echo started"
for i in $(seq 1 48); do
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then break; fi
done
RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no driver result — last log:"; kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -20 /tmp/cp2b_run.log 2>/dev/null || true
  bad "T-CP2B-004/005 daemon agent + workflow" "driver did not report"
else
  while IFS= read -r line; do
    case "$line" in
      PASS*) echo "$line"; PASS=$((PASS+1)) ;;
      FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    esac
  done <<< "$RES"
fi

echo ""
echo "=== CP2b summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "CP2b INFRA SMOKE FAILED"; exit 1; fi
echo "CP2b INFRA SMOKE PASSED"

#!/usr/bin/env bash
# scripts/e2e/suite-68-daemon-no-input.sh
#
# E2E Suite 68: DAEMON / SCHEDULED runs with NO user input (Gap 3). NO fakes.
#
# A schedule (or webhook) can fire a run with no job spec at all — there is no live
# user and no message. Previously that produced an EMPTY user turn (HumanMessage
# content=""), which the LLM provider rejects (non-whitespace-empty content), so the
# run failed. The fix (declarative-runner 0.1.44) never builds an empty turn: when the
# resolved input is blank, the runner drives the graph with a clean daemon kickoff
# (daemon_kickoff_if_empty in workflow_executor.py; DAEMON_KICKOFF in the durable /run
# path). This is the SAME shared runner code the production scheduler exercises (see
# suite-66 for the scheduler firing) — here we prove the no-input handling itself.
#
# Real path: create a durable DAEMON agent -> deploy sandbox -> wait running ->
# POST /playground/runs with input_payload={} (NO message) -> the platform dispatches
# the runner's /run with an empty payload -> assert the run reaches status=completed
# (NOT failed with an empty-content error).
#
#   T-S68-001 — a durable daemon run with EMPTY input completes (kickoff, not 4xx/failed)
#
# Detached in-pod driver (PYTHONPATH=/app -> result file); polled with short execs.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi
echo "=== Suite 68: daemon/scheduled run with NO user input (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

DRIVER=/tmp/s68_driver.py; OUTFILE=/tmp/s68_out.txt
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, uuid, httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, Deployment, PlaygroundRun
BASE = "http://localhost:8000/api/v1"
H = {"X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6", "X-User-Team": "platform"}
SFX = uuid.uuid4().hex[:6]
NAME = f"s68-daemon-{SFX}"
INSTR = ("You are an autonomous check agent. When you run, reply with exactly the "
         "word READY and nothing else. There is no user to talk to.")

async def prov(c):
    return (await c.get("/llm-providers/", params={"team": "platform"})).json()["items"][0]["id"]

async def wait_running(name, t=60):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
            if a:
                d = (await s.execute(
                    select(Deployment).where(Deployment.agent_id == a.id,
                                             Deployment.environment == "sandbox")
                    .order_by(desc(Deployment.deployed_at)).limit(1)
                )).scalars().first()
                if d and d.status == "running":
                    return True
        await asyncio.sleep(3)
    return False

async def wait_run_terminal(run_id, t=80):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            r = (await s.execute(select(PlaygroundRun).where(PlaygroundRun.id == run_id))).scalars().first()
            if r and r.status in ("completed", "failed"):
                # PlaygroundRun has no error_message column; failure/answer detail
                # lives in output_text (step-level errors are on RunStep).
                return r.status, (r.output_text or "")
        await asyncio.sleep(3)
    return "timeout", ""

async def main():
    results = []
    async with httpx.AsyncClient(base_url=BASE, headers=H, timeout=30.0) as c:
        pid = await prov(c)
        # Durable DAEMON agent — the class that runs without a live user.
        r = await c.post("/agents/", json={
            "name": NAME, "team": "platform", "agent_type": "declarative",
            "execution_shape": "durable", "agent_class": "daemon",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []},
        })
        assert r.status_code in (200, 201), f"create agent: {r.status_code} {r.text[:200]}"
        await c.post(f"/agents/{NAME}/deploy", json={"environment": "sandbox"})
        assert await wait_running(NAME), "agent never reached running (sandbox)"

        # Fire a durable run with EMPTY input — no message, no job spec.
        run = await c.post("/playground/runs", json={
            "agent_name": NAME, "execution_shape": "durable", "input_payload": {},
        })
        # The create itself must NOT 4xx on missing input.
        results.append(("T-S68-001a create empty-input durable run accepted",
                        run.status_code in (200, 201), f"status={run.status_code} {run.text[:160]}"))
        run_id = run.json().get("run_id") if run.status_code in (200, 201) else None

        status, err = ("no-run", "")
        if run_id:
            status, err = await wait_run_terminal(run_id)
        # The run must COMPLETE — the empty turn used to fail the provider's
        # non-empty-content check; the daemon kickoff fixes it.
        ok = status == "completed"
        results.append(("T-S68-001 empty-input daemon run completes (kickoff)",
                        ok, f"terminal={status} err={err[:160]}"))

        # cleanup
        await c.delete(f"/agents/{NAME}")

    passed = sum(1 for _, ok, _ in results if ok)
    with open("/tmp/s68_out.txt", "w") as f:
        for name, ok, detail in results:
            f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
        f.write(f"SUMMARY {passed}/{len(results)}\n")

asyncio.run(main())
PY

echo "Running driver detached in-pod (deploy + empty-input run can take ~2 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app nohup python3 $DRIVER > /tmp/s68_run.log 2>&1 & echo started"

# Poll for the result file with short execs (survives long runs).
for i in $(seq 1 60); do
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    break
  fi
done

echo ""; echo "=== Results ==="
RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no result file — driver log:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat /tmp/s68_run.log 2>/dev/null | tail -30 || true
  exit 1
fi
echo "$RES"
if echo "$RES" | grep -q "FAIL"; then echo ""; echo "SUITE 68 FAILED"; exit 1; fi
echo ""; echo "SUITE 68 PASSED"

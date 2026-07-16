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

# Per-invocation paths (the suite-74 lesson): a fixed /tmp/s68_out.txt lets two
# overlapping invocations (a retry, a second operator, a CI re-run against the same pod)
# share a result file and silently read each OTHER's results.
RUN_TAG="$(date +%s)$$"
DRIVER="/tmp/s68_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s68_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s68_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, os, uuid, httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, Deployment, PlaygroundRun
BASE = "http://localhost:8000/api/v1"
H = {"X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6", "X-User-Team": "platform"}
OUT = os.environ["S68_OUT"]
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
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=30.0)
    try:
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

    except Exception as exc:
        # FAIL LOUD (the suite-74 lesson). Without this, a bare run writes only the cases
        # recorded BEFORE the crash and the bash summary (PASS>0, FAIL==0) reports the
        # suite GREEN while silently dropping every remaining case — a partial run must
        # never look like a pass.
        import traceback
        results.append(("T-S68-999 driver ran every case without crashing", False,
                        f"driver CRASHED mid-run — cases after this point never ran: "
                        f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}"))
    finally:
        # write results BEFORE cleanup (the suite-69 lesson), then tear down: a cleanup
        # hiccup (or a crash inside it) must not be able to swallow the verdict.
        passed = sum(1 for _, ok, _ in results if ok)
        with open(OUT, "w") as f:
            for name, ok, detail in results:
                f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")
        try:
            await c.delete(f"/agents/{NAME}")
        except Exception:
            pass
        await c.aclose()

asyncio.run(main())
PY

echo "Running driver detached in-pod (deploy + empty-input run can take ~2 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app S68_OUT=$OUTFILE nohup python3 $DRIVER > $RUNLOG 2>&1 & echo started"

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
# The trailing space in the grep is load-bearing: this suite has sub-lettered IDs, and a
# bare "T-S68-001" would also match "T-S68-001a" — so a crashed 001 would hide behind 001a.
REQUIRED_IDS="001a 001"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RES" | grep -q "T-S68-$id " || MISSING="$MISSING T-S68-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S68-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S68-COMPLETE every gate assertion ran (001a, 001 — none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true

echo ""
echo "=== suite-68 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "SUITE 68 FAILED"; exit 1; fi
if [ "$PASS" -eq 0 ]; then echo "SUITE 68 INCONCLUSIVE (no assertions ran)"; exit 1; fi
echo "SUITE 68 PASSED"

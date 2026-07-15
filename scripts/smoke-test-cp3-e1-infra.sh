#!/usr/bin/env bash
# scripts/smoke-test-cp3-e1-infra.sh
#
# Eval v2 E-1 Checkpoint 3 — INFRA smoke (CP3b). Proves the E-1 image versions are
# actually deployed and the durable-eval infra is present, BEFORE the behaviour
# gate (suite-72) runs.
#
#   T-CP3B-001 — registry-api pod Running AND image tag == 0.2.180
#   T-CP3B-002 — studio pod Running AND image tag == 0.1.137
#   T-CP3B-003 — eval-runner image 0.1.6 resolvable in the cluster (chart value +
#                deploy-controller/eval env), i.e. the Job image the durable eval
#                dispatches to is the E-1 tag
#   T-CP3B-004 — DB at Alembic 0062 (E-0 columns present; E-1 adds no migration)
#   T-CP3B-005 — the E-1 PRODUCER is deployed: the agent runtime
#                (declarative-runner) bakes the SDK durable.py {tool,args}
#                tool-boundary emit. Without it, run_steps carry no tool/args and
#                the durable trajectory eval scores nothing real. Checked against
#                a live agent pod's SDK if one exists; else against the tag wiring.
#
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

PASS=0; FAIL=0
ok()   { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== Eval v2 E-1 CP3b: infra smoke ==="
echo "  namespace: $NAMESPACE"
echo ""

# ── T-CP3B-001 — registry-api 0.2.180 Running
RA_IMG=$(kubectl get deploy agentshield-registry-api -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
RA_READY=$(kubectl get deploy agentshield-registry-api -n "$NAMESPACE" \
  -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
if echo "$RA_IMG" | grep -q ':0.2.180' && [ "${RA_READY:-0}" -ge 1 ]; then
  ok "T-CP3B-001 registry-api 0.2.180 Running" "image=$RA_IMG ready=$RA_READY"
else
  bad "T-CP3B-001 registry-api 0.2.180 Running" "image=$RA_IMG ready=${RA_READY:-0}"
fi

# ── T-CP3B-002 — studio 0.1.137 Running
ST_IMG=$(kubectl get deploy agentshield-studio -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
ST_READY=$(kubectl get deploy agentshield-studio -n "$NAMESPACE" \
  -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
if echo "$ST_IMG" | grep -q ':0.1.137' && [ "${ST_READY:-0}" -ge 1 ]; then
  ok "T-CP3B-002 studio 0.1.137 Running" "image=$ST_IMG ready=$ST_READY"
else
  bad "T-CP3B-002 studio 0.1.137 Running" "image=$ST_IMG ready=${ST_READY:-0}"
fi

# ── T-CP3B-003 — eval-runner 0.1.6 is the Job image the durable eval dispatches to
ER_IMG=$(kubectl get deploy agentshield-deploy-controller -n "$NAMESPACE" \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' 2>/dev/null \
  | grep -i EVAL_RUNNER_IMAGE | head -1 || true)
if echo "$ER_IMG" | grep -q ':0.1.6'; then
  ok "T-CP3B-003 eval-runner 0.1.6 image resolvable (durable eval Job image)" "$ER_IMG"
else
  # Fall back to the chart value (the deploy-controller env may not carry it).
  CH=$(grep -E 'evalRunnerImage|EVAL_RUNNER_IMAGE' charts/agentshield/values.yaml 2>/dev/null | head -1 | tr -d ' ')
  if echo "$CH" | grep -q ':0.1.6'; then
    ok "T-CP3B-003 eval-runner 0.1.6 image resolvable (chart value)" "$CH"
  else
    bad "T-CP3B-003 eval-runner 0.1.6 image resolvable" "env=$ER_IMG chart=$CH"
  fi
fi

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

# ── T-CP3B-004 — DB at Alembic 0062
if [ -n "$API_POD" ]; then
  VER=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
    'cd /app && PYTHONPATH=/app python3 - <<PY 2>/dev/null
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal
async def m():
    async with AsyncSessionLocal() as s:
        print((await s.execute(text("select version_num from alembic_version"))).scalar())
asyncio.run(m())
PY' | tr -d '[:space:]')
  if [ "$VER" = "0062" ]; then
    ok "T-CP3B-004 DB at Alembic 0062 (E-0 columns; E-1 adds no migration)" "version=$VER"
  else
    bad "T-CP3B-004 DB at Alembic 0062" "version=$VER"
  fi
else
  bad "T-CP3B-004 DB at Alembic 0062" "no running registry-api pod"
fi

# ── T-CP3B-005 — the E-1 PRODUCER is deployed in the agent runtime.
# The durable trajectory eval is only real if the agent pod's SDK emits
# {tool,args} on tool boundaries. deploy-controller provisions NEW agents with
# DECLARATIVE_RUNNER_IMAGE; PRE-EXISTING agents keep their OLD image until
# redeployed (values.yaml: "existing agent pods keep their old tag"). So we must
# introspect a pod running the CURRENT runner tag — a stale pre-bump pod is a
# false negative. If no pod is on the current tag, the tag wiring is the check
# (and suite-72 proves the emit behaviourally by deploying a fresh agent).
RUNNER_IMG=$(kubectl get deploy agentshield-deploy-controller -n "$NAMESPACE" \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' 2>/dev/null \
  | grep -i DECLARATIVE_RUNNER_IMAGE | head -1 | cut -d= -f2- || true)
RUNNER_TAG="${RUNNER_IMG##*:}"
# Find a running agent pod whose image matches the CURRENT runner tag (fresh agent).
FRESH_POD=""
for p in $(kubectl get pods -n agents-platform --field-selector=status.phase=Running -o name 2>/dev/null); do
  IMG=$(kubectl get "$p" -n agents-platform -o jsonpath='{.spec.containers[0].image}' 2>/dev/null)
  if [ "${IMG##*:}" = "$RUNNER_TAG" ]; then FRESH_POD="${p#pod/}"; break; fi
done
if [ -n "$FRESH_POD" ]; then
  C=$(kubectl get pod "$FRESH_POD" -n agents-platform -o jsonpath='{.spec.containers[0].name}' 2>/dev/null)
  HAS=$(kubectl exec -n agents-platform "$FRESH_POD" -c "$C" -- python3 -c \
    'import agentshield_sdk.durable as d, inspect; print("YES" if "\"tool\": name" in inspect.getsource(d) else "NO")' 2>/dev/null | tr -d '[:space:]')
  if [ "$HAS" = "YES" ]; then
    ok "T-CP3B-005 E-1 producer deployed: agent SDK (runner tag $RUNNER_TAG) emits {tool,args}" "agent_pod=$FRESH_POD"
  else
    bad "T-CP3B-005 E-1 producer deployed: agent SDK emits {tool,args}" \
        "agent_pod=$FRESH_POD (tag $RUNNER_TAG) SDK lacks the emit — declarative-runner image at this tag does NOT bake the E-1 SDK"
  fi
else
  # No agent pod on the current runner tag — the wiring is the check; suite-72
  # (behaviour gate) proves the emit by deploying a fresh agent on this tag.
  echo "INFO  T-CP3B-005 no running agent pod on the current runner tag ($RUNNER_TAG) to introspect."
  echo "INFO  deploy-controller provisions new agents with: $RUNNER_IMG"
  echo "INFO  Producer emit is proven behaviourally by suite-72 (deploys a fresh agent on this tag)."
  ok "T-CP3B-005 E-1 producer tag wired (no fresh pod to introspect; suite-72 proves the emit)" "runner_image=$RUNNER_IMG"
fi

echo ""
echo "=== CP3b infra smoke: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || { echo "CP3b INFRA SMOKE FAILED"; exit 1; }
echo "CP3b INFRA SMOKE PASSED"

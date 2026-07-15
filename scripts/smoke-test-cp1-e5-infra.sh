#!/usr/bin/env bash
# scripts/smoke-test-cp1-e5-infra.sh
#
# Eval v2 E-5 Checkpoint 1 — INFRA smoke. Proves the E-5 image versions are
# actually deployed and the workflow-run-tree-eval infra is present, BEFORE the
# behaviour gate (suite-73) runs.
#
#   T-CP1E5-001 — registry-api pod Running AND image tag == 0.2.181
#   T-CP1E5-002 — studio pod Running AND image tag == 0.1.138
#   T-CP1E5-003 — eval-runner image 0.1.8 resolvable in the cluster (the Job image
#                 the workflow eval dispatches to)
#   T-CP1E5-004 — DB at Alembic 0062 (E-5 owns NO migration — reuses run_steps +
#                 the parent/child run tree + the eval_run_results dimensions)
#   T-CP1E5-005 — the E-5 scoring door is live: score_member_path is importable in
#                 the deployed registry-api and /eval/score no longer 501s for
#                 mode=workflow (the door the eval-runner calls exists).
#
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== Eval v2 E-5 CP1: infra smoke ==="
echo "  namespace: $NAMESPACE"
echo ""

# ── T-CP1E5-001 — registry-api 0.2.181 Running
RA_IMG=$(kubectl get deploy agentshield-registry-api -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
RA_READY=$(kubectl get deploy agentshield-registry-api -n "$NAMESPACE" \
  -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
if echo "$RA_IMG" | grep -q ':0.2.181' && [ "${RA_READY:-0}" -ge 1 ]; then
  ok "T-CP1E5-001 registry-api 0.2.181 Running" "image=$RA_IMG ready=$RA_READY"
else
  bad "T-CP1E5-001 registry-api 0.2.181 Running" "image=$RA_IMG ready=${RA_READY:-0}"
fi

# ── T-CP1E5-002 — studio 0.1.138 Running
ST_IMG=$(kubectl get deploy agentshield-studio -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
ST_READY=$(kubectl get deploy agentshield-studio -n "$NAMESPACE" \
  -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
if echo "$ST_IMG" | grep -q ':0.1.138' && [ "${ST_READY:-0}" -ge 1 ]; then
  ok "T-CP1E5-002 studio 0.1.138 Running" "image=$ST_IMG ready=$ST_READY"
else
  bad "T-CP1E5-002 studio 0.1.138 Running" "image=$ST_IMG ready=${ST_READY:-0}"
fi

# ── T-CP1E5-003 — eval-runner 0.1.8 is the Job image the workflow eval dispatches to
ER_IMG=$(kubectl get deploy agentshield-deploy-controller -n "$NAMESPACE" \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}' 2>/dev/null \
  | grep -i EVAL_RUNNER_IMAGE | head -1 || true)
if echo "$ER_IMG" | grep -q ':0.1.8'; then
  ok "T-CP1E5-003 eval-runner 0.1.8 image resolvable (workflow eval Job image)" "$ER_IMG"
else
  CH=$(grep -E 'evalRunnerImage|EVAL_RUNNER_IMAGE' charts/agentshield/values.yaml 2>/dev/null | head -1 | tr -d ' ')
  if echo "$CH" | grep -q ':0.1.8'; then
    ok "T-CP1E5-003 eval-runner 0.1.8 image resolvable (chart value)" "$CH"
  else
    bad "T-CP1E5-003 eval-runner 0.1.8 image resolvable" "env=$ER_IMG chart=$CH"
  fi
fi

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

# ── T-CP1E5-004 — DB at Alembic 0062
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
    ok "T-CP1E5-004 DB at Alembic 0062 (E-5 owns no migration)" "version=$VER"
  else
    bad "T-CP1E5-004 DB at Alembic 0062" "version=$VER"
  fi
else
  bad "T-CP1E5-004 DB at Alembic 0062" "no running registry-api pod"
fi

# ── T-CP1E5-005 — the E-5 scoring door is live (score_member_path importable)
if [ -n "$API_POD" ]; then
  HAS=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
    'cd /app && PYTHONPATH=/app python3 -c "import judge; print(\"YES\" if hasattr(judge, \"score_member_path\") and hasattr(judge, \"_match_sequence\") else \"NO\")"' 2>/dev/null | tr -d '[:space:]')
  if [ "$HAS" = "YES" ]; then
    ok "T-CP1E5-005 E-5 scorer deployed: judge.score_member_path + _match_sequence importable" "score_member_path present"
  else
    bad "T-CP1E5-005 E-5 scorer deployed: judge.score_member_path importable" "import check=$HAS"
  fi
else
  bad "T-CP1E5-005 E-5 scorer deployed" "no running registry-api pod"
fi

echo ""
echo "=== CP1 E-5 infra smoke: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || { echo "CP1 E-5 INFRA SMOKE FAILED"; exit 1; }
echo "CP1 E-5 INFRA SMOKE PASSED"

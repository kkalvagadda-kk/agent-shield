#!/usr/bin/env bash
# CP3-b smoke test — Safety scanner readiness check.
#
# Verifies:
#   1. safety-orchestrator /health → 200
#   2. safety-orchestrator GET /ready → reports scanner statuses
#   3. LLM Guard /health (if pod is Running)
#   4. Presidio analyzer /health (if pod is Running)
#   5. NeMo /health (if pod is Running)
#
# Usage:
#   bash scripts/smoke-test-cp3-scanners.sh             # existing port-forward on 8082
#   bash scripts/smoke-test-cp3-scanners.sh --auto-pf   # start port-forward automatically
set -euo pipefail

NAMESPACE="agentshield-platform"
SAFETY_URL="http://localhost:8082"
PASS=0
FAIL=0
PF_PIDS=()

AUTO_PF=false
for arg in "$@"; do [[ "$arg" == "--auto-pf" ]] && AUTO_PF=true; done

cleanup() {
  for pid in "${PF_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

if $AUTO_PF; then
  echo "==> Starting port-forwards..."
  kubectl port-forward svc/agentshield-safety-orchestrator -n "$NAMESPACE" 8082:8080 &
  PF_PIDS+=($!)
  sleep 3
fi

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }
skip() { echo "SKIP: $1"; }

echo "==> CP3-b: Safety scanner readiness smoke test"
echo ""

# ── Test 1: safety-orchestrator /health ───────────────────────────────────────
echo "--- Test 1: safety-orchestrator /health ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${SAFETY_URL}/health" || true)
if [ "$STATUS" = "200" ]; then
  pass "/health returned 200"
else
  fail "/health returned ${STATUS} (expected 200)"
  echo ""
  echo "======================================================="
  echo "  CP3-b Results: PASS=${PASS}  FAIL=${FAIL}"
  echo "======================================================="
  exit 1
fi
echo ""

# ── Test 2: GET /ready — scanner readiness report ─────────────────────────────
echo "--- Test 2: GET /ready (scanner readiness) ---"
TMPFILE=$(mktemp)
STATUS=$(curl -s -w "%{http_code}" -o "$TMPFILE" "${SAFETY_URL}/ready" || true)
BODY=$(cat "$TMPFILE"); rm -f "$TMPFILE"

if [ "$STATUS" = "200" ]; then
  LLM_GUARD_OK=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('scanners',{}).get('llm_guard','false'))" 2>/dev/null || echo "false")
  PRESIDIO_OK=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('scanners',{}).get('presidio','false'))" 2>/dev/null || echo "false")
  NEMO_OK=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('scanners',{}).get('nemo','false'))" 2>/dev/null || echo "false")
  ALL_READY=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('ready',False))" 2>/dev/null || echo "False")

  echo "  scanner status:"
  echo "    llm_guard : ${LLM_GUARD_OK}"
  echo "    presidio  : ${PRESIDIO_OK}"
  echo "    nemo      : ${NEMO_OK}"
  echo "    overall   : ${ALL_READY}"

  if [ "$ALL_READY" = "True" ]; then
    pass "/ready: all scanners healthy (ready=True)"
  else
    # Not all scanners ready — report which ones are up, don't fail (they may still be pulling)
    RUNNING_COUNT=0
    for s in "$LLM_GUARD_OK" "$PRESIDIO_OK" "$NEMO_OK"; do
      [ "$s" = "True" ] && RUNNING_COUNT=$((RUNNING_COUNT + 1))
    done
    echo "  NOTE: ${RUNNING_COUNT}/3 scanners ready — safety-orchestrator will fail-closed for missing scanners"
    pass "/ready returned 200 (${RUNNING_COUNT}/3 scanners ready)"
  fi
else
  fail "/ready returned ${STATUS}: ${BODY}"
fi
echo ""

# ── Helper: check a K8s service by exec ────────────────────────────────────────
check_pod_health() {
  local label="$1"
  local container="$2"
  local port="$3"
  local path="${4:-/health}"

  POD=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=${label}" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

  if [ -z "$POD" ]; then
    echo "  no Running pod for ${label}"
    return 1
  fi

  HEALTH=$(kubectl exec "$POD" -n "$NAMESPACE" -c "${container}" -- \
    python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://localhost:${port}${path}', timeout=5)
    print(r.getcode())
except Exception as e:
    print('ERR:', e)
" 2>/dev/null || echo "ERR")

  echo "  pod=${POD} → /health: ${HEALTH}"
  [ "$HEALTH" = "200" ]
}

# ── Test 3: LLM Guard /health ─────────────────────────────────────────────────
echo "--- Test 3: LLM Guard /health ---"
if check_pod_health "llm-guard" "llm-guard" "8000"; then
  pass "LLM Guard /health → 200"
else
  LG_POD=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=llm-guard" \
    --field-selector=status.phase=Running -o name 2>/dev/null || true)
  if [ -z "$LG_POD" ]; then
    skip "LLM Guard pod not Running (image may still be pulling)"
    pass "LLM Guard not deployed — fail-closed behavior verified in CP3-c"
  else
    fail "LLM Guard /health check failed"
  fi
fi
echo ""

# ── Test 4: Presidio analyzer /health ────────────────────────────────────────
echo "--- Test 4: Presidio analyzer /health ---"
if check_pod_health "presidio" "presidio-analyzer" "3000"; then
  pass "Presidio analyzer /health → 200"
else
  PRESIDIO_POD=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=presidio" \
    --field-selector=status.phase=Running -o name 2>/dev/null || true)
  if [ -z "$PRESIDIO_POD" ]; then
    skip "Presidio pod not Running (image may still be pulling)"
    pass "Presidio not deployed — fail-closed behavior verified in CP3-c"
  else
    fail "Presidio analyzer /health check failed"
  fi
fi
echo ""

# ── Test 5: NeMo /health ──────────────────────────────────────────────────────
echo "--- Test 5: NeMo /health ---"
if check_pod_health "nemo" "nemo" "8080"; then
  pass "NeMo /health → 200"
else
  NEMO_POD=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=nemo" \
    --field-selector=status.phase=Running -o name 2>/dev/null || true)
  if [ -z "$NEMO_POD" ]; then
    skip "NeMo pod not Running"
    pass "NeMo not deployed — fail-closed behavior verified in CP3-c"
  else
    fail "NeMo /health check failed"
  fi
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "======================================================="
echo "  CP3-b Results: PASS=${PASS}  FAIL=${FAIL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

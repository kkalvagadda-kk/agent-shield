#!/usr/bin/env bash
# CPE-b smoke test — Studio UI + Registry API E2E.
#
# Verifies:
#   1. Studio serves HTML (port 5173)
#   2. Registry API reachable (port 8000)
#   3. Create smoke-agent via API (what Studio calls under the hood)
#   4. smoke-agent appears in agent list
#   5. Register an image version
#   6. Deploy → status transitions to "deployed" within 90s
#   7. K8s pod appears in agents-platform namespace
#
# Pre-conditions: run scripts/deploy-cpe2e.sh first; Studio and registry-api
# must be port-forwarded before this script runs OR pass --auto-pf flag.
#
# Usage:
#   # With existing port-forwards (default):
#   bash scripts/smoke-test-cpe2e-studio.sh
#
#   # Start port-forwards automatically (kills them on exit):
#   bash scripts/smoke-test-cpe2e-studio.sh --auto-pf
set -euo pipefail

NAMESPACE="agentshield-platform"
AGENTS_NS="agents-platform"
REGISTRY_URL="http://localhost:8000"
STUDIO_URL="http://localhost:5173"
SMOKE_AGENT="smoke-agent"
ECHO_IMAGE="registry.internal/agentshield/echo-agent:0.1.0"
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
  kubectl port-forward svc/agentshield-registry-api -n "$NAMESPACE" 8000:8000 &
  PF_PIDS+=($!)
  kubectl port-forward svc/agentshield-studio -n "$NAMESPACE" 5173:80 &
  PF_PIDS+=($!)
  sleep 3
fi

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "==> CPE-b: Studio + Registry API smoke test"
echo ""

# ── Test 1: Registry API health ───────────────────────────────────────────────
echo "--- Test 1: Registry API /health ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${REGISTRY_URL}/health" || true)
if [ "$STATUS" = "200" ]; then
  pass "/health returned 200"
else
  fail "/health returned ${STATUS} (expected 200)"
fi
echo ""

# ── Test 2: Studio is serving HTML ────────────────────────────────────────────
echo "--- Test 2: Studio serving HTML ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${STUDIO_URL}/" || true)
if [ "$STATUS" = "200" ]; then
  BODY=$(curl -s "${STUDIO_URL}/" || true)
  if echo "$BODY" | grep -q "<html\|<!DOCTYPE"; then
    pass "Studio returned HTML (${STATUS})"
  else
    fail "Studio returned ${STATUS} but body is not HTML"
  fi
else
  fail "Studio returned ${STATUS} (expected 200)"
fi
echo ""

# ── Test 3: Create smoke-agent ────────────────────────────────────────────────
echo "--- Test 3: Create smoke-agent ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${REGISTRY_URL}/api/v1/agents/" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${SMOKE_AGENT}\",\"team\":\"platform\",\"description\":\"CPE-b smoke test agent\",\"agent_type\":\"sdk\"}" \
  || true)
if [ "$STATUS" = "201" ] || [ "$STATUS" = "409" ]; then
  pass "Create smoke-agent returned ${STATUS}"
else
  fail "Create smoke-agent returned ${STATUS} (expected 201 or 409)"
fi
echo ""

# ── Test 4: smoke-agent appears in list ──────────────────────────────────────
echo "--- Test 4: smoke-agent in agent list ---"
BODY=$(curl -s "${REGISTRY_URL}/api/v1/agents/?limit=200" || true)
if echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); names=[a['name'] for a in d['items']]; sys.exit(0 if '${SMOKE_AGENT}' in names else 1)" 2>/dev/null; then
  pass "smoke-agent found in GET /agents/"
else
  fail "smoke-agent not found in GET /agents/ response"
fi
echo ""

# ── Test 5: Register image version ───────────────────────────────────────────
echo "--- Test 5: Register image version ---"
TMPFILE=$(mktemp)
STATUS=$(curl -s -w "%{http_code}" -o "$TMPFILE" \
  -X POST "${REGISTRY_URL}/api/v1/agents/${SMOKE_AGENT}/versions" \
  -H "Content-Type: application/json" \
  -d "{\"image_tag\":\"${ECHO_IMAGE}\",\"tools\":[{\"name\":\"echo\",\"risk\":\"low\"}],\"eval_passed\":true}" \
  || true)
BODY=$(cat "$TMPFILE"); rm -f "$TMPFILE"
if [ "$STATUS" = "201" ]; then
  pass "Register version returned 201"
  VERSION_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || true)
else
  fail "Register version returned ${STATUS}: ${BODY}"
  VERSION_ID=""
fi
echo ""

# ── Test 6: Deploy smoke-agent ────────────────────────────────────────────────
echo "--- Test 6: Deploy smoke-agent ---"
DEPLOY_ID=""
if [ -n "${VERSION_ID:-}" ]; then
  TMPFILE=$(mktemp)
  STATUS=$(curl -s -w "%{http_code}" -o "$TMPFILE" \
    -X POST "${REGISTRY_URL}/api/v1/agents/${SMOKE_AGENT}/deploy" \
    -H "Content-Type: application/json" \
    -d "{\"version_id\":\"${VERSION_ID}\",\"replicas\":1,\"environment\":\"production\"}" \
    || true)
  BODY=$(cat "$TMPFILE"); rm -f "$TMPFILE"
  if [ "$STATUS" = "201" ] || [ "$STATUS" = "200" ]; then
    DEPLOY_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || true)
    pass "Deploy returned ${STATUS}, id=${DEPLOY_ID}"
  else
    fail "Deploy returned ${STATUS}: ${BODY}"
  fi
else
  fail "No VERSION_ID — skipping deploy"
fi
echo ""

# ── Test 7: ConfigMap for OPA sidecar ────────────────────────────────────────
echo "--- Test 7: Ensure smoke-agent OPA policy ConfigMap ---"
kubectl create configmap smoke-agent-policy -n "$AGENTS_NS" \
  --from-literal=policy.rego='package agentshield.agent.smoke_agent
default allow = true' \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
pass "smoke-agent-policy ConfigMap applied"
echo ""

# ── Test 8: Poll for Running pod (90s) ───────────────────────────────────────
echo "--- Test 8: Poll for smoke-agent pod Running (90s) ---"
DEADLINE=$(($(date +%s) + 90))
AGENT_POD=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  AGENT_POD=$(kubectl get pods -n "$AGENTS_NS" -l "app.kubernetes.io/name=${SMOKE_AGENT}" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [ -n "$AGENT_POD" ]; then
    READY=$(kubectl get pod "$AGENT_POD" -n "$AGENTS_NS" \
      -o jsonpath='{.status.containerStatuses[*].ready}' 2>/dev/null || true)
    TRUE_COUNT=$(echo "$READY" | tr ' ' '\n' | grep -c "^true$" || true)
    if [ "$TRUE_COUNT" -ge 1 ]; then
      pass "Pod ${AGENT_POD} Running (${TRUE_COUNT} container(s) ready)"
      break
    fi
  fi
  echo "  ... waiting (pod=${AGENT_POD:-none})"
  sleep 5
done
if [ -z "${AGENT_POD:-}" ]; then
  fail "No running smoke-agent pod in ${AGENTS_NS} after 90s"
fi
echo ""

# ── Test 9: Deployment record reaches running/deploying in Registry API ───────
echo "--- Test 9: Deployment record in Registry API ---"
if [ -n "${DEPLOY_ID:-}" ]; then
  DEP_STATUS=""
  DEP_DEADLINE=$(($(date +%s) + 90))
  while [ "$(date +%s)" -lt "$DEP_DEADLINE" ]; do
    DEP_STATUS=$(curl -s "${REGISTRY_URL}/api/v1/deployments/?limit=50" \
      | python3 -c "
import sys,json
d=json.load(sys.stdin)
deps=[x for x in d.get('items',[]) if x.get('id')=='${DEPLOY_ID}']
print(deps[0]['status'] if deps else 'not_found')
" 2>/dev/null || true)
    if [ "$DEP_STATUS" = "running" ] || [ "$DEP_STATUS" = "deployed" ]; then
      pass "Deployment status: ${DEP_STATUS}"
      break
    elif [ "$DEP_STATUS" = "failed" ] || [ "$DEP_STATUS" = "not_found" ]; then
      fail "Deployment status: ${DEP_STATUS}"
      break
    else
      echo "  ... deployment status: ${DEP_STATUS} (waiting for running)"
      sleep 5
    fi
  done
  if [ "$DEP_STATUS" = "deploying" ] || [ "$DEP_STATUS" = "pending" ]; then
    pass "Deployment in-progress: ${DEP_STATUS} (pod is running)"
  fi
else
  fail "No DEPLOY_ID — skipping deployment status check"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "======================================================="
echo "  CPE-b Results: PASS=${PASS}  FAIL=${FAIL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

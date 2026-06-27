#!/usr/bin/env bash
# CP2b smoke test — verify Deploy Controller deploys echo-agent with OPA sidecar.
set -euo pipefail

NAMESPACE="agentshield-platform"
AGENTS_NS="agents-platform"
RELEASE="agentshield"
ECHO_AGENT_IMAGE="registry.internal/agentshield/echo-agent:0.1.0"
PASS=0
FAIL=0

echo "==> CP2b smoke test: Deploy Controller + echo-agent"
echo ""

# ── Locate registry-api pod ───────────────────────────────────────────────────
REGISTRY_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

echo "Registry-API pod: ${REGISTRY_POD}"
echo ""

# ── Helper: run python3 snippet in registry-api pod ──────────────────────────
# We base64-encode request bodies to avoid shell quoting issues.
run_api() {
  local method="$1"
  local path="$2"
  local body_b64="${3:-}"

  local script
  if [ -n "$body_b64" ]; then
    script=$(cat <<PYEOF
import urllib.request, urllib.error, base64, json, sys
body = base64.b64decode("${body_b64}")
req = urllib.request.Request(
    "http://localhost:8000${path}",
    data=body,
    headers={"Content-Type": "application/json"},
    method="${method}"
)
try:
    r = urllib.request.urlopen(req)
    print(r.getcode())
    print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PYEOF
)
  else
    script=$(cat <<PYEOF
import urllib.request, urllib.error, sys
req = urllib.request.Request(
    "http://localhost:8000${path}",
    method="${method}"
)
try:
    r = urllib.request.urlopen(req)
    print(r.getcode())
    print(r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PYEOF
)
  fi

  kubectl exec "$REGISTRY_POD" -n "$NAMESPACE" -- python3 -c "$script"
}

# ── Test 1: Register agent ────────────────────────────────────────────────────
echo "--- Test 1: Register echo-agent ---"
AGENT_BODY='{"name":"echo-agent","team":"platform","description":"CP2 smoke test echo agent","agent_type":"sdk"}'
AGENT_BODY_B64=$(printf '%s' "$AGENT_BODY" | base64)
RESULT=$(run_api "POST" "/api/v1/agents/" "$AGENT_BODY_B64")
STATUS=$(echo "$RESULT" | head -1)
if [ "$STATUS" = "201" ] || [ "$STATUS" = "409" ]; then
  echo "PASS: Register agent returned ${STATUS}"
  PASS=$((PASS + 1))
else
  echo "FAIL: Register agent returned ${STATUS}"
  echo "$RESULT"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Test 2: Register version ──────────────────────────────────────────────────
echo "--- Test 2: Register echo-agent version ---"
VERSION_BODY="{\"image_tag\":\"${ECHO_AGENT_IMAGE}\",\"tools\":[{\"name\":\"echo\",\"risk\":\"low\"}],\"eval_passed\":true}"
VERSION_BODY_B64=$(printf '%s' "$VERSION_BODY" | base64)
RESULT=$(run_api "POST" "/api/v1/agents/echo-agent/versions" "$VERSION_BODY_B64")
STATUS=$(echo "$RESULT" | head -1)
if [ "$STATUS" = "201" ]; then
  echo "PASS: Register version returned ${STATUS}"
  PASS=$((PASS + 1))
else
  echo "FAIL: Register version returned ${STATUS}"
  echo "$RESULT"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Test 3: Get version ID ────────────────────────────────────────────────────
echo "--- Test 3: Get version ID ---"
RESULT=$(run_api "GET" "/api/v1/agents/echo-agent/versions")
STATUS=$(echo "$RESULT" | head -1)
if [ "$STATUS" = "200" ]; then
  VERSION_BODY_RESP=$(echo "$RESULT" | tail -n +2)
  VERSION_ID=$(kubectl exec "$REGISTRY_POD" -n "$NAMESPACE" -- python3 -c \
    "import json,sys; data=json.loads('''${VERSION_BODY_RESP}'''); print(data[0]['id'])" 2>/dev/null || true)
  if [ -n "$VERSION_ID" ]; then
    echo "PASS: Got version ID: ${VERSION_ID}"
    PASS=$((PASS + 1))
  else
    echo "FAIL: Could not parse version ID from response"
    echo "$VERSION_BODY_RESP"
    FAIL=$((FAIL + 1))
    VERSION_ID=""
  fi
else
  echo "FAIL: Get versions returned ${STATUS}"
  echo "$RESULT"
  FAIL=$((FAIL + 1))
  VERSION_ID=""
fi
echo ""

# ── Test 4: Post deployment ───────────────────────────────────────────────────
echo "--- Test 4: Post deployment ---"
DEPLOY_ID=""
if [ -n "$VERSION_ID" ]; then
  DEPLOY_BODY="{\"version_id\":\"${VERSION_ID}\",\"replicas\":1,\"environment\":\"production\"}"
  DEPLOY_BODY_B64=$(printf '%s' "$DEPLOY_BODY" | base64)
  RESULT=$(run_api "POST" "/api/v1/agents/echo-agent/deploy" "$DEPLOY_BODY_B64")
  STATUS=$(echo "$RESULT" | head -1)
  if [ "$STATUS" = "201" ] || [ "$STATUS" = "200" ]; then
    DEPLOY_BODY_RESP=$(echo "$RESULT" | tail -n +2)
    DEPLOY_ID=$(kubectl exec "$REGISTRY_POD" -n "$NAMESPACE" -- python3 -c \
      "import json; data=json.loads('''${DEPLOY_BODY_RESP}'''); print(data.get('id',''))" 2>/dev/null || true)
    echo "PASS: Deployment posted, ID: ${DEPLOY_ID}"
    PASS=$((PASS + 1))
  else
    echo "FAIL: Post deployment returned ${STATUS}"
    echo "$RESULT"
    FAIL=$((FAIL + 1))
  fi
else
  echo "SKIP: No version ID — skipping deployment post"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Test 5: Ensure agents-platform namespace exists ───────────────────────────
echo "--- Test 5: Ensure agents-platform namespace ---"
if kubectl get ns "$AGENTS_NS" >/dev/null 2>&1; then
  echo "PASS: Namespace ${AGENTS_NS} already exists"
  PASS=$((PASS + 1))
else
  kubectl create namespace "$AGENTS_NS"
  echo "PASS: Created namespace ${AGENTS_NS}"
  PASS=$((PASS + 1))
fi
echo ""

# ── Test 6: Ensure echo-agent-policy ConfigMap (allow-all Rego) ───────────────
echo "--- Test 6: Create echo-agent-policy ConfigMap ---"
kubectl create configmap echo-agent-policy -n "$AGENTS_NS" \
  --from-literal=policy.rego='package agentshield.agent.echo_agent
default allow = true' \
  --dry-run=client -o yaml | kubectl apply -f -
echo "PASS: echo-agent-policy ConfigMap applied"
PASS=$((PASS + 1))
echo ""

# ── Test 7: Poll for running pod with 2 containers ───────────────────────────
echo "--- Test 7: Poll for echo-agent pod with 2 containers (90s timeout) ---"
DEADLINE=$(($(date +%s) + 90))
ECHO_POD=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  ECHO_POD=$(kubectl get pods -n "$AGENTS_NS" -l app.kubernetes.io/name=echo-agent \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
  if [ -n "$ECHO_POD" ]; then
    READY=$(kubectl get pod "$ECHO_POD" -n "$AGENTS_NS" \
      -o jsonpath='{.status.containerStatuses[*].ready}' 2>/dev/null || true)
    # Count "true" tokens — expect 2
    TRUE_COUNT=$(echo "$READY" | tr ' ' '\n' | grep -c "^true$" || true)
    if [ "$TRUE_COUNT" -ge 2 ]; then
      echo "PASS: Pod ${ECHO_POD} running with ${TRUE_COUNT} containers ready"
      PASS=$((PASS + 1))
      break
    fi
  fi
  echo "  ... waiting (pod=${ECHO_POD:-none})"
  sleep 5
done
if [ -z "$ECHO_POD" ]; then
  echo "FAIL: No running echo-agent pod found in ${AGENTS_NS} after 90s"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Test 8: Verify /health endpoint in echo-agent container ──────────────────
echo "--- Test 8: Verify echo-agent /health endpoint ---"
if [ -n "$ECHO_POD" ]; then
  HTTP_CODE=$(kubectl exec "$ECHO_POD" -n "$AGENTS_NS" -c echo-agent -- \
    python3 -c "import urllib.request; r = urllib.request.urlopen('http://localhost:8080/health'); print(r.getcode())" \
    2>/dev/null || true)
  if [ "$HTTP_CODE" = "200" ]; then
    echo "PASS: /health returned ${HTTP_CODE}"
    PASS=$((PASS + 1))
  else
    echo "FAIL: /health returned '${HTTP_CODE}' (expected 200)"
    FAIL=$((FAIL + 1))
  fi
else
  echo "SKIP: No pod available — skipping health check"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "================================================"
echo "CP2b Results: PASS=${PASS}  FAIL=${FAIL}"
echo "================================================"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi

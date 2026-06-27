#!/usr/bin/env bash
# CP2c smoke test — verify OPA sidecar returns allow decision for echo-agent.
set -euo pipefail

AGENTS_NS="agents-platform"
PASS=0
FAIL=0

echo "==> CP2c smoke test: OPA sidecar policy evaluation"
echo ""

# ── Locate echo-agent pod ─────────────────────────────────────────────────────
ECHO_POD=$(kubectl get pod -n "$AGENTS_NS" -l app.kubernetes.io/name=echo-agent \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$ECHO_POD" ]; then
  echo "FAIL: No running echo-agent pod found in namespace ${AGENTS_NS}"
  echo "      Run smoke-test-cp2-deploy.sh first to deploy the echo-agent."
  exit 1
fi

echo "echo-agent pod: ${ECHO_POD}"
echo ""

# ── Test 1: OPA health check ──────────────────────────────────────────────────
echo "--- Test 1: OPA /health endpoint ---"
OPA_HEALTH_CODE=$(kubectl exec "$ECHO_POD" -n "$AGENTS_NS" -c echo-agent -- \
  python3 -c "
import urllib.request, urllib.error
try:
    r = urllib.request.urlopen('http://localhost:8181/health')
    print(r.getcode())
except urllib.error.HTTPError as e:
    print(e.code)
" 2>/dev/null || true)

if [ "$OPA_HEALTH_CODE" = "200" ]; then
  echo "PASS: OPA /health returned ${OPA_HEALTH_CODE}"
  PASS=$((PASS + 1))
else
  echo "FAIL: OPA /health returned '${OPA_HEALTH_CODE}' (expected 200)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Test 2: OPA policy decision (allow=true) ──────────────────────────────────
echo "--- Test 2: OPA allow decision for echo_agent ---"
# Path uses underscores: agentshield.agent.echo_agent
# Method: POST with input body
# Expected: {"result": true}
INPUT_BODY='{"input":{"tool_name":"echo","args":{}}}'
INPUT_BODY_B64=$(printf '%s' "$INPUT_BODY" | base64)

OPA_RESULT=$(kubectl exec "$ECHO_POD" -n "$AGENTS_NS" -c echo-agent -- \
  python3 -c "
import urllib.request, urllib.error, base64, json
body = base64.b64decode('${INPUT_BODY_B64}')
req = urllib.request.Request(
    'http://localhost:8181/v1/data/agentshield/agent/echo_agent/allow',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req)
    raw = r.read().decode()
    print(r.getcode())
    print(raw)
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
" 2>/dev/null || true)

OPA_STATUS=$(echo "$OPA_RESULT" | head -1)
OPA_BODY=$(echo "$OPA_RESULT" | tail -n +2)

if [ "$OPA_STATUS" = "200" ]; then
  # Parse result field — expect true
  ALLOW_VAL=$(kubectl exec "$ECHO_POD" -n "$AGENTS_NS" -c echo-agent -- \
    python3 -c "
import json, sys
data = json.loads('''${OPA_BODY}''')
print(str(data.get('result', False)).lower())
" 2>/dev/null || true)

  if [ "$ALLOW_VAL" = "true" ]; then
    echo "PASS: OPA returned allow=true"
    echo "      Response: ${OPA_BODY}"
    PASS=$((PASS + 1))
  else
    echo "FAIL: OPA returned allow=${ALLOW_VAL} (expected true)"
    echo "      Response: ${OPA_BODY}"
    FAIL=$((FAIL + 1))
  fi
else
  echo "FAIL: OPA policy query returned HTTP ${OPA_STATUS}"
  echo "      Response: ${OPA_BODY}"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Test 3: OPA returns correct structure {"result": true} ────────────────────
echo "--- Test 3: Verify full OPA response structure ---"
if [ -n "$OPA_BODY" ]; then
  STRUCT_OK=$(kubectl exec "$ECHO_POD" -n "$AGENTS_NS" -c echo-agent -- \
    python3 -c "
import json
try:
    data = json.loads('''${OPA_BODY}''')
    assert 'result' in data, 'missing result key'
    assert data['result'] is True, f'result is not true: {data[\"result\"]}'
    print('ok')
except Exception as e:
    print(f'fail: {e}')
" 2>/dev/null || true)

  if [ "$STRUCT_OK" = "ok" ]; then
    echo "PASS: Response structure is {\"result\": true}"
    PASS=$((PASS + 1))
  else
    echo "FAIL: Response structure check failed: ${STRUCT_OK}"
    FAIL=$((FAIL + 1))
  fi
else
  echo "FAIL: No OPA response body to validate"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "================================================"
echo "CP2c Results: PASS=${PASS}  FAIL=${FAIL}"
echo "================================================"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi

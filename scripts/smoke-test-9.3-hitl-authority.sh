#!/usr/bin/env bash
# smoke-test-9.3-hitl-authority.sh
# Tests Phase 9.3: HITL authority scoping + playground approvals endpoint.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app=agentshield-registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

PASS=0
FAIL=0

run_test() {
  local desc="$1"
  shift
  if kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$@" 2>/dev/null; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== Phase 9.3: HITL Authority Scoping ==="
echo ""

# 1. Create approval_authority for tool 'cancel_order' assigned to reviewer-1
run_test "Create ApprovalAuthority for tool cancel_order → reviewer-1" "
import urllib.request, json
body = json.dumps({
  'resource_type': 'tool',
  'resource_id': 'cancel_order',
  'approver_user_id': 'reviewer-1',
  'granted_by': 'smoke-test-admin'
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/admin/approval-authority',
  data=body,
  headers={'Content-Type': 'application/json'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('approver_user_id') == 'reviewer-1'
"

# 2. GET /playground/approvals returns 200 empty list
run_test "GET /playground/approvals returns 200" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/approvals?status=pending')
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert isinstance(data, list), f'expected list got {type(data)}'
"

# 3. GET /approvals/ returns 200 (admin fallback — no X-User-Sub)
run_test "GET /approvals/ returns 200 (no auth header — admin fallback)" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals/?status=pending')
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert 'items' in data
"

# 4. GET /approvals/ with X-User-Sub of unknown user returns empty (no authority)
run_test "GET /approvals/ with unknown X-User-Sub returns empty list" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/approvals/?status=pending',
  headers={'X-User-Sub': 'unknown-user-xyz'}
)
r = urllib.request.urlopen(req)
assert r.status == 200
data = json.loads(r.read())
assert data.get('total', -1) == 0, f'expected 0 items got {data.get(\"total\")}'
"

# 5. Approvals GET with authority holder returns 200
run_test "GET /approvals/ scoped to reviewer-1 returns 200" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/approvals/?status=pending',
  headers={'X-User-Sub': 'reviewer-1'}
)
r = urllib.request.urlopen(req)
assert r.status == 200
data = json.loads(r.read())
assert 'items' in data
"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "9.3 HITL authority: PASS" && exit 0 || { echo "9.3 HITL authority: FAIL"; exit 1; }

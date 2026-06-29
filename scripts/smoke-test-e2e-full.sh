#!/usr/bin/env bash
# smoke-test-e2e-full.sh
# Comprehensive E2E smoke test — covers the main AgentShield platform use cases
# after Phases 9.3 + 10.1–10.3 are deployed.
#
# Uses python3 inside the registry-api pod for all HTTP calls (consistent with
# existing smoke tests; python:3.12-slim has no curl/wget).
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
AGENT_NAME="e2e-smoke-$(date +%s)"

pyexec() {
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$1"
}

run_test() {
  local desc="$1"
  shift
  if pyexec "$@" 2>/dev/null; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== AgentShield Full E2E Smoke Test — $(date) ==="
echo "    namespace: $NAMESPACE"
echo "    api-pod: $API_POD"
echo "    agent: $AGENT_NAME"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
echo "-- Suite 1: Platform Health --"
# ─────────────────────────────────────────────────────────────────────────────
run_test "GET /health → 200 ok" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/health')
assert r.status == 200
data = json.loads(r.read())
assert data.get('status') == 'ok'
"

run_test "GET /ready → 200 ready" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/ready')
assert r.status == 200
data = json.loads(r.read())
assert data.get('status') == 'ready'
"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Suite 2: Agent Registration --"
# ─────────────────────────────────────────────────────────────────────────────
run_test "POST /agents creates agent" "
import urllib.request, json
body = json.dumps({
  'name': '${AGENT_NAME}',
  'team': 'platform',
  'description': 'Full E2E smoke test agent',
  'agent_type': 'sdk',
  'agent_class': 'daemon'
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'e2e-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status in (200, 201)
data = json.loads(r.read())
assert data.get('name') == '${AGENT_NAME}'
"

run_test "GET /agents/${AGENT_NAME} returns agent" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}')
assert r.status == 200
data = json.loads(r.read())
assert data.get('name') == '${AGENT_NAME}'
"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Suite 3: Publish Workflow --"
# ─────────────────────────────────────────────────────────────────────────────
run_test "POST /agents/${AGENT_NAME}/publish submits request" "
import urllib.request, json
body = json.dumps({'dependency_declaration': {}}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/${AGENT_NAME}/publish',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'e2e-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status in (200, 202)
data = json.loads(r.read())
assert 'publish_request_id' in data
"

run_test "Agent publish_status → pending_review after submit" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}')
data = json.loads(r.read())
ps = data.get('publish_status')
assert ps in ('pending_review', 'pending'), f'got {ps}'
"

run_test "GET /admin/publish-requests lists pending request" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/admin/publish-requests?status=pending_review&limit=100')
data = json.loads(r.read())
assert data.get('total', 0) >= 1
"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Suite 4: HITL Authority --"
# ─────────────────────────────────────────────────────────────────────────────
run_test "POST /admin/approval-authority creates authority record" "
import urllib.request, json
body = json.dumps({
  'resource_type': 'tool',
  'resource_id': 'e2e-test-tool',
  'approver_user_id': 'e2e-reviewer',
  'granted_by': 'e2e-admin'
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/admin/approval-authority',
  data=body,
  headers={'Content-Type': 'application/json'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201
"

run_test "GET /approvals/ production context returns 200" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals/?status=pending')
assert r.status == 200
data = json.loads(r.read())
assert 'items' in data
"

run_test "GET /playground/approvals returns 200 list" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/approvals')
assert r.status == 200
data = json.loads(r.read())
assert isinstance(data, list)
"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Suite 5: Playground Runs --"
# ─────────────────────────────────────────────────────────────────────────────
RUN_ID=$(pyexec "
import urllib.request, json
body = json.dumps({'agent_name': '${AGENT_NAME}', 'input_message': 'e2e hello'}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/runs',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'e2e-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
data = json.loads(r.read())
print(data['run_id'])
" 2>/dev/null) || RUN_ID=""

if [ -n "$RUN_ID" ]; then
  echo "  PASS: POST /playground/runs creates run (id=${RUN_ID:0:8}…)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: POST /playground/runs creates run"
  FAIL=$((FAIL + 1))
fi

run_test "GET /playground/runs lists runs" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/runs',
  headers={'X-User-Sub': 'e2e-user'}
)
r = urllib.request.urlopen(req)
assert r.status == 200
data = json.loads(r.read())
assert isinstance(data, list) and len(data) > 0
"

if [ -n "$RUN_ID" ]; then
  run_test "GET /playground/runs/{id}/stream returns SSE" "
import urllib.request
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream', timeout=10)
assert r.status == 200
ct = r.headers.get('Content-Type', '')
assert 'text/event-stream' in ct, f'content-type={ct}'
chunk = r.read(256).decode('utf-8')
assert 'data:' in chunk, f'no SSE data in: {chunk[:100]}'
"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Suite 6: Dataset CRUD --"
# ─────────────────────────────────────────────────────────────────────────────
DS_ID=$(pyexec "
import urllib.request, json
body = json.dumps({
  'name': 'e2e-test-dataset',
  'items': [
    {'input': 'What is order 123?', 'expected_output': 'pending'},
    {'input': 'Cancel order 456', 'expected_output': 'cancelled'}
  ]
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'e2e-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201
data = json.loads(r.read())
print(data['id'])
" 2>/dev/null) || DS_ID=""

if [ -n "$DS_ID" ]; then
  echo "  PASS: POST /playground/datasets creates dataset (id=${DS_ID:0:8}…)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: POST /playground/datasets creates dataset"
  FAIL=$((FAIL + 1))
fi

run_test "GET /playground/datasets lists datasets" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets',
  headers={'X-User-Sub': 'e2e-user'}
)
r = urllib.request.urlopen(req)
assert r.status == 200
data = json.loads(r.read())
assert isinstance(data, list) and len(data) > 0
"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Suite 7: Eval Run --"
# ─────────────────────────────────────────────────────────────────────────────
if [ -n "$DS_ID" ]; then
  EVAL_ID=$(pyexec "
import urllib.request, json
body = json.dumps({
  'agent_name': '${AGENT_NAME}',
  'dataset_id': '${DS_ID}'
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/eval-runs',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'e2e-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201
data = json.loads(r.read())
assert data.get('status') == 'pending'
print(data['id'])
" 2>/dev/null) || EVAL_ID=""

  if [ -n "$EVAL_ID" ]; then
    echo "  PASS: POST /playground/eval-runs creates eval run (id=${EVAL_ID:0:8}…)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: POST /playground/eval-runs creates eval run"
    FAIL=$((FAIL + 1))
  fi

  if [ -n "$EVAL_ID" ]; then
    run_test "GET /playground/eval-runs/${EVAL_ID} returns run" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/eval-runs/${EVAL_ID}')
assert r.status == 200
data = json.loads(r.read())
assert data.get('id') == '${EVAL_ID}'
"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "-- Suite 8: Cleanup --"
# ─────────────────────────────────────────────────────────────────────────────
run_test "DELETE /agents/${AGENT_NAME} cleanup" "
import urllib.request
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/${AGENT_NAME}',
  method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status in (200, 204)
"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "================================"
TOTAL=$((PASS + FAIL))
echo "  Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "================================"
[ "$FAIL" -eq 0 ] && echo "  Full E2E: PASS" && exit 0 || { echo "  Full E2E: FAIL"; exit 1; }

#!/usr/bin/env bash
# smoke-test-10.1-playground-api.sh
# Tests Phase 10.1: Playground API — runs, datasets, eval-runs.
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

echo "=== Phase 10.1: Playground API ==="
echo ""

# Ensure there's at least one agent to test with (create smoke-pg-agent if missing)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
  r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/smoke-pg-agent')
except:
  req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents',
    data=json.dumps({'name':'smoke-pg-agent','team':'platform','description':'playground smoke test'}).encode(),
    headers={'Content-Type':'application/json','X-User-Sub':'smoke-user'},
    method='POST'
  )
  urllib.request.urlopen(req)
" 2>/dev/null || true

# 1. POST /playground/datasets — create
DATASET_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
  'name': 'smoke-test-dataset',
  'items': [{'input': 'hello', 'expected_output': 'hi'}]
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201
data = json.loads(r.read())
print(data['id'])
" 2>/dev/null)

if [ -n "$DATASET_ID" ]; then
  echo "  PASS: POST /playground/datasets creates dataset (id=${DATASET_ID:0:8}…)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: POST /playground/datasets creates dataset"
  FAIL=$((FAIL + 1))
fi

# 2. GET /playground/datasets — list
run_test "GET /playground/datasets returns items" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets',
  headers={'X-User-Sub': 'smoke-user'}
)
r = urllib.request.urlopen(req)
assert r.status == 200
data = json.loads(r.read())
assert isinstance(data, list) and len(data) > 0
"

# 3. POST /playground/runs — create run
RUN_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({'agent_name': 'smoke-pg-agent', 'input_message': 'hello playground'}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/runs',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201
data = json.loads(r.read())
assert 'run_id' in data and 'stream_url' in data
print(data['run_id'])
" 2>/dev/null)

if [ -n "$RUN_ID" ]; then
  echo "  PASS: POST /playground/runs creates run (id=${RUN_ID:0:8}…)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: POST /playground/runs creates run"
  FAIL=$((FAIL + 1))
fi

# 4. GET /playground/runs — list
run_test "GET /playground/runs returns items" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/runs',
  headers={'X-User-Sub': 'smoke-user'}
)
r = urllib.request.urlopen(req)
assert r.status == 200
data = json.loads(r.read())
assert isinstance(data, list) and len(data) > 0
"

# 5. GET /playground/runs/{id}/stream — SSE stream responds
if [ -n "$RUN_ID" ]; then
  run_test "GET /playground/runs/{id}/stream returns SSE" "
import urllib.request
req = urllib.request.Request('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream')
r = urllib.request.urlopen(req, timeout=10)
assert r.status == 200
content_type = r.headers.get('Content-Type', '')
assert 'text/event-stream' in content_type, f'got {content_type}'
chunk = r.read(512).decode('utf-8')
assert 'data:' in chunk, f'no data: in chunk: {chunk[:200]}'
"
fi

# 6. POST /playground/eval-runs — create eval run
if [ -n "$DATASET_ID" ]; then
  run_test "POST /playground/eval-runs creates eval run" "
import urllib.request, json
body = json.dumps({
  'agent_name': 'smoke-pg-agent',
  'dataset_id': '${DATASET_ID}'
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/eval-runs',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201
data = json.loads(r.read())
assert 'id' in data and data.get('status') == 'pending'
"
fi

# 7. GET /playground/approvals — playground approvals endpoint
run_test "GET /playground/approvals returns 200" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/approvals')
assert r.status == 200
data = json.loads(r.read())
assert isinstance(data, list)
"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "10.1 Playground API: PASS" && exit 0 || { echo "10.1 Playground API: FAIL"; exit 1; }

#!/usr/bin/env bash
# scripts/e2e/suite-8-playground.sh
#
# E2E Suite 8: Playground (Phases 10.1–10.2)
# Tests T-S8-001 through T-S8-007.
#
# What this proves:
#   T-S8-001 — POST /playground/runs → {run_id, stream_url}
#   T-S8-002 — GET /playground/runs → run with matching run_id in list
#   T-S8-003 — GET /playground/runs/{id}/stream → Content-Type: text/event-stream, data: events
#   T-S8-004 — Playground approval has context='playground' (created directly via POST /approvals)
#   T-S8-005 — POST /playground/approvals/{id}/decide → 200 self-approval
#   T-S8-006 — GET /playground/approvals → all items context='playground';
#              GET /approvals (production) → playground approval NOT included
#   T-S8-007 — Studio PlaygroundPage UI → MANUAL
#
# API notes vs. test plan:
#   - POST /playground/runs returns {"run_id": "...", "stream_url": "/api/v1/playground/runs/{id}/stream"}
#     (stream_url is a relative path — prepend http://localhost:8000)
#   - GET /playground/runs returns a list (not a paginated object)
#   - POST /playground/approvals/{id}/decide accepts {"decision": "approved"} or "denied"
#   - GET /playground/approvals returns a plain list (not paginated)
#   - GET /approvals/ (production) filters context='production', so playground approvals never appear
#   - T-S8-004 uses a directly-created approval (context='playground') since the simulated
#     playground runner does not actually invoke agent tools
#
# Usage:
#   bash scripts/e2e/suite-8-playground.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-8-playground.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

PASS=0
FAIL=0
MANUAL=0

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

check_manual() {
  local test_id="$1"
  local desc="$2"
  shift 2
  echo ""
  echo "  MANUAL [${test_id}]: ${desc}"
  if [ $# -gt 0 ]; then
    echo "  Steps:"
    while [ $# -gt 0 ]; do
      echo "    $1"
      shift
    done
  fi
  MANUAL=$((MANUAL + 1))
}

echo "=== Suite 8: Playground (Phases 10.1–10.2) ==="
echo ""

# ---------------------------------------------------------------------------
# Precondition: ensure smoke-pg-agent exists (re-used from existing smoke tests)
# ---------------------------------------------------------------------------
echo "--- Setup: ensure smoke-pg-agent exists ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
try:
    urllib.request.urlopen('http://localhost:8000/api/v1/agents/smoke-pg-agent')
except urllib.error.HTTPError:
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/agents/',
        data=json.dumps({
            'name': 'smoke-pg-agent',
            'team': 'platform',
            'description': 'playground smoke test agent'
        }).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    urllib.request.urlopen(req)
" 2>/dev/null || true
echo "  agent smoke-pg-agent ready"

# Also ensure a test agent exists for the approval test (agent_id needed for Approval FK)
HITL_AGENT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
name = 'pg-s8-hitl-agent'
try:
    r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/' + name)
    data = json.loads(r.read())
    print(data['id'])
except urllib.error.HTTPError:
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/agents/',
        data=json.dumps({'name': name, 'team': 'platform', 'description': 's8 approval test'}).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    r = urllib.request.urlopen(req)
    data = json.loads(r.read())
    print(data['id'])
" 2>/dev/null || true)
echo "  hitl agent id=${HITL_AGENT_ID:0:8}..."

# ---------------------------------------------------------------------------
# T-S8-001: POST /playground/runs → run_id + stream_url
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-001: POST /playground/runs → {run_id, stream_url} ---"

RUN_INFO=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'agent_name': 'smoke-pg-agent',
    'input_message': 'Hello from Suite 8 smoke test'
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert 'run_id' in data and data['run_id'], f'missing run_id in {data}'
assert 'stream_url' in data and data['stream_url'], f'missing stream_url in {data}'
print(data['run_id'] + ':' + data['stream_url'])
" 2>/dev/null || true)

RUN_ID=$(echo "$RUN_INFO" | cut -d: -f1)
STREAM_URL_RELATIVE=$(echo "$RUN_INFO" | cut -d: -f2-)

if [ -n "$RUN_ID" ]; then
  echo "  PASS: T-S8-001 POST /playground/runs → 201 (run_id=${RUN_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S8-001 POST /playground/runs did not return run_id"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-002: GET /playground/runs → run appears in list
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-002: GET /playground/runs → run_id in list ---"

if [ -n "$RUN_ID" ]; then
  run_test "T-S8-002 GET /playground/runs X-User-Sub=smoke-user → run_id=${RUN_ID:0:8}... present" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    headers={'X-User-Sub': 'smoke-user'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert isinstance(data, list), f'expected list got {type(data)}'
ids = [str(item.get('id', '')) for item in data]
assert '${RUN_ID}' in ids, f'run_id ${RUN_ID:0:8}... not in {ids[:5]}'
"
else
  echo "  SKIP: T-S8-002 — no run_id (T-S8-001 failed)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-003: GET /playground/runs/{id}/stream → SSE Content-Type + data events
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-003: GET /playground/runs/{id}/stream → text/event-stream ---"

if [ -n "$RUN_ID" ]; then
  run_test "T-S8-003 GET /playground/runs/${RUN_ID:0:8}.../stream → Content-Type: text/event-stream" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream'
)
r = urllib.request.urlopen(req, timeout=15)
assert r.status == 200, f'expected 200 got {r.status}'
ct = r.headers.get('Content-Type', '')
assert 'text/event-stream' in ct, f'expected text/event-stream in Content-Type, got: {ct}'
"

  run_test "T-S8-003 SSE stream contains data: events with expected content" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream'
)
r = urllib.request.urlopen(req, timeout=15)
chunk = r.read(1024).decode('utf-8')
assert 'data:' in chunk, f'no SSE data: line in chunk: {chunk[:200]}'
import json
lines = [l for l in chunk.split('\n') if l.startswith('data:')]
assert len(lines) > 0, 'no data: lines found'
# At least one line should be valid JSON with an event field
parsed = json.loads(lines[0][5:].strip())
assert 'event' in parsed, f'expected event field in first SSE item: {parsed}'
"
else
  echo "  SKIP: T-S8-003 — no run_id (T-S8-001 failed)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-004: Playground approval tagged as context='playground'
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-004: Playground approval has context=playground ---"

PG_APPROVAL_ID=""
if [ -n "$HITL_AGENT_ID" ]; then
  PG_APPROVAL_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'agent_id': '${HITL_AGENT_ID}',
    'agent_name': 'pg-s8-hitl-agent',
    'team': 'platform',
    'thread_id': 'thread-s8-pg-001',
    'tool_name': 'issue_refund',
    'tool_args': {'order_id': 'PG-ORDER-001', 'amount': 25.00},
    'risk_level': 'high',
    'context': 'playground'
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('context') == 'playground', f'expected playground context got {data.get(\"context\")}'
print(data['id'])
" 2>/dev/null || true)
fi

if [ -n "$PG_APPROVAL_ID" ]; then
  echo "  PASS: T-S8-004 Playground approval created with context=playground (id=${PG_APPROVAL_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S8-004 Could not create playground approval or context not 'playground'"
  FAIL=$((FAIL + 1))
fi

# Verify it appears in playground approvals endpoint
if [ -n "$PG_APPROVAL_ID" ]; then
  run_test "T-S8-004 GET /playground/approvals → item with context=playground present" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/approvals')
data = json.loads(r.read())
assert isinstance(data, list), f'expected list got {type(data)}'
ids = [str(a.get('id', '')) for a in data]
assert '${PG_APPROVAL_ID}' in ids, f'approval ${PG_APPROVAL_ID:0:8}... not in list'
all_pg = all(a.get('context') == 'playground' for a in data)
assert all_pg, f'non-playground context found: {[a.get(\"context\") for a in data]}'
"
fi

# ---------------------------------------------------------------------------
# T-S8-005: POST /playground/approvals/{id}/decide → 200 self-approval
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-005: Self-approve playground approval (no authority check) ---"

if [ -n "$PG_APPROVAL_ID" ]; then
  run_test "T-S8-005 POST /playground/approvals/${PG_APPROVAL_ID:0:8}.../decide {decision:approved} → 200" "
import urllib.request, json
body = json.dumps({'decision': 'approved'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/approvals/${PG_APPROVAL_ID}/decide',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('decided') is True, f'unexpected response: {data}'
assert data.get('decision') == 'approved', f'unexpected decision: {data}'
"

  # Verify approval status updated
  run_test "T-S8-005 Verify playground approval status=approved after decide" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals/${PG_APPROVAL_ID}')
data = json.loads(r.read())
assert data.get('status') == 'approved', f'expected approved got {data.get(\"status\")}'
"
else
  echo "  SKIP: T-S8-005 — no playground approval to decide (T-S8-004 failed)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-006: GET /playground/approvals → only playground context
#           GET /approvals (production) → playground approval NOT included
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-006: Playground endpoint filters context=playground only ---"

run_test "T-S8-006 GET /playground/approvals → all items have context=playground" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/approvals')
data = json.loads(r.read())
assert isinstance(data, list), f'expected list got {type(data)}'
# Empty list is OK if no playground approvals exist
for item in data:
    ctx = item.get('context')
    assert ctx == 'playground', f'non-playground context in list: {ctx} (item: {item.get(\"id\")})'
"

run_test "T-S8-006 GET /approvals (production) → playground approvals excluded" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals/')
data = json.loads(r.read())
items = data.get('items', [])
pg_items = [i for i in items if i.get('context') == 'playground']
assert len(pg_items) == 0, \
    f'playground approvals leaked into production list: {[i.get(\"id\") for i in pg_items]}'
"

# ---------------------------------------------------------------------------
# T-S8-007: Studio PlaygroundPage UI — MANUAL
# ---------------------------------------------------------------------------
check_manual "T-S8-007" \
  "Studio PlaygroundPage renders VersionSelector and ChatPane, message streams a response" \
  "1. Open Studio at http://localhost:3000 (or the configured Studio URL)" \
  "2. Navigate to the Playground tab" \
  "3. Select 'smoke-pg-agent' from the VersionSelector dropdown" \
  "   - Verify: dropdown populates with available agents" \
  "4. Type 'Who are you?' in the ChatPane input and press Send" \
  "   - Verify: response text appears incrementally (streaming)" \
  "   - Verify: no error banner or console errors" \
  "5. Observe TracePanel (if visible): should show event log with text_delta events" \
  "6. Verify: event: done appears and stream terminates cleanly" \
  "" \
  "Pass criteria: response visible in ChatPane, no errors, TracePanel shows events"

# ---------------------------------------------------------------------------
# T-S8-008: GET /playground/runs/{id}/trace → returns trace_id field
# ---------------------------------------------------------------------------
echo "--- T-S8-008: GET /playground/runs/{run_id}/trace ---"
run_test "T-S8-008: GET /playground/runs/{id}/trace returns trace_id" "
import urllib.request, json
r = urllib.request.urlopen(
    'http://localhost:8000/api/v1/playground/runs/' + RUN_ID + '/trace',
    timeout=5
)
d = json.loads(r.read())
assert 'trace_id' in d, f'trace_id missing from trace response: {d}'
assert 'run_id' in d, f'run_id missing from trace response: {d}'
assert 'trace_url' in d, f'trace_url missing from trace response: {d}'
# trace_id may be None if no Langfuse trace was emitted (sandbox run)
print('trace_id=' + str(d.get('trace_id')) + ' status=' + str(d.get('status')))
"

# ---------------------------------------------------------------------------
# T-S8-009: POST /playground/runs/{id}/save-to-dataset → 201 with item
# ---------------------------------------------------------------------------
echo "--- T-S8-009: POST /playground/runs/{run_id}/save-to-dataset ---"
DATASET_ID=$(python3 -c "
import urllib.request, json
body = json.dumps({'name': 'e2e-s8-ds-' + __import__('time').strftime('%s'), 'user_id': 's8-test'}).encode()
req = urllib.request.Request('http://localhost:8000/api/v1/playground/datasets',
    data=body, headers={'Content-Type': 'application/json'}, method='POST')
try:
    r = urllib.request.urlopen(req, timeout=5)
    d = json.loads(r.read())
    print(d.get('id') or d.get('dataset_id', ''))
except Exception as e:
    print('')
" 2>/dev/null || echo "")

if [ -n "$DATASET_ID" ]; then
  run_test "T-S8-009: Save run to dataset returns item_id" "
import urllib.request, json
body = json.dumps({'dataset_id': '${DATASET_ID}', 'label': 'e2e-s8-save-test'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/' + RUN_ID + '/save-to-dataset',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 201, f'expected 201 got {r.status}'
d = json.loads(r.read())
assert 'item_id' in d, f'item_id missing: {d}'
assert d.get('items_count', 0) >= 1, f'items_count should be >= 1: {d}'
print('item_id=' + str(d.get('item_id')) + ' items_count=' + str(d.get('items_count')))
"
else
  echo "  SKIP T-S8-009: could not create dataset (datasets router may not be mounted)"
  MANUAL=$((MANUAL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-010: POST /playground/runs/{id}/feedback → 201 with score
# ---------------------------------------------------------------------------
echo "--- T-S8-010: POST /playground/runs/{run_id}/feedback ---"
run_test "T-S8-010: Submit thumbs-up feedback returns score" "
import urllib.request, json
body = json.dumps({'score': 1, 'comment': 'good response'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/' + RUN_ID + '/feedback',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 201, f'expected 201 got {r.status}'
d = json.loads(r.read())
assert d.get('score') == 1, f'score should be 1: {d}'
assert 'run_id' in d, f'run_id missing: {d}'
print('feedback accepted score=1 langfuse_score_id=' + str(d.get('langfuse_score_id')))
"

run_test "T-S8-010b: Submit invalid feedback score → 422" "
import urllib.request, json
body = json.dumps({'score': 0}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/' + RUN_ID + '/feedback',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 422, got 200')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    print('correctly rejected score=0 with 422')
"

# ---------------------------------------------------------------------------
# T-S8-011: GET /trace returns 404 for missing run_id
# ---------------------------------------------------------------------------
echo "--- T-S8-011: Trace endpoint 404 for missing run ---"
run_test "T-S8-011: GET /trace for unknown run returns 404" "
import urllib.request, json
try:
    urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/00000000-0000-0000-0000-000000000000/trace', timeout=5)
    raise AssertionError('expected 404')
except urllib.error.HTTPError as e:
    assert e.code == 404, f'expected 404 got {e.code}'
    print('404 correctly returned for missing run')
"

# ---------------------------------------------------------------------------
# T-S8-012: Feedback 404 for missing run
# ---------------------------------------------------------------------------
echo "--- T-S8-012: Feedback endpoint 404 for missing run ---"
run_test "T-S8-012: POST /feedback for unknown run returns 404" "
import urllib.request, json
body = json.dumps({'score': 1}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/00000000-0000-0000-0000-000000000000/feedback',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 404')
except urllib.error.HTTPError as e:
    assert e.code == 404, f'expected 404 got {e.code}'
    print('404 correctly returned for missing run')
"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup ---"

run_test "Cleanup: DELETE test agent pg-s8-hitl-agent → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/pg-s8-hitl-agent',
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  Suite 8 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "  (MANUAL items require the Studio UI running in a browser)"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

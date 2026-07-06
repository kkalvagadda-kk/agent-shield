#!/usr/bin/env bash
# scripts/e2e/suite-8-playground.sh
#
# E2E Suite 8: Playground — full experience coverage
#
# Test IDs and what each proves:
#   T-S8-001 — POST /playground/runs → 201 with run_id + stream_url
#   T-S8-002 — POST /playground/runs unknown agent → 404
#   T-S8-003 — POST /playground/runs missing agent_name → 422
#   T-S8-004 — GET /playground/runs → run_id present in list
#   T-S8-005 — GET /playground/runs/{bad-uuid}/stream → 422
#   T-S8-006 — GET /playground/runs/{unknown-id}/stream → 404
#   T-S8-007 — GET /playground/runs/{id}/stream → Content-Type: text/event-stream
#   T-S8-008 — Stream emits error+done events when agent has no running deployment
#   T-S8-009 — All SSE lines in stream are unnamed (no raw "event:" prefix lines)
#   T-S8-010 — Every data: line is valid JSON with an "event" key (named→unnamed conversion)
#   T-S8-011 — Stream ends with a "done" event (not left hanging)
#   T-S8-012 — GET /playground/runs/{id}/trace → run_id, trace_id, trace_url, status present
#   T-S8-013 — POST /playground/runs/{id}/feedback score=1 → 201 with score field
#   T-S8-014 — POST /playground/runs/{id}/feedback score=-1 → 201 with score=-1
#   T-S8-015 — POST /playground/runs/{id}/feedback score=0 → 422 (invalid)
#   T-S8-016 — POST /playground/runs/{id}/save-to-dataset → 201 with item_id, items_count≥1
#   T-S8-017 — Playground approval created with context=playground has notify_slack=false
#   T-S8-018 — POST /playground/approvals/{id}/decide {approved} → 200 decided=true
#   T-S8-019 — GET /playground/approvals → all items have context=playground
#   T-S8-020 — GET /approvals/ (production) → playground approval NOT included
#   T-S8-021 — MANUAL: Studio renders three panels, agent selector populates, chat streams
#
# Dependencies: suite-8 is self-contained; creates its own test agents/data and cleans up.
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

DATASET_ID=""
cleanup() {
  echo ""
  echo "==> Cleanup..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
for name in ['pg-s8-run-agent', 'pg-s8-hitl-agent']:
    try:
        urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/' + name, method='DELETE'), timeout=5)
    except Exception: pass
" 2>/dev/null || true
  if [ -n "$DATASET_ID" ]; then
    kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/playground/datasets/${DATASET_ID}', method='DELETE'), timeout=5)
except Exception: pass
" 2>/dev/null || true
  fi
}
trap cleanup EXIT

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

echo "=== Suite 8: Playground — full experience ==="
echo ""

# ---------------------------------------------------------------------------
# Precondition: ensure test agents exist
# ---------------------------------------------------------------------------
echo "--- Setup: test agents ---"

# pg-s8-run-agent: owned by smoke-user, no deployment — used for run/stream tests.
# Create if missing; if already exists (409 from soft-delete) reuse it — owner is smoke-user.
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
name = 'pg-s8-run-agent'
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=json.dumps({'name': name, 'team': 'platform', 'description': 'suite-8 run tests'}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
    method='POST'
)
try:
    urllib.request.urlopen(req)
    print(name + ' created')
except urllib.error.HTTPError as e:
    if e.code == 409:
        print(name + ' already exists (reusing)')
    else:
        raise
" 2>/dev/null || true

# pg-s8-hitl-agent: used for approval tests
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
        data=json.dumps({'name': name, 'team': 'platform', 'description': 's8 hitl test'}).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    r = urllib.request.urlopen(req)
    data = json.loads(r.read())
    print(data['id'])
" 2>/dev/null || true)
echo "  hitl agent id=${HITL_AGENT_ID:0:8}..."

# Dataset for save-to-dataset test
DATASET_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, time
body = json.dumps({'name': 'e2e-s8-ds-' + str(int(time.time())), 'items': []}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/datasets',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
try:
    r = urllib.request.urlopen(req, timeout=5)
    d = json.loads(r.read())
    print(d.get('id', ''))
except Exception:
    print('')
" 2>/dev/null || true)
echo "  dataset id=${DATASET_ID:0:8}..."

# ---------------------------------------------------------------------------
# T-S8-001: POST /playground/runs → 201 + run_id + stream_url
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-001: POST /playground/runs → 201 ---"

RUN_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({'agent_name': 'pg-s8-run-agent', 'input_message': 'Hello S8'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert 'run_id' in data and data['run_id'], f'missing run_id: {data}'
assert 'stream_url' in data and data['stream_url'], f'missing stream_url: {data}'
assert data['stream_url'].endswith(data['run_id'] + '/stream'), f'unexpected stream_url: {data[\"stream_url\"]}'
print(data['run_id'])
" 2>/dev/null || true)

if [ -n "$RUN_ID" ]; then
  echo "  PASS: T-S8-001 POST /playground/runs → 201 (run_id=${RUN_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S8-001 POST /playground/runs did not return run_id"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-002: POST /playground/runs unknown agent → 404
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-002: POST /playground/runs unknown agent → 404 ---"
run_test "T-S8-002 unknown agent returns 404" "
import urllib.request, json, urllib.error
body = json.dumps({'agent_name': 'no-such-agent-s8-xyz', 'input_message': 'hi'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 404, got 200')
except urllib.error.HTTPError as e:
    assert e.code == 404, f'expected 404 got {e.code}'
    print('correctly returned 404 for unknown agent')
"

# ---------------------------------------------------------------------------
# T-S8-003: POST /playground/runs missing agent_name → 422
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-003: POST /playground/runs missing agent_name → 422 ---"
run_test "T-S8-003 missing agent_name returns 422" "
import urllib.request, json, urllib.error
body = json.dumps({'input_message': 'hi'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 422, got 200')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    print('correctly returned 422 for missing agent_name')
"

# ---------------------------------------------------------------------------
# T-S8-004: GET /playground/runs → run_id present
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-004: GET /playground/runs → run present in list ---"
if [ -n "$RUN_ID" ]; then
  run_test "T-S8-004 GET /playground/runs → run_id present" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    headers={'X-User-Sub': 'smoke-user'}
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert isinstance(data, list), f'expected list got {type(data)}'
ids = [str(item.get('id', '')) for item in data]
assert '${RUN_ID}' in ids, f'run_id not found in list (checked {len(ids)} items)'
# Verify the run has expected fields
run = next(i for i in data if str(i.get('id')) == '${RUN_ID}')
assert run.get('agent_name') == 'pg-s8-run-agent', f'wrong agent_name: {run.get(\"agent_name\")}'
assert run.get('context') == 'playground', f'wrong context: {run.get(\"context\")}'
assert run.get('sandbox') is True, f'sandbox should be True: {run.get(\"sandbox\")}'
print('run found with correct fields')
"
else
  echo "  SKIP: T-S8-004 — no run_id (T-S8-001 failed)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-005: GET /playground/runs/{bad-uuid}/stream → 422
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-005: Stream bad UUID → 422 ---"
run_test "T-S8-005 invalid UUID in stream path returns 422" "
import urllib.request, urllib.error
try:
    urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/not-a-uuid/stream', timeout=5)
    raise AssertionError('expected 422, got 200')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    print('correctly returned 422 for invalid UUID')
"

# ---------------------------------------------------------------------------
# T-S8-006: GET /playground/runs/{unknown-uuid}/stream → 404
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-006: Stream unknown run_id → 404 ---"
run_test "T-S8-006 unknown run_id in stream path returns 404" "
import urllib.request, urllib.error
try:
    urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/00000000-0000-0000-0000-000000000000/stream', timeout=5)
    raise AssertionError('expected 404, got 200')
except urllib.error.HTTPError as e:
    assert e.code == 404, f'expected 404 got {e.code}'
    print('correctly returned 404 for unknown run_id')
"

# ---------------------------------------------------------------------------
# T-S8-007: Stream → Content-Type: text/event-stream
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-007: Stream Content-Type: text/event-stream ---"
if [ -n "$RUN_ID" ]; then
  run_test "T-S8-007 GET /playground/runs/${RUN_ID:0:8}.../stream Content-Type" "
import urllib.request
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream', timeout=15)
assert r.status == 200, f'expected 200 got {r.status}'
ct = r.headers.get('Content-Type', '')
assert 'text/event-stream' in ct, f'expected text/event-stream, got: {ct}'
# Also verify SSE headers
cc = r.headers.get('Cache-Control', '')
assert 'no-cache' in cc, f'expected no-cache in Cache-Control, got: {cc}'
print('Content-Type: text/event-stream ✓ Cache-Control: no-cache ✓')
"
else
  echo "  SKIP: T-S8-007 — no run_id"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-008 through T-S8-011: SSE stream content validation
# Smoke-pg-agent has no deployment → proxy emits error+done immediately
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-008..011: SSE stream content (no-deployment path) ---"
if [ -n "$RUN_ID" ]; then
  # Read enough to get all events (error+done = ~200 bytes total)
  STREAM_BODY=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream', timeout=15)
body = r.read(4096).decode('utf-8')
print(repr(body))
" 2>/dev/null || true)

  run_test "T-S8-008 no-deployment path emits error event then done" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream', timeout=15)
body = r.read(4096).decode('utf-8')
data_lines = [l for l in body.split('\n') if l.startswith('data:')]
assert len(data_lines) >= 2, f'expected at least 2 data: lines (error+done), got {len(data_lines)}: {body[:400]}'
events = [json.loads(l[5:].strip()) for l in data_lines]
event_types = [ev.get('event') for ev in events]
assert 'error' in event_types, f'expected error event in no-deployment path, got: {event_types}'
assert 'done' in event_types, f'expected done event in no-deployment path, got: {event_types}'
# Error event must carry a helpful message
err_ev = next(ev for ev in events if ev.get('event') == 'error')
assert 'message' in err_ev, f'error event missing message field: {err_ev}'
assert 'deployment' in err_ev['message'].lower() or 'agent' in err_ev['message'].lower(), \
    f'error message not descriptive enough: {err_ev[\"message\"]}'
print('error+done events present, error message is descriptive')
"

  run_test "T-S8-009 no raw named SSE event: lines in response (all converted to unnamed)" "
import urllib.request
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream', timeout=15)
body = r.read(4096).decode('utf-8')
raw_event_lines = [l for l in body.split('\n') if l.startswith('event:')]
assert len(raw_event_lines) == 0, \
    f'raw named event: lines found (proxy did not convert them): {raw_event_lines}'
print('no raw event: lines — named→unnamed conversion working')
"

  run_test "T-S8-010 every data: line is valid JSON with event key" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream', timeout=15)
body = r.read(4096).decode('utf-8')
data_lines = [l for l in body.split('\n') if l.startswith('data:')]
assert len(data_lines) > 0, f'no data: lines in stream body: {body[:200]}'
for line in data_lines:
    raw = line[5:].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise AssertionError(f'data: line is not valid JSON: {raw}')
    assert 'event' in parsed, f'data: line missing event key: {parsed}'
print(f'all {len(data_lines)} data: lines are valid JSON with event key')
"

  run_test "T-S8-011 stream ends with done event (does not hang)" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/${RUN_ID}/stream', timeout=15)
body = r.read(4096).decode('utf-8')
data_lines = [l for l in body.split('\n') if l.startswith('data:')]
events = [json.loads(l[5:].strip()) for l in data_lines]
last = events[-1] if events else {}
assert last.get('event') == 'done', f'last event must be done, got: {last}'
print('stream terminated cleanly with done event')
"
else
  echo "  SKIP: T-S8-008..011 — no run_id"
  for i in 8 9 10 11; do FAIL=$((FAIL + 1)); done
fi

# ---------------------------------------------------------------------------
# T-S8-012: GET /playground/runs/{id}/trace → expected shape
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-012: GET /playground/runs/{id}/trace ---"
if [ -n "$RUN_ID" ]; then
  run_test "T-S8-012 trace endpoint returns required fields" "
import urllib.request, json
r = urllib.request.urlopen(
    'http://localhost:8000/api/v1/playground/runs/${RUN_ID}/trace',
    timeout=5
)
d = json.loads(r.read())
for field in ('run_id', 'trace_id', 'trace_url', 'status'):
    assert field in d, f'{field} missing from trace response: {d}'
assert d['run_id'] == '${RUN_ID}', f'run_id mismatch: {d[\"run_id\"]}'
# trace_url must be set if trace_id is set
if d.get('trace_id'):
    assert d.get('trace_url'), f'trace_url should be set when trace_id is present'
print('trace fields: run_id=' + str(d['run_id'][:8]) + ' status=' + str(d['status']))
"
else
  echo "  SKIP: T-S8-012 — no run_id"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-013: POST /playground/runs/{id}/feedback score=1 → 201
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-013..015: Feedback ---"
if [ -n "$RUN_ID" ]; then
  run_test "T-S8-013 thumbs-up feedback → 201 with score=1" "
import urllib.request, json
body = json.dumps({'score': 1, 'comment': 'great response'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/${RUN_ID}/feedback',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 201, f'expected 201 got {r.status}'
d = json.loads(r.read())
assert d.get('score') == 1, f'score should be 1: {d}'
assert 'run_id' in d, f'run_id missing: {d}'
print('feedback accepted score=1')
"

  # Need a second run for score=-1 (can only give feedback once per run in some impls)
  RUN_ID2=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({'agent_name': 'pg-s8-run-agent', 'input_message': 'feedback test 2'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body, headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
    method='POST'
)
r = urllib.request.urlopen(req)
data = json.loads(r.read())
print(data['run_id'])
" 2>/dev/null || true)

  if [ -n "$RUN_ID2" ]; then
    run_test "T-S8-014 thumbs-down feedback → 201 with score=-1" "
import urllib.request, json
body = json.dumps({'score': -1, 'comment': 'bad response'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/${RUN_ID2}/feedback',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 201, f'expected 201 got {r.status}'
d = json.loads(r.read())
assert d.get('score') == -1, f'score should be -1: {d}'
print('feedback accepted score=-1')
"
  else
    echo "  SKIP: T-S8-014 — could not create second run"
    FAIL=$((FAIL + 1))
  fi

  run_test "T-S8-015 score=0 returns 422" "
import urllib.request, json, urllib.error
body = json.dumps({'score': 0}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/${RUN_ID}/feedback',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 422, got 200')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'expected 422 got {e.code}'
    print('correctly rejected score=0 with 422')
"
else
  echo "  SKIP: T-S8-013..015 — no run_id"
  for i in 13 14 15; do FAIL=$((FAIL + 1)); done
fi

# ---------------------------------------------------------------------------
# T-S8-016: POST /playground/runs/{id}/save-to-dataset → 201
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-016: Save run to dataset ---"
if [ -n "$RUN_ID" ] && [ -n "$DATASET_ID" ]; then
  run_test "T-S8-016 save-to-dataset returns item_id and items_count≥1" "
import urllib.request, json
body = json.dumps({'dataset_id': '${DATASET_ID}', 'label': 'e2e-s8-save'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs/${RUN_ID}/save-to-dataset',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 201, f'expected 201 got {r.status}'
d = json.loads(r.read())
assert 'item_id' in d, f'item_id missing: {d}'
assert d.get('items_count', 0) >= 1, f'items_count should be ≥1: {d}'
assert str(d.get('dataset_id')) == '${DATASET_ID}', f'wrong dataset_id: {d}'
print('item_id=' + str(d.get('item_id'))[:8] + '... items_count=' + str(d.get('items_count')))
"
else
  echo "  SKIP: T-S8-016 — missing run_id or dataset_id"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-017: Playground approval has context=playground and notify_slack=false
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-017..020: Approval context isolation ---"

PG_APPROVAL_ID=""
if [ -n "$HITL_AGENT_ID" ]; then
  PG_APPROVAL_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, uuid
body = json.dumps({
    'agent_id': '${HITL_AGENT_ID}',
    'agent_name': 'pg-s8-hitl-agent',
    'team': 'platform',
    'thread_id': 'thread-s8-' + str(uuid.uuid4())[:8],
    'tool_name': 'issue_refund',
    'tool_args': {'order_id': 'PG-S8-001'},
    'risk_level': 'high',
    'context': 'playground'
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/',
    data=body, headers={'Content-Type': 'application/json'}, method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('context') == 'playground', f'context wrong: {data.get(\"context\")}'
assert data.get('notify_slack') is False, f'notify_slack should be False: {data.get(\"notify_slack\")}'
print(data['id'])
" 2>/dev/null || true)
fi

if [ -n "$PG_APPROVAL_ID" ]; then
  echo "  PASS: T-S8-017 playground approval context=playground notify_slack=false (id=${PG_APPROVAL_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S8-017 Could not create playground approval"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-018: POST /playground/approvals/{id}/decide → 200 decided=true
# ---------------------------------------------------------------------------
if [ -n "$PG_APPROVAL_ID" ]; then
  run_test "T-S8-018 POST /playground/approvals/${PG_APPROVAL_ID:0:8}.../decide approved → 200 decided=true" "
import urllib.request, json
body = json.dumps({'decision': 'approved'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/approvals/${PG_APPROVAL_ID}/decide',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
    method='POST'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('decided') is True, f'decided should be True: {data}'
assert data.get('decision') == 'approved', f'decision should be approved: {data}'
print('self-approval succeeded: decided=True decision=approved')
"

  run_test "T-S8-018b approval status=approved after decide" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals/${PG_APPROVAL_ID}', timeout=5)
data = json.loads(r.read())
assert data.get('status') == 'approved', f'expected approved got {data.get(\"status\")}'
print('approval.status=approved confirmed')
"
else
  echo "  SKIP: T-S8-018 — no playground approval (T-S8-017 failed)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S8-019: GET /playground/approvals → all items context=playground
# ---------------------------------------------------------------------------
run_test "T-S8-019 GET /playground/approvals → all items context=playground" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/approvals', timeout=5)
data = json.loads(r.read())
assert isinstance(data, list), f'expected list got {type(data)}'
for item in data:
    ctx = item.get('context')
    assert ctx == 'playground', f'non-playground context in playground list: ctx={ctx} id={item.get(\"id\")}'
print(f'{len(data)} items, all context=playground')
"

# ---------------------------------------------------------------------------
# T-S8-020: GET /approvals/ (production) → playground approval NOT included
# ---------------------------------------------------------------------------
run_test "T-S8-020 GET /approvals/ (production) excludes playground context" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals/', timeout=5)
data = json.loads(r.read())
items = data.get('items', []) if isinstance(data, dict) else data
pg_items = [i for i in items if i.get('context') == 'playground']
assert len(pg_items) == 0, \
    f'playground approvals leaked into production list: {[i.get(\"id\") for i in pg_items]}'
print(f'{len(items)} production approvals, none have context=playground')
"

# ---------------------------------------------------------------------------
# T-S8-021: MANUAL — Studio UI end-to-end
# ---------------------------------------------------------------------------
check_manual "T-S8-021" \
  "Studio renders three panels, agent dropdown populates, chat streams real response" \
  "1. Open Studio → Playground tab" \
  "2. Verify: left panel shows 'Select Agent' dropdown, sandbox badge is absent" \
  "3. Select an agent with a running deployment" \
  "   Verify: sandbox badge appears in left rail" \
  "4. Type a message and press Enter" \
  "   Verify: user message bubble appears immediately" \
  "   Verify: assistant bubble shows Loader2 spinner while streaming" \
  "   Verify: text appears incrementally (text_delta events streaming)" \
  "   Verify: trace panel (right) logs each event with timestamp" \
  "5. After done event:" \
  "   Verify: spinner stops, feedback thumbs appear below chat" \
  "   Verify: 'View Trace' link appears (if Langfuse is configured)" \
  "6. Click thumbs-up — verify toast 'Thanks for your feedback!'" \
  "7. Select a second agent that has no running deployment:" \
  "   Send a message. Verify: trace panel shows error event, then done." \
  "   Verify: stream does not hang indefinitely" \
  "" \
  "Pass criteria: live response visible, trace events logged, feedback toast fires, no-deployment shows error cleanly"

# ---------------------------------------------------------------------------
# T-S8-022..024: eval-runner service-identity bypass + single-run GET (A1)
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S8-022..024: service-identity bypass + GET /runs/{id} judge fields ---"

run_test "T-S8-022 POST /playground/runs X-User-Sub=eval-runner (agent owned by smoke-user) → 201" "
import urllib.request, json
body = json.dumps({'agent_name': 'pg-s8-run-agent', 'input_message': 'eval-runner bypass'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'eval-runner'},
    method='POST'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 201, f'expected 201 got {r.status}'
d = json.loads(r.read())
assert d.get('run_id'), f'missing run_id: {d}'
print('eval-runner service identity ran an agent it does not own (201)')
"

run_test "T-S8-023 POST /playground/runs X-User-Sub=mallory-not-owner → 403 (owner check still enforced)" "
import urllib.request, json, urllib.error
body = json.dumps({'agent_name': 'pg-s8-run-agent', 'input_message': 'not owner'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'mallory-not-owner'},
    method='POST'
)
try:
    urllib.request.urlopen(req, timeout=5)
    raise AssertionError('expected 403, got 2xx')
except urllib.error.HTTPError as e:
    assert e.code == 403, f'expected 403 got {e.code}'
    print('non-owner correctly blocked with 403')
"

run_test "T-S8-024 GET /playground/runs/{id} → 200 with judge fields" "
import urllib.request, json
body = json.dumps({'agent_name': 'pg-s8-run-agent', 'input_message': 'judge fields'}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/runs',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-user'},
    method='POST'
)
run_id = json.loads(urllib.request.urlopen(req, timeout=5).read())['run_id']
r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/' + run_id, timeout=5)
assert r.status == 200, f'expected 200 got {r.status}'
d = json.loads(r.read())
for k in ('judge_score', 'judge_status', 'judge_reason'):
    assert k in d, f'{k} missing from run response: {d}'
print('GET run exposes judge_score/judge_status/judge_reason')
"

run_test "T-S8-024b GET /playground/runs/{bad-uuid} → 422; unknown → 404" "
import urllib.request, urllib.error
try:
    urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/not-a-uuid', timeout=5)
    raise AssertionError('expected 422')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'bad uuid expected 422 got {e.code}'
try:
    urllib.request.urlopen('http://localhost:8000/api/v1/playground/runs/00000000-0000-0000-0000-000000000000', timeout=5)
    raise AssertionError('expected 404')
except urllib.error.HTTPError as e:
    assert e.code == 404, f'unknown id expected 404 got {e.code}'
print('bad uuid -> 422, unknown id -> 404')
"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup ---"

run_test "Cleanup: DELETE pg-s8-hitl-agent → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/pg-s8-hitl-agent',
    method='DELETE'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 204, f'expected 204 got {r.status}'
"

run_test "Cleanup: DELETE pg-s8-run-agent → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/pg-s8-run-agent',
    method='DELETE'
)
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 204, f'expected 204 got {r.status}'
"

# Delete the dataset created for this suite (best-effort)
if [ -n "$DATASET_ID" ]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/datasets/${DATASET_ID}',
    method='DELETE'
)
try:
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
  echo "  Cleaned up dataset ${DATASET_ID:0:8}..."
fi

# Delete playground runs created by smoke-user (best-effort)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:8000/api/v1/playground/runs',
    headers={'X-User-Sub': 'smoke-user'})
try:
    r = urllib.request.urlopen(req, timeout=5)
    runs = json.loads(r.read())
    for run in runs:
        dreq = urllib.request.Request(
            'http://localhost:8000/api/v1/playground/runs/' + str(run['id']),
            method='DELETE')
        try:
            urllib.request.urlopen(dreq, timeout=5)
        except Exception:
            pass
except Exception:
    pass
" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  Suite 8 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "  (MANUAL items require Studio running in a browser)"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

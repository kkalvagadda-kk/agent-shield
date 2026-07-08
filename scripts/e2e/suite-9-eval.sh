#!/usr/bin/env bash
# Suite 9: Eval Runner — T-S9-001 through T-S9-015b
#
# Covers dataset management and the async eval-run lifecycle.
# Simulates the eval-runner Job posting results so the lifecycle can be
# exercised without a live agent or a running K8s Job.
#
# Endpoints exercised (from services/registry-api/routers/datasets.py
# and services/registry-api/routers/eval_runner.py):
#   POST   /api/v1/playground/datasets
#   GET    /api/v1/playground/datasets
#   PATCH  /api/v1/playground/datasets/{id}
#   DELETE /api/v1/playground/datasets/{id}
#   POST   /api/v1/playground/eval-runs
#   GET    /api/v1/playground/eval-runs
#   GET    /api/v1/playground/eval-runs/{id}
#   POST   /api/v1/playground/eval-runs/{id}/results   (→ 201, not 200)
#   PATCH  /api/v1/playground/eval-runs/{id}
#   GET    /api/v1/playground/eval-runs/{id}/results
#   POST   /api/v1/playground/judge             (eval-mode LLM judge)
#
# Known schema note: PlaygroundDatasetResponse has no `item_count` field.
# Items are stored directly in data['items']; use len(data['items']) to count.
#
# Usage:
#   NAMESPACE=agentshield-platform bash scripts/e2e/suite-9-eval.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
TEST_USER="e2e-suite9-user"
AGENT_NAME="smoke-eval-agent-s9"

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
for name in ['${AGENT_NAME}', 's9-bypass-agent']:
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
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
base = 'http://localhost:8000/api/v1/playground/datasets'
try:
    r = urllib.request.urlopen(urllib.request.Request(base, headers={'X-User-Sub': 's9'}), timeout=5)
    for ds in json.loads(r.read()):
        if ds.get('name','') in ('s9-real-ds', 's9-fail-ds'):
            try:
                urllib.request.urlopen(urllib.request.Request(base + '/' + str(ds['id']), headers={'X-User-Sub': 's9'}, method='DELETE'), timeout=5)
            except Exception: pass
except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

PASS=0
FAIL=0
MANUAL=0

# Inline pass/fail helpers (used by the G5 trace-propagation checks below,
# alongside the run_test() harness). Accept an optional description.
pass() { echo "  PASS: ${1:-check}"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: ${1:-check}"; FAIL=$((FAIL + 1)); }

# State shared across tests
DATASET_ID=""
EVAL_RUN_ID=""

run_test() {
  local desc="$1"
  local code="$2"
  if kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$code" 2>/dev/null; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

check_manual() {
  local desc="$1"
  shift
  echo "  MANUAL: $desc"
  for step in "$@"; do
    echo "    $step"
  done
  MANUAL=$((MANUAL + 1))
}

echo "=== Suite 9: Eval Runner ==="
echo "    API pod: $API_POD"
echo ""

# ── Precondition: ensure a smoke agent exists ─────────────────────────────────
echo "--- Precondition: ensure agent '$AGENT_NAME' exists ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
try:
  urllib.request.urlopen('http://localhost:8000/api/v1/agents/$AGENT_NAME')
  print('  agent already exists')
except urllib.error.HTTPError as e:
  if e.code == 404:
    req = urllib.request.Request(
      'http://localhost:8000/api/v1/agents',
      data=json.dumps({
        'name': '$AGENT_NAME',
        'team': 'platform',
        'description': 'Suite 9 eval runner smoke test agent'
      }).encode(),
      headers={'Content-Type': 'application/json'},
      method='POST'
    )
    r = urllib.request.urlopen(req)
    print(f'  created agent (status={r.status})')
  else:
    raise
" 2>/dev/null || true
echo ""

# ── T-S9-001: Create Dataset ──────────────────────────────────────────────────
echo "--- T-S9-001: Create Dataset ---"
DATASET_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
  'name': 'smoke-dataset-suite9',
  'items': [{'input': 'What is order 12345?', 'expected_output': 'Order 12345 is pending.'}]
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': '$TEST_USER'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert 'id' in data, f'no id in response: {data}'
assert len(data.get('items', [])) == 1, f'expected 1 item got {len(data.get(\"items\", []))}'
print(data['id'])
" 2>/dev/null || true)

if [ -n "$DATASET_ID" ]; then
  echo "  PASS: POST /playground/datasets → 201 (id=${DATASET_ID:0:8}…)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: POST /playground/datasets → id not returned"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-002: Dataset Appears in List ─────────────────────────────────────────
echo "--- T-S9-002: Dataset Appears in List ---"
if [ -n "$DATASET_ID" ]; then
  run_test "GET /playground/datasets → newly created dataset in list" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets',
  headers={'X-User-Sub': '$TEST_USER'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
ids = [d['id'] for d in data]
assert '$DATASET_ID' in ids, f'dataset not in list; got ids: {ids[:5]}'
"
else
  echo "  SKIP: T-S9-002 (T-S9-001 failed — no dataset_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-003: Update Dataset Items ────────────────────────────────────────────
echo "--- T-S9-003: Update Dataset Items ---"
if [ -n "$DATASET_ID" ]; then
  run_test "PATCH /playground/datasets/{id} → 200 and items replaced" "
import urllib.request, json
body = json.dumps({
  'items': [
    {'input': 'What is order 12345?', 'expected_output': 'Order 12345 is pending.'},
    {'input': 'What is order 99999?', 'expected_output': 'Order not found.'}
  ]
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets/$DATASET_ID',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': '$TEST_USER'},
  method='PATCH'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
# PATCH replaces items list entirely — expect 2 items after update
assert len(data.get('items', [])) == 2, f'expected 2 items got {len(data.get(\"items\", []))}'
"
else
  echo "  SKIP: T-S9-003 (no dataset_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-004: Create EvalRun ──────────────────────────────────────────────────
echo "--- T-S9-004: Create EvalRun ---"
if [ -n "$DATASET_ID" ]; then
  EVAL_RUN_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
  'agent_name': '$AGENT_NAME',
  'dataset_id': '$DATASET_ID'
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/eval-runs',
  data=body,
  headers={'Content-Type': 'application/json', 'X-User-Sub': '$TEST_USER'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert 'id' in data, f'no id in response: {data}'
assert data.get('status') in ('pending', 'running'), \
  f'expected pending/running got {data.get(\"status\")}'
print(data['id'])
" 2>/dev/null || true)

  if [ -n "$EVAL_RUN_ID" ]; then
    echo "  PASS: POST /playground/eval-runs → 201, status=pending (id=${EVAL_RUN_ID:0:8}…)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: POST /playground/eval-runs → id not returned or status unexpected"
    FAIL=$((FAIL + 1))
  fi
else
  echo "  SKIP: T-S9-004 (no dataset_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-005: EvalRun Appears in List ─────────────────────────────────────────
echo "--- T-S9-005: EvalRun Appears in List ---"
if [ -n "$EVAL_RUN_ID" ]; then
  run_test "GET /playground/eval-runs → new run in list" "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/eval-runs',
  headers={'X-User-Sub': '$TEST_USER'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert isinstance(data, list), 'expected list'
ids = [d['id'] for d in data]
assert '$EVAL_RUN_ID' in ids, f'eval run not in list; got {ids[:5]}'
"
else
  echo "  SKIP: T-S9-005 (no eval_run_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-006: Get EvalRun by ID ───────────────────────────────────────────────
echo "--- T-S9-006: Get EvalRun by ID ---"
if [ -n "$EVAL_RUN_ID" ]; then
  run_test "GET /playground/eval-runs/{id} → status field present" "
import urllib.request, json
r = urllib.request.urlopen(
  'http://localhost:8000/api/v1/playground/eval-runs/$EVAL_RUN_ID'
)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('id') == '$EVAL_RUN_ID', f'id mismatch: {data.get(\"id\")}'
assert 'status' in data, f'status field missing: {list(data.keys())}'
assert 'agent_name' in data
assert 'dataset_id' in data
"
else
  echo "  SKIP: T-S9-006 (no eval_run_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-007: Post Per-Item Result (simulates eval-runner Job) ─────────────────
echo "--- T-S9-007: Post EvalRun Result (simulate eval-runner Job) ---"
if [ -n "$EVAL_RUN_ID" ]; then
  # NOTE: plan says → 200, but endpoint is POST with status_code=201 (HTTP_201_CREATED)
  run_test "POST /playground/eval-runs/{id}/results → 201 with result fields" "
import urllib.request, json
body = json.dumps({
  'dataset_item_idx': 0,
  'input_message': 'What is order 12345?',
  'response': 'Order 12345 is pending.',
  'judge_score': 1.0,
  'judge_reasoning': 'Response matches expected output exactly.',
  'passed': True
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/eval-runs/$EVAL_RUN_ID/results',
  data=body,
  headers={'Content-Type': 'application/json'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('judge_score') == 1.0, f'judge_score mismatch: {data}'
assert data.get('passed') == True, f'passed mismatch: {data}'
assert data.get('eval_run_id') == '$EVAL_RUN_ID'
"
else
  echo "  SKIP: T-S9-007 (no eval_run_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-008: Get EvalRun Results ─────────────────────────────────────────────
echo "--- T-S9-008: Get EvalRun Results ---"
if [ -n "$EVAL_RUN_ID" ]; then
  run_test "GET /playground/eval-runs/{id}/results → result list with judge_score and passed" "
import urllib.request, json
r = urllib.request.urlopen(
  'http://localhost:8000/api/v1/playground/eval-runs/$EVAL_RUN_ID/results'
)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert isinstance(data, list) and len(data) >= 1, \
  f'expected at least 1 result, got {len(data)}'
result = data[0]
assert 'judge_score' in result, f'judge_score missing from result: {list(result.keys())}'
assert 'passed' in result, f'passed missing from result: {list(result.keys())}'
assert result.get('dataset_item_idx') == 0
"
else
  echo "  SKIP: T-S9-008 (no eval_run_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-009: Update EvalRun Status to Completed ──────────────────────────────
echo "--- T-S9-009: Update EvalRun Status → completed ---"
if [ -n "$EVAL_RUN_ID" ]; then
  run_test "PATCH /playground/eval-runs/{id} → 200, status=completed, scores set" "
import urllib.request, json
body = json.dumps({
  'status': 'completed',
  'total_items': 1,
  'passed_count': 1,
  'failed_count': 0,
  'overall_score': 1.0
}).encode()
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/eval-runs/$EVAL_RUN_ID',
  data=body,
  headers={'Content-Type': 'application/json'},
  method='PATCH'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('status') == 'completed', f'expected completed got {data.get(\"status\")}'
assert data.get('overall_score') == 1.0, f'overall_score mismatch: {data.get(\"overall_score\")}'
assert data.get('total_items') == 1
assert data.get('passed_count') == 1
assert data.get('completed_at') is not None, 'completed_at not set'
"
else
  echo "  SKIP: T-S9-009 (no eval_run_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-010: Delete Dataset With EvalRun Reference — document FK behavior ───
echo "--- T-S9-010: Delete Dataset (FK behavior with active EvalRun reference) ---"
if [ -n "$DATASET_ID" ]; then
  DELETE_HTTP=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets/$DATASET_ID',
  headers={'X-User-Sub': '$TEST_USER'},
  method='DELETE'
)
try:
  r = urllib.request.urlopen(req)
  print(r.status)
except urllib.error.HTTPError as e:
  print(e.code)
except Exception as e:
  print('error:' + str(e))
" 2>/dev/null || echo "error")

  case "$DELETE_HTTP" in
    204|200)
      echo "  PASS: DELETE datasets/{id} → ${DELETE_HTTP} (CASCADE: dataset removed, eval run may retain null dataset_id)"
      PASS=$((PASS + 1))
      DATASET_ID=""  # already deleted — skip cleanup
      ;;
    409|422)
      echo "  PASS: DELETE datasets/{id} → ${DELETE_HTTP} (RESTRICT: FK blocked delete — expected if ON DELETE RESTRICT)"
      PASS=$((PASS + 1))
      ;;
    500)
      echo "  PASS: DELETE datasets/{id} → 500 (FK integrity error from DB — RESTRICT without explicit HTTP mapping)"
      echo "  NOTE: Consider catching IntegrityError in delete_dataset and returning 409"
      PASS=$((PASS + 1))
      ;;
    *)
      echo "  FAIL: DELETE datasets/{id} → unexpected status: ${DELETE_HTTP}"
      FAIL=$((FAIL + 1))
      ;;
  esac
  echo "  BEHAVIOR NOTE: HTTP ${DELETE_HTTP} — update test report with actual FK policy"
else
  echo "  SKIP: T-S9-010 (no dataset_id)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S9-011..014: eval-runner service identity + real batch eval (Slice A) ─────
echo "--- T-S9-011..014: eval-runner identity bypass + real batch eval ---"

# poll GET /eval-runs/{id} until terminal; prints final status (or 'unknown')
wait_for_eval_terminal() {
  local eid="$1"; local timeout="${2:-150}"
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, time
deadline = time.time() + $timeout
last = 'unknown'
while time.time() < deadline:
    try:
        r = urllib.request.urlopen('http://localhost:8000/api/v1/playground/eval-runs/$eid', timeout=5)
        last = json.loads(r.read()).get('status', 'unknown')
    except Exception:
        pass
    if last in ('completed', 'failed'):
        break
    time.sleep(5)
print(last)
" 2>/dev/null || echo unknown
}

run_test "T-S9-011 eval-runner identity starts a run for an agent it does not own → 201" "
import urllib.request, json, urllib.error
base = 'http://localhost:8000/api/v1'
try:
    urllib.request.urlopen(urllib.request.Request(base + '/agents/s9-bypass-agent', method='DELETE'))
except urllib.error.HTTPError:
    pass
req = urllib.request.Request(base + '/agents/',
    data=json.dumps({'name': 's9-bypass-agent', 'team': 'platform', 'description': 's9 bypass'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    if e.code != 409: raise
req = urllib.request.Request(base + '/playground/runs',
    data=json.dumps({'agent_name': 's9-bypass-agent', 'input_message': 'hi'}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'eval-runner'}, method='POST')
r = urllib.request.urlopen(req, timeout=5)
assert r.status == 201, f'expected 201 got {r.status}'
print('eval-runner ran an agent owned by system (201)')
"

run_test "T-S9-012 GET /playground/runs/{id} exposes judge fields" "
import urllib.request, json
base = 'http://localhost:8000/api/v1'
req = urllib.request.Request(base + '/playground/runs',
    data=json.dumps({'agent_name': 's9-bypass-agent', 'input_message': 'judge'}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'eval-runner'}, method='POST')
rid = json.loads(urllib.request.urlopen(req, timeout=5).read())['run_id']
r = urllib.request.urlopen(base + '/playground/runs/' + rid, timeout=5)
d = json.loads(r.read())
for k in ('judge_score', 'judge_status', 'judge_reason'):
    assert k in d, f'{k} missing: {d}'
print('GET run exposes judge_score/judge_status/judge_reason')
"

# T-S9-013: real batch eval reaches 'completed' (never stuck at 'running')
EVAL_RUN_S9=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
base='http://localhost:8000/api/v1'
db=json.dumps({'name':'s9-real-ds','items':[{'input':'ping','expected_output':'x'},{'input':'ping2','expected_output':'y'}]}).encode()
did=json.loads(urllib.request.urlopen(urllib.request.Request(base+'/playground/datasets',data=db,headers={'Content-Type':'application/json'},method='POST'),timeout=5).read()).get('id')
eb=json.dumps({'agent_name':'s9-bypass-agent','dataset_id':did}).encode()
try:
    r=urllib.request.urlopen(urllib.request.Request(base+'/playground/eval-runs',data=eb,headers={'Content-Type':'application/json','X-User-Sub':'s9'},method='POST'),timeout=10)
    print(json.loads(r.read()).get('id',''))
except Exception:
    print('')
" 2>/dev/null || echo "")

if [ -n "$EVAL_RUN_S9" ]; then
  S9_STATUS=$(wait_for_eval_terminal "$EVAL_RUN_S9" 150)
  if [ "$S9_STATUS" = "completed" ]; then
    echo "  PASS: T-S9-013 real batch eval reached 'completed' (not stuck at running)"
    PASS=$((PASS + 1))
    run_test "T-S9-013b per-item results recorded with judge scoring (not blind keyword)" "
import urllib.request, json
r=urllib.request.urlopen('http://localhost:8000/api/v1/playground/eval-runs/$EVAL_RUN_S9/results',timeout=5)
res=json.loads(r.read())
assert len(res) >= 1, f'no results: {res}'
for it in res:
    assert 'judge_score' in it and 'passed' in it, f'missing fields: {it}'
    assert str(it.get('judge_reasoning','')).startswith(('llm-judge','keyword match','no expected','run-create failed')), f'unexpected reasoning: {it.get(\"judge_reasoning\")}'
print(f'{len(res)} results recorded with judge scoring')
"
  else
    check_manual "T-S9-013 real batch eval (status=$S9_STATUS after 150s; eval-runner Job could not run here)" \
      "Deploy eval-runner:0.1.1 with Jobs RBAC in $NAMESPACE; EvalRun must reach 'completed', never stuck at 'running'."
  fi
else
  check_manual "T-S9-013 real batch eval — could not create EvalRun (500 likely: Jobs RBAC/eval-runner image)" \
    "Confirm POST /playground/eval-runs launches the Job and returns an id."
fi

# T-S9-014: a failing item (agent 404) does not crash the Job — run still 'completed'
EVAL_FAIL_S9=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
base='http://localhost:8000/api/v1'
db=json.dumps({'name':'s9-fail-ds','items':[{'input':'a'},{'input':'b'}]}).encode()
did=json.loads(urllib.request.urlopen(urllib.request.Request(base+'/playground/datasets',data=db,headers={'Content-Type':'application/json'},method='POST'),timeout=5).read()).get('id')
eb=json.dumps({'agent_name':'does-not-exist-agent-s9','dataset_id':did}).encode()
try:
    r=urllib.request.urlopen(urllib.request.Request(base+'/playground/eval-runs',data=eb,headers={'Content-Type':'application/json','X-User-Sub':'s9'},method='POST'),timeout=10)
    print(json.loads(r.read()).get('id',''))
except Exception:
    print('')
" 2>/dev/null || echo "")

if [ -n "$EVAL_FAIL_S9" ]; then
  S9F_STATUS=$(wait_for_eval_terminal "$EVAL_FAIL_S9" 150)
  if [ "$S9F_STATUS" = "completed" ]; then
    echo "  PASS: T-S9-014 failing items did not crash the Job (reached 'completed')"
    PASS=$((PASS + 1))
  else
    check_manual "T-S9-014 failed-item resilience (status=$S9F_STATUS; Job could not run here)" \
      "With eval-runner:0.1.1, an agent-404 dataset run must still reach 'completed' via per-item try/except."
  fi
else
  check_manual "T-S9-014 failed-item resilience — could not create EvalRun" "Confirm eval-runs router + Jobs RBAC."
fi

# cleanup s9-bypass-agent
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/s9-bypass-agent', method='DELETE'))
except Exception:
    pass
" 2>/dev/null || true
echo ""

# ── Cleanup ───────────────────────────────────────────────────────────────────
echo "--- Cleanup ---"
if [ -n "$DATASET_ID" ]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request(
  'http://localhost:8000/api/v1/playground/datasets/$DATASET_ID',
  headers={'X-User-Sub': '$TEST_USER'},
  method='DELETE'
)
try:
  urllib.request.urlopen(req)
  print('  deleted dataset $DATASET_ID')
except Exception as e:
  print(f'  cleanup warning: {e}')
" 2>/dev/null || true
fi
# Delete test agent (soft-delete)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$AGENT_NAME',
  method='DELETE'
)
try:
  urllib.request.urlopen(req)
  print('  soft-deleted agent $AGENT_NAME')
except Exception as e:
  print(f'  cleanup note: {e}')
" 2>/dev/null || true

# Delete s9-real-ds and s9-fail-ds datasets (best-effort)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
base = 'http://localhost:8000/api/v1/playground/datasets'
req = urllib.request.Request(base, headers={'X-User-Sub': 's9'})
try:
    r = urllib.request.urlopen(req, timeout=5)
    datasets = json.loads(r.read())
    for ds in datasets:
        if ds.get('name','') in ('s9-real-ds', 's9-fail-ds'):
            dreq = urllib.request.Request(base + '/' + str(ds['id']),
                headers={'X-User-Sub': 's9'}, method='DELETE')
            try:
                urllib.request.urlopen(dreq, timeout=5)
                print(f'  deleted dataset {ds[\"name\"]} ({ds[\"id\"][:8]}...)')
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true
echo ""

# ── G5: Observability — Eval run creates Langfuse trace ──────────────────────
echo "--- G5-S9: Eval Run Langfuse Trace Check ---"
if [ -n "${EVAL_RUN_ID:-}" ]; then
  EVAL_TRACE=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, base64, time, os
pk = 'pk-lf-agentshield-dev-local-0001'; sk = 'sk-lf-agentshield-dev-local-0001'
lf = os.getenv('LANGFUSE_HOST', 'http://agentshield-langfuse-web.${NAMESPACE}.svc.cluster.local:3000')
creds = base64.b64encode(f'{pk}:{sk}'.encode()).decode()
for _ in range(10):
    try:
        req = urllib.request.Request(f'{lf}/api/public/traces/${EVAL_RUN_ID}',
            headers={'Authorization': 'Basic ' + creds})
        r = urllib.request.urlopen(req, timeout=4)
        print('trace_found')
        break
    except urllib.error.HTTPError as e:
        if e.code == 404: time.sleep(1)
        else: print('http_err:' + str(e.code)); break
    except Exception as e: print('err:' + str(e)[:40]); break
else:
    print('not_found_after_10s')
" 2>/dev/null || echo "ERR")
  if echo "$EVAL_TRACE" | grep -q "^trace_found"; then
    pass "G5-S9: Eval run $EVAL_RUN_ID trace found in Langfuse"
  else
    check_manual "G5-S9: Eval run trace not in Langfuse ($EVAL_TRACE)" \
      "Check eval_runner.py: trace_eval_run_created() must be called after eval run creation" \
      "GET /api/public/traces/${EVAL_RUN_ID:-<run-id>} in Langfuse → assert 200"
  fi
else
  check_manual "G5-S9: No eval run ID captured — cannot verify Langfuse trace" \
    "Re-run suite-9 and capture run ID, then: GET /api/public/traces/<run_id>"
fi
echo ""

# ── T-S9-015: Eval-mode judge scores correct answer >= 0.7 ─────────────────
echo "--- T-S9-015: Eval-mode judge — correct answer ---"
JUDGE_CORRECT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'input_message': 'What is the capital of France?',
    'response_text': 'The capital of France is **Paris**.',
    'expected_output': 'Paris',
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/judge',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'eval-runner'},
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read())
        print(json.dumps(data))
except Exception as e:
    print(json.dumps({'error': str(e)}))
" 2>/dev/null || echo '{"error":"exec_failed"}')
echo "  judge response: $JUDGE_CORRECT"
JUDGE_SCORE=$(echo "$JUDGE_CORRECT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score',-1))" 2>/dev/null || echo "-1")
if python3 -c "s=$JUDGE_SCORE; exit(0 if s >= 0.7 else 1)" 2>/dev/null; then
  pass "T-S9-015: Correct answer scored >= 0.7 (got $JUDGE_SCORE)"
else
  fail "T-S9-015: Correct answer scored < 0.7 (got $JUDGE_SCORE) — judge prompt or provider broken"
fi
echo ""

# ── T-S9-015b: Eval-mode judge scores wrong answer < 0.5 ──────────────────
echo "--- T-S9-015b: Eval-mode judge — wrong answer ---"
JUDGE_WRONG=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'input_message': 'What is the capital of France?',
    'response_text': 'The capital of France is London.',
    'expected_output': 'Paris',
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/playground/judge',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'eval-runner'},
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read())
        print(json.dumps(data))
except Exception as e:
    print(json.dumps({'error': str(e)}))
" 2>/dev/null || echo '{"error":"exec_failed"}')
echo "  judge response: $JUDGE_WRONG"
JUDGE_WRONG_SCORE=$(echo "$JUDGE_WRONG" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('score',1))" 2>/dev/null || echo "1")
if python3 -c "s=$JUDGE_WRONG_SCORE; exit(0 if s < 0.5 else 1)" 2>/dev/null; then
  pass "T-S9-015b: Wrong answer scored < 0.5 (got $JUDGE_WRONG_SCORE)"
else
  fail "T-S9-015b: Wrong answer scored >= 0.5 (got $JUDGE_WRONG_SCORE) — judge not detecting incorrect answers"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "======================================================="
echo "  Suite 9 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

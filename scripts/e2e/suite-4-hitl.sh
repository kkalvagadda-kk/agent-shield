#!/usr/bin/env bash
# Suite 4: HITL Approval Flow (Production)
# Tests T-S4-001 through T-S4-007
#
# API contracts (from routers/approvals.py + schemas.py):
#   POST   /api/v1/approvals/       — body: ApprovalCreate (agent_id UUID required)
#   GET    /api/v1/approvals/       — query params: status, thread_id; filters context=production
#   GET    /api/v1/approvals/{id}   — single approval (returns version for optimistic lock)
#   PATCH  /api/v1/approvals/{id}   — body: { decision: "approved"|"rejected",
#                                              reviewer_id: str,
#                                              version: int }
#                                    reviewer_id="system" bypasses authority check (test use)
#
# The SSE stream portion of T-S4-001 (approval_requested event) is a MANUAL test.
# The automated portion creates and verifies the approval record directly.
#
# Usage:
#   bash scripts/e2e/suite-4-hitl.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-4-hitl.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0; MANUAL=0

TS=$(date +%s)
HITL_AGENT="hitl-smoke-${TS}"

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
check_manual() {
  local desc="$1"; shift
  echo "  MANUAL: $desc"
  printf "    Steps: %s\n" "$*"
  MANUAL=$((MANUAL + 1))
}

# Find the Registry API pod
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "${API_POD:-}" ]; then
  echo "FATAL: Registry API pod not found in $NAMESPACE"
  exit 1
fi

# Cleanup on exit: deprecate test agent
cleanup() {
  echo ""
  echo "==> Cleanup: deprecating $HITL_AGENT..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/agents/${HITL_AGENT}',
        data=json.dumps({'status': 'deprecated'}).encode(),
        headers={'Content-Type': 'application/json'}, method='PUT'
    )
    urllib.request.urlopen(req, timeout=5)
    print('  deprecated: ${HITL_AGENT}')
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 4: HITL Approval Flow (Production)"
echo "    Namespace:  $NAMESPACE"
echo "    HITL agent: $HITL_AGENT"
echo ""

# ── Setup: Create a smoke agent and capture its UUID ─────────────────────
echo "==> Setup: registering HITL smoke agent..."
SETUP_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, sys
body = json.dumps({
    'name': '${HITL_AGENT}',
    'team': 'platform',
    'description': 'HITL Suite 4 smoke agent',
    'agent_type': 'sdk'
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    d = json.loads(r.read())
    print(r.getcode())
    print(d.get('id', ''))
except urllib.error.HTTPError as e:
    if e.code == 409:
        r2 = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${HITL_AGENT}', timeout=5)
        d = json.loads(r2.read())
        print(409)
        print(d.get('id', ''))
    else:
        print(e.code)
        print('')
" 2>/dev/null || echo "0
")
SETUP_STATUS=$(echo "$SETUP_OUT" | sed -n '1p')
HITL_AGENT_ID=$(echo "$SETUP_OUT" | sed -n '2p')

if [ -z "${HITL_AGENT_ID:-}" ] || [ "$SETUP_STATUS" = "0" ]; then
  echo "FATAL: Could not create/fetch HITL smoke agent (status=$SETUP_STATUS)"
  exit 1
fi
echo "  Agent $HITL_AGENT created (id=$HITL_AGENT_ID)"
echo ""

# Helper: create an approval and return its ID and version (space-separated)
# Usage: create_approval <thread_id> [timeout_seconds]
create_approval() {
  local thread_id="$1" timeout_sec="${2:-1800}"
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'agent_id': '${HITL_AGENT_ID}',
    'agent_name': '${HITL_AGENT}',
    'team': 'platform',
    'thread_id': '${thread_id}',
    'tool_name': 'issue_refund',
    'tool_args': {'order_id': 'ORD-12345', 'amount': 99.99},
    'risk_level': 'high',
    'timeout_seconds': ${timeout_sec},
    'context': 'production'
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    d = json.loads(r.read())
    print(r.getcode())
    print(d.get('id', ''))
    print(d.get('version', 0))
    print(d.get('status', ''))
except urllib.error.HTTPError as e:
    print(e.code)
    print('')
    print('')
    print(e.read().decode()[:200])
" 2>/dev/null || echo "0


ERR"
}

# ── T-S4-001: Create Approval (automated portion) + SSE stream (manual) ───
echo "--- T-S4-001: Invoke High-Risk Tool → approval_requested ---"
THREAD_T4_001="t4-001-${TS}"
CREATE_OUT=$(create_approval "$THREAD_T4_001" 1800)
CREATE_STATUS=$(echo "$CREATE_OUT" | sed -n '1p')
APPROVAL_ID_001=$(echo "$CREATE_OUT" | sed -n '2p')
APPROVAL_VER_001=$(echo "$CREATE_OUT" | sed -n '3p')
CREATE_DETAIL=$(echo "$CREATE_OUT" | sed -n '4p')

if [ "$CREATE_STATUS" = "201" ] && [ -n "${APPROVAL_ID_001:-}" ]; then
  pass "T-S4-001 (automated): Approval record created (id=$APPROVAL_ID_001, version=$APPROVAL_VER_001, status=pending)"
else
  fail "T-S4-001: Create approval returned $CREATE_STATUS ($CREATE_DETAIL)"
  APPROVAL_ID_001=""
fi

# SSE stream portion is manual (requires live agent pod + LLM credentials)
check_manual "T-S4-001 (SSE stream): approval_requested event emitted from /chat/stream" \
  "Deploy order-agent with LLM credentials in pod env" \
  "POST /chat/stream with '{\"message\":\"please issue a refund for order 12345\",\"thread_id\":\"t4-001\"}'; read SSE events; assert 'approval_requested' event received before 'done'"
echo ""

# ── T-S4-002: Pending Approval Appears in Queue ────────────────────────────
echo "--- T-S4-002: Pending Approval Appears in GET /approvals ---"
if [ -n "${APPROVAL_ID_001:-}" ]; then
  LIST_RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen(
        'http://localhost:8000/api/v1/approvals/?status=pending',
        timeout=10
    )
    d = json.loads(r.read())
    items = d.get('items', [])
    # Find our approval
    match = [x for x in items if x.get('thread_id') == '${THREAD_T4_001}']
    if match:
        a = match[0]
        print('found')
        print(a.get('id', ''))
        print(a.get('status', ''))
        print(a.get('tool_name', ''))
    else:
        print('not_found')
        print('')
        print('')
        print('total_pending=' + str(len(items)))
except Exception as e:
    print('ERR:' + str(e))
" 2>/dev/null || echo "ERR")
  LIST_STATUS=$(echo "$LIST_RESULT" | sed -n '1p')
  LIST_ID=$(echo "$LIST_RESULT" | sed -n '2p')
  LIST_ASTATUS=$(echo "$LIST_RESULT" | sed -n '3p')
  LIST_TOOL=$(echo "$LIST_RESULT" | sed -n '4p')

  if [ "$LIST_STATUS" = "found" ]; then
    pass "T-S4-002: Approval in pending queue (id=$LIST_ID, status=$LIST_ASTATUS, tool=$LIST_TOOL)"
  else
    fail "T-S4-002: Approval $APPROVAL_ID_001 not found in pending list ($LIST_RESULT)"
  fi
else
  fail "T-S4-002: No approval_id from T-S4-001 — skipping"
fi
echo ""

# ── T-S4-003: Approve Decision Resumes Stream ──────────────────────────────
echo "--- T-S4-003: Approve Decision → 200, decision_at Set ---"
if [ -n "${APPROVAL_ID_001:-}" ]; then
  APPROVE_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'decision': 'approved',
    'reviewer_id': 'system',
    'reviewer_notes': 'E2E Suite 4 auto-test',
    'version': ${APPROVAL_VER_001:-0}
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${APPROVAL_ID_001}',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='PATCH'
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    d = json.loads(r.read())
    print(r.getcode())
    print(d.get('status', ''))
    print(d.get('decision_at') or '')
    print(d.get('reviewer_id', ''))
    print(d.get('version', 0))
except urllib.error.HTTPError as e:
    print(e.code)
    print('')
    print('')
    print(e.read().decode()[:200])
" 2>/dev/null || echo "0

ERR")
  APPROVE_STATUS=$(echo "$APPROVE_OUT" | sed -n '1p')
  APPROVE_ASTATUS=$(echo "$APPROVE_OUT" | sed -n '2p')
  APPROVE_DEC_AT=$(echo "$APPROVE_OUT" | sed -n '3p')
  APPROVE_REVIEWER=$(echo "$APPROVE_OUT" | sed -n '4p')
  APPROVE_VER=$(echo "$APPROVE_OUT" | sed -n '5p')

  if [ "$APPROVE_STATUS" = "200" ] && [ "$APPROVE_ASTATUS" = "approved" ]; then
    if [ -n "${APPROVE_DEC_AT:-}" ] && [ "$APPROVE_DEC_AT" != "None" ]; then
      pass "T-S4-003: Approval approved (status=$APPROVE_ASTATUS, decision_at=$APPROVE_DEC_AT, reviewer=$APPROVE_REVIEWER)"
    else
      fail "T-S4-003: PATCH returned 200 and status=approved but decision_at is not set"
    fi
  else
    fail "T-S4-003: PATCH returned $APPROVE_STATUS (status=$APPROVE_ASTATUS)"
  fi

  # T-S4-007 is verified inline here: decision_at is set
  if [ -n "${APPROVE_DEC_AT:-}" ] && [ "$APPROVE_DEC_AT" != "None" ] && [ "$APPROVE_STATUS" = "200" ]; then
    pass "T-S4-007: decision_at is set after approve (decision_at=$APPROVE_DEC_AT)"
  elif [ "$APPROVE_STATUS" = "200" ]; then
    fail "T-S4-007: decision_at was not set after approve"
  fi
else
  fail "T-S4-003: No approval_id from T-S4-001 — skipping"
  fail "T-S4-007: Cannot verify decision_at (no approval from T-S4-001)"
fi
echo ""
# Note: SSE resume stream verification (approval_decided event → done) is manual
check_manual "T-S4-003 (SSE resume): approval_decided and done events emitted after approve" \
  "With live agent pod: POST /resume/{thread_id}; read SSE; assert approval_decided then done events"
echo ""

# ── T-S4-004: HITL Timeout ────────────────────────────────────────────────
echo "--- T-S4-004: HITL Timeout ---"
# Create a new approval with a short timeout and verify the expiry is enforced.
# The API has no explicit min on timeout_seconds in ApprovalCreate, so we try 5s.
# We wait 10s then attempt to PATCH — the approve endpoint checks expires_at < now.
# Note: status may not auto-transition to 'timed_out' without a background worker.
THREAD_T4_004="t4-004-${TS}"
TIMEOUT_CREATE=$(create_approval "$THREAD_T4_004" 5)
TIMEOUT_STATUS=$(echo "$TIMEOUT_CREATE" | sed -n '1p')
TIMEOUT_ID=$(echo "$TIMEOUT_CREATE" | sed -n '2p')
TIMEOUT_VER=$(echo "$TIMEOUT_CREATE" | sed -n '3p')
TIMEOUT_DETAIL=$(echo "$TIMEOUT_CREATE" | sed -n '4p')

if [ "$TIMEOUT_STATUS" = "201" ] && [ -n "${TIMEOUT_ID:-}" ]; then
  echo "  Approval created (id=$TIMEOUT_ID). Waiting 10s for expiry..."
  sleep 10
  # Attempt to approve the expired approval — should get 409
  TIMEOUT_PATCH=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'decision': 'approved',
    'reviewer_id': 'system',
    'version': ${TIMEOUT_VER:-0}
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${TIMEOUT_ID}',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='PATCH'
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    print(r.getcode())
    print('unexpectedly_passed')
except urllib.error.HTTPError as e:
    print(e.code)
    body = e.read().decode()
    print('expired' if 'expired' in body.lower() else body[:100])
" 2>/dev/null || echo "0
ERR")
  TPATCH_STATUS=$(echo "$TIMEOUT_PATCH" | sed -n '1p')
  TPATCH_DETAIL=$(echo "$TIMEOUT_PATCH" | sed -n '2p')

  if [ "$TPATCH_STATUS" = "409" ] && echo "$TPATCH_DETAIL" | grep -qi "expired"; then
    pass "T-S4-004: Expired approval PATCH returned 409 with 'expired' message"
  elif [ "$TPATCH_STATUS" = "409" ]; then
    pass "T-S4-004: Expired approval PATCH returned 409 ($TPATCH_DETAIL)"
  else
    fail "T-S4-004: Expected 409 for expired approval, got $TPATCH_STATUS ($TPATCH_DETAIL)"
  fi

  # Check if background worker has transitioned status to timed_out
  TCHECK=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals/${TIMEOUT_ID}', timeout=5)
d = json.loads(r.read())
print(d.get('status', ''))
" 2>/dev/null || echo "unknown")
  if [ "$TCHECK" = "timed_out" ]; then
    pass "T-S4-004: Status auto-transitioned to 'timed_out' (background worker active)"
  else
    echo "  NOTE: Status is '$TCHECK' (not 'timed_out') — no background timeout worker may be running"
    echo "        The expiry enforcement above (409 on PATCH) is the primary validation."
  fi
else
  if echo "$TIMEOUT_DETAIL" | grep -qi "minimum\|validation\|ge="; then
    check_manual "T-S4-004: timeout_seconds validation rejected short timeout ($TIMEOUT_DETAIL)" \
      "Set approval_timeout_seconds to minimum allowed value and wait; then attempt PATCH; expect 409 with 'expired'"
  else
    fail "T-S4-004: Could not create approval for timeout test (status=$TIMEOUT_STATUS, $TIMEOUT_DETAIL)"
  fi
fi
echo ""

# ── T-S4-005: HITL Deny Closes Stream ─────────────────────────────────────
echo "--- T-S4-005: Reject Decision → 200 ---"
THREAD_T4_005="t4-005-${TS}"
REJECT_CREATE=$(create_approval "$THREAD_T4_005" 1800)
REJECT_STATUS=$(echo "$REJECT_CREATE" | sed -n '1p')
REJECT_ID=$(echo "$REJECT_CREATE" | sed -n '2p')
REJECT_VER=$(echo "$REJECT_CREATE" | sed -n '3p')

if [ "$REJECT_STATUS" = "201" ] && [ -n "${REJECT_ID:-}" ]; then
  REJECT_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'decision': 'rejected',
    'reviewer_id': 'system',
    'reviewer_notes': 'E2E reject test',
    'version': ${REJECT_VER:-0}
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${REJECT_ID}',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='PATCH'
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    d = json.loads(r.read())
    print(r.getcode())
    print(d.get('status', ''))
    print(d.get('decision_at') or '')
except urllib.error.HTTPError as e:
    print(e.code)
    print('')
    print(e.read().decode()[:150])
" 2>/dev/null || echo "0

ERR")
  REJ_PATCH_STATUS=$(echo "$REJECT_OUT" | sed -n '1p')
  REJ_ASTATUS=$(echo "$REJECT_OUT" | sed -n '2p')
  REJ_DEC_AT=$(echo "$REJECT_OUT" | sed -n '3p')

  if [ "$REJ_PATCH_STATUS" = "200" ] && [ "$REJ_ASTATUS" = "rejected" ]; then
    pass "T-S4-005: Rejection PATCH returned 200 (status=$REJ_ASTATUS, decision_at=$REJ_DEC_AT)"
  else
    fail "T-S4-005: PATCH returned $REJ_PATCH_STATUS (status=$REJ_ASTATUS, detail=$REJ_DEC_AT)"
  fi
else
  fail "T-S4-005: Could not create approval for reject test (status=$REJECT_STATUS)"
fi

# SSE stream close after rejection is manual (requires live agent)
check_manual "T-S4-005 (SSE stream): approval_decided(rejected) emitted then stream closes" \
  "With live agent pod: after rejection PATCH, POST /resume/{thread_id}; read SSE; assert 'approval_decided' with decision='rejected' then EOF within 10s"
echo ""

# ── T-S4-006: Optimistic Lock Prevents Double-Approval ────────────────────
echo "--- T-S4-006: Optimistic Lock Prevents Double-Approval ---"
THREAD_T4_006="t4-006-${TS}"
LOCK_CREATE=$(create_approval "$THREAD_T4_006" 1800)
LOCK_STATUS=$(echo "$LOCK_CREATE" | sed -n '1p')
LOCK_ID=$(echo "$LOCK_CREATE" | sed -n '2p')
LOCK_VER=$(echo "$LOCK_CREATE" | sed -n '3p')

if [ "$LOCK_STATUS" = "201" ] && [ -n "${LOCK_ID:-}" ]; then
  # PATCH with stale version (99) → expect 409 version conflict
  LOCK_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'decision': 'approved',
    'reviewer_id': 'system',
    'version': 99
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${LOCK_ID}',
    data=body,
    headers={'Content-Type': 'application/json'},
    method='PATCH'
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    print(r.getcode())
    print('unexpectedly_passed')
except urllib.error.HTTPError as e:
    print(e.code)
    body = e.read().decode()
    print('optimistic_lock_conflict' if ('version' in body or 'conflict' in body.lower()) else body[:100])
" 2>/dev/null || echo "0
ERR")
  LOCK_PATCH_STATUS=$(echo "$LOCK_OUT" | sed -n '1p')
  LOCK_DETAIL=$(echo "$LOCK_OUT" | sed -n '2p')

  if [ "$LOCK_PATCH_STATUS" = "409" ]; then
    pass "T-S4-006: Stale-version PATCH returned 409 ($LOCK_DETAIL)"
  else
    fail "T-S4-006: Expected 409 for stale version, got $LOCK_PATCH_STATUS ($LOCK_DETAIL)"
  fi
else
  fail "T-S4-006: Could not create approval for lock test (status=$LOCK_STATUS)"
fi
echo ""

# ── T-S4-007: Approval Audit Row (decision_at) ────────────────────────────
echo "--- T-S4-007: decision_at Set After Approve ---"
echo "  (Verified inline with T-S4-003 above)"
echo ""

echo "========================================================"
echo "  Suite 4 Results: $PASS passed, $FAIL failed, $MANUAL manual"
echo "========================================================"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

#!/usr/bin/env bash
# scripts/e2e/suite-5-hitl-authority.sh
#
# E2E Suite 5: HITL Authority Scoping (Phase 9.3)
# Tests T-S5-001 through T-S5-005.
#
# What this proves — with TWO REAL Keycloak personas, both 'contributor', so the only thing
# separating them is the ApprovalAuthority grant this suite creates (that grant IS the
# subject). Each acts with its OWN token; neither is named by a header:
#   T-S5-001 — ApprovalAuthority created for issue_refund → s5-reviewer-1's sub (201)
#   T-S5-001 — GET /admin/approval-authority returns the record
#   T-S5-002 — s5-reviewer-1 (granted) sees the pending approval for issue_refund
#   T-S5-003 — s5-reviewer-2 (no grant) sees an empty list
#   T-S5-004 — s5-reviewer-2 PATCH decide → 403 not_authorized_to_decide, row still pending
#   T-S5-005 — s5-reviewer-1 PATCH decide → 200 approved; reviewer_id is its verified sub
#
# API notes vs. test plan:
#   - Endpoint is /api/v1/admin/approval-authority (not /api/v1/approval-authorities)
#   - PATCH decide path is /api/v1/approvals/{id} (no /decide suffix)
#   - ApprovalDecision body requires: decision, reviewer_id, version (optimistic lock)
#   - Authority scoping IS implemented in the approvals router
#
# Usage:
#   bash scripts/e2e/suite-5-hitl-authority.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-5-hitl-authority.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

# R1/FR-11: every /api/v1/admin/* route (routers/admin.py) requires a real JWT — here that
# is the approval-authority create / list / delete.
#
# THE REST OF THIS COMMENT USED TO SAY '/api/v1/approvals/*' and '/api/v1/agents/*' "are NOT
# among R1's ten routers and stay as they are". That is no longer true and following it is
# what left this suite red:
#   * identity P3 (0.2.279) required a credential on GET /approvals/ and PATCH /approvals/{id}
#   * 0.2.281 added GET /approvals/{id} and POST /approvals/{id}/reopen
#   * R3 (0.2.264) gated DELETE /api/v1/agents/{name}
# EVERY call in this suite carries a credential now, and the two reviewers are real Keycloak
# personas rather than header strings. Call e2e_set_token BARE — a command substitution
# swallows its abort (lib/e2e-auth.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

# ── REAL reviewer personas, not the strings 'reviewer-1' / 'reviewer-2' ───────
# This suite identified its two reviewers by typing 'X-User-Sub: reviewer-1' on requests
# that carried no credential at all. Identity P3 made the caller come from the credential
# and ONLY the credential, so those cases stopped testing authority and started returning
# 401 — T-S5-004 expects 403 and got 401, which is a different rule failing. Worse, a sub
# no Keycloak user owns cannot authenticate at all, so "act as reviewer-1" is no longer a
# thing a test can express with a header. It needs a CREDENTIAL.
#
# 'e2e_ensure_persona' creates each user through the real POST /api/v1/admin/users (which
# also writes its user_team_assignments row) and echoes a token. Both are 'contributor':
# the authority difference between them must come from the ApprovalAuthority grant this
# suite creates, which is the thing under test — not from one of them being an admin.
S5_R1_TOKEN="$(e2e_ensure_persona "$NAMESPACE" "$API_POD" "s5-reviewer-1" "contributor")"
S5_R2_TOKEN="$(e2e_ensure_persona "$NAMESPACE" "$API_POD" "s5-reviewer-2" "contributor")"
# The subs come OUT of the tokens — never chosen here. 'python3 -' reads the token from the
# environment rather than the command line so it does not land in a process listing.
S5_R1_SUB="$(S5_TOK="$S5_R1_TOKEN" python3 -c '
import base64, json, os
p = os.environ["S5_TOK"].split(".")[1]; p += "=" * (-len(p) % 4)
print(json.loads(base64.urlsafe_b64decode(p))["sub"])')"
S5_R2_SUB="$(S5_TOK="$S5_R2_TOKEN" python3 -c '
import base64, json, os
p = os.environ["S5_TOK"].split(".")[1]; p += "=" * (-len(p) % 4)
print(json.loads(base64.urlsafe_b64decode(p))["sub"])')"
[ -z "$S5_R1_SUB" ] || [ -z "$S5_R2_SUB" ] && { echo "ERROR: could not resolve persona subs"; exit 1; }
echo "  personas: s5-reviewer-1=${S5_R1_SUB:0:8}… (granted below)  s5-reviewer-2=${S5_R2_SUB:0:8}… (no grant)"

AUTHORITY_ID=""
cleanup() {
  # Re-mint first: Keycloak tokens live 300s and this suite runs longer, so the token
  # from setup is expired by the time cleanup needs it. Cleanup only began needing a
  # credential when R3 gated agent DELETE — see e2e_refresh_token in lib/e2e-auth.sh.
  e2e_refresh_token "$NAMESPACE" "$API_POD" || true

  echo ""
  echo "==> Cleanup..."
  if [ -n "$AUTHORITY_ID" ]; then
    kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/admin/approval-authority/${AUTHORITY_ID}',
        headers={'Authorization': 'Bearer ${E2E_TOKEN}'}, method='DELETE'), timeout=5)
except Exception: pass
" 2>/dev/null || true
  fi
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/hitl-s5-agent', method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}), timeout=5)
except Exception: pass
" 2>/dev/null || true
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
    echo "  Run manually:"
    while [ $# -gt 0 ]; do
      echo "    $1"
      shift
    done
  fi
  MANUAL=$((MANUAL + 1))
}

echo "=== Suite 5: HITL Authority Scoping ==="
echo ""

# ---------------------------------------------------------------------------
# Precondition: ensure test agent exists, capture UUID (needed for approval FK)
# ---------------------------------------------------------------------------
echo "--- Setup: create test agent hitl-s5-agent ---"
AGENT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error, json
name = 'hitl-s5-agent'
try:
    r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/' + name)
    data = json.loads(r.read())
    print(data['id'])
except urllib.error.HTTPError:
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/agents/',
        data=json.dumps({'name': name, 'team': 'platform', 'description': 'Suite 5 HITL test'}).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'},
        method='POST'
    )
    r = urllib.request.urlopen(req)
    data = json.loads(r.read())
    print(data['id'])
" 2>/dev/null || true)

if [ -z "$AGENT_ID" ]; then
  echo "ERROR: Could not create/find test agent hitl-s5-agent"
  exit 1
fi
echo "  agent id=${AGENT_ID:0:8}..."

# ---------------------------------------------------------------------------
# T-S5-001: Create ApprovalAuthority for issue_refund → reviewer-1
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S5-001: Create ApprovalAuthority ---"

AUTHORITY_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'resource_type': 'tool',
    'resource_id': 'issue_refund',
    'approver_user_id': '${S5_R1_SUB}'
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/admin/approval-authority',
    data=body,
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-admin',
             'Authorization': 'Bearer ${E2E_TOKEN}'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('approver_user_id') == '${S5_R1_SUB}', f'unexpected: {data}'
print(data['id'])
" 2>/dev/null || true)

if [ -n "$AUTHORITY_ID" ]; then
  echo "  PASS: T-S5-001 POST /admin/approval-authority → 201 (id=${AUTHORITY_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S5-001 POST /admin/approval-authority → failed to create or capture id"
  FAIL=$((FAIL + 1))
fi

run_test "T-S5-001 GET /admin/approval-authority?resource_id=issue_refund → granted reviewer's record present" "
import urllib.request, json
r = urllib.request.urlopen(urllib.request.Request(
    'http://localhost:8000/api/v1/admin/approval-authority?resource_type=tool&resource_id=issue_refund',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
))
data = json.loads(r.read())
items = data.get('items', [])
assert len(items) > 0, 'no records returned'
assert any(i.get('approver_user_id') == '${S5_R1_SUB}' for i in items), \
    f'granted reviewer not in items: {[i.get(\"approver_user_id\") for i in items]}'
"

# ---------------------------------------------------------------------------
# Precondition: Create a pending production approval for issue_refund
# ---------------------------------------------------------------------------
echo ""
# Per-run thread id. It was the fixed literal 'thread-s5-smoke', and POST /approvals/
# is idempotent on (agent, thread, tool, args) -- so the second run got back the FIRST
# run's approval, already decided by that run's T-S5-005. Three checks then failed
# against an already-approved row while reporting it as a product fault.
S5_THREAD="thread-s5-$(date +%s)-$$"

echo "--- Setup: create pending production approval for issue_refund ---"

APPROVAL_INFO=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
body = json.dumps({
    'agent_id': '${AGENT_ID}',
    'agent_name': 'hitl-s5-agent',
    'team': 'platform',
    'thread_id': '${S5_THREAD}',
    'tool_name': 'issue_refund',
    'tool_args': {'order_id': 'ORD-001', 'amount': 50.00},
    'risk_level': 'high',
    'context': 'production'
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
print(str(data['id']) + ':' + str(data['version']))
" 2>/dev/null || true)

APPROVAL_ID=$(echo "$APPROVAL_INFO" | cut -d: -f1)
APPROVAL_VERSION=$(echo "$APPROVAL_INFO" | cut -d: -f2)

if [ -z "$APPROVAL_ID" ]; then
  echo "  FAIL: Could not create test approval for issue_refund"
  FAIL=$((FAIL + 1))
  echo ""
  echo "--- Cleanup (partial) ---"
  [ -n "$AUTHORITY_ID" ] && kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/admin/approval-authority/${AUTHORITY_ID}',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'},
    method='DELETE'
)
try: urllib.request.urlopen(req)
except: pass
" 2>/dev/null || true
  echo ""
  echo "======================================================="
  echo "  Suite 5 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
  echo "======================================================="
  exit 1
fi
echo "  approval id=${APPROVAL_ID:0:8}... version=${APPROVAL_VERSION}"

# ---------------------------------------------------------------------------
# T-S5-002: reviewer-1 sees the pending approval
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S5-002: Authorized reviewer sees pending approval ---"
run_test "T-S5-002 GET /approvals?status=pending as granted reviewer (own token) → issue_refund visible" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/?status=pending',
    headers={'Authorization': 'Bearer ${S5_R1_TOKEN}'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
items = data.get('items', [])
assert any(i.get('tool_name') == 'issue_refund' for i in items), \
    f'issue_refund not found in items: {[i.get(\"tool_name\") for i in items]}'
"

# ---------------------------------------------------------------------------
# T-S5-003: reviewer-2 (no authority) sees empty list
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S5-003: Unauthorized reviewer sees empty list ---"
run_test "T-S5-003 GET /approvals?status=pending as ungranted reviewer (own token) → total=0" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/?status=pending',
    headers={'Authorization': 'Bearer ${S5_R2_TOKEN}'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
total = data.get('total', -1)
items = data.get('items', [])
assert total == 0 and items == [], f'expected empty list got total={total} items={items}'
"

# ---------------------------------------------------------------------------
# T-S5-004: reviewer-2 attempt to decide → 403
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S5-004: Unauthorized PATCH decide → 403 not_authorized_to_decide ---"
run_test "T-S5-004 PATCH /approvals/${APPROVAL_ID:0:8}... as ungranted reviewer (own token) → 403" "
import urllib.request, urllib.error, json
body = json.dumps({
    'decision': 'approved',
    'reviewer_id': '${S5_R2_SUB}',
    'version': ${APPROVAL_VERSION:-0}
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${APPROVAL_ID}',
    data=body,
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${S5_R2_TOKEN}'},
    method='PATCH'
)
try:
    r = urllib.request.urlopen(req)
    raise AssertionError(f'Expected 403 but got {r.status}')
except urllib.error.HTTPError as e:
    assert e.code == 403, f'Expected 403 got {e.code}'
    resp = json.loads(e.read())
    detail = resp.get('detail', '')
    assert 'not_authorized_to_decide' in detail, f'unexpected detail: {detail}'
"

# Confirm approval is still pending after the 403 attempt
run_test "T-S5-004 GET /approvals/${APPROVAL_ID:0:8}... → status still pending after 403" "
import urllib.request, json
# GET /approvals/{id} requires a credential since 0.2.281 — it returns the full record
# plus principal_display/requested_by/team. This read only checks status, so the admin
# token is the right caller: the AUTHORITY question is already settled above.
r = urllib.request.urlopen(urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${APPROVAL_ID}',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'}))
data = json.loads(r.read())
assert data.get('status') == 'pending', f'expected pending got {data.get(\"status\")}'
"

# ---------------------------------------------------------------------------
# T-S5-005: reviewer-1 decides → 200 approved
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S5-005: Authorized PATCH decide → 200 approved ---"
run_test "T-S5-005 PATCH /approvals/${APPROVAL_ID:0:8}... as granted reviewer (own token) → 200 approved" "
import urllib.request, json
body = json.dumps({
    'decision': 'approved',
    'reviewer_id': '${S5_R1_SUB}',
    'version': ${APPROVAL_VERSION:-0}
}).encode()
req = urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${APPROVAL_ID}',
    data=body,
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${S5_R1_TOKEN}'},
    method='PATCH'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('status') == 'approved', f'expected approved got {data.get(\"status\")}'
assert data.get('reviewer_id') == '${S5_R1_SUB}', f'unexpected reviewer_id: {data.get(\"reviewer_id\")}'
"

run_test "T-S5-005 GET /approvals/${APPROVAL_ID:0:8}... → status=approved, reviewer_id=the granted reviewer's sub" "
import urllib.request, json
# GET /approvals/{id} requires a credential since 0.2.281 — it returns the full record
# plus principal_display/requested_by/team. This read only checks status, so the admin
# token is the right caller: the AUTHORITY question is already settled above.
r = urllib.request.urlopen(urllib.request.Request(
    'http://localhost:8000/api/v1/approvals/${APPROVAL_ID}',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'}))
data = json.loads(r.read())
assert data.get('status') == 'approved', f'expected approved got {data.get(\"status\")}'
assert data.get('reviewer_id') == '${S5_R1_SUB}', f'unexpected reviewer_id: {data.get(\"reviewer_id\")}'
assert data.get('decision_at') is not None, 'decision_at should be set'
"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup ---"

if [ -n "$AUTHORITY_ID" ]; then
  run_test "Cleanup: DELETE /admin/approval-authority/${AUTHORITY_ID:0:8}... → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/admin/approval-authority/${AUTHORITY_ID}',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'},
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"
fi

run_test "Cleanup: DELETE /agents/hitl-s5-agent → 204 (soft-delete)" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/hitl-s5-agent',
    method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'})
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  Suite 5 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

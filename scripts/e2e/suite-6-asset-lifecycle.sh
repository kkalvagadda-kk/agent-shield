#!/usr/bin/env bash
# scripts/e2e/suite-6-asset-lifecycle.sh
#
# E2E Suite 6: Asset Lifecycle (Publish + Grant)
# Tests T-S6-001 through T-S6-012.
#
# What this proves:
#   T-S6-001 — New agent starts with publish_status='private'
#   T-S6-002 — Publish blocked when a critical-risk tool is bound (422)
#   T-S6-003 — Publish succeeds after removing critical tool (202, pending_review)
#   T-S6-004 — Publish request appears in admin queue
#   T-S6-005 — Admin reject returns agent to private
#   T-S6-006 — Re-publish after rejection creates new request (202)
#   T-S6-007 — Admin approve creates team grant (200, grants_created≥1)
#   T-S6-008 (plan T-S6-007 cont.) — Agent publish_status='published' after approve
#   T-S6-009 (plan T-S6-008) — AssetGrant row visible via GET /admin/grants
#   T-S6-010 (plan T-S6-009) — DELETE grant → 204; audit row check → MANUAL (no audit API)
#   T-S6-011 (plan T-S6-010) — Deploy blocked after grant revocation → MANUAL
#
# API notes vs. test plan:
#   - DELETE /admin/grants/{id} returns 204 (no body), not 200
#   - GET /admin/grants/{id}/audit does NOT exist — audit row check is MANUAL
#   - approve response: {"approved": true, "grants_created": N}
#   - reject response:  {"rejected": true}
#   - publish response: {"publish_request_id": "uuid"}
#
# Usage:
#   bash scripts/e2e/suite-6-asset-lifecycle.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-6-asset-lifecycle.sh
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

AGENT_NAME="publish-test-s6-agent"

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

echo "=== Suite 6: Asset Lifecycle (Publish + Grant) ==="
echo ""

# ---------------------------------------------------------------------------
# T-S6-001: New agent starts private
# ---------------------------------------------------------------------------
echo "--- T-S6-001: New agent starts with publish_status=private ---"

# Idempotent: soft-delete from a previous run before recreating
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
try:
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/agents/${AGENT_NAME}',
        method='DELETE'
    )
    urllib.request.urlopen(req)
except: pass
" 2>/dev/null || true

AGENT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=json.dumps({
        'name': '${AGENT_NAME}',
        'team': 'platform',
        'description': 'Suite 6 publish lifecycle test'
    }).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('publish_status') == 'private', f'expected private got {data.get(\"publish_status\")}'
print(data['id'])
" 2>/dev/null || true)

if [ -n "$AGENT_ID" ]; then
  echo "  PASS: T-S6-001 Agent created with publish_status=private (id=${AGENT_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S6-001 Could not create agent or publish_status not 'private'"
  FAIL=$((FAIL + 1))
  echo ""
  echo "======================================================="
  echo "  Suite 6 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
  echo "======================================================="
  exit 1
fi

run_test "T-S6-001 GET /agents/${AGENT_NAME} → publish_status=private confirmed" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}')
data = json.loads(r.read())
assert data.get('publish_status') == 'private', f'expected private got {data.get(\"publish_status\")}'
"

# ---------------------------------------------------------------------------
# T-S6-002: Publish blocked with critical-risk tool bound
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-002: Publish blocked with critical-risk tool ---"

CRITICAL_TOOL_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/tools/',
    data=json.dumps({
        'name': 's6-critical-tool',
        'type': 'native',
        'risk_level': 'critical',
        'description': 'Suite 6 critical risk test tool'
    }).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
r = urllib.request.urlopen(req)
data = json.loads(r.read())
print(data['id'])
" 2>/dev/null || true)

if [ -n "$CRITICAL_TOOL_ID" ]; then
  echo "  Setup: critical tool created (id=${CRITICAL_TOOL_ID:0:8}...)"
else
  echo "  FAIL: T-S6-002 Could not create critical test tool"
  FAIL=$((FAIL + 1))
fi

# Bind critical tool to agent
if [ -n "$CRITICAL_TOOL_ID" ]; then
  run_test "T-S6-002 Bind critical tool to ${AGENT_NAME}" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}/tools',
    data=json.dumps({'tool_id': '${CRITICAL_TOOL_ID}'}).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
"
fi

# Attempt publish with critical tool → 422
run_test "T-S6-002 POST /agents/${AGENT_NAME}/publish with critical tool → 422 critical_risk_not_publishable" "
import urllib.request, urllib.error, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}/publish',
    data=json.dumps({}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'dev-user'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req)
    raise AssertionError(f'Expected 422 but got {r.status}')
except urllib.error.HTTPError as e:
    assert e.code == 422, f'Expected 422 got {e.code}'
    resp = json.loads(e.read())
    detail = resp.get('detail', {})
    err = detail.get('error', '') if isinstance(detail, dict) else str(detail)
    assert 'critical_risk' in err, f'unexpected detail: {resp}'
"

# ---------------------------------------------------------------------------
# T-S6-003: Remove critical tool, publish succeeds → 202
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-003: Publish after removing critical tool → 202, pending_review ---"

if [ -n "$CRITICAL_TOOL_ID" ]; then
  run_test "T-S6-003 Unbind critical tool from ${AGENT_NAME}" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}/tools/${CRITICAL_TOOL_ID}',
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"
fi

PUBLISH_REQUEST_ID_1=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}/publish',
    data=json.dumps({}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'dev-user'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 202, f'expected 202 got {r.status}'
data = json.loads(r.read())
assert 'publish_request_id' in data, f'missing publish_request_id in {data}'
print(data['publish_request_id'])
" 2>/dev/null || true)

if [ -n "$PUBLISH_REQUEST_ID_1" ]; then
  echo "  PASS: T-S6-003 POST /publish → 202 (request_id=${PUBLISH_REQUEST_ID_1:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S6-003 POST /publish did not return 202 or publish_request_id"
  FAIL=$((FAIL + 1))
fi

run_test "T-S6-003 GET /agents/${AGENT_NAME} → publish_status=pending_review" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}')
data = json.loads(r.read())
assert data.get('publish_status') == 'pending_review', \
    f'expected pending_review got {data.get(\"publish_status\")}'
"

# ---------------------------------------------------------------------------
# T-S6-004: Publish request appears in admin queue
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-004: Publish request appears in admin queue ---"
run_test "T-S6-004 GET /admin/publish-requests?status=pending_review → ${AGENT_NAME} present" "
import urllib.request, json
r = urllib.request.urlopen(
    'http://localhost:8000/api/v1/admin/publish-requests?status=pending_review'
)
data = json.loads(r.read())
items = data.get('items', [])
asset_ids = [str(i.get('asset_id', '')) for i in items]
assert '${AGENT_ID}' in asset_ids, \
    f'agent_id ${AGENT_ID:0:8}... not found in pending requests: {asset_ids}'
"

# ---------------------------------------------------------------------------
# T-S6-005: Admin reject → agent reverts to private
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-005: Admin reject publish request → private ---"

if [ -n "$PUBLISH_REQUEST_ID_1" ]; then
  run_test "T-S6-005 POST /admin/publish-requests/${PUBLISH_REQUEST_ID_1:0:8}.../reject → 200" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/admin/publish-requests/${PUBLISH_REQUEST_ID_1}/reject',
    data=json.dumps({'notes': 'Suite 6 test rejection'}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-admin'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('rejected') is True, f'unexpected response: {data}'
"
fi

run_test "T-S6-005 GET /agents/${AGENT_NAME} → publish_status=private after rejection" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}')
data = json.loads(r.read())
assert data.get('publish_status') == 'private', \
    f'expected private got {data.get(\"publish_status\")}'
"

# ---------------------------------------------------------------------------
# T-S6-006: Re-publish after rejection → new request ID
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-006: Re-publish after rejection → new publish request ---"

PUBLISH_REQUEST_ID_2=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}/publish',
    data=json.dumps({}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'dev-user'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 202, f'expected 202 got {r.status}'
data = json.loads(r.read())
print(data['publish_request_id'])
" 2>/dev/null || true)

if [ -n "$PUBLISH_REQUEST_ID_2" ]; then
  echo "  PASS: T-S6-006 Re-publish → 202 (request_id=${PUBLISH_REQUEST_ID_2:0:8}...)"
  PASS=$((PASS + 1))
  if [ "$PUBLISH_REQUEST_ID_2" != "$PUBLISH_REQUEST_ID_1" ]; then
    echo "  PASS: T-S6-006 New request ID differs from first (separate request)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: T-S6-006 publish_request_id same as first request (expected different)"
    FAIL=$((FAIL + 1))
  fi
else
  echo "  FAIL: T-S6-006 Re-publish did not return 202 or publish_request_id"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S6-007: Admin approve creates team grant; T-S6-008: status=published
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-007/008: Admin approve → grants_created≥1, publish_status=published ---"

GRANTS_CREATED=0
if [ -n "$PUBLISH_REQUEST_ID_2" ]; then
  GRANTS_CREATED=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/admin/publish-requests/${PUBLISH_REQUEST_ID_2}/approve',
    data=json.dumps({'grantee_teams': ['platform']}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-admin'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('approved') is True, f'unexpected: {data}'
gc = data.get('grants_created', 0)
assert gc >= 1, f'expected grants_created>=1 got {gc}'
print(gc)
" 2>/dev/null || true)
fi

if [ -n "$GRANTS_CREATED" ] && [ "$GRANTS_CREATED" -ge 1 ] 2>/dev/null; then
  echo "  PASS: T-S6-007 POST /admin/publish-requests/.../approve → 200 grants_created=${GRANTS_CREATED}"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S6-007 Approve failed or grants_created=0"
  FAIL=$((FAIL + 1))
fi

run_test "T-S6-008 GET /agents/${AGENT_NAME} → publish_status=published after approve" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}')
data = json.loads(r.read())
assert data.get('publish_status') == 'published', \
    f'expected published got {data.get(\"publish_status\")}'
"

# ---------------------------------------------------------------------------
# T-S6-009 (plan T-S6-008): AssetGrant row visible via admin grants API
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-009 (plan T-S6-008): AssetGrant row visible in admin grants ---"

GRANT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
r = urllib.request.urlopen(
    'http://localhost:8000/api/v1/admin/grants?asset_id=${AGENT_ID}'
)
data = json.loads(r.read())
items = data.get('items', [])
assert len(items) > 0, f'no grants found for asset_id=${AGENT_ID:0:8}'
grant = next(
    (i for i in items if i.get('grantee_team') == 'platform' and i.get('revoked_at') is None),
    None
)
assert grant is not None, f'platform grant not found in: {items}'
print(grant['id'])
" 2>/dev/null || true)

if [ -n "$GRANT_ID" ]; then
  echo "  PASS: T-S6-009 Grant found for platform team (id=${GRANT_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S6-009 No active grant found for platform team"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S6-010 (plan T-S6-009 partial): Revoke the grant → 204
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-010 (plan T-S6-009): Revoke grant → 204 ---"

if [ -n "$GRANT_ID" ]; then
  run_test "T-S6-010 DELETE /admin/grants/${GRANT_ID:0:8}... → 204 revoked" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/admin/grants/${GRANT_ID}',
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

  # Verify grant is gone from the active list
  run_test "T-S6-010 GET /admin/grants?asset_id=${AGENT_ID:0:8}... → no active grants remain" "
import urllib.request, json
r = urllib.request.urlopen(
    'http://localhost:8000/api/v1/admin/grants?asset_id=${AGENT_ID}'
)
data = json.loads(r.read())
items = data.get('items', [])
active = [i for i in items if i.get('revoked_at') is None]
assert len(active) == 0, f'expected 0 active grants got {active}'
"
fi

# Audit row check — no API endpoint exists
check_manual "T-S6-009-audit" \
  "Grant revocation creates a GrantAudit row — no GET /admin/grants/{id}/audit endpoint; verify in DB directly" \
  "kubectl exec -n ${NAMESPACE} \$(kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}') -- python3 -c \"import asyncio; from db import AsyncSessionLocal; from models import GrantAudit; from sqlalchemy import select; ..." \
  "# Or query Postgres directly: SELECT * FROM grant_audits WHERE asset_id='${AGENT_ID}' ORDER BY created_at DESC LIMIT 5;"

# Deploy-after-revocation check — requires a deployed version
check_manual "T-S6-012 (plan T-S6-010)" \
  "Deploy blocked after grant revocation — requires a version_id from a prior deploy" \
  "# First create a version: POST /api/v1/agents/${AGENT_NAME}/versions with {image_tag, tools}" \
  "# Then attempt deploy: POST /api/v1/agents/${AGENT_NAME}/deploy with {version_id}" \
  "# Expected: 422 with error referencing grant revocation or missing grant"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup ---"

run_test "Cleanup: DELETE /agents/${AGENT_NAME} → 204 (soft-delete)" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}',
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

if [ -n "$CRITICAL_TOOL_ID" ]; then
  run_test "Cleanup: DELETE /tools/s6-critical-tool (id=${CRITICAL_TOOL_ID:0:8}...) → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/tools/${CRITICAL_TOOL_ID}',
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  Suite 6 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

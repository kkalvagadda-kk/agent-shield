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
#   T-S6-013 — a CROSS-TEAM private tool blocks publish (422 tool_not_publishable_cross_team)
#   T-S6-014 — an OWN-TEAM private tool does NOT block (the over-reach guard for 013)
#   T-S6-015 — approve CASCADES: the own-team tool flips to published and the response says so
#   T-S6-016 — an ALREADY-PUBLISHED cross-team tool does not block either
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
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

# R1/FR-11: this suite drives four of the ten protected routers —
#   • routers/admin.py       : /admin/publish-requests*, /admin/grants*
#   • routers/agent_tools.py : POST /{name}/tools, DELETE /{name}/tools/{id}
#   • routers/versions.py    : POST/GET/PATCH /{name}/versions*
#   • routers/deployments.py : POST /{name}/deploy
# all of which now require a real JWT. /api/v1/agents/*, /api/v1/tools/* and
# /agents/{name}/publish are NOT among the ten and stay as they are.
# Call e2e_set_token BARE — a command substitution swallows its abort (lib/e2e-auth.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

CRITICAL_TOOL_ID=""
cleanup() {
  echo ""
  echo "==> Cleanup..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/${AGENT_NAME}', method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}), timeout=5)
except Exception: pass
" 2>/dev/null || true
  if [ -n "$CRITICAL_TOOL_ID" ]; then
    kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/tools/${CRITICAL_TOOL_ID}', method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}), timeout=5)
except Exception: pass
" 2>/dev/null || true
  fi
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/?limit=200', timeout=5)
    agents = json.loads(r.read()).get('items', [])
    for a in agents:
        if 'high-risk-gate-' in a.get('name','') or 'crit-tool-gate-' in a.get('name',''):
            try:
                urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/' + a['name'], method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}), timeout=5)
            except Exception: pass
except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

PASS=0
FAIL=0
MANUAL=0

# Timestamped so a soft-deleted (deprecated) agent from a prior run can't cause
# a name conflict at T-S6-001 (agents are soft-deleted; the name stays reserved).
AGENT_NAME="publish-test-s6-agent-$(date +%s)"
# Same for the critical tool (a fixed name lingers and 409s on re-run).
CRITICAL_TOOL_NAME="s6-critical-tool-$(date +%s)"

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
        method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
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
    # R2 (0.2.263): POST /agents/ requires a real JWT and contributor+. It used to take
    # get_optional_user and fall back to an X-User-Sub header, so this call worked with
    # no identity at all — see docs/bugs/anonymous-agent-creation-with-forged-attribution.md.
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
        'name': '${CRITICAL_TOOL_NAME}',
        'type': 'native',
        'risk_level': 'critical',
        'description': 'Suite 6 critical risk test tool'
    }).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'dev-user', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'},
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"
fi

# Create an eval-passed version so the publish gate (Decision 20) is satisfied.
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}/versions',
    data=json.dumps({'image_tag': 'registry.internal/s6-publish:v1', 'eval_passed': True, 'adversarial_eval_passed': True}).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST')
try:
    urllib.request.urlopen(req)
except Exception as e:
    print('s6 version create:', e)
" 2>/dev/null || true

PUBLISH_REQUEST_ID_1=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${AGENT_NAME}/publish',
    data=json.dumps({}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'dev-user', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
r = urllib.request.urlopen(urllib.request.Request(
    'http://localhost:8000/api/v1/admin/publish-requests?status=pending_review',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
))
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
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-admin', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'dev-user', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-admin', 'Authorization': 'Bearer ${E2E_TOKEN}'},
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
r = urllib.request.urlopen(urllib.request.Request(
    'http://localhost:8000/api/v1/admin/grants?asset_id=${AGENT_ID}',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
))
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
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'},
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

  # Verify grant is gone from the active list
  run_test "T-S6-010 GET /admin/grants?asset_id=${AGENT_ID:0:8}... → no active grants remain" "
import urllib.request, json
r = urllib.request.urlopen(urllib.request.Request(
    'http://localhost:8000/api/v1/admin/grants?asset_id=${AGENT_ID}',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
))
data = json.loads(r.read())
items = data.get('items', [])
active = [i for i in items if i.get('revoked_at') is None]
assert len(active) == 0, f'expected 0 active grants got {active}'
"
fi

# Audit row check — no API endpoint exists
check_manual "T-S6-009-audit" \
  "Grant revocation creates a GrantAudit row — no GET /admin/grants/{id}/audit endpoint; verify in DB directly" \
  "kubectl exec -n ${NAMESPACE} \$(kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/name=registry-api --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}') -- python3 -c \"import asyncio; from db import AsyncSessionLocal; from models import GrantAudit; from sqlalchemy import select; ..." \
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
    method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

if [ -n "$CRITICAL_TOOL_ID" ]; then
  run_test "Cleanup: DELETE /tools/${CRITICAL_TOOL_NAME} (id=${CRITICAL_TOOL_ID:0:8}...) → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/tools/${CRITICAL_TOOL_ID}',
    method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'})
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"
fi

# ---------------------------------------------------------------------------
# G3-T002: Deploy Gate Completeness (T-S6-LG-001 through T-S6-LG-004)
# ---------------------------------------------------------------------------
echo ""
echo "=== G3-T002: Deploy Gate — Adversarial Eval + Critical Tool Gates ==="
echo ""

TS6LG=$(date +%s)

# T-S6-LG-001: High-risk agent blocked without adversarial eval
echo "--- T-S6-LG-001: High-risk agent deploy blocked without adversarial_eval_passed ---"
run_test "T-S6-LG-001: POST deploy for high-risk agent → 422 adversarial eval required" "
import urllib.request, json, time
base = 'http://localhost:8000'
ts = '${TS6LG}'

# Create agent with risk_level=high
ag = json.dumps({'name': 'high-risk-gate-' + ts, 'team': 'platform',
                  'description': 'gate test', 'risk_level': 'high'}).encode()
try:
    r = urllib.request.urlopen(urllib.request.Request(base + '/api/v1/agents/',
        data=ag, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
    agent = json.loads(r.read())
    agent_name = agent.get('name')
except urllib.error.HTTPError as e:
    if e.code == 409:
        agent_name = 'high-risk-gate-' + ts
    else:
        raise

# Create version with eval_passed=true but adversarial_eval_passed=false
# Uses a high-risk tool to trigger adversarial eval gate
v = json.dumps({'agent_name': agent_name, 'description': 'v1',
                 'tools': [{'name': 'issue_refund', 'risk': 'high'}],
                 'eval_passed': True,
                 'adversarial_eval_passed': False}).encode()
r = urllib.request.urlopen(urllib.request.Request(
    base + '/api/v1/agents/' + agent_name + '/versions',
    data=v, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
version = json.loads(r.read())
version_id = str(version.get('id'))

# Attempt deploy — must be blocked
d = json.dumps({'agent_name': agent_name, 'version_id': version_id,
                 'deployer_team': 'platform'}).encode()
try:
    urllib.request.urlopen(urllib.request.Request(
        base + '/api/v1/agents/' + agent_name + '/deploy',
        data=d, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
    raise AssertionError('Expected 422 but deploy succeeded')
except urllib.error.HTTPError as e:
    body = json.loads(e.read())
    detail = str(body.get('detail', ''))
    assert e.code == 422, f'expected 422 got {e.code}: {detail}'
    assert 'adversarial' in detail.lower(), f'expected \"adversarial\" in detail: {detail}'
    print('BLOCKED 422 detail=' + detail[:80])
"

# T-S6-LG-002: Deploy succeeds after setting adversarial_eval_passed=true
echo "--- T-S6-LG-002: Deploy succeeds after setting adversarial_eval_passed=true ---"
run_test "T-S6-LG-002: PATCH adversarial_eval_passed=true then deploy → 201/202" "
import urllib.request, json
base = 'http://localhost:8000'
ts = '${TS6LG}'
agent_name = 'high-risk-gate-' + ts

# Get latest version id
r = urllib.request.urlopen(urllib.request.Request(
    base + '/api/v1/agents/' + agent_name + '/versions',
    headers={'Authorization': 'Bearer ${E2E_TOKEN}'}), timeout=5)
versions = json.loads(r.read())
# versions may be a list or paginated — handle both
items = versions if isinstance(versions, list) else versions.get('items', versions.get('data', []))
assert items, f'no versions found: {versions}'
version_id = str(items[-1].get('id'))

# Patch adversarial_eval_passed=true
patch = json.dumps({'adversarial_eval_passed': True}).encode()
req = urllib.request.Request(
    base + '/api/v1/agents/' + agent_name + '/versions/' + version_id,
    data=patch, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='PATCH')
r = urllib.request.urlopen(req, timeout=5)
assert r.getcode() in (200, 204), f'patch failed: {r.getcode()}'

# Deploy should now succeed (or return 202 accepted)
d = json.dumps({'agent_name': agent_name, 'version_id': version_id,
                 'deployer_team': 'platform'}).encode()
try:
    r2 = urllib.request.urlopen(urllib.request.Request(
        base + '/api/v1/agents/' + agent_name + '/deploy',
        data=d, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
    print('DEPLOY_OK status=' + str(r2.getcode()))
except urllib.error.HTTPError as e:
    body = json.loads(e.read())
    detail = str(body.get('detail', ''))
    # 409 = already deployed is acceptable
    if e.code == 409:
        print('ALREADY_DEPLOYED (acceptable) detail=' + detail[:60])
    else:
        raise AssertionError(f'Expected 201/202/409 got {e.code}: {detail}')
"

# T-S6-LG-003: Deploy blocked when version has critical-risk tool (risk field)
echo "--- T-S6-LG-003: Deploy blocked when tool risk='critical' ---"
run_test "T-S6-LG-003: Deploy with critical tool → 422 with tool name in detail" "
import urllib.request, json
base = 'http://localhost:8000'
ts = '${TS6LG}'
agent_name = 'crit-tool-gate-' + ts

ag = json.dumps({'name': agent_name, 'team': 'platform', 'description': 'gate test'}).encode()
try:
    urllib.request.urlopen(urllib.request.Request(base + '/api/v1/agents/',
        data=ag, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
except urllib.error.HTTPError as e:
    if e.code != 409: raise

v = json.dumps({'agent_name': agent_name, 'description': 'v1',
                 'tools': [{'name': 'nuke_everything', 'risk': 'critical'}],
                 'eval_passed': True,
                 'adversarial_eval_passed': True}).encode()
r = urllib.request.urlopen(urllib.request.Request(
    base + '/api/v1/agents/' + agent_name + '/versions',
    data=v, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
version_id = str(json.loads(r.read()).get('id'))

d = json.dumps({'agent_name': agent_name, 'version_id': version_id,
                 'deployer_team': 'platform'}).encode()
try:
    urllib.request.urlopen(urllib.request.Request(
        base + '/api/v1/agents/' + agent_name + '/deploy',
        data=d, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
    raise AssertionError('Expected 422 but deploy succeeded')
except urllib.error.HTTPError as e:
    body = json.loads(e.read())
    detail = str(body.get('detail', ''))
    assert e.code == 422, f'expected 422 got {e.code}: {detail}'
    assert 'critical' in detail.lower(), f'expected \"critical\" in detail: {detail}'
    print('BLOCKED 422 detail=' + detail[:80])
"

# T-S6-LG-004: Gate violations return structured 422 (not 500)
echo "--- T-S6-LG-004: Deploy gate violations return 422 not 500 ---"
run_test "T-S6-LG-004: Gate violation response is structured {detail: ...} JSON at 422" "
import urllib.request, json
base = 'http://localhost:8000'
ts = '${TS6LG}'

# Try to deploy a non-existent agent — should get a structured error
d = json.dumps({'agent_name': 'nonexistent-agent-' + ts, 'version_id': '00000000-0000-0000-0000-000000000000',
                 'deployer_team': 'platform'}).encode()
try:
    urllib.request.urlopen(urllib.request.Request(
        base + '/api/v1/agents/nonexistent-agent-' + ts + '/deploy',
        data=d, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST'), timeout=5)
    raise AssertionError('Expected 4xx but got 200')
except urllib.error.HTTPError as e:
    assert e.code in (404, 422), f'expected 404 or 422 got {e.code}'
    body = json.loads(e.read())
    assert 'detail' in body, f'response missing detail key: {body}'
    assert e.code != 500, '500 Internal Server Error — gate must return structured error'
    print('STRUCTURED_ERROR code=' + str(e.code) + ' detail=' + str(body.get('detail',''))[:60])
"

echo ""

# Cleanup deploy-gate test agents (best-effort)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/?limit=200', timeout=5)
    agents = json.loads(r.read()).get('items', [])
    for a in agents:
        if 'high-risk-gate-' in a.get('name','') or 'crit-tool-gate-' in a.get('name',''):
            req = urllib.request.Request(
                'http://localhost:8000/api/v1/agents/' + a['name'],
                method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'})
            try:
                urllib.request.urlopen(req, timeout=5)
                print('  deleted: ' + a['name'])
            except Exception:
                pass
except Exception:
    pass
" 2>/dev/null || true

# ---------------------------------------------------------------------------
# T-S6-013..016 — Decision 47 option C: the tool publish CASCADE
#
# Publishing an agent publishes the agent's own-team unpublished tools along with it.
# Tools have no publish endpoint of their own, by design — one review covers both.
#
# The four cases are the four ways this goes wrong:
#   013  a cross-team private tool must BLOCK the request at submit time. Otherwise
#        approving an agent becomes a way to publish another team's private draft, and
#        the deciding reviewer never sees whose work they just exposed.
#   014  an own-team private tool must NOT block. 013 alone is satisfied by refusing
#        every publish that binds an unpublished tool, which would make the cascade
#        unreachable and the feature pointless.
#   015  approve actually flips it, and SAYS SO. A silent side effect inside an existing
#        approval is the escalation Decision 47 exists to prevent, so the response has to
#        name what cascaded.
#   016  an already-published cross-team tool must NOT block. The guard is about
#        publishing someone else's PRIVATE work; a tool that is already org-wide costs
#        nothing to keep using, and blocking on it would make most real agents
#        unpublishable.
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S6-013..016: Decision 47 tool publish cascade ---"

CASCADE_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- env TOK="${E2E_TOKEN}" python3 -c '
import json, os, time, urllib.request, urllib.error

BASE = "http://localhost:8000/api/v1"
TOK = os.environ["TOK"]
TS = str(int(time.time()))
out = []

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": "Bearer " + TOK}
    if data: h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, (json.loads(raw) if raw else {})
        except Exception: return e.code, {"raw": raw[:200].decode("utf-8", "replace")}

def check(tid, ok, detail):
    # Verdict precomputed — an escaped quote inside an f-string inside a single-quoted
    # bash string is a SyntaxError, and this repo has paid for it twice (suite-42, suite-99).
    verdict = "PASS" if ok else "FAIL"
    out.append(tid + "|" + verdict + "|" + str(detail)[:320])

AGENT = "s6c-cascade-" + TS
OWN   = "s6c_own_" + TS          # platform, private  -> cascades
FOREIGN = "s6c_foreign_" + TS    # operations, private -> BLOCKS

# --- fixtures -------------------------------------------------------------
call("POST", "/teams/", {"name": "operations", "namespace": "agents-operations"})
_, own = call("POST", "/tools/", {"name": OWN, "type": "http", "description": "cascade own",
                                  "risk_level": "low", "http_method": "GET",
                                  "http_url": "https://example.invalid/own"})
# owner_team is honoured for a platform-admin only (Decision 46) — that is what this token is.
_, foreign = call("POST", "/tools/", {"name": FOREIGN, "type": "http", "description": "cascade foreign",
                                      "risk_level": "low", "http_method": "GET",
                                      "http_url": "https://example.invalid/foreign",
                                      "owner_team": "operations"})
own_id, foreign_id = own.get("id"), foreign.get("id")

# An ALREADY-published tool this team does not own. Taken from the live catalog rather
# than minted: tools have no publish endpoint, and the ~192 pre-0080 rows are exactly the
# shared-library population this case is about.
pub_id = pub_name = None
_, listing = call("GET", "/tools/?limit=200")
for row in listing.get("items", []):
    if row.get("publish_status") == "published" and row.get("owner_team") != "platform" \
       and row.get("risk_level") in ("low", "medium") and row.get("status") == "active":
        pub_id, pub_name = row.get("id"), row.get("name"); break

sc, _ = call("POST", "/agents/", {"name": AGENT, "team": "platform",
                                  "description": "suite 6 cascade probe"})
call("POST", "/agents/" + AGENT + "/versions",
     {"image_tag": "registry.internal/s6c:v1", "eval_passed": True, "adversarial_eval_passed": True})

if sc != 201 or not own_id or not foreign_id:
    for tid in ("T-S6-013", "T-S6-014", "T-S6-015", "T-S6-016"):
        check(tid, False, "fixture failed: agent=%s own=%s foreign=%s" % (sc, own_id, foreign_id))
    print("\n".join(out)); raise SystemExit(0)

# --- 013: a cross-team PRIVATE tool blocks the submission -----------------
call("POST", "/agents/" + AGENT + "/tools", {"tool_id": foreign_id})
code, body = call("POST", "/agents/" + AGENT + "/publish", {})
detail = body.get("detail") or {}
err = detail.get("error") if isinstance(detail, dict) else str(detail)
named = FOREIGN in json.dumps(detail)
check("T-S6-013", code == 422 and err == "tool_not_publishable_cross_team" and named,
      "code=%s error=%s names_the_tool=%s" % (code, err, named))

# --- 014: own-team private + already-published foreign do NOT block -------
call("DELETE", "/agents/" + AGENT + "/tools/" + str(foreign_id))
call("POST", "/agents/" + AGENT + "/tools", {"tool_id": own_id})
if pub_id:
    call("POST", "/agents/" + AGENT + "/tools", {"tool_id": pub_id})
code, body = call("POST", "/agents/" + AGENT + "/publish", {})
pr_id = body.get("publish_request_id")
check("T-S6-014", code == 202 and bool(pr_id),
      "code=%s request=%s (own-team private tool must not block)" % (code, str(pr_id)[:8]))
check("T-S6-016", code == 202 and bool(pub_id),
      "an already-published cross-team tool (%s) did not block: code=%s" % (pub_name, code))

# --- 015: approve cascades, and the response says what it published -------
if pr_id:
    code, body = call("POST", "/admin/publish-requests/" + pr_id + "/approve", {"grantee_teams": ["platform"]})
    cascaded = body.get("cascaded_tools") or []
    _, reread = call("GET", "/tools/?name=" + OWN + "&limit=1")
    items = reread.get("items", [])
    now_pub = items[0].get("publish_status") if items else "NOT_VISIBLE"
    # The foreign one must be untouched — it was unbound before this publish, and nothing
    # about approving this agent confers authority over a tool another team owns.
    # (No apostrophe on purpose: this whole driver is inside a single-quoted bash string.)
    _, fre = call("GET", "/tools/?name=" + FOREIGN + "&limit=1")
    fitems = fre.get("items", [])
    foreign_ps = fitems[0].get("publish_status") if fitems else "NOT_VISIBLE"
    # The foreign tool must NOT be published. Asserted as "not published" rather than
    # "== private" because this reader is a platform-admin in team `platform`, and
    # catalog visibility is team-scoped with NO admin exemption by design
    # (catalog_visibility.py) — so another team private tool reads as NOT_VISIBLE here.
    # That is conclusive, not a weaker check: published implies visible to everyone, so
    # NOT_VISIBLE proves it did not cascade. Pinning "private" instead would make this
    # case fail the day the admin exemption question is settled either way.
    check("T-S6-015",
          code == 200 and OWN in cascaded and now_pub == "published"
          and foreign_ps != "published",
          "approve=%s cascaded=%s own_now=%s foreign_still=%s" % (code, cascaded, now_pub, foreign_ps))
else:
    check("T-S6-015", False, "no publish_request_id from 014 — nothing to approve")

# --- cleanup --------------------------------------------------------------
call("DELETE", "/agents/" + AGENT)
for tid in (own_id, foreign_id):
    if tid: call("DELETE", "/tools/" + str(tid))

print("\n".join(out))
' 2>&1 || true)

for tid in T-S6-013 T-S6-014 T-S6-015 T-S6-016; do
  line="$(echo "$CASCADE_OUT" | grep "^${tid}|" || true)"
  if [ -z "$line" ]; then
    echo "  FAIL: ${tid} produced no result | driver tail: $(echo "$CASCADE_OUT" | tail -3 | tr '\n' ' ')"
    FAIL=$((FAIL + 1))
  elif [ "$(echo "$line" | cut -d'|' -f2)" = "PASS" ]; then
    echo "  PASS: ${tid} $(echo "$line" | cut -d'|' -f3)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: ${tid} $(echo "$line" | cut -d'|' -f3)"
    FAIL=$((FAIL + 1))
  fi
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "======================================================="
echo "  Suite 6 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

#!/usr/bin/env bash
# scripts/e2e/suite-15-artifact-isolation.sh
#
# E2E Suite 15: Artifact Isolation (created_by + visibility filter)
# Tests T-S15-001 through T-S15-008.
#
# What this proves:
#   T-S15-001 — POST /agents/ with X-User-Sub header → created_by == caller
#   T-S15-002 — POST /agents/ without X-User-Sub → created_by == 'system'
#   T-S15-003 — GET /agents/ with X-User-Sub: user-alice → alice's private agent in list
#   T-S15-004 — GET /agents/ with X-User-Sub: user-bob → alice's private agent NOT in list
#   T-S15-005 — GET /agents/ without X-User-Sub → published-only (deny-by-default; private agents NOT leaked)
#   T-S15-006 — Published agent visible to any authenticated user (user-bob sees it)
#   T-S15-007 — GET /agents/{name} (direct fetch) has no isolation — known gap / MANUAL
#   T-S15-008 — Studio UX: agent created by alice not visible to bob — MANUAL
#
# Background on the bugs fixed:
#   - POST /agents/ now reads X-User-Sub and stores it in created_by (default: 'system')
#   - GET /agents/ applies visibility filter when X-User-Sub is present:
#       returns publish_status='published' OR created_by == caller
#     Without the header (system calls), all agents are returned.
#   - agents.created_by is now NOT NULL (migration 0014 backfills nulls to 'system')
#
# Usage:
#   bash scripts/e2e/suite-15-artifact-isolation.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-15-artifact-isolation.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

cleanup() {
  echo ""
  echo "==> Cleanup: deleting test agents..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
for name in ['${ALICE_AGENT}', '${SYSTEM_AGENT}']:
    try:
        urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/' + name, method='DELETE'), timeout=5)
    except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

PASS=0
FAIL=0
MANUAL=0

# Timestamped so soft-deleted (deprecated) agents from prior runs don't cause
# name-conflict failures on re-run (agents soft-delete; the name stays reserved).
_S15_TS="$(date +%s)"
ALICE_AGENT="s15-alice-agent-${_S15_TS}"
SYSTEM_AGENT="s15-system-agent-${_S15_TS}"

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

echo "=== Suite 15: Artifact Isolation (created_by + visibility filter) ==="
echo ""

# ---------------------------------------------------------------------------
# Setup: clean up any leftover test agents from a prior run
# ---------------------------------------------------------------------------
echo "--- Setup: removing any leftover test agents ---"
for name in "$ALICE_AGENT" "$SYSTEM_AGENT"; do
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
try:
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/agents/${name}',
        method='DELETE'
    )
    urllib.request.urlopen(req)
except: pass
" 2>/dev/null || true
done
echo "  cleanup done"

# ---------------------------------------------------------------------------
# T-S15-001: created_by is set from X-User-Sub header
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-001: POST /agents/ with X-User-Sub: user-alice → created_by=user-alice ---"

ALICE_AGENT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=json.dumps({
        'name': '${ALICE_AGENT}',
        'team': 'platform',
        'description': 'Suite 15 artifact isolation test — alice-owned private agent'
    }).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'user-alice'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('created_by') == 'user-alice', \
    f'expected created_by=user-alice got {data.get(\"created_by\")}'
print(data['id'])
" 2>/dev/null || true)

if [ -n "$ALICE_AGENT_ID" ]; then
  echo "  PASS: T-S15-001 created_by=user-alice confirmed (id=${ALICE_AGENT_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S15-001 Could not create agent or created_by != 'user-alice'"
  FAIL=$((FAIL + 1))
fi

# Also verify via GET /agents/{name} that the field persists
if [ -n "$ALICE_AGENT_ID" ]; then
  run_test "T-S15-001 GET /agents/${ALICE_AGENT} → created_by=user-alice persisted" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${ALICE_AGENT}')
data = json.loads(r.read())
assert data.get('created_by') == 'user-alice', \
    f'expected user-alice got {data.get(\"created_by\")}'
"
fi

# ---------------------------------------------------------------------------
# T-S15-002: No X-User-Sub header → created_by defaults to 'system'
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-002: POST /agents/ without X-User-Sub → created_by=system ---"

SYSTEM_AGENT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=json.dumps({
        'name': '${SYSTEM_AGENT}',
        'team': 'platform',
        'description': 'Suite 15 artifact isolation test — system-created agent'
    }).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('created_by') == 'system', \
    f'expected created_by=system got {data.get(\"created_by\")}'
print(data['id'])
" 2>/dev/null || true)

if [ -n "$SYSTEM_AGENT_ID" ]; then
  echo "  PASS: T-S15-002 created_by=system confirmed (id=${SYSTEM_AGENT_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S15-002 Could not create agent or created_by != 'system'"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S15-003: Owner (user-alice) can see their own private agent in the list
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-003: GET /agents/ with X-User-Sub: user-alice → alice's private agent present ---"

if [ -n "$ALICE_AGENT_ID" ]; then
  run_test "T-S15-003 GET /agents/ X-User-Sub=user-alice → ${ALICE_AGENT} in list" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    headers={'X-User-Sub': 'user-alice'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
items = data if isinstance(data, list) else data.get('items', data.get('data', []))
names = [item.get('name') for item in items]
assert '${ALICE_AGENT}' in names, \
    f'${ALICE_AGENT} not in alice list: {names}'
# Verify the found agent has the right created_by
alice_entry = next(i for i in items if i.get('name') == '${ALICE_AGENT}')
assert alice_entry.get('created_by') == 'user-alice', \
    f'created_by mismatch: {alice_entry.get(\"created_by\")}'
"
else
  echo "  SKIP: T-S15-003 — no alice agent (T-S15-001 failed)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S15-004: Other user (user-bob) cannot see alice's private agent
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-004: GET /agents/ with X-User-Sub: user-bob → alice's private agent absent ---"

if [ -n "$ALICE_AGENT_ID" ]; then
  run_test "T-S15-004 GET /agents/ X-User-Sub=user-bob → ${ALICE_AGENT} NOT in list" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    headers={'X-User-Sub': 'user-bob'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
items = data if isinstance(data, list) else data.get('items', data.get('data', []))
names = [item.get('name') for item in items]
assert '${ALICE_AGENT}' not in names, \
    f'ISOLATION BREACH: ${ALICE_AGENT} visible to user-bob: {names}'
"
else
  echo "  SKIP: T-S15-004 — no alice agent (T-S15-001 failed)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S15-005: Anonymous call (no X-User-Sub) is DENY-BY-DEFAULT → published only
# ---------------------------------------------------------------------------
# NOTE: previously this asserted a no-header call returned ALL agents. That was
# the multi-tenant leak (a caller with no identity saw every tenant's private
# agents). Fixed: an anonymous list returns ONLY published agents; private
# agents (alice's + the system agent) must NOT appear.
echo ""
echo "--- T-S15-005: GET /agents/ without X-User-Sub → published-only (deny-by-default) ---"

if [ -n "$ALICE_AGENT_ID" ] && [ -n "$SYSTEM_AGENT_ID" ]; then
  run_test "T-S15-005 GET /agents/ no header → private agents (${ALICE_AGENT}, ${SYSTEM_AGENT}) NOT leaked" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/?limit=500')
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
items = data if isinstance(data, list) else data.get('items', data.get('data', []))
names = [item.get('name') for item in items]
# Both test agents are private ⇒ must be hidden from an anonymous caller.
assert '${ALICE_AGENT}' not in names, \
    f'LEAK: private ${ALICE_AGENT} visible to anonymous caller: {names[:10]}'
assert '${SYSTEM_AGENT}' not in names, \
    f'LEAK: private ${SYSTEM_AGENT} visible to anonymous caller: {names[:10]}'
# Anything returned must be published (deny-by-default).
leaked = [i.get('name') for i in items if i.get('publish_status') != 'published']
assert not leaked, f'LEAK: non-published agents in anonymous list: {leaked[:10]}'
"
else
  echo "  SKIP: T-S15-005 — missing alice or system agent (prior failures)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S15-006: Published agent is visible to any authenticated user
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-006: Publish alice's agent; user-bob can then see it ---"

PUBLISH_REQUEST_ID=""
if [ -n "$ALICE_AGENT_ID" ]; then
  # Create an eval-passed version so the publish gate (Decision 20) is satisfied.
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${ALICE_AGENT}/versions',
    data=json.dumps({'image_tag': 'registry.internal/s15:v1', 'eval_passed': True, 'adversarial_eval_passed': True}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'user-alice'}, method='POST')
try:
    urllib.request.urlopen(req)
except Exception as e:
    print('s15 version create:', e)
" 2>/dev/null || true
  PUBLISH_REQUEST_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${ALICE_AGENT}/publish',
    data=json.dumps({}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'user-alice'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 202, f'expected 202 got {r.status}'
data = json.loads(r.read())
assert 'publish_request_id' in data, f'missing publish_request_id in {data}'
print(data['publish_request_id'])
" 2>/dev/null || true)
fi

if [ -n "$PUBLISH_REQUEST_ID" ]; then
  echo "  Setup: publish request created (id=${PUBLISH_REQUEST_ID:0:8}...)"
else
  echo "  FAIL: T-S15-006 Could not submit publish request for ${ALICE_AGENT}"
  FAIL=$((FAIL + 1))
fi

# Admin approve the publish request
GRANTS_CREATED=0
if [ -n "$PUBLISH_REQUEST_ID" ]; then
  GRANTS_CREATED=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/admin/publish-requests/${PUBLISH_REQUEST_ID}/approve',
    data=json.dumps({'grantee_teams': ['platform']}).encode(),
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-admin'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('approved') is True, f'unexpected approve response: {data}'
gc = data.get('grants_created', 0)
assert gc >= 1, f'expected grants_created>=1 got {gc}'
print(gc)
" 2>/dev/null || true)
fi

if [ -n "$GRANTS_CREATED" ] && [ "$GRANTS_CREATED" -ge 1 ] 2>/dev/null; then
  echo "  Setup: approved, grants_created=${GRANTS_CREATED}"
else
  echo "  FAIL: T-S15-006 Approve failed or grants_created=0"
  FAIL=$((FAIL + 1))
fi

# Verify publish_status=published
run_test "T-S15-006 GET /agents/${ALICE_AGENT} → publish_status=published after approve" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${ALICE_AGENT}')
data = json.loads(r.read())
assert data.get('publish_status') == 'published', \
    f'expected published got {data.get(\"publish_status\")}'
"

# Now user-bob can see alice's published agent
run_test "T-S15-006 GET /agents/ X-User-Sub=user-bob → published ${ALICE_AGENT} now visible" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    headers={'X-User-Sub': 'user-bob'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
items = data if isinstance(data, list) else data.get('items', data.get('data', []))
names = [item.get('name') for item in items]
assert '${ALICE_AGENT}' in names, \
    f'published ${ALICE_AGENT} not visible to user-bob: {names}'
# Confirm it's actually published in the response
alice_entry = next(i for i in items if i.get('name') == '${ALICE_AGENT}')
assert alice_entry.get('publish_status') == 'published', \
    f'expected published got {alice_entry.get(\"publish_status\")}'
"

# ---------------------------------------------------------------------------
# T-S15-007: GET /agents/{name} direct fetch — no per-user isolation (known gap)
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-007: Direct GET /agents/{name} — isolation gap ---"

# Automated check: confirm the gap exists (user-bob CAN fetch alice's system agent by name)
run_test "T-S15-007 GET /agents/${SYSTEM_AGENT} with X-User-Sub: user-bob → 200 (no isolation on by-name fetch)" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${SYSTEM_AGENT}',
    headers={'X-User-Sub': 'user-bob'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('name') == '${SYSTEM_AGENT}', f'unexpected agent: {data.get(\"name\")}'
# NOTE: This is a known isolation gap — GET /agents/{name} does NOT enforce
# the same visibility filter as GET /agents/. Any caller who knows the name
# can retrieve the full agent record regardless of created_by or publish_status.
# Filed as a known gap; fix should add ownership/visibility check to the
# single-agent GET handler.
print('known gap confirmed: by-name fetch returns private agent to any caller')
"

check_manual "T-S15-007" \
  "GET /agents/{name} direct fetch has no per-user isolation — known gap" \
  "1. Note: POST /agents/ + GET /agents/ now enforce isolation, but GET /agents/{name} does not." \
  "2. Any caller who knows the agent name can retrieve it — no created_by or publish_status check." \
  "3. To validate: curl -H 'X-User-Sub: user-bob' http://<api>/api/v1/agents/${SYSTEM_AGENT}" \
  "   Expected gap: 200 OK returned even though bob did not create it and it is not published." \
  "4. Fix: add a visibility guard in agents.py router for GET /agents/{name} before shipping to prod."

# ---------------------------------------------------------------------------
# T-S15-008: Studio UX — agent created by alice not visible to bob (MANUAL)
# ---------------------------------------------------------------------------
check_manual "T-S15-008" \
  "Studio UX: agent created by alice not visible to bob in the agent list" \
  "1. Log in to Studio as alice (Keycloak user: alice)." \
  "2. Navigate to 'My Agents' or 'Agent List' and create a new agent (e.g. 'alice-isolation-check')." \
  "   - Verify: agent appears in alice's list with no publish_status indicator (private)." \
  "3. Log out. Log in as bob (Keycloak user: bob)." \
  "4. Navigate to the same Agent List page." \
  "   Expected: 'alice-isolation-check' does NOT appear in bob's list." \
  "   Expected: bob's list shows only published agents + agents bob created." \
  "5. Log back in as alice, publish 'alice-isolation-check' (submit + admin approve)." \
  "6. Log in as bob again — 'alice-isolation-check' should now appear as a published agent." \
  "" \
  "Pass criteria: private agents are invisible across user sessions; published agents are visible to all."

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup ---"

run_test "Cleanup: DELETE /agents/${ALICE_AGENT} → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${ALICE_AGENT}',
    method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

run_test "Cleanup: DELETE /agents/${SYSTEM_AGENT} → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${SYSTEM_AGENT}',
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
echo "  Suite 15 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "  (MANUAL items require the Studio UI running in a browser)"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0

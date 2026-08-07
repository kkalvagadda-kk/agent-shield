#!/usr/bin/env bash
# scripts/e2e/suite-15-artifact-isolation.sh
#
# E2E Suite 15: Artifact Isolation (created_by + visibility filter)
# Tests T-S15-001 through T-S15-008.
#
# What this proves:
#   T-S15-001 — POST /agents/ as alice -> created_by == alice's REAL Keycloak sub
#   T-S15-002 — POST /agents/ with NO credential -> 401 (was: created_by == 'system')
#   T-S15-003 — GET /agents/ as alice -> alice's private agent in list
#   T-S15-004 — GET /agents/ as bob   -> alice's private agent NOT in list
#   T-S15-005 — GET /agents/ anonymous -> published-only (deny-by-default; no leak)
#   T-S15-006 — Published agent visible to any authenticated user (bob sees it)
#   T-S15-007 — GET /agents/{name} (direct fetch) has no isolation — known gap / MANUAL
#   T-S15-008 — Studio UX: agent created by alice not visible to bob — MANUAL
#
# MIGRATED TO REAL IDENTITIES 2026-08-06 (RBAC R2, registry-api 0.2.263). READ THIS.
# ------------------------------------------------------------------------------
# This suite used to drive isolation with an `X-User-Sub: user-alice` HEADER and no
# credential, because `POST /agents/` took `get_optional_user` and fell back to that
# header for `created_by`. R2 deleted the fallback: a header any client can set is an
# audit stamp, never an identity, and the old behaviour let an ANONYMOUS caller create
# an agent attributed to anyone and auto-grant `agent-admin` on it
# (docs/bugs/anonymous-agent-creation-with-forged-attribution.md).
#
# `user-alice` and `user-bob` were never real: no Keycloak user, no role row. So the
# suite proved that the filter matches a string the caller supplied — which it would
# do whether or not the identity meant anything. It now uses two REAL personas created
# through `POST /api/v1/admin/users` (the same mechanism suite-98/76/78/82 use) and
# asserts against their actual `sub`. Same isolation semantics, an identity that cannot
# be typed by the caller.
#
# `list_agents` resolves `(user or {}).get("sub") or x_user_sub`, so the JWT wins and
# the read path is unchanged by this migration. That the header is STILL accepted as a
# read-path fallback is a real remaining gap — identity-propagation Phase 3 owns it —
# and T-S15-007 is where it shows.
#
# Background on the original bugs:
#   - GET /agents/ applies a visibility filter: publish_status='published' OR
#     created_by == caller. An anonymous caller sees published only.
#   - agents.created_by is NOT NULL (migration 0014 backfilled nulls to 'system')
#
# Usage:
#   bash scripts/e2e/suite-15-artifact-isolation.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-15-artifact-isolation.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

# R1/FR-11: POST /agents/{name}/versions and POST /api/v1/admin/publish-requests/{id}/approve
# require a real JWT. R2 added POST /agents/ to that list. The isolation assertions now
# ride real persona tokens too (see the migration note in the header).
# Call e2e_set_token BARE — a command substitution swallows its abort (lib/e2e-auth.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

# Two REAL personas. Both `contributor`: R2 gates POST /agents/ on can_create_agent
# (contributor+), and the isolation being tested is per-CREATOR, not per-role — making
# one of them a consumer would change what the suite proves.
ALICE_TOK="$(e2e_ensure_persona "$NAMESPACE" "$API_POD" "s15-alice" "contributor")"
BOB_TOK="$(e2e_ensure_persona   "$NAMESPACE" "$API_POD" "s15-bob"   "contributor")"
if [ -z "$ALICE_TOK" ] || [ -z "$BOB_TOK" ]; then
  echo "ERROR: could not provision the s15-alice / s15-bob personas — cannot test isolation"
  echo "       between two identities without two identities."
  exit 1
fi

# The sub is read OUT OF THE TOKEN, never hardcoded. A hardcoded sub silently stops
# matching the moment the realm is recreated (R0's re-pin case), and the isolation
# assertions would then pass or fail for reasons unrelated to isolation.
sub_of() { printf '%s' "$1" | cut -d. -f2 | python3 -c "
import base64, json, sys
raw = sys.stdin.read().strip()
print(json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))['sub'])
"; }
ALICE_SUB="$(sub_of "$ALICE_TOK")"
BOB_SUB="$(sub_of "$BOB_TOK")"
echo "  personas: s15-alice=${ALICE_SUB:0:8}…  s15-bob=${BOB_SUB:0:8}…"

cleanup() {
  echo ""
  echo "==> Cleanup: deleting test agents..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
for name in ['${ALICE_AGENT}', '${SYSTEM_AGENT}']:
    try:
        urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/' + name, method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}), timeout=5)
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
        method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
    )
    urllib.request.urlopen(req)
except: pass
" 2>/dev/null || true
done
echo "  cleanup done"

# ---------------------------------------------------------------------------
# T-S15-001: created_by is taken from the CALLER'S VERIFIED TOKEN, not a header
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-001: POST /agents/ as s15-alice → created_by = alice's real sub ---"

ALICE_AGENT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=json.dumps({
        'name': '${ALICE_AGENT}',
        'team': 'platform',
        'description': 'Suite 15 artifact isolation test — alice-owned private agent'
    }).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${ALICE_TOK}'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
data = json.loads(r.read())
assert data.get('created_by') == '${ALICE_SUB}', \
    f'expected created_by=${ALICE_SUB} got {data.get(\"created_by\")}'
print(data['id'])
" 2>/dev/null || true)

if [ -n "$ALICE_AGENT_ID" ]; then
  echo "  PASS: T-S15-001 created_by=alice's real sub confirmed (id=${ALICE_AGENT_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S15-001 Could not create agent as s15-alice, or created_by != ${ALICE_SUB}"
  FAIL=$((FAIL + 1))
fi

# Also verify via GET /agents/{name} that the field persists
if [ -n "$ALICE_AGENT_ID" ]; then
  run_test "T-S15-001 GET /agents/${ALICE_AGENT} → created_by=alice's sub persisted" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/${ALICE_AGENT}')
data = json.loads(r.read())
assert data.get('created_by') == '${ALICE_SUB}', \
    f'expected ${ALICE_SUB} got {data.get(\"created_by\")}'
"
fi

# ---------------------------------------------------------------------------
# T-S15-002: an UNCREDENTIALED create is refused (RBAC R2)
#
# This case used to assert the opposite — that a POST with no X-User-Sub succeeded and
# stored created_by='system'. That WAS the behaviour, and it was the defect: combined
# with the header fallback, anyone who could reach the API could create an agent, name
# its owner, and receive an `agent-admin` grant on it. The suite encoded the hole as the
# expectation, which is why nothing here ever went red over it. Now it asserts the
# refusal. An `X-User-Sub` header is sent DELIBERATELY: it must not be enough.
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-002: POST /agents/ with no credential → 401 ---"

ANON_CODE=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=json.dumps({
        'name': '${SYSTEM_AGENT}-anon-probe',
        'team': 'platform',
        'description': 'Suite 15 — anonymous create must be refused'
    }).encode(),
    # NO Authorization header — that is the entire point of this case. The X-User-Sub
    # below is sent deliberately: it must not be enough on its own.
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'user-alice'},
    method='POST'
)
try:
    r = urllib.request.urlopen(req); print(r.status)
except urllib.error.HTTPError as e: print(e.code)
except Exception: print(0)
" 2>/dev/null | tr -d '\r\n' || true)

if [ "$ANON_CODE" = "401" ]; then
  echo "  PASS: T-S15-002 anonymous POST /agents/ → 401 (an X-User-Sub header is not a credential)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S15-002 anonymous POST /agents/ → ${ANON_CODE} (want 401). If this is 201, the"
  echo "        X-User-Sub identity fallback is back and created_by is caller-supplied again."
  FAIL=$((FAIL + 1))
fi

# The second agent T-S15-007 needs. Created by the ADMIN over a real token — it exists to
# be a NON-alice-owned artifact, and who owns it beyond "not alice" does not matter.
SYSTEM_AGENT_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    data=json.dumps({
        'name': '${SYSTEM_AGENT}',
        'team': 'platform',
        'description': 'Suite 15 artifact isolation test — non-alice-owned agent'
    }).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${E2E_TOKEN}'},
    method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 201, f'expected 201 got {r.status}'
print(json.loads(r.read())['id'])
" 2>/dev/null || true)

if [ -n "$SYSTEM_AGENT_ID" ]; then
  echo "  PASS: T-S15-002b seeded a non-alice-owned agent (id=${SYSTEM_AGENT_ID:0:8}...)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: T-S15-002b could not seed the non-alice-owned agent T-S15-007 needs"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# T-S15-003: Owner (user-alice) can see their own private agent in the list
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S15-003: GET /agents/ as s15-alice → alice's private agent present ---"

if [ -n "$ALICE_AGENT_ID" ]; then
  run_test "T-S15-003 GET /agents/ as s15-alice → ${ALICE_AGENT} in list" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    headers={'Authorization': 'Bearer ${ALICE_TOK}'}
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
assert alice_entry.get('created_by') == '${ALICE_SUB}', \
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
echo "--- T-S15-004: GET /agents/ as s15-bob → alice's private agent absent ---"

if [ -n "$ALICE_AGENT_ID" ]; then
  run_test "T-S15-004 GET /agents/ as s15-bob → ${ALICE_AGENT} NOT in list" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    headers={'Authorization': 'Bearer ${BOB_TOK}'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
items = data if isinstance(data, list) else data.get('items', data.get('data', []))
names = [item.get('name') for item in items]
assert '${ALICE_AGENT}' not in names, \
    f'ISOLATION BREACH: ${ALICE_AGENT} visible to s15-bob: {names}'
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
echo "--- T-S15-006: Publish alice's agent; s15-bob can then see it ---"

PUBLISH_REQUEST_ID=""
if [ -n "$ALICE_AGENT_ID" ]; then
  # Create an eval-passed version so the publish gate (Decision 20) is satisfied.
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${ALICE_AGENT}/versions',
    data=json.dumps({'image_tag': 'registry.internal/s15:v1', 'eval_passed': True, 'adversarial_eval_passed': True}).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${ALICE_TOK}',
             'Authorization': 'Bearer ${E2E_TOKEN}'}, method='POST')
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
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ${ALICE_TOK}'},
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
    headers={'Content-Type': 'application/json', 'X-User-Sub': 'smoke-admin',
             'Authorization': 'Bearer ${E2E_TOKEN}'},
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
run_test "T-S15-006 GET /agents/ as s15-bob → published ${ALICE_AGENT} now visible" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/',
    headers={'Authorization': 'Bearer ${BOB_TOK}'}
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
items = data if isinstance(data, list) else data.get('items', data.get('data', []))
names = [item.get('name') for item in items]
assert '${ALICE_AGENT}' in names, \
    f'published ${ALICE_AGENT} not visible to s15-bob: {names}'
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
run_test "T-S15-007 GET /agents/${SYSTEM_AGENT} as s15-bob → 200 (no isolation on by-name fetch)" "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${SYSTEM_AGENT}',
    headers={'Authorization': 'Bearer ${BOB_TOK}'}
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
  "3. To validate: curl -H 'Authorization: Bearer <s15-bob token>' http://<api>/api/v1/agents/${SYSTEM_AGENT}" \
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
    method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
)
r = urllib.request.urlopen(req)
assert r.status == 204, f'expected 204 got {r.status}'
"

run_test "Cleanup: DELETE /agents/${SYSTEM_AGENT} → 204" "
import urllib.request
req = urllib.request.Request(
    'http://localhost:8000/api/v1/agents/${SYSTEM_AGENT}',
    method='DELETE', headers={'Authorization': 'Bearer ${E2E_TOKEN}'}
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

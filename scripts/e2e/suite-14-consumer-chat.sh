#!/usr/bin/env bash
# scripts/e2e/suite-14-consumer-chat.sh
#
# E2E Suite 14: Consumer Chat (Phase B)
# Tests T-S14-001 through T-S14-007.
#
# What this proves:
#   T-S14-001 — POST /agents/{name}/chat returns 401 without token
#   T-S14-002 — GET /deployments/?status=running returns only running deployments
#   T-S14-003 — Approve with empty body: 0 grants created, publish_status=published
#   T-S14-004 — Production HITL queue excludes playground-context approvals
#   T-S14-005 — Chat returns 503 when agent has no running deployment (MANUAL)
#   T-S14-006 — Chat SSE stream returns text/event-stream Content-Type (MANUAL)
#   T-S14-007 — Cleanup test artifacts
#
# Usage:
#   bash scripts/e2e/suite-14-consumer-chat.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-14-consumer-chat.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 14: Consumer Chat (Phase B) ==="
echo "  Pod: $API_POD"
echo ""

PASS=0
FAIL=0
MANUAL=0

pass() { echo "  PASS: $1"; ((PASS++)) || true; }
fail() { echo "  FAIL: $1"; ((FAIL++)) || true; }
check_manual() {
  local id="$1" desc="$2" instructions="$3"
  echo "  MANUAL [$id]: $desc"
  echo "    → $instructions"
  ((MANUAL++)) || true
}

# ---------------------------------------------------------------------------
# T-S14-001: POST /agents/{name}/chat returns 401 without auth token
# ---------------------------------------------------------------------------
echo "[T-S14-001] Chat endpoint returns 401 without auth token"
STATUS=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
r = httpx.post('http://localhost:8000/api/v1/agents/customer-intelligence-agent/chat',
               json={'message': 'hello'}, timeout=5)
print(r.status_code)
" 2>/dev/null)
[ "$STATUS" = "401" ] && pass "T-S14-001: chat returns 401 without token" \
                       || fail "T-S14-001: expected 401, got $STATUS"

# ---------------------------------------------------------------------------
# T-S14-002: GET /deployments/?status=running returns only running deployments
# ---------------------------------------------------------------------------
echo "[T-S14-002] /deployments/?status=running returns only running deployments"
RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, json
r = httpx.get('http://localhost:8000/api/v1/deployments/?status=running&limit=100', timeout=5)
if r.status_code != 200:
    print(f'FAIL: HTTP {r.status_code}')
else:
    items = r.json().get('items', [])
    bad = [d.get('status') for d in items if d.get('status') != 'running']
    print('FAIL: non-running items: ' + str(bad) if bad else 'ok')
" 2>/dev/null)
[ "$RESULT" = "ok" ] && pass "T-S14-002: /deployments/?status=running is clean" \
                       || fail "T-S14-002: $RESULT"

# ---------------------------------------------------------------------------
# T-S14-003: Approve with empty body creates 0 grants, sets publish_status=published
# ---------------------------------------------------------------------------
echo "[T-S14-003] Approve with no grantee_teams → 0 grants + publish_status=published"
RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Create test agent
r = httpx.post('http://localhost:8000/api/v1/agents/',
    json={'name': 's14-promote-test', 'team': 'platform', 'agent_type': 'declarative'},
    timeout=5)
if r.status_code not in (200, 201, 409):
    print(f'agent create: {r.status_code} {r.text[:80]}')
    sys.exit(0)

# Submit for publish
pub = httpx.post('http://localhost:8000/api/v1/agents/s14-promote-test/publish',
    json={'dependency_declaration': {}}, timeout=5)
if pub.status_code not in (200, 201, 202):
    print(f'publish: {pub.status_code} {pub.text[:80]}')
    sys.exit(0)
pr_id = pub.json().get('publish_request_id', '')

# Approve with empty body (no grantee_teams)
apr = httpx.post(
    f'http://localhost:8000/api/v1/admin/publish-requests/{pr_id}/approve',
    json={}, timeout=5)
if apr.status_code != 200:
    print(f'approve: {apr.status_code} {apr.text[:80]}')
    sys.exit(0)
d = apr.json()
if d.get('grants_created', 99) != 0:
    print(f'expected 0 grants, got: {d}')
    sys.exit(0)

# Verify publish_status
ag = httpx.get('http://localhost:8000/api/v1/agents/s14-promote-test', timeout=5).json()
if ag.get('publish_status') != 'published':
    print(f'publish_status={ag.get(\"publish_status\")}')
    sys.exit(0)

print('ok')
" 2>/dev/null)
[ "$RESULT" = "ok" ] && pass "T-S14-003: promote-only approve → 0 grants + published" \
                       || fail "T-S14-003: $RESULT"

# ---------------------------------------------------------------------------
# T-S14-004: Production HITL queue excludes playground-context approvals
# ---------------------------------------------------------------------------
echo "[T-S14-004] Production HITL queue excludes playground-context approvals"
RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
r = httpx.get('http://localhost:8000/api/v1/approvals/?status=pending&limit=100', timeout=5)
if r.status_code != 200:
    print(f'FAIL: HTTP {r.status_code}')
else:
    items = r.json().get('items', r.json() if isinstance(r.json(), list) else [])
    pg = [i for i in items if i.get('context') == 'playground']
    print('FAIL: playground items in prod queue: ' + str(pg) if pg else 'ok')
" 2>/dev/null)
[ "$RESULT" = "ok" ] && pass "T-S14-004: production HITL excludes playground approvals" \
                       || fail "T-S14-004: $RESULT"

# ---------------------------------------------------------------------------
# T-S14-005: MANUAL — chat returns 503 when agent has no running deployment
# ---------------------------------------------------------------------------
echo "[T-S14-005] MANUAL: chat returns 503 when agent has no running deployment"
check_manual "T-S14-005" \
  "Chat returns 503 when no running deployment exists" \
  "POST /api/v1/agents/s14-promote-test/chat with valid Bearer token → expect HTTP 503"

# ---------------------------------------------------------------------------
# T-S14-006: MANUAL — chat SSE stream returns text/event-stream
# ---------------------------------------------------------------------------
echo "[T-S14-006] MANUAL: chat SSE stream returns text/event-stream"
check_manual "T-S14-006" \
  "Chat SSE stream Content-Type is text/event-stream" \
  "For a deployed agent: POST /chat → use run_id for GET stream_url → verify Content-Type header and data: lines"

# ---------------------------------------------------------------------------
# T-S14-007: Cleanup — delete s14-promote-test agent
# ---------------------------------------------------------------------------
echo "[T-S14-007] Cleanup: delete s14-promote-test agent"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
httpx.delete('http://localhost:8000/api/v1/agents/s14-promote-test', timeout=5)
" 2>/dev/null || true
pass "T-S14-007: cleanup complete"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Suite 14 Results"
echo "  PASS=$PASS  FAIL=$FAIL  MANUAL=$MANUAL"
echo "  (MANUAL items require a running agent deployment and valid Bearer token)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

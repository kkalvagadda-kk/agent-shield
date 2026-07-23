#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP5b — MCP as a Tool Source (Phase 1): Studio UI infra smoke.
#
#   - studio pod Ready
#   - GET / (the SPA index) -> 200 (served by studio's nginx)
#   - the API the UI consumes reachable: GET /api/v1/mcp-servers/ -> 200 with a
#     paginated {items,total} body (queried from inside the registry-api pod)
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint CP5: studio infra smoke (SPA + mcp-servers API) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"

fail() { echo "FAIL: $1" >&2; exit 1; }

# ── 1. studio pod Ready ───────────────────────────────────────────────────────
echo "--- studio pod Ready ---"
kubectl rollout status deploy/agentshield-studio -n "$NAMESPACE" --timeout=180s \
  || fail "studio deployment not Ready within timeout"
STUDIO_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$STUDIO_POD" ] || fail "no Running studio pod found"
echo "  OK: pod $STUDIO_POD Ready"

# ── 2. GET / (SPA) -> 200 ─────────────────────────────────────────────────────
echo "--- GET / (SPA index) -> 200 ---"
SPA_CODE="$(kubectl exec -n "$NAMESPACE" "$STUDIO_POD" -- \
  sh -c "wget -q -O /dev/null -S http://localhost:80/ 2>&1 | awk '/HTTP\//{print \$2; exit}'" \
  2>/dev/null || echo "000")"
[ "$SPA_CODE" = "200" ] || fail "GET / -> $SPA_CODE (want 200)"
echo "  OK: SPA index served"

# ── 3. GET /api/v1/mcp-servers/ -> 200 paginated ──────────────────────────────
echo "--- GET /api/v1/mcp-servers/ -> 200 {items,total} ---"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod found"
RESULT="$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, json, sys
r = httpx.get('http://localhost:8000/api/v1/mcp-servers/', timeout=10)
assert r.status_code == 200, f'status {r.status_code}'
b = r.json()
assert 'items' in b and 'total' in b, f'not paginated: {list(b)}'
print('OK', r.status_code, 'total=' + str(b['total']))
" 2>&1 || true)"
echo "  $RESULT"
echo "$RESULT" | grep -q '^OK 200' || fail "GET /api/v1/mcp-servers/ not a 200 paginated body"

echo "PASS"

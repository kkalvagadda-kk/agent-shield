#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP2b — MCP as a Tool Source (Phase 1): mcp-proxy infrastructure smoke.
#
#   - mcp-proxy pod Ready + GET /health -> 200
#   - proxy SA least-privilege RBAC (kubectl auth can-i --as=<proxy SA>):
#       get secrets  -n agentshield-mcp        -> yes
#       get secrets  -n agentshield-platform   -> NO   (cannot reach the master key)
#       create tokenreviews.authentication.k8s.io -> yes
#       get pods     -n agentshield-platform   -> NO
#   - POST /internal/discover:
#       missing token                -> 401
#       wrong-subject SA token       -> 403  (admin-plane: only registry-api's SA)
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint CP2: mcp-proxy infra smoke (health + RBAC + authn) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
PROXY_SA="system:serviceaccount:${NAMESPACE}:agentshield-mcp-proxy"
PROXY_AUDIENCE="${PROXY_AUDIENCE:-agentshield-mcp-proxy}"

fail() { echo "FAIL: $1" >&2; exit 1; }

# ── 1. proxy pod Ready ────────────────────────────────────────────────────────
echo "--- mcp-proxy pod Ready ---"
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=mcp-proxy -n "$NAMESPACE" --timeout=180s \
  || fail "mcp-proxy pod not Ready within timeout"
PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod found"
echo "  OK: pod $PROXY_POD Ready"

# ── 2. GET /health -> 200 (unauthenticated probe target) ──────────────────────
echo "--- GET /health -> 200 ---"
HEALTH_CODE="$(kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health', timeout=10).getcode())" \
  2>/dev/null || echo "000")"
[ "$HEALTH_CODE" = "200" ] || fail "GET /health -> $HEALTH_CODE (want 200)"
echo "  OK: /health -> 200"

# ── 3. Proxy SA least-privilege RBAC matrix ───────────────────────────────────
echo "--- RBAC can-i matrix for ${PROXY_SA} ---"
assert_cani() {  # $1 = expected (yes/no), $2.. = kubectl auth can-i args
  local want="$1"; shift
  local got
  got="$(kubectl auth can-i "$@" --as="$PROXY_SA" 2>/dev/null || true)"
  if [ "$got" = "$want" ]; then
    echo "  OK: can-i $* -> $got"
  else
    fail "can-i $* -> '${got:-<empty>}' (want '$want')"
  fi
}
assert_cani yes get secrets -n "$MCP_NS"
assert_cani no  get secrets -n "$NAMESPACE"
assert_cani yes create tokenreviews.authentication.k8s.io
assert_cani no  get pods -n "$NAMESPACE"

# ── 4. /internal/discover auth failures (401 missing, 403 wrong subject) ──────
echo "--- POST /internal/discover authn (401 / 403) ---"
# Mint a token with the proxy's audience but a NON-registry-api subject
# (the platform-namespace 'default' SA stands in for any authenticated
# non-registry-api / agent caller). Authn passes; the admin-plane subject
# check then rejects it with 403.
WRONG_TOKEN="$(kubectl create token default -n "$NAMESPACE" \
  --audience "$PROXY_AUDIENCE" --duration 10m 2>/dev/null || true)"
[ -n "$WRONG_TOKEN" ] || fail "could not mint a wrong-subject SA token"

DISCOVER_OUT="$(kubectl exec -i -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  env WRONG_TOKEN="$WRONG_TOKEN" python3 - <<'PY'
import os, json, urllib.request, urllib.error

URL = "http://localhost:8080/internal/discover"
BODY = json.dumps({"server_id": "00000000-0000-0000-0000-000000000000"}).encode()

def code(token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(URL, data=BODY, headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=10).getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa: BLE001
        return f"ERR:{e}"

print("MISSING", code(None))
print("WRONGSUB", code(os.environ["WRONG_TOKEN"]))
PY
)" || { echo "$DISCOVER_OUT"; fail "discover authn probe errored"; }
echo "$DISCOVER_OUT"
echo "$DISCOVER_OUT" | grep -q "MISSING 401"  || fail "missing-token discover did not return 401"
echo "$DISCOVER_OUT" | grep -q "WRONGSUB 403" || fail "wrong-subject discover did not return 403"
echo "  OK: 401 on missing token, 403 on wrong subject"

echo "PASS"

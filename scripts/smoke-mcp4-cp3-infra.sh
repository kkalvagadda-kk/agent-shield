#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP3b — MCP Phase 4 (WS-2 proxy token-read): infrastructure smoke.
#
# Proves the proxy is wired to authenticate to registry-api's token endpoint:
#   - mcp-proxy + registry-api pods Ready
#   - the mcp-proxy pod mounts the projected SA token (audience agentshield-registry-api)
#     at /var/run/secrets/registry-api/token and the file is NON-EMPTY
#   - POST /api/v1/internal/mcp/oauth/access-token is REACHABLE from the proxy pod, and the
#     proxy's OWN projected token is accepted (TokenReview passes: NOT 401/403). With no
#     grant for a random (server,user) the endpoint returns 200 {status:needs_auth} — that
#     is the "reachable + authenticated" proof (a fail-closed grant outcome, not an auth error).
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP3: proxy token-read infra smoke (projected token + reachability) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
TOKEN_PATH="${MCP_PROXY_REGISTRY_API_TOKEN_PATH:-/var/run/secrets/registry-api/token}"
TOKEN_URL="${REGISTRY_API_OAUTH_TOKEN_URL:-http://agentshield-registry-api.${NAMESPACE}:8000/api/v1/internal/mcp/oauth/access-token}"

fail() { echo "FAIL: $1" >&2; exit 1; }

echo "--- mcp-proxy + registry-api pods Ready ---"
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=mcp-proxy \
  -n "$NAMESPACE" --timeout=180s || fail "mcp-proxy pod not Ready"
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=registry-api \
  -n "$NAMESPACE" --timeout=180s || fail "registry-api pod not Ready"
PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod"
echo "  OK: proxy=$PROXY_POD"

# ── 1. Projected SA token mounted + non-empty ─────────────────────────────────
echo "--- proxy mounts the agentshield-registry-api projected token at $TOKEN_PATH ---"
LEN="$(kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  sh -c "wc -c < '$TOKEN_PATH' 2>/dev/null || echo 0" | tr -d '[:space:]')"
[ -n "$LEN" ] && [ "$LEN" -gt 0 ] 2>/dev/null \
  || fail "projected token at $TOKEN_PATH is missing/empty (len=${LEN:-0})"
echo "  OK: projected token present (${LEN} bytes)"

# ── 2. Token endpoint reachable + the proxy token is accepted (not 401/403) ────
echo "--- POST $TOKEN_URL from the proxy pod with its OWN projected token ---"
OUT="$(kubectl exec -i -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  env TOKEN_PATH="$TOKEN_PATH" TOKEN_URL="$TOKEN_URL" python3 - <<'PY'
import os, httpx
tok = open(os.environ["TOKEN_PATH"]).read().strip()
r = httpx.post(os.environ["TOKEN_URL"],
    json={"server_id": "00000000-0000-0000-0000-000000000000", "user_sub": "cp3-probe"},
    headers={"Authorization": f"Bearer {tok}"}, timeout=20)
try:
    status = r.json().get("status")
except Exception:
    status = None
print("CODE", r.status_code, "STATUS", status)
PY
)" || { echo "$OUT"; fail "token endpoint call from the proxy pod errored"; }
echo "  $OUT"
# A reachable + AUTHENTICATED proxy gets a 200 grant outcome (needs_auth for a random pair),
# NOT 401 (bad token) or 403 (wrong subject). Either of those = the projected token/RBAC is
# misconfigured.
echo "$OUT" | grep -q "CODE 401" && fail "token endpoint returned 401 — proxy projected token rejected (audience/mount misconfig)"
echo "$OUT" | grep -q "CODE 403" && fail "token endpoint returned 403 — proxy subject not pinned (MCP_PROXY_SA_SUBJECT mismatch)"
echo "$OUT" | grep -q "CODE 200 STATUS needs_auth" \
  || fail "expected 200 needs_auth for a random (server,user); got: $OUT"
echo "  OK: token endpoint reachable + proxy token accepted (200 needs_auth for a random pair)"

echo "PASS"

#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster + the Keycloak confidential client (quickstart.md).
# =============================================================================
# CP3b — MCP Phase 2 (WS-C internal identity): infrastructure smoke.
#
# Proves the identity plumbing is mounted + least-privilege RBAC is UNCHANGED:
#   - the mcp-proxy pod mounts the Keycloak client secret at
#     /var/run/secrets/mcp-proxy-keycloak/client-secret (a FILE, not an API read)
#   - RBAC unchanged: proxy SA can `get secrets` in agentshield-mcp (yes) but NOT in
#     agentshield-platform (no) — the Keycloak secret is a mount, not a `get secrets`
#   - the proxy can reach Keycloak: a service_identity register+discover succeeds
#     (status=connected → minting worked; a mint failure would be status=error)
#   - a per-server Secret's connection.identity_mode matches the DB row (T-S85-020)
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP3: identity infra smoke (mount + RBAC + Keycloak reach + secret) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
PROXY_SA="system:serviceaccount:${NAMESPACE}:agentshield-mcp-proxy"
KC_SECRET_PATH="${KC_SECRET_PATH:-/var/run/secrets/mcp-proxy-keycloak/client-secret}"
SUFFIX="$(date +%s | tail -c 7)"

fail() { echo "FAIL: $1" >&2; exit 1; }

PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
echo "  proxy=$PROXY_POD api=$API_POD"

# ── 1. Keycloak client-secret file is mounted + non-empty ─────────────────────
echo "--- Keycloak client secret mounted at ${KC_SECRET_PATH} ---"
SZ="$(kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  sh -c "test -s '$KC_SECRET_PATH' && wc -c < '$KC_SECRET_PATH'" 2>/dev/null || echo "0")"
[ "${SZ:-0}" -gt 0 ] 2>/dev/null || fail "Keycloak client secret not mounted / empty at ${KC_SECRET_PATH}"
echo "  OK: client secret mounted (${SZ} bytes)"

# ── 2. RBAC unchanged (get secrets: mcp yes / platform no) ────────────────────
echo "--- RBAC can-i matrix for ${PROXY_SA} (UNCHANGED from Phase 1) ---"
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
echo "  OK: least-privilege RBAC unchanged (Keycloak secret is a mount, not a get-secrets)"

# ── 3. Start the fixture + register a service_identity server → connected ──────
echo "--- proxy can reach Keycloak: a service_identity register+discover succeeds ---"
kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  sh -c 'setsid nohup python3 /app/fixtures/stub_mcp_server.py >/tmp/stub.log 2>&1 &' || true
READY=""
for _ in $(seq 1 20); do
  if kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- python3 -c \
       "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',9999)); s.close()" \
       2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ -n "$READY" ] || fail "stub fixture did not start listening on 127.0.0.1:9999"

REG_OUT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, httpx, sys
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/mcp-servers/", headers=HDR, json={
            "name": f"cp3-si-{SUFFIX}", "description": "cp3 service_identity fixture",
            "server_url": "http://127.0.0.1:9999/mcp", "transport": "streamable_http",
            "owner_team": "platform", "is_external": False,
            "identity_mode": "service_identity", "scan_results": True,
            "transport_config": {"identity_audience": f"aud-{SUFFIX}"}})
        if r.status_code != 201:
            print("REGFAIL", r.status_code, r.text[:200]); sys.exit(1)
        b = r.json()
        print("SERVER_ID", b["id"])
        print("STATUS", b.get("status"))
asyncio.run(main())
PY
)" || { echo "$REG_OUT"; fail "service_identity register failed"; }
echo "$REG_OUT"
SERVER_ID="$(echo "$REG_OUT" | sed -n 's/^SERVER_ID //p' | tr -d '[:space:]')"
[ -n "$SERVER_ID" ] || fail "could not capture SERVER_ID"
cleanup() {
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env SID="$SERVER_ID" python3 - <<'PY' 2>/dev/null || true
import os, asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        await c.delete(f"http://localhost:8000/api/v1/mcp-servers/{os.environ['SID']}",
                       headers={"X-User-Sub": "platform-admin"})
asyncio.run(main())
PY
}
trap cleanup EXIT
echo "$REG_OUT" | grep -q "STATUS connected" \
  || fail "service_identity server did not reach status=connected — Keycloak minting failed (client provisioned?)"
echo "  OK: service_identity discover connected (proxy → Keycloak mint works)"

# ── 4. The per-server Secret's connection.identity_mode matches the row (T-S85-020) ─
echo "--- per-server Secret connection.identity_mode == service_identity ---"
SECRET_NAME="agentshield-mcp-server-${SERVER_ID}"
CONN_B64="$(kubectl get secret "$SECRET_NAME" -n "$MCP_NS" -o jsonpath='{.data.connection}' 2>/dev/null || true)"
[ -n "$CONN_B64" ] || fail "per-server Secret ${SECRET_NAME} has no data.connection"
IDENT_MODE="$(echo "$CONN_B64" | base64 -d 2>/dev/null | jq -r '.identity_mode')"
IDENT_AUD="$(echo "$CONN_B64" | base64 -d 2>/dev/null | jq -r '.identity_audience')"
echo "  connection.identity_mode=$IDENT_MODE identity_audience=$IDENT_AUD"
[ "$IDENT_MODE" = "service_identity" ] \
  || fail "Secret connection.identity_mode=$IDENT_MODE (want service_identity)"
[ "$IDENT_AUD" = "aud-${SUFFIX}" ] \
  || fail "Secret connection.identity_audience=$IDENT_AUD (want aud-${SUFFIX})"
echo "  OK: identity_mode + identity_audience carried in the per-server Secret"

echo "PASS"

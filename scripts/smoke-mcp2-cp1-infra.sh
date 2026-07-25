#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP1b — MCP Phase 2 (WS-A health loop): infrastructure smoke.
#
# Proves the admin-plane /internal/health probe is real + correctly gated, and that
# the registry-api health loop is running:
#   - mcp-proxy + registry-api pods Ready
#   - start the stub fixture in the proxy pod; register it via registry-api
#   - proxy POST /internal/health with the registry-api SA token → 200 ok=true
#   - proxy POST /internal/health with NO token                  → 401
#   - proxy POST /internal/health with an agent-SA token          → 403 (admin plane)
#   - registry-api logs the health sweep ("mcp health sweep started")
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP1: health-probe infra smoke (200/401/403 + loop running) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
PROXY_AUDIENCE="${PROXY_AUDIENCE:-agentshield-mcp-proxy}"
REGISTRY_API_SA="${REGISTRY_API_SA:-agentshield-registry-api}"
SUFFIX="$(date +%s | tail -c 7)"

fail() { echo "FAIL: $1" >&2; exit 1; }

# ── 1. Both pods Ready ────────────────────────────────────────────────────────
echo "--- mcp-proxy + registry-api pods Ready ---"
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=mcp-proxy \
  -n "$NAMESPACE" --timeout=180s || fail "mcp-proxy pod not Ready"
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=registry-api \
  -n "$NAMESPACE" --timeout=180s || fail "registry-api pod not Ready"
PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
echo "  OK: proxy=$PROXY_POD api=$API_POD"

# ── 2. Start the stub MCP fixture inside the proxy pod ─────────────────────────
echo "--- starting stub MCP fixture inside the proxy pod ---"
kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  sh -c 'setsid nohup python3 /app/fixtures/stub_mcp_server.py >/tmp/stub.log 2>&1 &' || true
READY=""
for _ in $(seq 1 20); do
  if kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- python3 -c \
       "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',9999)); s.close()" \
       2>/dev/null; then
    READY=1; break
  fi
  sleep 2
done
[ -n "$READY" ] || fail "stub fixture did not start listening on 127.0.0.1:9999"
echo "  OK: fixture listening on 127.0.0.1:9999"

# ── 3. Register the fixture as an MCP server (so /internal/health has a target) ─
echo "--- register the fixture server via registry-api ---"
SERVER_ID="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, httpx
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/mcp-servers/", headers=HDR, json={
            "name": f"cp1-health-{SUFFIX}", "description": "cp1 health fixture",
            "server_url": "http://127.0.0.1:9999/mcp", "transport": "streamable_http",
            "owner_team": "platform", "is_external": False,
            "identity_mode": "none", "scan_results": True})
        r.raise_for_status()
        print(r.json()["id"])
asyncio.run(main())
PY
)" || fail "could not register the fixture server"
SERVER_ID="$(echo "$SERVER_ID" | tr -d '[:space:]')"
[ -n "$SERVER_ID" ] || fail "empty server id after register"
echo "  OK: server $SERVER_ID"
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

# ── 4. /internal/health auth matrix (200 ok=true / 401 / 403) ─────────────────
echo "--- POST /internal/health auth matrix ---"
REG_TOKEN="$(kubectl create token "$REGISTRY_API_SA" -n "$NAMESPACE" \
  --audience "$PROXY_AUDIENCE" --duration 10m 2>/dev/null || true)"
[ -n "$REG_TOKEN" ] || fail "could not mint the registry-api SA token"
# An 'agent' caller: any authenticated non-registry-api subject stands in for an agent SA.
AGENT_TOKEN="$(kubectl create token default -n "$NAMESPACE" \
  --audience "$PROXY_AUDIENCE" --duration 10m 2>/dev/null || true)"
[ -n "$AGENT_TOKEN" ] || fail "could not mint a stand-in agent SA token"

HEALTH_OUT="$(kubectl exec -i -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  env SID="$SERVER_ID" REG_TOKEN="$REG_TOKEN" AGENT_TOKEN="$AGENT_TOKEN" python3 - <<'PY'
import os, json, urllib.request, urllib.error
URL = "http://localhost:8080/internal/health"
BODY = json.dumps({"server_id": os.environ["SID"]}).encode()
def call(token=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(URL, data=BODY, headers=h, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.getcode(), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # noqa: BLE001
        return f"ERR:{e}", ""
c_reg, b_reg = call(os.environ["REG_TOKEN"])
c_missing, _ = call(None)
c_agent, _ = call(os.environ["AGENT_TOKEN"])
ok = False
try: ok = json.loads(b_reg).get("ok") is True
except Exception: pass
print("REG", c_reg, "OK" if ok else "NOTOK")
print("MISSING", c_missing)
print("AGENT", c_agent)
PY
)" || { echo "$HEALTH_OUT"; fail "/internal/health probe errored"; }
echo "$HEALTH_OUT"
echo "$HEALTH_OUT" | grep -q "REG 200 OK"  || fail "registry-api-token /internal/health did not return 200 ok=true"
echo "$HEALTH_OUT" | grep -q "MISSING 401" || fail "missing-token /internal/health did not return 401"
echo "$HEALTH_OUT" | grep -q "AGENT 403"   || fail "agent-token /internal/health did not return 403"
echo "  OK: 200 ok=true (registry-api SA), 401 (no token), 403 (agent SA)"

# ── 5. The health loop is running (verify its EFFECT, not a boot-time log line) ─
# A "sweep started" log is written once at task start and rotates out of the kubelet
# buffer on a chatty pod, so grepping for it is unreliable. Instead assert the loop's
# EFFECT: health_detail.last_success_at is populated ONLY by the health loop's
# successful probe (register/discover never writes it). Wait up to ~2 intervals for
# the just-registered fixture server (or any server) to be probed.
echo "--- registry-api health loop is probing (health_detail.last_success_at written) ---"
_loop_ok=0
for _i in $(seq 1 14); do
  N=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio
from db import AsyncSessionLocal
from sqlalchemy import text as t
async def m():
    async with AsyncSessionLocal() as s:
        n=(await s.execute(t(\"SELECT count(*) FROM mcp_servers WHERE health_detail->>'last_success_at' IS NOT NULL\"))).scalar()
        print(n)
asyncio.run(m())
" 2>/dev/null | tr -d '[:space:]')
  if [ "${N:-0}" -ge 1 ] 2>/dev/null; then _loop_ok=1; break; fi
  sleep 10
done
[ "$_loop_ok" -eq 1 ] || fail "no mcp_server has health_detail.last_success_at after ~2 intervals — the health loop is not probing"
echo "  OK: health loop probed a server (last_success_at written)"

echo "PASS"

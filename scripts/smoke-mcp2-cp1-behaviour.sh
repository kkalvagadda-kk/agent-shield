#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP1c — MCP Phase 2 (WS-A health loop): behaviour smoke.
#
# Drives the REAL periodic health loop (no monkeypatching — the suite-85 unit path
# does that; here we prove the deployed loop end-to-end):
#   1. Register a server at a DEAD url; over >= threshold intervals assert
#      mcp_servers.status flips to 'error' with health_detail.consecutive_failures
#      >= threshold and last_error set.
#   2. last_synced_at is UNCHANGED across the health writes (health != discovery).
#   3. Repoint the row at the live in-pod fixture; assert recovery to
#      'connected' / consecutive_failures 0.
#   4. Scale registry-api to 2 replicas; assert consecutive_failures advances by
#      ~1 per interval (single-flight advisory lock — NOT 2 per interval).
#
# Interval/threshold are read from the deployed config. jq/SQL assertions.
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP1: health-loop behaviour smoke (error/recover/single-flight) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"
# Match the deployed defaults (config.py: interval 60s, threshold 3). Override for a
# faster run by setting these AND MCP_HEALTH_CHECK_INTERVAL_SECONDS on the deployment.
INTERVAL="${INTERVAL:-60}"
THRESHOLD="${THRESHOLD:-3}"

fail() { echo "FAIL: $1" >&2; exit 1; }

PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
echo "  proxy=$PROXY_POD api=$API_POD interval=${INTERVAL}s threshold=${THRESHOLD}"

# ── Start the live fixture in the proxy pod (used for the recovery step) ───────
echo "--- starting stub MCP fixture inside the proxy pod ---"
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
echo "  OK: fixture listening"

# ── 1. Register a DEAD server + capture its initial last_synced_at ────────────
echo "--- register a dead server ---"
SERVER_ID="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, httpx
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        # 127.0.0.1:1 → connection refused fast; register still returns 201 (status error/connected).
        r = await c.post(f"{BASE}/mcp-servers/", headers=HDR, json={
            "name": f"cp1-dead-{SUFFIX}", "description": "cp1 dead server",
            "server_url": "http://127.0.0.1:1/mcp", "transport": "streamable_http",
            "owner_team": "platform", "is_external": False,
            "identity_mode": "none", "scan_results": True})
        r.raise_for_status()
        print(r.json()["id"])
asyncio.run(main())
PY
)" || fail "could not register the dead server"
SERVER_ID="$(echo "$SERVER_ID" | tr -d '[:space:]')"
[ -n "$SERVER_ID" ] || fail "empty server id"
echo "  OK: dead server $SERVER_ID"
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
  kubectl scale deployment/agentshield-registry-api -n "$NAMESPACE" --replicas=1 >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Helper: read status / consecutive_failures / last_error / last_synced_at as JSON.
read_state() {
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env SID="$SERVER_ID" python3 - <<'PY'
import os, asyncio, json
from sqlalchemy import select
async def main():
    from db import AsyncSessionLocal
    from models import MCPServer
    import uuid
    async with AsyncSessionLocal() as s:
        srv = (await s.execute(select(MCPServer).where(
            MCPServer.id == uuid.UUID(os.environ["SID"])))).scalar_one_or_none()
        if srv is None:
            print(json.dumps({})); return
        hd = srv.health_detail or {}
        print(json.dumps({
            "status": srv.status,
            "consecutive_failures": hd.get("consecutive_failures"),
            "last_error": hd.get("last_error"),
            "last_synced_at": srv.last_synced_at.isoformat() if srv.last_synced_at else None,
        }))
asyncio.run(main())
PY
}

INITIAL="$(read_state)"
INITIAL_SYNCED="$(echo "$INITIAL" | jq -r '.last_synced_at')"
echo "  initial: $INITIAL"

# ── Wait > threshold intervals for status to flip to 'error' ──────────────────
echo "--- waiting for status → error over >= ${THRESHOLD} intervals ---"
WAIT_CYCLES=$((THRESHOLD + 2))
GOT_ERROR=""
for i in $(seq 1 "$WAIT_CYCLES"); do
  sleep "$INTERVAL"
  ST="$(read_state)"
  STATUS="$(echo "$ST" | jq -r '.status')"
  CF="$(echo "$ST" | jq -r '.consecutive_failures // 0')"
  echo "  cycle $i: status=$STATUS consecutive_failures=$CF"
  if [ "$STATUS" = "error" ] && [ "$CF" -ge "$THRESHOLD" ]; then GOT_ERROR="$ST"; break; fi
done
[ -n "$GOT_ERROR" ] || fail "status did not flip to 'error' with consecutive_failures >= ${THRESHOLD}"
LAST_ERROR="$(echo "$GOT_ERROR" | jq -r '.last_error')"
[ "$LAST_ERROR" != "null" ] && [ -n "$LAST_ERROR" ] || fail "last_error not set on the errored server"
echo "  OK: status=error consecutive_failures>=${THRESHOLD} last_error set"

# ── 2. last_synced_at UNCHANGED across the health writes ───────────────────────
NOW_SYNCED="$(echo "$GOT_ERROR" | jq -r '.last_synced_at')"
[ "$NOW_SYNCED" = "$INITIAL_SYNCED" ] \
  || fail "last_synced_at changed across health writes ($INITIAL_SYNCED → $NOW_SYNCED) — health must not write last_synced_at"
echo "  OK: last_synced_at unchanged ($NOW_SYNCED)"

# ── 3. Repoint at the live fixture → recovery to connected / 0 ────────────────
echo "--- repoint at the live fixture and wait for recovery ---"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SID="$SERVER_ID" python3 - <<'PY' || fail "could not repoint the server url"
import os, asyncio, uuid
from sqlalchemy import select
async def main():
    from db import AsyncSessionLocal
    from models import MCPServer
    async with AsyncSessionLocal() as s:
        srv = (await s.execute(select(MCPServer).where(
            MCPServer.id == uuid.UUID(os.environ["SID"])))).scalar_one()
        srv.server_url = "http://127.0.0.1:9999/mcp"
        await s.commit()
asyncio.run(main())
PY
RECOVERED=""
for i in $(seq 1 "$WAIT_CYCLES"); do
  sleep "$INTERVAL"
  ST="$(read_state)"
  STATUS="$(echo "$ST" | jq -r '.status')"
  CF="$(echo "$ST" | jq -r '.consecutive_failures // 0')"
  echo "  cycle $i: status=$STATUS consecutive_failures=$CF"
  if [ "$STATUS" = "connected" ] && [ "$CF" -eq 0 ]; then RECOVERED=1; break; fi
done
[ -n "$RECOVERED" ] || fail "server did not recover to connected / consecutive_failures 0"
echo "  OK: recovered to connected / 0"

# ── 4. Single-flight under 2 replicas ─────────────────────────────────────────
# Repoint back to dead, scale to 2, and assert consecutive_failures advances by ~1
# per interval (advisory-lock single-flight) — NOT 2 per interval.
echo "--- single-flight: 2 replicas, +1 per interval ---"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SID="$SERVER_ID" python3 - <<'PY' || fail "could not repoint back to dead"
import os, asyncio, uuid
from sqlalchemy import select
async def main():
    from db import AsyncSessionLocal
    from models import MCPServer
    async with AsyncSessionLocal() as s:
        srv = (await s.execute(select(MCPServer).where(
            MCPServer.id == uuid.UUID(os.environ["SID"])))).scalar_one()
        srv.server_url = "http://127.0.0.1:1/mcp"
        srv.status = "connected"
        srv.health_detail = {"consecutive_failures": 0}
        await s.commit()
asyncio.run(main())
PY
kubectl scale deployment/agentshield-registry-api -n "$NAMESPACE" --replicas=2 >/dev/null
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout=5m
# Re-resolve the pod we read from (the original may have been recycled by the scale).
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')"
CF_A="$(read_state | jq -r '.consecutive_failures // 0')"
sleep "$INTERVAL"
CF_B="$(read_state | jq -r '.consecutive_failures // 0')"
DELTA=$((CF_B - CF_A))
echo "  consecutive_failures: ${CF_A} → ${CF_B} (delta=${DELTA}) over one interval with 2 replicas"
# Single-flight → exactly one increment per interval. Allow 1 (strict) — a 2 means both
# replicas incremented (lock failed).
[ "$DELTA" -le 1 ] || fail "consecutive_failures advanced by ${DELTA} in one interval — single-flight lock is NOT holding (want <= 1)"
echo "  OK: single-flight holds (delta <= 1 per interval under 2 replicas)"

echo "PASS"

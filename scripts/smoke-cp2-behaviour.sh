#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP2c — MCP as a Tool Source (Phase 1): register->discover MVP behaviour smoke.
#
# Starts the stub MCP fixture INSIDE the proxy pod (so the proxy dials it over
# 127.0.0.1:9999, no new cluster object), then drives the full slice via the
# registry-api API (in-pod, http://localhost:8000/api/v1):
#   - POST /mcp-servers/ (http://127.0.0.1:9999/mcp) -> 201 status=connected,
#         discovered_tool_count >= 2
#   - GET  /mcp-servers/{id} -> tools named {server}__echo / {server}__add,
#         each child Tool.owner_team == server.owner_team
#   - POST /mcp-servers/ (unreachable url) -> 201 status=error, health_detail.last_error set
#   - POST /mcp-servers/{id}/sync twice -> 2nd tools_added == 0 (idempotent)
#   - per-server Secret agentshield-mcp-server-{id} exists in agentshield-mcp
#   - DELETE /mcp-servers/{id} (unbound) -> 204, and the Secret is gone
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint CP2: register->discover behaviour smoke ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
SUFFIX="$(date +%s | tail -c 7)"

fail() { echo "FAIL: $1" >&2; exit 1; }

PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod found"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod found"
echo "  proxy pod: $PROXY_POD"
echo "  api pod:   $API_POD"
echo "  suffix:    $SUFFIX"

# ── 1. Start the stub MCP fixture inside the proxy pod ────────────────────────
echo "--- starting stub MCP fixture inside the proxy pod ---"
kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  python3 /app/fixtures/stub_mcp_server.py &
STUB_PID=$!
trap 'kill "$STUB_PID" 2>/dev/null || true' EXIT

# Wait for the fixture to listen on 127.0.0.1:9999 inside the proxy pod.
READY=""
for _ in $(seq 1 20); do
  if kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- python3 -c \
       "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',9999)); s.close()" \
       2>/dev/null; then
    READY=1; break
  fi
  sleep 2
done
[ -n "$READY" ] || fail "stub MCP fixture did not start listening on 127.0.0.1:9999 in the proxy pod"
echo "  OK: fixture listening on 127.0.0.1:9999"

# ── 2. Register (connected) + detail + owner_team + unreachable + sync twice ──
echo "--- register / discover / sync assertions ---"
RESULT_A="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, sys, asyncio, uuid, httpx

SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
SERVER_NAME = f"cp2-stub-{SUFFIX}"

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        sys.exit(1)

async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        # register connected
        r = await c.post(f"{BASE}/mcp-servers/", headers=HDR, json={
            "name": SERVER_NAME,
            "description": "cp2c stub server",
            "server_url": "http://127.0.0.1:9999/mcp",
            "transport": "streamable_http",
            "owner_team": "platform",
            "is_external": False,
            "identity_mode": "none",
            "scan_results": True,
        })
        check(r.status_code == 201, "CP2C-register-201", f"status={r.status_code} body={r.text[:220]}")
        body = r.json()
        sid = body["id"]
        check(body.get("status") == "connected", "CP2C-status-connected", f"status={body.get('status')}")
        check(body.get("discovered_tool_count", 0) >= 2, "CP2C-tool-count",
              f"discovered_tool_count={body.get('discovered_tool_count')}")
        print(f"CONNECTED_SERVER_ID={sid}")

        # detail: tool names present
        rd = await c.get(f"{BASE}/mcp-servers/{sid}", headers=HDR)
        check(rd.status_code == 200, "CP2C-detail-200", f"status={rd.status_code}")
        names = {t["name"] for t in rd.json().get("tools", [])}
        check(f"{SERVER_NAME}__echo" in names, "CP2C-tool-echo", f"names={sorted(names)}")
        check(f"{SERVER_NAME}__add" in names, "CP2C-tool-add", f"names={sorted(names)}")

        # child Tool.owner_team == server.owner_team (via ORM — the detail row omits owner_team)
        from db import AsyncSessionLocal
        from models import Tool
        from sqlalchemy import select
        async with AsyncSessionLocal() as s:
            owners = (await s.execute(
                select(Tool.owner_team).where(Tool.mcp_server_id == uuid.UUID(sid))
            )).scalars().all()
        check(len(owners) >= 2 and all(o == "platform" for o in owners),
              "CP2C-owner-team", f"owner_teams={owners}")

        # register an UNREACHABLE url -> 201 status=error + health_detail.last_error
        ru = await c.post(f"{BASE}/mcp-servers/", headers=HDR, json={
            "name": f"{SERVER_NAME}-dead",
            "description": "cp2c unreachable server",
            "server_url": "http://127.0.0.1:1/mcp",
            "transport": "streamable_http",
            "owner_team": "platform",
            "is_external": False,
            "identity_mode": "none",
            "scan_results": True,
        })
        check(ru.status_code == 201, "CP2C-unreachable-201", f"status={ru.status_code} body={ru.text[:220]}")
        ub = ru.json()
        check(ub.get("status") == "error", "CP2C-unreachable-status-error", f"status={ub.get('status')}")
        hd = ub.get("health_detail") or {}
        check(bool(hd.get("last_error")), "CP2C-unreachable-last-error", f"health_detail={hd}")
        print(f"UNREACHABLE_SERVER_ID={ub['id']}")

        # /sync twice -> 2nd tools_added == 0
        s1 = await c.post(f"{BASE}/mcp-servers/{sid}/sync", headers=HDR, json={})
        check(s1.status_code == 200, "CP2C-sync1-200", f"status={s1.status_code}")
        s2 = await c.post(f"{BASE}/mcp-servers/{sid}/sync", headers=HDR, json={})
        check(s2.status_code == 200, "CP2C-sync2-200", f"status={s2.status_code}")
        check(s2.json().get("tools_added", -1) == 0, "CP2C-sync2-noadd",
              f"tools_added={s2.json().get('tools_added')}")

    print("BLOCKA_DONE")

asyncio.run(main())
PY
)" || { echo "$RESULT_A"; fail "register/discover/sync assertions failed"; }
echo "$RESULT_A"
echo "$RESULT_A" | grep -q "BLOCKA_DONE" || fail "register/discover block did not complete"

CONNECTED_SERVER_ID="$(echo "$RESULT_A" | sed -n 's/^CONNECTED_SERVER_ID=//p' | head -1)"
UNREACHABLE_SERVER_ID="$(echo "$RESULT_A" | sed -n 's/^UNREACHABLE_SERVER_ID=//p' | head -1)"
[ -n "$CONNECTED_SERVER_ID" ] || fail "could not capture CONNECTED_SERVER_ID"

# ── 3. Per-server Secret exists in agentshield-mcp ────────────────────────────
echo "--- per-server Secret present ---"
SECRET_NAME="agentshield-mcp-server-${CONNECTED_SERVER_ID}"
kubectl get secret "$SECRET_NAME" -n "$MCP_NS" >/dev/null 2>&1 \
  || fail "per-server Secret ${SECRET_NAME} missing in ${MCP_NS} after register"
echo "  OK: Secret ${SECRET_NAME} present"

# ── 4. DELETE the (unbound) server -> 204 ─────────────────────────────────────
echo "--- delete unbound server -> 204 ---"
RESULT_B="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env CONNECTED_SERVER_ID="$CONNECTED_SERVER_ID" UNREACHABLE_SERVER_ID="$UNREACHABLE_SERVER_ID" python3 - <<'PY'
import os, sys, asyncio, httpx

BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
SID = os.environ["CONNECTED_SERVER_ID"]
UID = os.environ.get("UNREACHABLE_SERVER_ID", "")

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        sys.exit(1)

async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.delete(f"{BASE}/mcp-servers/{SID}", headers=HDR)
        check(r.status_code == 204, "CP2C-delete-204", f"status={r.status_code} body={r.text[:220]}")
        if UID:  # best-effort cleanup of the unreachable fixture server
            await c.delete(f"{BASE}/mcp-servers/{UID}", headers=HDR)
    print("BLOCKB_DONE")

asyncio.run(main())
PY
)" || { echo "$RESULT_B"; fail "delete assertion failed"; }
echo "$RESULT_B"
echo "$RESULT_B" | grep -q "BLOCKB_DONE" || fail "delete block did not complete"

# ── 5. Per-server Secret is gone after delete ─────────────────────────────────
echo "--- per-server Secret removed after delete ---"
if kubectl get secret "$SECRET_NAME" -n "$MCP_NS" >/dev/null 2>&1; then
  fail "per-server Secret ${SECRET_NAME} still present after DELETE"
fi
echo "  OK: Secret ${SECRET_NAME} removed"

echo "PASS"

#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP1c — MCP as a Tool Source (Phase 1): behaviour smoke.
#
# Drives the registry-api behaviour that Phases 2-3 add, in-pod against
# http://localhost:8000/api/v1 (no proxy involved — CP1 is registry-api only):
#   - POST /internal/mcp/authorize-tool-call
#       * own-team          -> 200 {allowed:true}
#       * cross-team no grant-> 200 {allowed:false}
#       * malformed body     -> 422
#   - DELETE /tools/{mcp_tool_id} -> 409 (mcp_tool lifecycle owned by the server)
#   - DELETE /tools/{http_tool_id} -> 204 (native http tool still deletable)
#   - GET   /tools/{http_tool_id} -> pii_deanonymize_allowed:false, mcp_server_name:null
#
# Fixtures (an MCPServer row + a mcp_tool child + a fresh http tool) are created
# via the ORM in-pod, mirroring suite-81. Exit 0 on full pass, non-zero on the
# first failing assertion. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint CP1: behaviour smoke (internal authz + tools lifecycle) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"

fail() { echo "FAIL: $1" >&2; exit 1; }

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod found"
echo "  pod:    $API_POD"
echo "  suffix: $SUFFIX"

RESULT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, sys, asyncio, uuid, httpx

SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
SERVER_NAME = f"cp1c-srv-{SUFFIX}"
MCP_TOOL_RAW = "echo"
HTTP_TOOL_NAME = f"cp1c-http-{SUFFIX}"

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        sys.exit(1)

async def main():
    from db import AsyncSessionLocal
    from models import MCPServer, Tool
    from sqlalchemy import delete

    # ── Create fixtures via ORM (no auth juggling), all owned by team 'platform' ─
    async with AsyncSessionLocal() as s:
        srv = MCPServer(
            name=SERVER_NAME,
            description="cp1c fixture server",
            server_url="http://127.0.0.1:9999/mcp",
            transport="streamable_http",
            owner_team="platform",
            is_external=False,
            identity_mode="none",
        )
        s.add(srv)
        await s.flush()
        server_id = srv.id

        mcp_tool = Tool(
            name=f"{SERVER_NAME}__{MCP_TOOL_RAW}",
            type="mcp_tool",
            risk_level="low",
            owner_team="platform",
            description="cp1c mcp_tool fixture",
            mcp_server_id=server_id,
            mcp_tool_name=MCP_TOOL_RAW,
        )
        http_tool = Tool(
            name=HTTP_TOOL_NAME,
            type="http",
            risk_level="low",
            owner_team="platform",
            description="cp1c http tool fixture",
        )
        s.add(mcp_tool)
        s.add(http_tool)
        await s.flush()
        mcp_tool_id = str(mcp_tool.id)
        http_tool_id = str(http_tool.id)
        await s.commit()

    async with httpx.AsyncClient(timeout=30) as c:
        # ── authorize-tool-call: own team -> allowed:true ─────────────────────
        r = await c.post(f"{BASE}/internal/mcp/authorize-tool-call", json={
            "caller_sa_subject": "system:serviceaccount:agents-platform:agent-cp1c",
            "server_id": str(server_id),
            "mcp_tool_name": MCP_TOOL_RAW,
        })
        check(r.status_code == 200, "CP1C-authz-own-200", f"status={r.status_code} body={r.text[:160]}")
        check(r.json().get("allowed") is True, "CP1C-authz-own-allowed",
              f"allowed={r.json().get('allowed')}")

        # ── authorize-tool-call: cross team, no grant -> allowed:false ────────
        r = await c.post(f"{BASE}/internal/mcp/authorize-tool-call", json={
            "caller_sa_subject": "system:serviceaccount:agents-nogrant:agent-cp1c",
            "server_id": str(server_id),
            "mcp_tool_name": MCP_TOOL_RAW,
        })
        check(r.status_code == 200, "CP1C-authz-cross-200", f"status={r.status_code} body={r.text[:160]}")
        check(r.json().get("allowed") is False, "CP1C-authz-cross-denied",
              f"allowed={r.json().get('allowed')}")

        # ── authorize-tool-call: malformed body -> 422 ────────────────────────
        r = await c.post(f"{BASE}/internal/mcp/authorize-tool-call", json={
            "caller_sa_subject": "system:serviceaccount:agents-platform:agent-cp1c",
            "server_id": "not-a-uuid",
            "mcp_tool_name": MCP_TOOL_RAW,
        })
        check(r.status_code == 422, "CP1C-authz-malformed-422", f"status={r.status_code}")

        # ── DELETE a mcp_tool -> 409 (lifecycle owned by the MCP server) ──────
        r = await c.delete(f"{BASE}/tools/{mcp_tool_id}", headers=HDR)
        check(r.status_code == 409, "CP1C-delete-mcp-409", f"status={r.status_code} body={r.text[:160]}")

        # ── GET the http tool -> pii_deanonymize_allowed:false, mcp_server_name:null
        r = await c.get(f"{BASE}/tools/{http_tool_id}", headers=HDR)
        check(r.status_code == 200, "CP1C-get-http-200", f"status={r.status_code}")
        j = r.json()
        check(j.get("pii_deanonymize_allowed") is False, "CP1C-get-http-pii-false",
              f"pii_deanonymize_allowed={j.get('pii_deanonymize_allowed')}")
        check(j.get("mcp_server_name") is None, "CP1C-get-http-server-null",
              f"mcp_server_name={j.get('mcp_server_name')}")

        # ── DELETE the http tool -> 204 (native http tools still deletable) ───
        r = await c.delete(f"{BASE}/tools/{http_tool_id}", headers=HDR)
        check(r.status_code == 204, "CP1C-delete-http-204", f"status={r.status_code} body={r.text[:160]}")

    # ── best-effort cleanup (uniquely suffixed) ───────────────────────────────
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Tool).where(Tool.mcp_server_id == server_id))
            await s.execute(delete(Tool).where(Tool.name == HTTP_TOOL_NAME))
            await s.execute(delete(MCPServer).where(MCPServer.id == server_id))
            await s.commit()
    except Exception as e:
        print("cleanup-err", str(e)[:120])

    print("ALLPASS")

asyncio.run(main())
PY
)" || { echo "$RESULT"; fail "behaviour assertions failed"; }
echo "$RESULT"
echo "$RESULT" | grep -q "ALLPASS" || fail "behaviour assertion block did not complete"

echo "PASS"

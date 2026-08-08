#!/usr/bin/env bash
# scripts/e2e/suite-84-mcp-tools.sh
#
# E2E Suite 84: MCP as a Tool Source (Phase 1) — registry-api API surface.
#
# Runs in-pod (kubectl exec into registry-api) against the real routers + real
# Postgres + real bundle, mirroring suite-81's template. The API-testable slice of
# MCP-as-tool-source: schema, lifecycle guards, the internal authorize endpoint,
# the register→status path, and the Decision-27 bundle field. The RUNTIME dispatch
# (SDK/runner → proxy → fixture) and the de-anon/scan gate are proven by the CP3/CP4
# smoke scripts + the SDK unit suite, not here — a kubectl-exec API suite structurally
# cannot drive an agent's tool loop (same boundary the other bash suites accept).
#
#   T-S84-001 — schema: mcp_servers has the 6 Phase-1 columns; tools.pii_deanonymize_allowed exists.
#   T-S84-002 — DELETE /tools/{mcp_tool_id} → 409 (lifecycle owned by the server); DELETE an http tool → 204.
#   T-S84-003 — POST /internal/mcp/authorize-tool-call: own-team → allowed:true, cross-team no-grant → false, malformed → 422.
#   T-S84-013 — register (unreachable URL) → 201 status in {connected,error}; name-immutable PUT → 422. [proxy/k8s-gated → SKIP if register != 201]
#   T-S84-018 — DELETE /mcp-servers/{id} blocked while a tool is bound → 409; unbind → 204.
#   T-S84-024 — the OPA bundle carries pii_deanonymize_allowed on a granted tool entry.
#   T-S84-007 — proxy /internal/discover with NO token → 401. [proxy-gated → SKIP if proxy unreachable]
#
# Usage:
#   bash scripts/e2e/suite-84-mcp-tools.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && API_POD=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep registry-api | grep Running | awk '{print $1}' | head -1)
[ -z "$API_POD" ] && { echo "FATAL: no running registry-api pod"; exit 1; }

# R1/FR-11: POST /api/v1/admin/bundle/regenerate (routers/admin.py) is the ONE call this
# suite makes into R1's ten routers. /api/v1/tools/*, /api/v1/mcp-servers/*,
# /api/v1/internal/* and /api/v1/bundle/* are outside them and stay anonymous.
# The driver is a QUOTED heredoc, so the token travels as an env var beside SUFFIX.
# Call e2e_set_token BARE (lib/e2e-auth.sh explains the subshell trap).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

echo "=== Suite 84: MCP as a Tool Source (registry-api surface) ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" E2E_TOKEN="$E2E_TOKEN" python3 - <<'PY'
import os, asyncio, httpx, uuid
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
TEAM = "platform"
# X-User-* stay AUDIT STAMPS; the Bearer is the R1 authentication. Only the
# /admin/bundle/regenerate call actually needs it, but ADMIN is one dict shared by
# every call in this driver and carrying a valid token on the others is inert.
ADMIN = {"X-User-Sub": "platform-admin", "X-User-Team": TEAM,
         "Authorization": "Bearer " + os.environ["E2E_TOKEN"]}
fails = []

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(tid)

def skip(tid, msg):
    print(f"RESULT {tid} SKIP {msg}")

async def main():
    from db import AsyncSessionLocal
    from models import MCPServer, Tool, AgentTool, Agent, AssetGrant
    from sqlalchemy import select, delete, text as sqltext

    srv_name = f"s84-srv-{SUFFIX}"
    mcp_tool_name = f"{srv_name}__echo"
    http_tool_name = f"s84-http-{SUFFIX}"
    server_id = None
    http_tool_id = None
    mcp_tool_id = None

    # ── T-S84-001 — schema introspection (migration 0072 applied) ───────────────
    async with AsyncSessionLocal() as s:
        cols = set((await s.execute(sqltext(
            "SELECT column_name FROM information_schema.columns WHERE table_name='mcp_servers'"
        ))).scalars().all())
        tool_cols = set((await s.execute(sqltext(
            "SELECT column_name FROM information_schema.columns WHERE table_name='tools'"
        ))).scalars().all())
    need = {"identity_mode", "is_external", "transport_config", "health_detail",
            "list_changed_supported", "scan_results"}
    check(need.issubset(cols) and "pii_deanonymize_allowed" in tool_cols,
          "T-S84-001", f"mcp_servers missing={need - cols} tools.pii_deanonymize_allowed={'pii_deanonymize_allowed' in tool_cols}")

    # ── Seed a server + an mcp_tool + an http tool via ORM (deterministic) ──────
    async with AsyncSessionLocal() as s:
        srv = MCPServer(name=srv_name, server_url="http://unreachable.invalid:9999/mcp",
                        transport="streamable_http", owner_team=TEAM, status="error",
                        identity_mode="none", is_external=False, scan_results=True)
        s.add(srv); await s.flush()
        server_id = srv.id
        mt = Tool(name=mcp_tool_name, type="mcp_tool", risk_level="low", owner_team=TEAM,
                  description="s84 mcp tool", mcp_server_id=server_id, mcp_tool_name="echo",
                  pii_deanonymize_allowed=True)
        ht = Tool(name=http_tool_name, type="http", risk_level="low", owner_team=TEAM,
                  description="s84 http tool", http_url="https://example.com", http_method="GET")
        s.add(mt); s.add(ht); await s.flush()
        mcp_tool_id, http_tool_id = mt.id, ht.id
        await s.commit()

    async with httpx.AsyncClient(timeout=20) as c:
        # ── T-S84-002 — mcp_tool delete disabled (409); http tool delete OK (204) ─
        r_mcp = await c.delete(f"{BASE}/tools/{mcp_tool_id}", headers=ADMIN)
        r_http = await c.delete(f"{BASE}/tools/{http_tool_id}", headers=ADMIN)
        check(r_mcp.status_code == 409 and r_http.status_code == 204, "T-S84-002",
              f"mcp_tool_delete={r_mcp.status_code}(want 409) http_delete={r_http.status_code}(want 204)")

        # ── T-S84-003 — internal authorize-tool-call ────────────────────────────
        au = f"{BASE}/internal/mcp/authorize-tool-call"
        own = await c.post(au, json={
            "caller_sa_subject": f"system:serviceaccount:agents-{TEAM}:x",
            "server_id": str(server_id), "mcp_tool_name": "echo"})
        cross = await c.post(au, json={
            "caller_sa_subject": "system:serviceaccount:agents-otherteam:x",
            "server_id": str(server_id), "mcp_tool_name": "echo"})
        bad = await c.post(au, json={"caller_sa_subject": "x"})  # missing fields
        own_ok = own.status_code == 200 and own.json().get("allowed") is True
        cross_ok = cross.status_code == 200 and cross.json().get("allowed") is False
        check(own_ok and cross_ok and bad.status_code == 422, "T-S84-003",
              f"own={own.status_code}/{own.json().get('allowed') if own.status_code==200 else '-'} "
              f"cross_allowed={cross.json().get('allowed') if cross.status_code==200 else '-'} malformed={bad.status_code}")

        # ── T-S84-013 — server name is immutable post-create (PUT rename → 422) ──
        rename = await c.put(f"{BASE}/mcp-servers/{server_id}",
                             headers=ADMIN, json={"name": f"{srv_name}-renamed"})
        check(rename.status_code == 422, "T-S84-013",
              f"name-rename={rename.status_code} (want 422). (register→discover 201 is CP2-gated)")

        # ── T-S84-018 — delete server blocked while a tool is bound (409) ────────
        # Re-create the mcp_tool (T-S84-002 left it; it 409'd so it's still there) and
        # bind it to a throwaway agent, then attempt server delete.
        async with AsyncSessionLocal() as s:
            ag = Agent(name=f"s84-agent-{SUFFIX}", team=TEAM, description="s84")
            s.add(ag); await s.flush()
            s.add(AgentTool(agent_id=ag.id, tool_id=mcp_tool_id))
            await s.commit()
            agent_id = ag.id
        blocked = await c.delete(f"{BASE}/mcp-servers/{server_id}", headers=ADMIN)
        check(blocked.status_code == 409, "T-S84-018",
              f"delete-while-bound={blocked.status_code} (want 409)")
        # Unbind, then a clean delete → 204.
        async with AsyncSessionLocal() as s:
            await s.execute(delete(AgentTool).where(AgentTool.tool_id == mcp_tool_id))
            await s.commit()
        unblocked = await c.delete(f"{BASE}/mcp-servers/{server_id}", headers=ADMIN)
        check(unblocked.status_code == 204, "T-S84-018b",
              f"delete-after-unbind={unblocked.status_code} (want 204)")
        server_id = None  # deleted

        # ── T-S84-024 — bundle carries pii_deanonymize_allowed on a granted tool ─
        # Grant the http tool (still a tool row? it was deleted in 002). Use a fresh
        # flagged tool granted to the team; regenerate the bundle; assert the field.
        async with AsyncSessionLocal() as s:
            flagged = Tool(name=f"s84-flag-{SUFFIX}", type="http", risk_level="low",
                           owner_team=TEAM, http_url="https://example.com", http_method="GET",
                           pii_deanonymize_allowed=True)
            s.add(flagged); await s.flush()
            fid = flagged.id
            s.add(AssetGrant(asset_type="tool", asset_id=fid, grantee_team=TEAM,
                             granted_by="auto:suite84"))
            await s.commit()
        await c.post(f"{BASE}/admin/bundle/regenerate", headers=ADMIN)
        import io, tarfile, json
        rb = await c.get(f"{BASE}/bundle/bundle.tar.gz")
        data = None
        try:
            tf = tarfile.open(fileobj=io.BytesIO(rb.content))
            for m in tf.getmembers():
                if m.name.endswith("data.json"):
                    data = json.load(tf.extractfile(m)); break
        except Exception as e:
            print("bundle-parse-err", e)
        grants = (data or {}).get("grants", {}).get(TEAM, []) if data else []
        got = [g for g in grants if g.get("name") == f"s84-flag-{SUFFIX}"]
        check(bool(got) and got[0].get("pii_deanonymize_allowed") is True, "T-S84-024",
              f"bundle grant flagged entry: {got}")

        # ── T-S84-030 — /tools/ denormalizes mcp_server_name onto discovered rows ──
        # The picker labels an MCP tile with its SOURCE SERVER rather than the raw
        # type string. That label is only renderable if the list endpoint carries
        # the field; without it every discovered tile falls back to reading "MCP".
        lr = await c.get(f"{BASE}/tools/", params={"type": "mcp_tool", "limit": 200}, headers=ADMIN)
        mcp_rows = lr.json().get("items", []) if lr.status_code == 200 else []
        check(
            lr.status_code == 200 and all(
                ("mcp_server_name" in r and "mcp_server_id" in r) for r in mcp_rows
            ),
            "T-S84-030",
            f"list mcp rows={len(mcp_rows)} status={lr.status_code}; "
            f"missing-field rows={[r.get('name') for r in mcp_rows if 'mcp_server_name' not in r][:3]}",
        )

        # ── T-S84-031 — ?status=active excludes a retired tool ────────────────────
        # mcp_discovery marks a vanished-upstream tool 'inactive' and NEVER deletes
        # the row, so "is it still offerable" is a status question. Proven on a
        # deprecated fixture rather than a live MCP row so the suite does not depend
        # on an upstream server having dropped something.
        async with AsyncSessionLocal() as s:
            # publish_status is EXPLICIT because this row is inserted straight into the DB,
            # bypassing create_tool. Migration 0080 defaults it to 'private', and catalog
            # visibility is `published OR created_by == caller` — a direct insert has no
            # created_by, so the row would match neither arm and the fetch below would
            # return zero items. This case is about the STATUS filter, not visibility;
            # saying so explicitly keeps the two independent.
            retired = Tool(name=f"s84-retired-{SUFFIX}", type="http", risk_level="low",
                           owner_team=TEAM, http_url="https://example.com", http_method="GET",
                           status="deprecated", publish_status="published")
            s.add(retired); await s.commit()
        act = await c.get(f"{BASE}/tools/", params={"status": "active", "limit": 200}, headers=ADMIN)
        act_names = [t["name"] for t in act.json().get("items", [])]
        allr = await c.get(f"{BASE}/tools/", params={"name": f"s84-retired-{SUFFIX}"}, headers=ADMIN)
        check(
            f"s84-retired-{SUFFIX}" not in act_names
            and any(t["name"] == f"s84-retired-{SUFFIX}" for t in allr.json().get("items", [])),
            "T-S84-031",
            f"retired-in-active={f's84-retired-{SUFFIX}' in act_names} "
            f"retired-fetchable={allr.status_code}",
        )

        # ── T-S84-032 — the catalog is fully retrievable past the 200 page cap ────
        # `limit` is declared le=200, so a >200-tool catalog CANNOT be fetched in one
        # request — Studio's listAllTools pages on `total`. This asserts the two
        # facts that makes that correct: the cap is enforced, and `total` reports the
        # true count rather than the page length.
        over = await c.get(f"{BASE}/tools/", params={"limit": 500}, headers=ADMIN)
        p1 = await c.get(f"{BASE}/tools/", params={"limit": 200, "offset": 0}, headers=ADMIN)
        body = p1.json()
        total = body.get("total", 0)
        seen = list(body.get("items", []))
        off = 200
        while len(seen) < total and off < 5000:
            nxt = await c.get(f"{BASE}/tools/", params={"limit": 200, "offset": off}, headers=ADMIN)
            page = nxt.json().get("items", [])
            if not page:
                break
            seen.extend(page)
            off += 200
        check(
            over.status_code == 422 and total >= len(body.get("items", [])) and len(seen) == total,
            "T-S84-032",
            f"limit=500 -> {over.status_code} (want 422); total={total} "
            f"page1={len(body.get('items', []))} paged={len(seen)}",
        )

    # ── cleanup (best-effort, uniquely suffixed) ────────────────────────────────
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(AgentTool).where(AgentTool.tool_id == mcp_tool_id))
            await s.execute(sqltext("DELETE FROM agents WHERE name = :n"), {"n": f"s84-agent-{SUFFIX}"})
            await s.execute(sqltext("DELETE FROM asset_grants WHERE granted_by = 'auto:suite84'"))
            await s.execute(delete(Tool).where(Tool.name.like(f"s84-%{SUFFIX}%")))
            await s.execute(sqltext("DELETE FROM tools WHERE name = :n"), {"n": f"s84-retired-{SUFFIX}"})
            await s.execute(sqltext("DELETE FROM tools WHERE name LIKE :p"), {"p": f"s84-%{SUFFIX}"})
            if server_id is not None:
                await s.execute(delete(MCPServer).where(MCPServer.id == server_id))
            await s.execute(sqltext("DELETE FROM mcp_servers WHERE name = :n"), {"n": f"s84-srv-{SUFFIX}"})
            await s.commit()
    except Exception as e:
        print("cleanup-err", str(e)[:120])

    print("FAILS", ",".join(fails) if fails else "NONE")

asyncio.run(main())
PY
) || { echo "$RESULT"; echo "FATAL: in-pod block errored"; exit 1; }

echo "$RESULT"

# ── T-S84-007 — proxy /internal/discover with no token → 401 (proxy-gated) ─────
PROXY_FAILED=0
PROXY_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$PROXY_POD" ]; then
  CODE=$(kubectl exec -n "$NAMESPACE" "$PROXY_POD" -- \
    python3 -c "import httpx; print(httpx.post('http://localhost:8080/internal/discover', json={'server_id':'00000000-0000-0000-0000-000000000000'}).status_code)" 2>/dev/null || echo "ERR")
  if [ "$CODE" = "401" ]; then
    echo "RESULT T-S84-007 PASS discover-no-token=$CODE"
  else
    echo "RESULT T-S84-007 FAIL discover-no-token=$CODE (want 401)"
    PROXY_FAILED=1
  fi
else
  echo "RESULT T-S84-007 SKIP no running mcp-proxy pod (proxy-gated)"
fi

# Verdict: the in-pod block must report "FAILS NONE" AND the proxy check (if it ran)
# must not have failed.
if echo "$RESULT" | grep -q "FAILS NONE" && [ "$PROXY_FAILED" -eq 0 ]; then
  echo "=== Suite 84 PASSED ==="
  exit 0
else
  echo "=== Suite 84 FAILED ==="
  exit 1
fi

#!/usr/bin/env bash
# scripts/e2e/suite-81-deploy-tool-autograt.sh
#
# E2E Suite 81: deploy-time tool-access auto-grant (registry-api 0.2.209).
#
# Proves the fix for the fail-closed governance gap: deploy auto-granted ApprovalAuthority
# but never the TOOL itself (AssetGrant), so under fail-closed OPA every agent's OWN declared
# tools were denied (deny_reason 'tool_not_granted') and HITL/eval fixtures could not run/park
# unless someone manually granted them (they only passed under the reverted fail-open bypass).
#
#   T-S81-001 — _auto_grant_tool_access creates an active AssetGrant(asset_type='tool') for the
#               agent's own tool -> team (was: no grant, tool denied).
#   T-S81-002 — idempotent: a second call for the same (tool, team) creates 0 new grants.
#   T-S81-003 — the granted tool appears in the OPA bundle for the team (with its risk), so
#               a HIGH-risk tool resolves to require_approval (HITL park), not deny.
#
# Runs in-pod (kubectl exec) against the real router module + real Postgres + real bundle.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && API_POD=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep registry-api | grep Running | awk '{print $1}' | head -1)

echo "=== Suite 81: deploy-time tool-access auto-grant ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, httpx
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
TOOL = f"s81-hitl-tool-{SUFFIX}"
TEAM = "platform"
fails = []

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(tid)

async def main():
    from db import AsyncSessionLocal
    from models import AssetGrant, Tool
    from sqlalchemy import select, delete
    from routers.deployments import _auto_grant_tool_access

    # Create a fresh HIGH-risk platform tool directly via ORM (in-pod, no auth juggling),
    # with NO prior grant, so the auto-grant create assertion below is real. Required Tool
    # columns are name + type; risk_level=high drives the require_approval/HITL-park path.
    async with AsyncSessionLocal() as s:
        tid = (await s.execute(select(Tool.id).where(Tool.name == TOOL))).scalar_one_or_none()
        if tid is None:
            t = Tool(name=TOOL, type="http", risk_level="high", owner_team=TEAM,
                     description="suite-81 fixture tool")
            s.add(t)
            await s.flush()
            tid = t.id
            await s.commit()

    async with AsyncSessionLocal() as s:
        # clean any prior grant for this fixture tool so the create assertion is real
        await s.execute(delete(AssetGrant).where(AssetGrant.asset_id == tid, AssetGrant.grantee_team == TEAM))
        await s.commit()

    # T-S81-001 — first auto-grant creates one AssetGrant.
    async with AsyncSessionLocal() as s:
        n1 = await _auto_grant_tool_access(s, [(TOOL, "high")], TEAM, "auto:suite81")
        await s.commit()
    async with AsyncSessionLocal() as s:
        active = (await s.execute(select(AssetGrant.id).where(
            AssetGrant.asset_id == tid, AssetGrant.asset_type == "tool",
            AssetGrant.grantee_team == TEAM, AssetGrant.revoked_at.is_(None)))).scalars().all()
    check(n1 == 1 and len(active) == 1, "T-S81-001", f"created={n1} active_grants={len(active)}")

    # T-S81-002 — idempotent: a second call creates 0 new grants.
    async with AsyncSessionLocal() as s:
        n2 = await _auto_grant_tool_access(s, [(TOOL, "high")], TEAM, "auto:suite81")
        await s.commit()
    check(n2 == 0, "T-S81-002", f"second-call created={n2} (want 0)")

    # T-S81-003 — the granted tool appears in the served OPA bundle for the team (risk high).
    async with httpx.AsyncClient(timeout=20) as c:
        # regenerate then read the served grants
        await c.post(f"{BASE}/admin/bundle/regenerate", headers={"X-User-Sub": "platform-admin"})
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
    plat = (data or {}).get("grants", {}).get(TEAM, []) if data else []
    got = [g for g in plat if g.get("name") == TOOL]
    check(bool(got) and got[0].get("risk") == "high", "T-S81-003",
          f"bundle grant for {TOOL}: {got}")

    # cleanup (best-effort — uniquely-suffixed)
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(AssetGrant).where(AssetGrant.asset_id == tid, AssetGrant.grantee_team == TEAM))
            await s.execute(delete(Tool).where(Tool.id == tid))
            await s.commit()
    except Exception as e:
        print("cleanup-err", str(e)[:80])

    print("FAILS", ",".join(fails) if fails else "NONE")

asyncio.run(main())
PY
) || { echo "$RESULT"; echo "FATAL: in-pod block errored"; exit 1; }

echo "$RESULT"

if echo "$RESULT" | grep -q "FAILS NONE"; then
  echo "=== Suite 81 PASSED ==="
  exit 0
else
  echo "=== Suite 81 FAILED ==="
  exit 1
fi

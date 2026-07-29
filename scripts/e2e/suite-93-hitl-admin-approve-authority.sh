#!/usr/bin/env bash
# suite-93-hitl-admin-approve-authority.sh
#
# T-S93 — Production HITL decide honors the platform-admin role (special case), and the
# per-tool authority check no longer 500s on multiple grants.
#
# REGRESSION GUARD for the prod-HITL 403 the Claude-in-Chrome journey caught (leg 12b):
# a platform-admin reviewer clicked Approve on a production approval and got
# 403 not_authorized_to_decide, because decide_approval required a per-tool
# ApprovalAuthority grant and never honored the caller's role — AND _ADMIN_ROLES was
# spelled "platform_admin" (underscore) while the real role is "platform-admin" (hyphen).
# See docs/bugs/production-hitl-decide-403-authority.md.
#
#   T-S93-001  platform-admin role (NO per-tool grant) can decide a production approval (was 403)
#   T-S93-002  a non-admin caller with no grant is still 403 (authority still enforced)
#   T-S93-003  a caller with 2 active grants for one tool decides without 500 (MultipleResultsFound fix)
#   T-S93-004  GATEWAY PATH: caller identified via X-User-Id (Envoy JWT header) + the frontend's
#              hardcoded reviewer_id="studio-user" body → platform-admin still decides (was 403,
#              because the endpoint read only X-User-Sub and Envoy injects X-User-Id).
set -euo pipefail

NS="${NS:-agentshield-platform}"
POD="$(kubectl -n "$NS" get pod -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -z "$POD" ] && POD="$(kubectl -n "$NS" get pod 2>/dev/null | grep -i registry-api | grep Running | head -1 | awk '{print $1}')"
[ -z "$POD" ] && { echo "no running registry-api pod in $NS"; exit 1; }
echo "registry-api pod: $POD"

kubectl -n "$NS" exec -i "$POD" -c registry-api -- python3 - <<'PY'
import asyncio, uuid, httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from db import AsyncSessionLocal

BASE = "http://localhost:8000/api/v1"
ADMIN = "suite93-admin-" + uuid.uuid4().hex[:8]
USER  = "suite93-user-"  + uuid.uuid4().hex[:8]
GRANTEE = "suite93-2grant-" + uuid.uuid4().hex[:8]
TOOL  = "suite93-tool-" + uuid.uuid4().hex[:6]
made = {"approvals": [], "assignments": [ADMIN, USER, GRANTEE], "grants": []}

async def seed_assignment(db, sub, role):
    await db.execute(text("""
        INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at)
        VALUES (:s, 'platform', :r, 'suite93', now())
    """), {"s": sub, "r": role})

async def seed_approval(db, agent_id, tool):
    aid = uuid.uuid4()
    made["approvals"].append(str(aid))
    await db.execute(text("""
        INSERT INTO approvals
          (id, agent_id, agent_name, team, thread_id, tool_name, tool_args, risk_level,
           status, expires_at, created_at, version, context, notify_slack)
        VALUES
          (:id, :agent_id, 'suite93-agent', 'platform', :thread, :tool, '{}'::jsonb, 'high',
           'pending', :exp, now(), 1, 'production', false)
    """), {"id": aid, "agent_id": agent_id, "thread": str(uuid.uuid4()), "tool": tool,
           "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1)})
    return str(aid)

async def seed_grant(db, sub, tool, tag):
    await db.execute(text("""
        INSERT INTO approval_authority (id, resource_type, resource_id, approver_user_id, granted_by, granted_at)
        VALUES (gen_random_uuid(), 'tool', :tool, :sub, :by, now())
    """), {"tool": tool, "sub": sub, "by": tag})

def decide(aid, sub):
    return httpx.patch(f"{BASE}/approvals/{aid}",
        headers={"X-User-Sub": sub, "X-User-Team": "platform", "Content-Type": "application/json"},
        json={"decision": "approved", "reviewer_id": sub, "version": 1}, timeout=20)

def decide_gateway(aid, sub):
    # Simulate the browser path exactly: Envoy injects the JWT sub as X-User-Id (NOT
    # X-User-Sub), and the Studio frontend hardcodes reviewer_id="studio-user".
    return httpx.patch(f"{BASE}/approvals/{aid}",
        headers={"X-User-Id": sub, "Content-Type": "application/json"},
        json={"decision": "approved", "reviewer_id": "studio-user", "version": 1}, timeout=20)

async def main():
    failures = []
    async with AsyncSessionLocal() as db:
        # borrow a real agent id for the NOT NULL FK-ish column
        agent_id = (await db.execute(text("SELECT id FROM agents LIMIT 1"))).scalar()
        await seed_assignment(db, ADMIN, "platform-admin")
        await seed_assignment(db, USER, "operator")
        await seed_assignment(db, GRANTEE, "operator")
        a_admin = await seed_approval(db, agent_id, TOOL)
        a_user  = await seed_approval(db, agent_id, TOOL)
        a_grant = await seed_approval(db, agent_id, TOOL)
        a_gw    = await seed_approval(db, agent_id, TOOL)
        # GRANTEE holds TWO active grants for the same tool (the scalar_one_or_none trap)
        await seed_grant(db, GRANTEE, TOOL, "suite93-a")
        await seed_grant(db, GRANTEE, TOOL, "suite93-b")
        await db.commit()

    try:
        # T-S93-001 — platform-admin, no per-tool grant → NOT 403.
        r = decide(a_admin, ADMIN)
        if r.status_code == 403:
            failures.append(f"T-S93-001 FAIL: platform-admin got 403 {r.text[:120]} — the bug is back")
        else:
            print(f"T-S93-001 PASS: platform-admin decided (status={r.status_code}, not 403)")

        # T-S93-002 — non-admin, no grant → 403 not_authorized_to_decide.
        r = decide(a_user, USER)
        if r.status_code == 403:
            print("T-S93-002 PASS: non-admin with no grant is 403 (authority still enforced)")
        else:
            failures.append(f"T-S93-002 FAIL: non-admin should be 403, got {r.status_code} {r.text[:120]}")

        # T-S93-003 — 2 grants for one tool → no 500 (MultipleResultsFound fix).
        r = decide(a_grant, GRANTEE)
        if r.status_code == 500:
            failures.append(f"T-S93-003 FAIL: 2 grants -> 500 (scalar_one_or_none MultipleResultsFound): {r.text[:120]}")
        elif r.status_code == 403:
            failures.append(f"T-S93-003 FAIL: grantee with a valid grant got 403 {r.text[:120]}")
        else:
            print(f"T-S93-003 PASS: caller with 2 grants decided without 500 (status={r.status_code})")

        # T-S93-004 — GATEWAY PATH: X-User-Id (not X-User-Sub) + reviewer_id="studio-user".
        r = decide_gateway(a_gw, ADMIN)
        if r.status_code == 403:
            failures.append(f"T-S93-004 FAIL: gateway platform-admin (X-User-Id) got 403 {r.text[:120]} — the browser bug is back")
        else:
            print(f"T-S93-004 PASS: gateway platform-admin decided via X-User-Id (status={r.status_code}, not 403)")
    finally:
        async with AsyncSessionLocal() as db:
            for aid in made["approvals"]:
                await db.execute(text("DELETE FROM approvals WHERE id = :id"), {"id": aid})
            await db.execute(text("DELETE FROM approval_authority WHERE granted_by IN ('suite93-a','suite93-b')"))
            await db.execute(text("DELETE FROM user_team_assignments WHERE assigned_by = 'suite93'"))
            await db.commit()

    if failures:
        print("\n".join(failures)); raise SystemExit(1)
    print("suite-93 GREEN")

asyncio.run(main())
PY

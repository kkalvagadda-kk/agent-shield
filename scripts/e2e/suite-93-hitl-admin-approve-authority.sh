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
#   T-S93-004  REWRITTEN 2026-08-09 (identity P3). It used to assert the GATEWAY PATH:
#              `X-User-Id` with NO token + reviewer_id="studio-user" → 200. That test was
#              asserting the VULNERABILITY. `decide_approval` resolved its caller as
#              `x_user_sub or x_user_id or JWT.sub or body.reviewer_id`, so a plaintext
#              header outranked the verified token on a route that required no credential
#              at all — anyone who could reach the API could approve any pending tool call
#              by naming a platform-admin's sub. The header arms are gone; identity now
#              comes from the token only. The case is inverted to lock that in:
#                004a  header-only, NO token                  → 401 (was 200)
#                004b  real admin token + reviewer_id="studio-user" in the BODY → decides,
#                      proving body.reviewer_id is a LABEL and never an identity.
#              There is no Envoy SecurityPolicy in this deployment, so the "gateway header"
#              path this suite simulated was never a real browser path — it was a way of
#              calling the API with no credential.
set -euo pipefail

NS="${NS:-agentshield-platform}"
POD="$(kubectl -n "$NS" get pod -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -z "$POD" ] && POD="$(kubectl -n "$NS" get pod 2>/dev/null | grep -i registry-api | grep Running | head -1 | awk '{print $1}')"
[ -z "$POD" ] && { echo "no running registry-api pod in $NS"; exit 1; }
echo "registry-api pod: $POD"

# REAL personas, not invented subs. This suite used to mint `suite93-admin-<hex>` strings,
# INSERT a user_team_assignments row for them, and then "act as" them by typing the sub in
# a header. Once identity comes from the credential, a sub with no Keycloak user cannot
# authenticate at all — so the personas have to be real users with real tokens. That is
# strictly better: the suite now exercises the same path a human does.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
S93_ADMIN_TOKEN="$(e2e_ensure_persona "$NS" "$POD" "s93-admin" "platform-admin")"
S93_USER_TOKEN="$(e2e_ensure_persona  "$NS" "$POD" "s93-user"  "contributor")"
S93_GRANTEE_TOKEN="$(e2e_ensure_persona "$NS" "$POD" "s93-grantee" "contributor")"

kubectl -n "$NS" exec -i "$POD" -c registry-api -- env \
  S93_ADMIN_TOKEN="$S93_ADMIN_TOKEN" \
  S93_USER_TOKEN="$S93_USER_TOKEN" \
  S93_GRANTEE_TOKEN="$S93_GRANTEE_TOKEN" \
  python3 - <<'PY'
import asyncio, os, uuid, httpx
import base64, json as _json


def _sub_of(tok):
    """The sub INSIDE the token — never a value this suite chose.

    Deriving it from the credential is the same rule lib/e2e-auth.sh applies for E2E_SUB,
    and it is what stops the suite asserting against an identity the server never saw.
    """
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return _json.loads(base64.urlsafe_b64decode(payload))["sub"]


ADMIN_TOKEN = os.environ["S93_ADMIN_TOKEN"]
USER_TOKEN = os.environ["S93_USER_TOKEN"]
GRANTEE_TOKEN = os.environ["S93_GRANTEE_TOKEN"]
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from db import AsyncSessionLocal

BASE = "http://localhost:8000/api/v1"
ADMIN = _sub_of(ADMIN_TOKEN)
USER  = _sub_of(USER_TOKEN)
GRANTEE = _sub_of(GRANTEE_TOKEN)
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

def decide(aid, token, reviewer_id=None):
    """Decide as the holder of `token`. Identity comes from the Bearer and nothing else."""
    return httpx.patch(f"{BASE}/approvals/{aid}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"decision": "approved", "reviewer_id": reviewer_id or "s93", "version": 1}, timeout=20)

def decide_header_only(aid, sub):
    """A forged identity: name a platform-admin in a header and send NO credential.
    This is exactly what used to be believed. It must now be refused."""
    return httpx.patch(f"{BASE}/approvals/{aid}",
        headers={"X-User-Sub": sub, "X-User-Id": sub, "Content-Type": "application/json"},
        json={"decision": "approved", "reviewer_id": "studio-user", "version": 1}, timeout=20)

async def main():
    failures = []
    async with AsyncSessionLocal() as db:
        # borrow a real agent id for the NOT NULL FK-ish column
        agent_id = (await db.execute(text("SELECT id FROM agents LIMIT 1"))).scalar()
        # No seed_assignment here: e2e_ensure_persona created each user through the real
        # POST /api/v1/admin/users, which writes the user_team_assignments row itself.
        # Inserting a second row for the same sub would violate the PK and, worse, would
        # be this suite inventing an authorization fact instead of exercising one.
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
        r = decide(a_admin, ADMIN_TOKEN)
        if r.status_code == 403:
            failures.append(f"T-S93-001 FAIL: platform-admin got 403 {r.text[:120]} — the bug is back")
        else:
            print(f"T-S93-001 PASS: platform-admin decided (status={r.status_code}, not 403)")

        # T-S93-002 — non-admin, no grant → 403 not_authorized_to_decide.
        r = decide(a_user, USER_TOKEN)
        if r.status_code == 403:
            print("T-S93-002 PASS: non-admin with no grant is 403 (authority still enforced)")
        else:
            failures.append(f"T-S93-002 FAIL: non-admin should be 403, got {r.status_code} {r.text[:120]}")

        # T-S93-003 — 2 grants for one tool → no 500 (MultipleResultsFound fix).
        r = decide(a_grant, GRANTEE_TOKEN)
        if r.status_code == 500:
            failures.append(f"T-S93-003 FAIL: 2 grants -> 500 (scalar_one_or_none MultipleResultsFound): {r.text[:120]}")
        elif r.status_code == 403:
            failures.append(f"T-S93-003 FAIL: grantee with a valid grant got 403 {r.text[:120]}")
        else:
            print(f"T-S93-003 PASS: caller with 2 grants decided without 500 (status={r.status_code})")

        # T-S93-004a — a FORGED identity must be refused. Naming a platform-admin in
        # X-User-Sub/X-User-Id with no credential used to be believed outright.
        r = decide_header_only(a_gw, ADMIN)
        if r.status_code == 401:
            print("T-S93-004a PASS: header-only forged admin identity is 401 (identity comes from the token)")
        else:
            failures.append(
                f"T-S93-004a FAIL: header-only forged admin got {r.status_code} {r.text[:160]} — "
                f"a plaintext header is being accepted as identity again")

        # T-S93-004b — body.reviewer_id is a LABEL, not an identity. A real admin token
        # decides even though the body names "studio-user", and the recorded reviewer is
        # the label. If reviewer_id were ever read back as identity this would 403.
        r = decide(a_gw, ADMIN_TOKEN, reviewer_id="studio-user")
        if r.status_code == 403:
            failures.append(f"T-S93-004b FAIL: real admin token got 403 {r.text[:160]}")
        else:
            print(f"T-S93-004b PASS: admin token decides with body reviewer_id='studio-user' (status={r.status_code})")
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

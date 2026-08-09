#!/usr/bin/env bash
# Suite 42: RBAC foundations — artifact_role_grants, creator auto-grant, /me enrichment
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
# Fixtures are timestamped: agents/workflows soft-delete, so a fixed name stays
# reserved and every re-run 409s (see suite-6 for the same guard).
RUN_TAG="$(date +%s)"

POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')

# R2 (registry-api 0.2.263): POST /agents/ requires a real JWT and contributor+, and the
# creator auto-grant is now keyed on the token's `sub`. This suite used to pass
# `X-User-Sub: test-rbac-user` with no credential and assert the grant landed on that
# literal — which proved the grant matches a string the CALLER TYPED, not that it
# matches the creator. A real persona makes the assertion mean what its name says.
# See docs/bugs/anonymous-agent-creation-with-forged-attribution.md.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
S42_TOK="$(e2e_ensure_persona "$NAMESPACE" "$POD" "s42-creator" "contributor")"
[ -n "$S42_TOK" ] || { echo "ERROR: could not provision the s42-creator persona"; exit 1; }
S42_SUB="$(printf '%s' "$S42_TOK" | cut -d. -f2 | python3 -c "
import base64, json, sys
raw = sys.stdin.read().strip()
print(json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))['sub'])
")"

run() {
  kubectl exec -n "$NAMESPACE" "$POD" -- python3 -c "$1"
}

echo "=== Suite 42: RBAC Foundations ==="

# --------------------------------------------------------------------------
# T-S42-001 — artifact_role_grants table exists
# --------------------------------------------------------------------------
echo "T-S42-001 — artifact_role_grants table exists"
run '
import httpx
from sqlalchemy import text
# Direct DB check via internal Python
import asyncio
async def check():
    from sqlalchemy.ext.asyncio import create_async_engine
    import os
    url = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentshield:agentshield@agentshield-postgresql:5432/agentshield")
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        r = await conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_name = '\''artifact_role_grants'\''"))
        count = r.scalar()
        assert count == 1, f"Table not found, got count={count}"
    await eng.dispose()
asyncio.run(check())
print("PASS: T-S42-001")
'

# --------------------------------------------------------------------------
# T-S42-002 — Creating an agent auto-grants agent-admin to creator
# --------------------------------------------------------------------------
echo "T-S42-002 — Creator auto-grant on agent creation"
run '
import httpx, asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import os

c = httpx.Client(follow_redirects=True, base_url="http://localhost:8000/api/v1", headers={"Authorization": "Bearer '"${S42_TOK}"'"})
# Create agent
r = c.post("/agents", json={"name":"s42-rbac-agent-'"${RUN_TAG}"'","team":"default","agent_type":"declarative"})
assert r.status_code in (200, 201), f"create agent -> {r.status_code} {r.text[:200]}"
agent_id = r.json()["id"]

# Check DB for auto-grant
url = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentshield:agentshield@agentshield-postgresql:5432/agentshield")
async def check():
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        r = await conn.execute(text(
            "SELECT role, grantee_id, granted_by FROM artifact_role_grants "
            "WHERE artifact_id = :aid AND revoked_at IS NULL"
        ), {"aid": agent_id})
        rows = r.fetchall()
        assert len(rows) >= 1, f"Expected at least 1 grant, got {len(rows)}"
        grant = rows[0]
        assert grant[0] == "agent-admin", f"Expected agent-admin, got {grant[0]}"
        assert grant[1] == "'"${S42_SUB}"'", f"Expected the creator real sub, got {grant[1]}"
        assert grant[2] == "system:auto-grant"
    await eng.dispose()
asyncio.run(check())
print("PASS: T-S42-002")
'

# --------------------------------------------------------------------------
# T-S42-003 — Creating a workflow auto-grants agent-admin to creator
# --------------------------------------------------------------------------
echo "T-S42-003 — Creator auto-grant on workflow creation"
run '
import httpx, asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import os

c = httpx.Client(follow_redirects=True, base_url="http://localhost:8000/api/v1", headers={"Authorization": "Bearer '"${S42_TOK}"'"})
rw = c.post("/workflows", json={"name":"s42-rbac-wf-'"${RUN_TAG}"'","team":"default","orchestration":"sequential"})
assert rw.status_code in (200, 201), f"create workflow -> {rw.status_code} {rw.text[:200]}"
wf = rw.json()
wf_id = wf["id"]

url = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentshield:agentshield@agentshield-postgresql:5432/agentshield")
async def check():
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        r = await conn.execute(text(
            "SELECT role, grantee_id FROM artifact_role_grants "
            "WHERE artifact_id = :aid AND artifact_type = '\''workflow'\'' AND revoked_at IS NULL"
        ), {"aid": wf_id})
        rows = r.fetchall()
        assert len(rows) >= 1, f"Expected at least 1 grant, got {len(rows)}"
        assert rows[0][0] == "agent-admin"
        assert rows[0][1] == "'"${S42_SUB}"'"
    await eng.dispose()
asyncio.run(check())
print("PASS: T-S42-003")
'

# --------------------------------------------------------------------------
# T-S42-004 — /me returns normalized role + artifact_roles
# --------------------------------------------------------------------------
echo "T-S42-004 — /me endpoint returns role and artifact_roles"
run '
import httpx
base = "http://localhost:8000/api/v1"

# Half 1 — the endpoint is guarded. NO Authorization header on purpose.
anon = httpx.Client(follow_redirects=True, base_url=base)
r = anon.get("/me")
assert r.status_code == 401, f"Expected 401 without token, got {r.status_code}"

# Half 2 — and it actually answers for a real caller. The old version could only do
# the 401 half and said so in a comment ("We need a JWT for /me"), so the case named
# "returns role and artifact_roles" never once checked either field. A real persona
# is now available, so it does.
authed = httpx.Client(follow_redirects=True, base_url=base,
                      headers={"Authorization": "Bearer '"${S42_TOK}"'"})
me = authed.get("/me")
assert me.status_code == 200, f"Expected 200 with a token, got {me.status_code} {me.text[:200]}"
body = me.json()
got_sub = body.get("sub")
assert got_sub == "'"${S42_SUB}"'", f"/me sub mismatch: {got_sub}"
got_role = body.get("role")
assert got_role == "contributor", f"expected contributor, got {got_role}"
ar = body.get("artifact_roles")
assert isinstance(ar, list), "artifact_roles must be a list"
# The persona created an agent and a workflow above, so its auto-grants must show here.
roles = {g["role"] for g in ar}
assert "agent-admin" in roles, f"creator auto-grants missing from /me: {ar}"
print("PASS: T-S42-004 (401 unauthenticated; role + artifact_roles correct when authenticated)")
'

# --------------------------------------------------------------------------
# T-S42-005 — Role normalization (legacy admin/operator/viewer → canonical)
# --------------------------------------------------------------------------
echo "T-S42-005 — Role normalization in rbac module"
run '
import sys
sys.path.insert(0, "/app")
from rbac import _normalize_role, ROLE_HIERARCHY, PLATFORM_ROLES
assert _normalize_role("admin") == "platform-admin"
assert _normalize_role("operator") == "contributor"
assert _normalize_role("viewer") == "consumer"
assert _normalize_role("platform-admin") == "platform-admin"
assert _normalize_role("contributor") == "contributor"
assert _normalize_role("consumer") == "consumer"

# CONTRACT CHANGE (R0 / FR-5, FR-6). `_normalize_role(None) == "contributor"` used to
# be asserted here — that was the invention Decision 41 named. A missing row now raises
# NoPlatformRole in get_user_global_role and never reaches this function, so `raw` is
# non-Optional. What replaces it: an UNRECOGNIZED value is returned VERBATIM and keeps
# rank 0. That is load-bearing, not a gap — `agent:reviewer` is a reviewer SCOPE read
# out of the same column by approvals.py:266 _caller_roles (Decision 42 / V-5).
assert _normalize_role("agent:reviewer") == "agent:reviewer"
assert ROLE_HIERARCHY.get(_normalize_role("agent:reviewer"), 0) == 0

# consumer is the floor of the hierarchy; viewer is no longer a canonical key
assert ROLE_HIERARCHY["consumer"] == 0, ROLE_HIERARCHY
assert "viewer" not in ROLE_HIERARCHY, ROLE_HIERARCHY

# Every legacy spelling normalizes into a real hierarchy key — guards against a
# legacy name mapping to a value that silently falls through to level 0.
for legacy in ("admin", "operator", "viewer"):
    assert _normalize_role(legacy) in ROLE_HIERARCHY, legacy

# PLATFORM_ROLES must cover canonical + legacy, else set_user_realm_role leaves
# a stale legacy realm role attached when replacing a users role.
for name in ("platform-admin", "contributor", "consumer", "admin", "operator", "viewer"):
    assert name in PLATFORM_ROLES, name
print("PASS: T-S42-005")
'

# --------------------------------------------------------------------------
# T-S42-007 — Migration 0072 left no legacy `viewer` rows behind
# --------------------------------------------------------------------------
echo "T-S42-007 — No viewer rows remain after migration 0075"
run '
import asyncio, os, sys
sys.path.insert(0, "/app")
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    eng = create_async_engine(os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://"))
    async with eng.begin() as conn:
        n = (await conn.execute(
            text("SELECT count(*) FROM user_team_assignments WHERE role = :r"),
            {"r": "viewer"},
        )).scalar_one()
        assert n == 0, f"{n} rows still hold legacy role viewer — migration 0075 did not run"
        # consumer must be an accepted stored value (no CHECK constraint blocks it)
        await conn.execute(text(
            "INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at) "
            "VALUES (:s, :t, :r, :b, now()) ON CONFLICT (user_sub) DO UPDATE SET role = EXCLUDED.role"
        ), {"s": "t-s42-007-probe", "t": "default", "r": "consumer", "b": "suite-42"})
        got = (await conn.execute(
            text("SELECT role FROM user_team_assignments WHERE user_sub = :s"),
            {"s": "t-s42-007-probe"},
        )).scalar_one()
        assert got == "consumer", got
        await conn.execute(text("DELETE FROM user_team_assignments WHERE user_sub = :s"), {"s": "t-s42-007-probe"})
    await eng.dispose()

asyncio.run(main())
print("PASS: T-S42-007")
'

# --------------------------------------------------------------------------
# T-S42-006 — Duplicate auto-grant is idempotent (ON CONFLICT DO NOTHING)
# --------------------------------------------------------------------------
echo "T-S42-006 — Duplicate auto-grant idempotent"
run '
import httpx
# Creating same agent twice would 409, but let us test by calling the grant function directly
import asyncio, sys, os
sys.path.insert(0, "/app")
from rbac import grant_creator_admin
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import uuid

url = os.getenv("DATABASE_URL", "postgresql+asyncpg://agentshield:agentshield@agentshield-postgresql:5432/agentshield")
async def check():
    eng = create_async_engine(url)
    Session = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        fake_id = uuid.uuid4()
        await grant_creator_admin(db, "agent", fake_id, "dup-test-user")
        await db.commit()
        await grant_creator_admin(db, "agent", fake_id, "dup-test-user")
        await db.commit()
        r = await db.execute(text(
            "SELECT count(*) FROM artifact_role_grants WHERE artifact_id = :aid AND grantee_id = :sub"
        ), {"aid": fake_id, "sub": "dup-test-user"})
        assert r.scalar() == 1, "Expected exactly 1 row after duplicate insert"
    await eng.dispose()
asyncio.run(check())
print("PASS: T-S42-006")
'

echo ""
echo "=== Suite 42 COMPLETE: 6/6 ==="

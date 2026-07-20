#!/usr/bin/env bash
# seed-platform-admin-role.sh — pin the `platform-admin` global role to the CURRENT
# Keycloak platform-admin user, every deploy. Idempotent + self-healing.
#
# WHY THIS EXISTS
# --------------
# The Studio Admin menu (and every platform-admin-gated feature) is gated by
# `GET /api/v1/me` returning role == "platform-admin". `/me` resolves the role from
# `user_team_assignments` keyed on the caller's Keycloak `sub` (routers/me.py).
#
# Nothing else in the install seeds that row — realm-init-job.yaml only creates the
# Keycloak *user*, never a DB assignment. So the mapping was hand-created once against
# whatever `sub` the platform-admin had then. When the Keycloak realm is later
# recreated (fresh cluster, realm re-import, etc.) the platform-admin gets a NEW `sub`,
# the old assignment strands on the dead `sub`, and the live admin only gets a JIT
# `contributor` row — the Admin menu silently vanishes. (Observed 2026-07-20:
# assignment pinned to old sub 643b0e62…, live admin 75c7c8b3… stuck as contributor.)
#
# This script closes that gap: it logs in AS the platform-admin (so it reads the sub
# the running Keycloak actually issues today) and UPSERTs role=platform-admin onto
# THAT sub. Re-running is a no-op once correct. `user_team_assignments` PK is
# `user_sub`, so ON CONFLICT (user_sub) DO UPDATE is the right upsert.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
TEAM="${PLATFORM_ADMIN_TEAM:-platform}"
KC_USER="${KC_PLATFORM_ADMIN_USER:-platform-admin}"
KC_PASS="${KC_PLATFORM_ADMIN_PASS:-PlatformAdmin2024}"
KC_INTERNAL_URL="${KC_INTERNAL_URL:-http://agentshield-keycloak}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "  [!!] seed-platform-admin-role: no Running registry-api pod — skipping (run again after rollout)"
  exit 0
fi

echo "==> Seeding platform-admin role for the live Keycloak '$KC_USER' (team '$TEAM')"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app KC_USER='$KC_USER' KC_PASS='$KC_PASS' TEAM='$TEAM' KC_URL='$KC_INTERNAL_URL' python3 -" <<'PY'
import asyncio
import base64
import json
import os
import urllib.parse
import urllib.request

from sqlalchemy import text
from db import AsyncSessionLocal

KC_USER = os.environ["KC_USER"]
KC_PASS = os.environ["KC_PASS"]
TEAM = os.environ["TEAM"]
KC_URL = os.environ["KC_URL"].rstrip("/")


def live_sub() -> str:
    """Log in as the platform-admin and read the `sub` the running Keycloak issues."""
    data = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id": "agentshield-studio",
        "username": KC_USER,
        "password": KC_PASS,
    }).encode()
    url = f"{KC_URL}/realms/agentshield/protocol/openid-connect/token"
    tok = json.loads(urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15).read())
    payload = tok["access_token"].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


async def main():
    sub = live_sub()
    async with AsyncSessionLocal() as s:
        await s.execute(
            text("""
                INSERT INTO user_team_assignments
                    (user_sub, team_name, role, assigned_by, assigned_at)
                VALUES (:sub, :team, 'platform-admin', 'system:seed-platform-admin', now())
                ON CONFLICT (user_sub)
                DO UPDATE SET role = 'platform-admin',
                              team_name = :team,
                              assigned_by = 'system:seed-platform-admin',
                              assigned_at = now()
            """),
            {"sub": sub, "team": TEAM},
        )
        await s.commit()
        row = (await s.execute(
            text("SELECT team_name, role FROM user_team_assignments WHERE user_sub = :sub"),
            {"sub": sub},
        )).mappings().one()
        print(f"    [OK] {KC_USER} sub={sub} -> role={row['role']} team={row['team_name']}")

asyncio.run(main())
PY

echo "    (Admin menu appears on next Studio load — /me reads this row, not the JWT.)"

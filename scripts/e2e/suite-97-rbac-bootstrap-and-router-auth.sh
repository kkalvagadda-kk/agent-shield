#!/usr/bin/env bash
# scripts/e2e/suite-97-rbac-bootstrap-and-router-auth.sh
#
# E2E Suite 97: RBAC R0/R1 — platform-admin bootstrap, missing-row refusal, and the
# router 401 matrix. NO fakes: every assertion drives the real Keycloak, the real DB,
# and the real HTTP surface.
#
# ⚠ THIS SUITE IS DESTRUCTIVE: IT RESTARTS registry-api, SCALES IT TO 2 REPLICAS,
#   AND TAKES KEYCLOAK DOWN AND BACK UP ⚠
# ------------------------------------------------------------------------------
# T-S97-004 DELETES the Keycloak `platform-admin` user and then restarts the
# registry-api Deployment. That is not incidental — it is the only way to reproduce
# the incident. Every other bash suite authenticates AS platform-admin
# (scripts/e2e/lib/e2e-auth.sh:52), so do not run this concurrently with another
# suite, and expect a ~2 minute window where the platform has no admin.
# T-S97-005 additionally scales Keycloak to 0 for ~90s: while it is down NOTHING in
# the cluster can mint a token. Both restore what they touched before the suite ends.
#
# RECOVERY, if the case fails and leaves you with no admin:
#   the running registry-api image must contain services/registry-api/bootstrap_admin.py
#   (R0). Confirm with
#     kubectl exec -n agentshield-platform <pod> -c registry-api -- \
#       python3 -c "import bootstrap_admin; print(bootstrap_admin.BOOTSTRAP_LOCK_KEY)"
#   (expect 4611686019521751996), then
#     kubectl rollout restart deploy/agentshield-registry-api -n agentshield-platform
#   An image WITHOUT the bootstrap cannot recreate the user — the realm-init Job no
#   longer creates users either (Decision 40) — so on a pre-R0 image you must recreate
#   it by hand with kcadm before any other suite will authenticate.
#
# WHAT T-S97-004 IS REALLY GUARDING (spec SC-2, the headline regression)
# ---------------------------------------------------------------------
# Observed 2026-07-20: the Studio Admin menu silently vanished. The
# `user_team_assignments` row had been hand-seeded once against whatever `sub` the
# platform-admin had at the time (643b0e62…). The realm was later recreated, Keycloak
# reissued the admin as a NEW subject (75c7c8b3…), the durable row stranded on the dead
# one, `/me` resolved no platform role, and `Sidebar.tsx` stopped rendering the Admin
# section. Nothing failed; a menu just disappeared.
#
# The design flaw was coupling a durable row to an identifier the IdP is free to
# reissue. The fix (bootstrap_admin.py) looks the admin up by USERNAME on every start,
# so re-pinning falls out of the design rather than out of a script someone remembers
# to run. This case asserts that property the only way it can be asserted: by taking
# the admin's `sub` away and requiring the platform to re-pin itself.
#
# THIS CASE MUST FAIL AGAINST PRE-R0 CODE (DoD rule 7 — regression-test-first).
# Before bootstrap_admin.py exists, deleting the Keycloak user is permanent: no
# platform-admin is recreated, so `exact` is empty and the case reports FAIL naming the
# missing user. That RED run is the reproduction.
#
# CASES
# -----
#   T-S97-001 — BOOTSTRAP INVARIANT: exactly one Keycloak `platform-admin`, and exactly
#               one assignment row on ITS id, (platform, platform-admin, system:bootstrap).
#               Asserted on the cluster AS FOUND, before this suite touches anything.
#   T-S97-002 — RESTART IDEMPOTENCE: same user_sub AND the same assigned_at across a
#               rollout restart. This is the DoD-2 save→reload→assert round-trip: the
#               guarded WHERE in _UPSERT_SQL is what makes a restart a genuine no-op.
#   T-S97-003 — SINGLE-FLIGHT: two replicas and two concurrent ensure_platform_admin()
#               calls still leave ONE user and ONE bootstrap row; the loser logs
#               "another replica holds the lock".
#   T-S97-004 — REALM-RECREATION RE-PIN: delete the Keycloak platform-admin, restart
#               registry-api, and require that (a) exactly one platform-admin user
#               exists again, (b) its id is DIFFERENT from the one we deleted, (c) the
#               assignment row is on the NEW id with team='platform',
#               role='platform-admin' and assigned_by='system:bootstrap' (which proves
#               the LIFESPAN BOOTSTRAP wrote it — not seed-platform-admin-role.sh,
#               which deploy no longer calls and which stamps
#               'system:seed-platform-admin'), and (d) a real password grant for
#               `platform-admin` calling GET /api/v1/me answers role == "platform-admin".
#               (d) is what the 2026-07-20 symptom was actually made of.
#   T-S97-005 — NON-FATAL: with Keycloak scaled to 0 the pod does NOT restart, /health
#               stays 200, and ensure_platform_admin returns False without raising.
#   T-S97-006 — /ready CONTRACT: 200 {"status":"ready"} when pinned; 503
#               {"status":"bootstrapping", detail, attempts} when it is not — produced
#               by the REAL Keycloak outage of T-S97-005, not by a stubbed flag.
#   T-S97-007 — ATOMIC CREATE: POST /api/v1/admin/users leaves Keycloak user + realm
#               role + row, and DELETE removes both sides.
#   T-S97-008 — COMPENSATION: a commit failure mid-create answers 502 AND leaves no
#               Keycloak user behind.
#   T-S97-009 — NO INVENTED ROLE: a role-omitting INSERT raises NOT NULL and
#               column_default is NULL (migration 0079).
#   T-S97-010 — REFUSAL + SCOPE: a row-less sub raises NoPlatformRole / answers 403
#               no_platform_role over HTTP, while a reviewer SCOPE resolves verbatim at
#               rank 0 (Decision 42).
#   T-S97-012 — IDENTITY AUDIT: the FR-12 read-only surface and its two arithmetic
#               invariants; zero orphans and zero stale rows (SC-3).
#
# T-S97-011 (the ten-router 401 matrix + exemption canary) is R1 and lands with the
# router change — plan T14 / tasks T042. It is deliberately NOT in REQUIRED_IDS below:
# a completeness gate that demands a case nobody has written yet fails for the wrong
# reason. Add "011" to REQUIRED_IDS in the SAME commit that appends the case.
#
# EXPECTED TOTAL TODAY: 14 PASS lines — eleven case IDs, of which T-S97-005 and T-S97-006
# each report TWO legs (the host measures the pod: restartCount and /health across the
# outage; the in-pod driver measures the bootstrap's own return value and the real /ready
# handler), plus the completeness gate. It becomes 15 when T-S97-011 lands. Count the IDs
# in REQUIRED_IDS, never the number of lines — a case that splits its evidence is still
# one case, and a dropped case is what the gate is for.
#
# ORDERING IS DELIBERATE. The non-destructive invariants run FIRST, before anything is
# deleted, restarted or scaled: if the destructive legs cannot run (Keycloak wobbles, no
# capacity for a second replica) the R0 invariants have still been proven rather than
# going dark, which is the failure mode this repo keeps paying for. The realm-recreation
# case runs LAST for the same reason — it is the one that leaves the platform without an
# admin for ~2 minutes.
#
# Host-driven, in phases (NOT one detached driver): the pod under test is replaced
# mid-suite, so the restarts and the pod re-resolution must happen host-side.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
DEPLOY="${REGISTRY_API_DEPLOY:-agentshield-registry-api}"
CONTAINER="registry-api"

resolve_pod() {
  # A pod that is being REPLACED stays phase=Running until it actually goes away, so
  # `--field-selector=status.phase=Running` + `.items[0]` can hand back a terminating
  # pod. This suite restarts the Deployment twice, and when it does, every subsequent
  # exec against that name dies with `error: unable to upgrade connection: container
  # not found ("registry-api")` — observed, and it took the suite down mid-run.
  # Filter on: no deletionTimestamp AND Ready=True, then PROVE the choice with a cheap
  # exec before returning it, because the pod can begin terminating between the list
  # and the use.
  local line name term
  kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.metadata.deletionTimestamp}{"|"}{range .status.conditions[?(@.type=="Ready")]}{.status}{end}{"\n"}{end}' 2>/dev/null \
  | while IFS='|' read -r name term ready; do
      [ -n "$name" ] || continue
      [ -z "$term" ] || continue          # terminating — skip
      [ "$ready" = "True" ] || continue   # not serving yet
      if kubectl exec -n "$NAMESPACE" "$name" -c "$CONTAINER" -- true >/dev/null 2>&1; then
        echo "$name"; break
      fi
    done | head -1
}

API_POD="$(resolve_pod)" || true
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
# Prove we can authenticate BEFORE we delete the admin. If this fails now, the cluster
# was already broken and the suite must not compound it by deleting the user.
e2e_require_token "$NAMESPACE" "$API_POD" >/dev/null
e2e_install_pyauth "$NAMESPACE" "$API_POD"

echo "=== Suite 97: RBAC R0/R1 bootstrap + router auth (no fakes) ==="
echo "  Namespace: $NAMESPACE"
echo "  Pod:       $API_POD"
echo "  Deployment:$DEPLOY"
echo ""

PASS=0; FAIL=0
RUN_TAG="$(date +%s)$$"
# Every result line the suite has produced, from every phase, in one place. The
# completeness gate reads THIS rather than the last driver's output — otherwise a phase
# that died early would take its cases' IDs out of the gate's sight and the suite would
# report green on the cases that did run.
ALL_RESULTS=""

# record_host <PASS|FAIL> <case text…> — for the assertions the HOST makes (the ones
# that restart or scale something, which an in-pod driver cannot do).
record_host() {
  local verdict="$1"; shift
  local line="$verdict  $*"
  echo "$line"
  ALL_RESULTS="$ALL_RESULTS
$line"
  if [ "$verdict" = "PASS" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
}

# collect <result-file> <run-log> — drain an in-pod driver's result file into the
# suite's tally. An empty file is a FAIL that PRINTS THE DRIVER LOG: a driver that died
# before writing anything is the one failure mode that otherwise reports nothing at all.
collect() {
  local out="$1" log="$2" res
  res=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- cat "$out" 2>/dev/null || true)
  if [ -z "$res" ]; then
    echo "FAIL  T-S97-DRIVER produced no result file ($out) — last 40 log lines:"
    kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- tail -40 "$log" 2>/dev/null | sed 's/^/    /' || true
    FAIL=$((FAIL+1))
    return 0
  fi
  while IFS= read -r line; do
    case "$line" in
      PASS*) echo "$line"; PASS=$((PASS+1)) ;;
      FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
      SUMMARY*) : ;;
      *) [ -n "$line" ] && echo "  $line" ;;
    esac
  done <<< "$res"
  ALL_RESULTS="$ALL_RESULTS
$res"
}

# snapshot_admin — echoes "<sub>|<assigned_at ISO>|<keycloak match count>".
# By USERNAME, the same way the bootstrap finds it. Reading the sub from the DB instead
# would assume the row is already correct, which is the very thing under test.
snapshot_admin() {
  kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- \
    bash -c 'cd /app && PYTHONPATH=/app python3 -c "
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal
import keycloak_client as kc
async def main():
    users = await kc.list_users(username=\"platform-admin\", exact=True)
    exact = [u for u in users if u.get(\"username\") == \"platform-admin\"]
    sub = exact[0][\"id\"] if len(exact) == 1 else \"\"
    at = \"\"
    if sub:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(text(\"SELECT assigned_at FROM user_team_assignments WHERE user_sub = :u\"), {\"u\": sub})).first()
            at = row[0].isoformat() if row and row[0] else \"\"
    print(\"%s|%s|%d\" % (sub, at, len(exact)))
asyncio.run(main())
"' 2>/dev/null | tr -d '\r\n'
}

# http_probe <path> — echoes "<status> <body>" for an in-pod request against the LIVE
# server. python3/urllib, NOT curl: the registry-api image is python:3.12-slim
# (services/registry-api/Dockerfile:1) and has no curl, so an exec would exit 127 and the
# empty output would read as an outage rather than as a missing binary.
http_probe() {
  kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- python3 -c "
import urllib.error, urllib.request
try:
    r = urllib.request.urlopen('http://localhost:8000$1', timeout=15)
    print('%d %s' % (r.status, r.read().decode('utf-8', 'replace').strip()))
except urllib.error.HTTPError as e:
    print('%d %s' % (e.code, e.read().decode('utf-8', 'replace').strip()))
except Exception as exc:
    print('000 %s' % exc)
" 2>/dev/null | tr -d '\r' | head -1
}

# http_code <path> — just the status code.
http_code() {
  http_probe "$1" | cut -d' ' -f1
}

# ── Phase 1 — the R0 invariants, on the cluster AS FOUND ────────────────────────
# Nothing here restarts, deletes or scales anything the suite does not create itself.
echo "[1/9] R0 invariants (bootstrap row, atomic create, NOT NULL, refusal, audit)…"
OUT_A="/tmp/s97a_out_${RUN_TAG}.txt"
DRIVER_A="/tmp/s97a_driver_${RUN_TAG}.py"
RUNLOG_A="/tmp/s97a_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c "cat > $DRIVER_A" <<'PY'
import asyncio, os, sys, traceback, uuid
sys.path.insert(0, "/tmp")

import httpx
from fastapi import HTTPException
from sqlalchemy import text

from db import AsyncSessionLocal
import keycloak_client as kc
from rbac import ROLE_HIERARCHY, NoPlatformRole, get_user_global_role
from routers.admin_users import UserCreate, create_user as create_user_route

from e2e_auth import BearerAuth

OUT = os.environ["S97_OUT"]
STAMP = os.environ["S97_STAMP"][-8:]
KC_URL = os.environ["S97_KC_URL"]
KC_CLIENT = os.environ["S97_KC_CLIENT"]

BASE = "http://localhost:8000"
ADMIN_USERNAME = "platform-admin"
PROBE_PASSWORD = "S97Probe2026!"

results = []


def record(name, ok, detail=""):
    results.append((name, bool(ok), detail))


async def run(name, fn):
    """One crashing case must not take the other five with it."""
    try:
        ok, detail = await fn()
    except Exception as exc:
        ok = False
        detail = (f"CRASHED: {type(exc).__name__}: {exc} :: "
                  f"{traceback.format_exc()[-400:]}")
    record(name, ok, detail)


# ── T-S97-001 ──────────────────────────────────────────────────────────────────
async def case_001():
    users = await kc.list_users(username=ADMIN_USERNAME, exact=True)
    exact = [u for u in users if u.get("username") == ADMIN_USERNAME]
    sub = exact[0]["id"] if len(exact) == 1 else None

    row = None
    if sub:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                text("SELECT team_name, role, assigned_by FROM user_team_assignments "
                     "WHERE user_sub = :u"),
                {"u": sub},
            )).mappings().first()

    ok = (
        len(exact) == 1
        and row is not None
        and row["team_name"] == "platform"
        and row["role"] == "platform-admin"
        # assigned_by is the load-bearing field: 'system:bootstrap' proves the LIFESPAN
        # wrote it. 'system:seed-platform-admin' would mean the row came from the manual
        # repair script, i.e. SC-1 (zero manual steps) is not actually met.
        and row["assigned_by"] == "system:bootstrap"
    )
    return ok, (f"kc_users={len(exact)} (want 1) sub={sub} row={dict(row) if row else None} "
                f"(want team=platform role=platform-admin assigned_by=system:bootstrap)")


# ── T-S97-007 ──────────────────────────────────────────────────────────────────
async def case_007():
    uname = "s97-atomic-%s" % STAMP
    kc_id = None
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=60.0, auth=BearerAuth()) as c:
            r = await c.post("/api/v1/admin/users", json={
                "username": uname,
                "email": "%s@example.com" % uname,
                "first_name": "Suite97",
                "last_name": "Atomic",
                "temp_password": PROBE_PASSWORD,
                "team": "platform",
                "role": "contributor",
            })
            created = r.status_code
            kc_id = r.json().get("kc_id") if r.status_code in (200, 201) else None

        kc_present = False
        row_present = None
        role_leg = "not-checked"
        if kc_id:
            kc_present = bool(await kc.get_user(kc_id))
            async with AsyncSessionLocal() as s:
                row_present = (await s.execute(
                    text("SELECT team_name, role FROM user_team_assignments WHERE user_sub = :u"),
                    {"u": kc_id},
                )).mappings().first()
            # The realm-role leg is conditional ON PURPOSE (G-R0-4): no realm-role
            # OBJECTS exist for the three global roles today, and set_user_realm_role
            # (keycloak_client.py:203) silently skips a name absent from role_map. So
            # assert the mapping only where the object exists; otherwise say so out loud
            # rather than assert something the platform never promised.
            realm_names = {r["name"] for r in await kc.get_realm_roles()}
            user_roles = await kc.get_user_realm_roles(kc_id)
            if "contributor" in realm_names:
                role_leg = "contributor in user roles: %s" % ("contributor" in user_roles)
                realm_ok = "contributor" in user_roles
            else:
                role_leg = ("no realm-role OBJECT named 'contributor' exists — G-R0-4, "
                            "set_user_realm_role skipped it by design")
                realm_ok = True
        else:
            realm_ok = False

        # DELETE must remove BOTH sides — a delete that leaves the row is exactly the
        # stale-row litter the audit reports (G-R0-3).
        deleted = None
        gone_kc = None
        gone_row = None
        if kc_id:
            async with httpx.AsyncClient(base_url=BASE, timeout=60.0, auth=BearerAuth()) as c:
                d = await c.delete("/api/v1/admin/users/%s" % kc_id)
                deleted = d.status_code
            left = await kc.list_users(username=uname, exact=True)
            gone_kc = not [u for u in left if u.get("username") == uname]
            async with AsyncSessionLocal() as s:
                gone_row = (await s.execute(
                    text("SELECT count(*) FROM user_team_assignments WHERE user_sub = :u"),
                    {"u": kc_id},
                )).scalar() == 0
            if gone_kc and gone_row:
                kc_id = None  # nothing left to clean up

        ok = (
            created == 201
            and kc_present
            and row_present is not None
            and row_present["team_name"] == "platform"
            and row_present["role"] == "contributor"
            and realm_ok
            and deleted == 204
            and gone_kc is True
            and gone_row is True
        )
        return ok, (f"POST -> {created} (want 201) kc_user_present={kc_present} "
                    f"row={dict(row_present) if row_present else None} | realm role: {role_leg} | "
                    f"DELETE -> {deleted} (want 204) kc_gone={gone_kc} row_gone={gone_row}")
    finally:
        if kc_id:
            try:
                await kc.delete_user(kc_id)
            except Exception:
                pass


# ── T-S97-008 ──────────────────────────────────────────────────────────────────
class _CommitBoom:
    """A real AsyncSession that fails ONLY on commit().

    A wrapper rather than a monkeypatched attribute because AsyncSession's attribute
    surface is not ours to assume; execute() and rollback() must stay genuinely real, or
    the case would prove nothing about the rollback half of the compensation.
    """

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_inner"), name)

    async def commit(self):
        raise RuntimeError("injected commit failure (T-S97-008)")


async def case_008():
    uname = "s97-compensate-%s" % STAMP
    status_code = None
    detail = None
    try:
        async with AsyncSessionLocal() as s:
            try:
                await create_user_route(
                    UserCreate(
                        username=uname,
                        email="%s@example.com" % uname,
                        first_name="Suite97",
                        last_name="Compensate",
                        temp_password=PROBE_PASSWORD,
                        team="platform",
                        role="contributor",
                    ),
                    db=_CommitBoom(s),
                    caller=None,
                )
            except HTTPException as exc:
                status_code = exc.status_code
                detail = str(exc.detail)[:160]

        left = await kc.list_users(username=uname, exact=True)
        survivors = [u for u in left if u.get("username") == uname]
        rows = None
        if survivors:
            async with AsyncSessionLocal() as s:
                rows = (await s.execute(
                    text("SELECT count(*) FROM user_team_assignments WHERE user_sub = :u"),
                    {"u": survivors[0]["id"]},
                )).scalar()

        ok = status_code == 502 and not survivors
        return ok, (f"HTTPException status={status_code} (want 502) detail={detail!r} | "
                    f"keycloak users named {uname} left behind: {len(survivors)} (want 0 — the "
                    f"compensating kc_delete ran; a survivor is the ORPHAN the audit exists to "
                    f"surface, rows for it={rows})")
    finally:
        for u in await kc.list_users(username=uname, exact=True):
            if u.get("username") == uname:
                try:
                    await kc.delete_user(u["id"])
                except Exception:
                    pass


# ── T-S97-009 ──────────────────────────────────────────────────────────────────
async def case_009():
    probe = "s97-nullrole-%s" % STAMP
    exc_name = None
    orig_name = None
    message = ""
    async with AsyncSessionLocal() as s:
        try:
            await s.execute(
                text("INSERT INTO user_team_assignments (user_sub, team_name) "
                     "VALUES (:s, 'platform')"),
                {"s": probe},
            )
            await s.commit()
        except Exception as exc:
            await s.rollback()
            exc_name = type(exc).__name__
            orig_name = type(getattr(exc, "orig", None)).__name__
            message = str(exc)[:200]

    # An insert that SUCCEEDED here is the failure: it means the column can still invent
    # a role. Clean up so the next run is not testing this run's litter.
    async with AsyncSessionLocal() as s:
        leftover = (await s.execute(
            text("SELECT count(*) FROM user_team_assignments WHERE user_sub = :s"),
            {"s": probe},
        )).scalar()
        if leftover:
            await s.execute(
                text("DELETE FROM user_team_assignments WHERE user_sub = :s"), {"s": probe}
            )
            await s.commit()

        col = (await s.execute(
            text("SELECT column_default, is_nullable FROM information_schema.columns "
                 "WHERE table_name = 'user_team_assignments' AND column_name = 'role'")
        )).mappings().first()

    default_value = col["column_default"] if col else "<no such column>"
    nullable = col["is_nullable"] if col else "<no such column>"
    not_null = (
        exc_name is not None
        and ("notnullviolation" in (orig_name or "").lower()
             or "null value in column" in message.lower())
    )
    ok = not_null and leftover == 0 and default_value is None and nullable == "NO"
    return ok, (f"role-omitting INSERT raised {exc_name}/{orig_name} (want a NOT NULL "
                f"violation) rows_left={leftover} | information_schema: "
                f"column_default={default_value!r} (want None — migration 0079 dropped it) "
                f"is_nullable={nullable} (want NO)")


# ── T-S97-010 ──────────────────────────────────────────────────────────────────
async def case_010():
    # (a) the resolution path itself refuses rather than defaulting
    ghost = str(uuid.uuid4())
    raised = None
    async with AsyncSessionLocal() as s:
        try:
            await get_user_global_role(s, ghost)
            raised = "NO EXCEPTION — it resolved a role for a sub with no row"
        except NoPlatformRole as exc:
            raised = "NoPlatformRole(%s)" % (exc.user_sub == ghost)
    leg_a = raised == "NoPlatformRole(True)"

    # (b) the same refusal over HTTP, with a REAL token for a REAL Keycloak user that
    # has no row. Anything less (a hand-made JWT, a header) would not prove the app-level
    # handler is reachable from an authenticated request.
    uname = "s97-norow-%s" % STAMP
    kc_id = None
    me_status = None
    me_body = None
    grant_status = None
    try:
        kc_id = await kc.create_user(
            username=uname,
            email="%s@example.com" % uname,
            first_name="Suite97",
            last_name="NoRow",
            temp_password=PROBE_PASSWORD,
        )
        # REQUIRED: create_user writes a temporary password + requiredActions, and
        # Keycloak refuses a direct grant for such a user with "Account is not fully set
        # up" — a 401 that names nothing. Same reason e2e_ensure_reviewer does it.
        await kc.reset_password(kc_id, PROBE_PASSWORD, temporary=False)

        async with httpx.AsyncClient(timeout=30.0) as c:
            tr = await c.post(KC_URL, data={
                "grant_type": "password",
                "client_id": KC_CLIENT,
                "username": uname,
                "password": PROBE_PASSWORD,
            })
            grant_status = tr.status_code
            token = tr.json().get("access_token") if tr.status_code == 200 else None

        if token:
            async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
                r = await c.get("/api/v1/me", headers={"Authorization": "Bearer %s" % token})
                me_status = r.status_code
                me_body = r.json()
    finally:
        if kc_id:
            try:
                await kc.delete_user(kc_id)
            except Exception:
                pass

    leg_b = (
        me_status == 403
        and isinstance(me_body, dict)
        and me_body.get("error_code") == "no_platform_role"
        and me_body.get("sub") == kc_id
    )

    # (c) a REVIEWER SCOPE is not a missing role and not an invented one: it comes back
    # verbatim, at rank 0. approvals.py:48 matches this literal against the same column
    # (Decision 42 / V-5) — "fixing" it into a global role is how a reviewer would
    # silently become a contributor.
    probe = "s97-scope-%s" % STAMP
    resolved = None
    rank = None
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(
                text("INSERT INTO user_team_assignments "
                     "(user_sub, team_name, role, assigned_by, assigned_at) "
                     "VALUES (:s, 'platform', 'agent:reviewer', 'suite-97', now()) "
                     "ON CONFLICT (user_sub) DO UPDATE SET role = EXCLUDED.role"),
                {"s": probe},
            )
            await s.commit()
            resolved = await get_user_global_role(s, probe)
            rank = ROLE_HIERARCHY.get(resolved, 0)
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(
                text("DELETE FROM user_team_assignments WHERE user_sub = :s"), {"s": probe}
            )
            await s.commit()
    leg_c = resolved == "agent:reviewer" and rank == 0

    ok = leg_a and leg_b and leg_c
    return ok, (f"(a) get_user_global_role(row-less sub) -> {raised} | "
                f"(b) grant={grant_status} GET /me -> {me_status} (want 403) body={me_body} "
                f"(want error_code=no_platform_role, sub={kc_id}) | "
                f"(c) role='agent:reviewer' resolved={resolved!r} rank={rank} (want verbatim, 0)")


# ── T-S97-012 ──────────────────────────────────────────────────────────────────
async def case_012():
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0, auth=BearerAuth()) as c:
        r = await c.get("/api/v1/admin/identity-audit")
    body = r.json() if r.status_code == 200 else {}
    fields = {"checked_at", "keycloak_user_count", "assignment_row_count",
              "orphan_users", "stale_rows", "matched_count"}

    inv_kc = inv_rows = False
    if set(body) == fields:
        inv_kc = body["matched_count"] + len(body["orphan_users"]) == body["keycloak_user_count"]
        inv_rows = body["matched_count"] + len(body["stale_rows"]) == body["assignment_row_count"]

    orphans = [u.get("username") for u in body.get("orphan_users", [])]
    stale = [row.get("user_sub") for row in body.get("stale_rows", [])]

    # SC-3 is a claim about the CLUSTER, so a non-empty list here is a real finding, not
    # a flaky assertion — the endpoint reports, it never cleans (OQ-2 option (a)), so the
    # names below are the work item. Every case in this suite removes what it created.
    ok = (
        r.status_code == 200
        and set(body) == fields
        and inv_kc
        and inv_rows
        and not orphans
        and not stale
    )
    return ok, (f"GET /api/v1/admin/identity-audit -> {r.status_code} fields_ok={set(body) == fields} "
                f"kc={body.get('keycloak_user_count')} rows={body.get('assignment_row_count')} "
                f"matched={body.get('matched_count')} | matched+orphans==kc: {inv_kc} | "
                f"matched+stale==rows: {inv_rows} | orphan_users={orphans} stale_rows={stale} "
                f"(SC-3 wants both empty)")


async def main():
    try:
        await run("T-S97-001 BOOTSTRAP INVARIANT: exactly one platform-admin, pinned by the "
                  "lifespan bootstrap", case_001)
        await run("T-S97-007 ATOMIC CREATE: POST /admin/users leaves user + realm role + row, "
                  "DELETE removes both", case_007)
        await run("T-S97-008 COMPENSATION: a commit failure answers 502 and leaves NO Keycloak "
                  "user behind", case_008)
        await run("T-S97-009 NO INVENTED ROLE: a role-omitting INSERT is refused and the column "
                  "default is gone", case_009)
        await run("T-S97-010 REFUSAL + SCOPE: a row-less sub is 403 no_platform_role; a reviewer "
                  "scope resolves verbatim at rank 0", case_010)
        # LAST, after every case above has cleaned up after itself — the audit is the one
        # assertion that reads the whole cluster, so anything this suite leaves behind
        # would be reported as its own failure.
        await run("T-S97-012 IDENTITY AUDIT: both arithmetic invariants hold and the cluster "
                  "carries no orphans or stale rows", case_012)
    finally:
        passed = sum(1 for _, ok, _ in results if ok)
        with open(OUT, "w") as f:
            for name, ok, detail in results:
                f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")


asyncio.run(main())
PY

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c \
  "cd /app && PYTHONPATH=/app S97_OUT=$OUT_A S97_STAMP=$RUN_TAG S97_KC_URL='$E2E_KC_URL' S97_KC_CLIENT='$E2E_KC_CLIENT' python3 $DRIVER_A > $RUNLOG_A 2>&1" \
  || true
collect "$OUT_A" "$RUNLOG_A"

# ── Phase 2 — T-S97-002: a restart must be a genuine no-op ──────────────────────
# DoD rule 2, the save→reload→assert round-trip: the row is written by one process,
# re-read after that process is REPLACED, and must be byte-identical. `assigned_at` is
# the discriminating field — an unconditional DO UPDATE would re-stamp it on every pod
# start and destroy the only signal for "when was this role last really changed", which
# is why _UPSERT_SQL carries the guarded WHERE.
echo ""
echo "[2/9] T-S97-002 — restart idempotence (same sub AND same assigned_at)…"
SNAP_BEFORE="$(snapshot_admin)" || true
if [ -z "$SNAP_BEFORE" ] || [ "${SNAP_BEFORE%%|*}" = "" ]; then
  record_host FAIL "T-S97-002 restart idempotence  |  could not read the admin snapshot before the restart (got '${SNAP_BEFORE}') — Keycloak or the DB is unreachable from the pod"
else
  kubectl rollout restart "deployment/$DEPLOY" -n "$NAMESPACE" >/dev/null
  if ! kubectl rollout status "deployment/$DEPLOY" -n "$NAMESPACE" --timeout=600s; then
    record_host FAIL "T-S97-002 restart idempotence  |  the rolled-out pod never became READY. /ready gates on the bootstrap, so the bootstrap did not succeed"
  else
    API_POD="$(resolve_pod)" || true
    if [ -z "$API_POD" ]; then
      record_host FAIL "T-S97-002 restart idempotence  |  no Running registry-api pod after the rollout"
    else
      e2e_install_pyauth "$NAMESPACE" "$API_POD"
      SNAP_AFTER="$(snapshot_admin)" || true
      if [ "$SNAP_BEFORE" = "$SNAP_AFTER" ] && [ "${SNAP_BEFORE##*|}" = "1" ]; then
        record_host PASS "T-S97-002 RESTART IDEMPOTENCE: the bootstrap re-ran and changed nothing  |  sub|assigned_at|kc_count before=$SNAP_BEFORE after=$SNAP_AFTER (identical, and exactly one Keycloak user)"
      else
        record_host FAIL "T-S97-002 RESTART IDEMPOTENCE: the bootstrap re-ran and changed nothing  |  before=$SNAP_BEFORE after=$SNAP_AFTER — a moved assigned_at means the guarded WHERE in bootstrap_admin._UPSERT_SQL stopped discriminating; a moved sub means the admin was recreated"
      fi
    fi
  fi
fi

# ── Phase 3 — T-S97-003: single-flight across replicas ─────────────────────────
echo ""
echo "[3/9] T-S97-003 — single-flight (2 replicas + a concurrent race)…"
ORIG_REPLICAS="$(kubectl get "deployment/$DEPLOY" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
[ -n "$ORIG_REPLICAS" ] || ORIG_REPLICAS=1
SCALED="no"
if kubectl scale "deployment/$DEPLOY" -n "$NAMESPACE" --replicas=2 >/dev/null 2>&1 \
   && kubectl rollout status "deployment/$DEPLOY" -n "$NAMESPACE" --timeout=300s >/dev/null 2>&1; then
  SCALED="yes"
fi
REPLICAS_READY="$(kubectl get "deployment/$DEPLOY" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
API_POD="$(resolve_pod)" || true
e2e_install_pyauth "$NAMESPACE" "$API_POD"

OUT_B="/tmp/s97b_out_${RUN_TAG}.txt"
DRIVER_B="/tmp/s97b_driver_${RUN_TAG}.py"
RUNLOG_B="/tmp/s97b_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c "cat > $DRIVER_B" <<'PY'
import asyncio, logging, os, traceback
from sqlalchemy import text

from db import AsyncSessionLocal
import keycloak_client as kc
import bootstrap_admin

OUT = os.environ["S97_OUT"]
REPLICAS_READY = os.environ.get("S97_REPLICAS_READY", "?")
SCALED = os.environ.get("S97_SCALED", "no")

ADMIN_USERNAME = "platform-admin"


class _Capture(logging.Handler):
    """Reads the bootstrap's own log instead of grepping pod logs.

    A rolling update brings replicas up SEQUENTIALLY (maxSurge), so two pods racing the
    advisory lock at the same instant is not something scaling can guarantee — a suite
    that greps for the lock-skip line across pods would be asserting a scheduling
    accident. Two concurrent calls inside one process race the SAME pg_try_advisory_lock
    on two different connections, which is the identical mechanism and is deterministic.
    """

    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


async def counts():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            text("SELECT count(*) FROM user_team_assignments "
                 "WHERE role = 'platform-admin' AND assigned_by = 'system:bootstrap'")
        )).scalar()
    users = await kc.list_users(username=ADMIN_USERNAME, exact=True)
    return rows, len([u for u in users if u.get("username") == ADMIN_USERNAME])


async def main():
    ok = False
    detail = ""
    try:
        rows_before, kc_before = await counts()

        cap = _Capture()
        prior_level = bootstrap_admin.logger.level
        bootstrap_admin.logger.addHandler(cap)
        bootstrap_admin.logger.setLevel(logging.INFO)
        try:
            first, second = await asyncio.gather(
                bootstrap_admin.ensure_platform_admin(),
                bootstrap_admin.ensure_platform_admin(),
            )
        finally:
            bootstrap_admin.logger.removeHandler(cap)
            bootstrap_admin.logger.setLevel(prior_level)

        lock_skipped = any("another replica holds the lock" in m for m in cap.messages)
        rows_after, kc_after = await counts()

        ok = (
            rows_before == 1
            and kc_before == 1
            and first is True
            and second is True
            and lock_skipped
            and rows_after == 1
            and kc_after == 1
        )
        detail = (
            f"replicas ready={REPLICAS_READY} (scaled to 2: {SCALED}) | "
            f"bootstrap rows before/after={rows_before}/{rows_after} (want 1) "
            f"keycloak platform-admins before/after={kc_before}/{kc_after} (want 1) | "
            f"concurrent ensure_platform_admin() -> {first}, {second} (want True, True — the "
            f"loser returns True because a peer doing the work is success for the CLUSTER) | "
            f"'another replica holds the lock' observed={lock_skipped}"
        )
    except Exception as exc:
        detail = (f"CRASHED: {type(exc).__name__}: {exc} :: "
                  f"{traceback.format_exc()[-400:]}")
    finally:
        with open(OUT, "w") as f:
            f.write("%s  T-S97-003 SINGLE-FLIGHT: concurrent bootstraps leave ONE user and ONE "
                    "row  |  %s\n" % ("PASS" if ok else "FAIL", detail))
            f.write("SUMMARY %d/1\n" % (1 if ok else 0))


asyncio.run(main())
PY

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c \
  "cd /app && PYTHONPATH=/app S97_OUT=$OUT_B S97_REPLICAS_READY=$REPLICAS_READY S97_SCALED=$SCALED python3 $DRIVER_B > $RUNLOG_B 2>&1" \
  || true
collect "$OUT_B" "$RUNLOG_B"

echo "      restoring replicas -> $ORIG_REPLICAS"
kubectl scale "deployment/$DEPLOY" -n "$NAMESPACE" --replicas="$ORIG_REPLICAS" >/dev/null 2>&1 || true
kubectl rollout status "deployment/$DEPLOY" -n "$NAMESPACE" --timeout=300s >/dev/null 2>&1 || true
API_POD="$(resolve_pod)" || true
if [ -z "$API_POD" ]; then echo "ERROR: no registry-api pod after restoring replicas"; exit 1; fi
e2e_install_pyauth "$NAMESPACE" "$API_POD"

# ── Phase 4 — T-S97-005 / T-S97-006: a Keycloak outage degrades, never crashes ──
# NOTE ON WHAT IS *NOT* DONE HERE. The obvious shape — "scale Keycloak to 0, then
# restart registry-api" — cannot assert what it claims: the pod has a `wait-for-keycloak`
# init container (charts/agentshield/charts/registry-api/templates/deployment.yaml:36-47)
# that blocks until the MASTER realm answers, so with Keycloak at 0 the new pod never
# reaches its main container and the old pod keeps serving a /ready that went green
# before the outage. The assertion would be about init containers, not about the
# bootstrap. What IS asserted is the actual NFR: the RUNNING pod does not restart, /health
# stays 200 while its IdP is gone, and the bootstrap itself returns False without raising
# — which is then read back through the REAL /ready handler, in the state a real outage
# produced.
echo ""
echo "[4/9] T-S97-005/006 — Keycloak outage: non-fatal, and the /ready contract…"
READY_BEFORE="$(http_probe /ready)" || true
READY_BEFORE_CODE="${READY_BEFORE%% *}"
READY_BEFORE_BODY="${READY_BEFORE#* }"

KC_WORKLOAD="$(kubectl get statefulset,deployment -n "$NAMESPACE" -o name 2>/dev/null \
  | grep -E '/[a-z0-9-]*keycloak$' | head -1 || true)"
if [ -z "$KC_WORKLOAD" ]; then
  record_host FAIL "T-S97-005 non-fatal Keycloak outage  |  could not resolve the Keycloak workload in $NAMESPACE (looked for a StatefulSet/Deployment whose name ends in 'keycloak') — refusing to scale something it cannot name"
  record_host FAIL "T-S97-006 /ready contract  |  not reached: the 503 leg is produced by the T-S97-005 outage and that phase could not start"
else
  KC_REPLICAS="$(kubectl get "$KC_WORKLOAD" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
  [ -n "$KC_REPLICAS" ] || KC_REPLICAS=1
  RESTARTS_BEFORE="$(kubectl get pod "$API_POD" -n "$NAMESPACE" \
    -o jsonpath='{.status.containerStatuses[?(@.name=="registry-api")].restartCount}' 2>/dev/null || echo "")"

  echo "      scaling $KC_WORKLOAD to 0 (was $KC_REPLICAS) — no token can be minted until it is back"
  kubectl scale "$KC_WORKLOAD" -n "$NAMESPACE" --replicas=0 >/dev/null 2>&1 || true

  # 90s of liveness, sampled. /health must never blink: it is what the livenessProbe
  # reads, and a 503 there is what would restart the pod on an IdP outage.
  HEALTH_CODES=""
  for _ in 1 2 3 4 5 6 7 8 9; do
    HEALTH_CODES="$HEALTH_CODES $(http_code /health)"
    sleep 10
  done
  RESTARTS_AFTER="$(kubectl get pod "$API_POD" -n "$NAMESPACE" \
    -o jsonpath='{.status.containerStatuses[?(@.name=="registry-api")].restartCount}' 2>/dev/null || echo "")"

  OUT_C="/tmp/s97c_out_${RUN_TAG}.txt"
  DRIVER_C="/tmp/s97c_driver_${RUN_TAG}.py"
  RUNLOG_C="/tmp/s97c_run_${RUN_TAG}.log"

  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c "cat > $DRIVER_C" <<'PY'
import asyncio, os, traceback

from fastapi import Response

import bootstrap_admin
import main as main_app

OUT = os.environ["S97_OUT"]

results = []


def record(name, ok, detail=""):
    results.append((name, bool(ok), detail))


async def main():
    try:
        # This is a SEPARATE process from the one serving traffic, so its bootstrap_state
        # is its own: failing it here cannot make the live /ready flap.
        raised = None
        try:
            returned = await bootstrap_admin.ensure_platform_admin()
        except Exception as exc:                      # noqa: BLE001 — that is the assertion
            returned = None
            raised = "%s: %s" % (type(exc).__name__, exc)

        state = bootstrap_admin.bootstrap_state
        record(
            "T-S97-005 NON-FATAL: with Keycloak down the bootstrap FAILS SOFT — returns "
            "False, records the cause, never raises",
            returned is False and raised is None and state.ok is False and bool(state.last_error),
            f"ensure_platform_admin() -> {returned} (want False) raised={raised!r} (want None — "
            f"a raise here would crash-loop the pod from lifespan) bootstrap_state.ok={state.ok} "
            f"last_error={state.last_error!r} attempts={state.attempts}",
        )

        # The /ready contract, read through the REAL handler in the state a REAL outage
        # produced — not a stubbed flag. Pulled off app.routes because `ready` is defined
        # inside create_app and has no importable name.
        ready_ep = next(
            r.endpoint for r in main_app.app.routes if getattr(r, "path", None) == "/ready"
        )
        resp = Response()
        body = await ready_ep(resp)
        record(
            "T-S97-006 /ready CONTRACT: 503 {status=bootstrapping, detail, attempts} while the "
            "admin is not pinned",
            resp.status_code == 503
            and body.get("status") == "bootstrapping"
            and bool(body.get("detail"))
            and "attempts" in body,
            f"status={resp.status_code} (want 503) body={body} (want status=bootstrapping plus a "
            f"detail naming the cause and an attempts counter — contract: "
            f"docs/plan/rbac-r0-r1/contracts/bootstrap-and-ready.md)",
        )
    except Exception as exc:
        record("T-S97-005 NON-FATAL driver", False,
               f"CRASHED: {type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        passed = sum(1 for _, ok, _ in results if ok)
        with open(OUT, "w") as f:
            for name, ok, detail in results:
                f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")


asyncio.run(main())
PY

  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c \
    "cd /app && PYTHONPATH=/app S97_OUT=$OUT_C python3 $DRIVER_C > $RUNLOG_C 2>&1" || true

  # Restore Keycloak BEFORE reporting: everything after this phase needs a token, and a
  # suite that reports a result while the platform is still down is reporting on a
  # cluster it broke.
  echo "      restoring $KC_WORKLOAD -> $KC_REPLICAS replicas"
  kubectl scale "$KC_WORKLOAD" -n "$NAMESPACE" --replicas="$KC_REPLICAS" >/dev/null 2>&1 || true
  kubectl rollout status "$KC_WORKLOAD" -n "$NAMESPACE" --timeout=600s >/dev/null 2>&1 || true

  # /ready is 200 again within 3 retry intervals (bootstrap_admin_loop's 30s), which is
  # the recovery half of the availability claim.
  READY_AFTER_CODE=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    READY_AFTER_CODE="$(http_code /ready)" || true
    [ "$READY_AFTER_CODE" = "200" ] && break
    sleep 10
  done

  BAD_HEALTH="$(printf '%s' "$HEALTH_CODES" | tr ' ' '\n' | grep -v '^200$' | grep -v '^$' | tr '\n' ',' || true)"
  if [ "$RESTARTS_BEFORE" = "$RESTARTS_AFTER" ] && [ -z "$BAD_HEALTH" ] && [ "$READY_AFTER_CODE" = "200" ]; then
    record_host PASS "T-S97-005 NON-FATAL (pod level): a 90s Keycloak outage neither restarted the pod nor blinked /health  |  restartCount ${RESTARTS_BEFORE} -> ${RESTARTS_AFTER} (want unchanged) /health samples:${HEALTH_CODES} (want all 200) /ready after recovery=${READY_AFTER_CODE}"
  else
    record_host FAIL "T-S97-005 NON-FATAL (pod level): a 90s Keycloak outage neither restarted the pod nor blinked /health  |  restartCount ${RESTARTS_BEFORE} -> ${RESTARTS_AFTER} (want unchanged) /health samples:${HEALTH_CODES} non-200:[${BAD_HEALTH}] /ready after recovery=${READY_AFTER_CODE} (want 200 within 3 retry intervals)"
  fi

  if [ "$READY_BEFORE_CODE" = "200" ] && [ "$READY_BEFORE_BODY" = '{"status":"ready"}' ]; then
    record_host PASS "T-S97-006 /ready CONTRACT (200 leg): a pinned admin answers exactly {\"status\":\"ready\"}  |  ${READY_BEFORE_CODE} ${READY_BEFORE_BODY}"
  else
    record_host FAIL "T-S97-006 /ready CONTRACT (200 leg): a pinned admin answers exactly {\"status\":\"ready\"}  |  got ${READY_BEFORE_CODE} ${READY_BEFORE_BODY} — contract: docs/plan/rbac-r0-r1/contracts/bootstrap-and-ready.md"
  fi

  collect "$OUT_C" "$RUNLOG_C"
fi

# Re-prove authentication before the destructive phase. If Keycloak did not come back,
# deleting the admin now would compound one outage into two — so this is a guard, not an
# `e2e_require_token` abort: aborting here would also throw away the eleven results
# already collected and the gate that reports which cases never ran.
KEYCLOAK_BACK="no"
if TOKEN_CHECK="$(e2e_token "$NAMESPACE" "$API_POD")" && [ -n "$TOKEN_CHECK" ]; then
  KEYCLOAK_BACK="yes"
  e2e_install_pyauth "$NAMESPACE" "$API_POD"
fi

# ── Phases 5-8 — T-S97-004, the realm-recreation re-pin (DESTRUCTIVE, LAST) ─────
# Wrapped in a function so a setup failure RETURNS instead of exiting: the eleven cases
# above have already produced results, and an `exit 1` here would throw them away along
# with the completeness gate that is supposed to notice.
run_repin_case() {
  local OUTFILE="/tmp/s97_out_${RUN_TAG}.txt"
  local DRIVER="/tmp/s97_driver_${RUN_TAG}.py"
  local RUNLOG="/tmp/s97_run_${RUN_TAG}.log"
  local SUB_BEFORE OLD_POD

  # ── Phase 5 — record the sub we are about to destroy ──────────────────────────
  # By USERNAME, the same way the bootstrap finds it. Reading it from the DB instead
  # would assume the row is already correct, which is the very thing under test.
  echo ""
  echo "[5/9] Recording the live platform-admin sub (lookup by username)…"
  # IMAGE-AGNOSTIC ON PURPOSE. This SETUP must run identically against the PRE-fix image
  # and the post-fix one, because T-S97-004 is the regression test for the 2026-07-20
  # incident and CLAUDE.md rule 7 requires it to go RED against the buggy code. An earlier
  # version called kc.list_users(username=…, exact=True) — which is T002, i.e. part of the
  # very fix under test. On the pre-fix image that raises TypeError, so the case could only
  # ever fail with "the fix isn't deployed" and never reproduce the DEFECT. Filtering the
  # unfiltered list client-side works on both images and costs one extra page.
  SUB_BEFORE="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- \
    bash -c 'cd /app && PYTHONPATH=/app python3 -c "
import asyncio, keycloak_client as kc
async def main():
    users = await kc.list_users()
    exact = [u for u in users if u.get(\"username\") == \"platform-admin\"]
    assert len(exact) == 1, f\"expected exactly 1 platform-admin, found {len(exact)}\"
    print(exact[0][\"id\"])
asyncio.run(main())
"' 2>/dev/null | tr -d '\r\n')"

  if [ -z "$SUB_BEFORE" ]; then
    record_host FAIL "T-S97-004 realm-recreation re-pin  |  SETUP: could not resolve the live platform-admin sub by username — refusing to delete anything. Check that Keycloak is reachable in-cluster and the realm holds exactly one 'platform-admin'."
    return 1
  fi
  echo "      sub_before = $SUB_BEFORE"

  # ── Phase 6 — recreate the realm's effect on this user: delete it ─────────────
  # Deleting the user is the smallest faithful reproduction of "the realm was
  # recreated": the durable row is left pinned to a subject Keycloak will never issue
  # again. Recreating the whole realm would also destroy the four clients and take the
  # platform down for minutes — the assertion is about the SUB, not about the realm.
  echo "[6/9] Deleting the Keycloak platform-admin (the realm-recreation effect)…"
  if ! kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- \
    bash -c "cd /app && PYTHONPATH=/app python3 -c \"
import asyncio, keycloak_client as kc
asyncio.run(kc.delete_user('${SUB_BEFORE}'))
print('deleted')
\""; then
    record_host FAIL "T-S97-004 realm-recreation re-pin  |  SETUP: delete_user('${SUB_BEFORE}') failed"
    return 1
  fi

  # ── Phase 7 — restart, and let /ready do the waiting ──────────────────────────
  # The readinessProbe is GET /ready (deployment.yaml:259-262) and /ready is 503
  # "bootstrapping" until ensure_platform_admin succeeds — so `rollout status`
  # completing IS the bootstrap having succeeded. A pre-R0 image never becomes ready
  # here and the rollout times out, which is the correct RED signal.
  echo "[7/9] Restarting $DEPLOY and waiting for readiness (=> bootstrap succeeded)…"
  OLD_POD="$API_POD"
  kubectl rollout restart "deployment/$DEPLOY" -n "$NAMESPACE" >/dev/null
  if ! kubectl rollout status "deployment/$DEPLOY" -n "$NAMESPACE" --timeout=600s; then
    record_host FAIL "T-S97-004 realm-recreation re-pin  |  the rolled-out pod never became READY. /ready gates on the platform-admin bootstrap, so this means ensure_platform_admin did not succeed."
    kubectl logs -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api -c "$CONTAINER" \
      --tail=40 2>/dev/null | sed 's/^/      /' || true
    return 1
  fi

  API_POD="$(resolve_pod)" || true
  if [ -z "$API_POD" ]; then
    record_host FAIL "T-S97-004 realm-recreation re-pin  |  no Running registry-api pod after rollout"
    return 1
  fi
  echo "      pod: $OLD_POD -> $API_POD"
  e2e_install_pyauth "$NAMESPACE" "$API_POD"

  # ── Phase 8 — assert the re-pin, all four legs, in-pod ────────────────────────
  echo "[8/9] Asserting the re-pin (new sub, row on the new sub, /me role)…"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, os, sys, traceback
sys.path.insert(0, "/tmp")

import httpx
from sqlalchemy import text

from db import AsyncSessionLocal
import keycloak_client as kc

OUT = os.environ["S97_OUT"]
SUB_BEFORE = os.environ["S97_SUB_BEFORE"]
ADMIN_USERNAME = "platform-admin"


async def main():
    results = []

    def record(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    try:
        # (a) exactly one platform-admin exists AGAIN
        users = await kc.list_users(username=ADMIN_USERNAME, exact=True)
        exact = [u for u in users if u.get("username") == ADMIN_USERNAME]
        sub_after = exact[0]["id"] if len(exact) == 1 else None

        # (c) the row followed the user onto its NEW id
        row = None
        if sub_after:
            async with AsyncSessionLocal() as s:
                row = (await s.execute(
                    text("SELECT team_name, role, assigned_by FROM "
                         "user_team_assignments WHERE user_sub = :u"),
                    {"u": sub_after},
                )).mappings().first()

        # (d) the symptom itself: a REAL password grant for the recreated admin, and
        # the role /me answers. Everything above can be right while this is wrong —
        # that combination is exactly what 2026-07-20 looked like from the browser.
        me_status = None
        me_role = None
        me_team = None
        if sub_after:
            from e2e_auth import mint
            token = mint()
            async with httpx.AsyncClient(
                base_url="http://localhost:8000/api/v1", timeout=30.0
            ) as c:
                r = await c.get("/me", headers={"Authorization": f"Bearer {token}"})
                me_status = r.status_code
                if r.status_code == 200:
                    body = r.json()
                    me_role = body.get("role")
                    me_team = body.get("team")

        ok = (
            len(exact) == 1
            and sub_after is not None
            and sub_after != SUB_BEFORE
            and row is not None
            and row["role"] == "platform-admin"
            and row["team_name"] == "platform"
            and row["assigned_by"] == "system:bootstrap"
            and me_status == 200
            and me_role == "platform-admin"
        )
        record(
            "T-S97-004 REALM-RECREATION RE-PIN: the platform re-creates its admin and "
            "re-pins the role row onto the NEW sub",
            ok,
            f"kc_users={len(exact)} (want 1) sub_before={SUB_BEFORE} sub_after={sub_after} "
            f"re_pinned={sub_after is not None and sub_after != SUB_BEFORE} | "
            f"row={dict(row) if row else None} (want team=platform role=platform-admin "
            f"assigned_by=system:bootstrap — assigned_by proves the LIFESPAN BOOTSTRAP "
            f"wrote it, not seed-platform-admin-role.sh) | "
            f"GET /me -> {me_status} role={me_role!r} team={me_team!r} (want 200 / "
            f"'platform-admin' — this is the leg the Admin menu is gated on)",
        )

        # Housekeeping, not an assertion: the row this test stranded on the DELETED
        # sub is exactly the G-R0-3 litter the platform deliberately never deletes
        # (GET /api/v1/admin/identity-audit REPORTS, it does not clean). The suite
        # made it, so the suite removes it — otherwise every later run of the
        # identity-audit case would see litter this suite manufactured.
        async with AsyncSessionLocal() as s:
            await s.execute(
                text("DELETE FROM user_team_assignments WHERE user_sub = :u"),
                {"u": SUB_BEFORE},
            )
            await s.commit()

    except Exception as exc:
        record("T-S97-999 driver ran every case without crashing", False,
               f"driver CRASHED: {type(exc).__name__}: {exc} :: "
               f"{traceback.format_exc()[-400:]}")
    finally:
        passed = sum(1 for _, ok, _ in results if ok)
        with open(OUT, "w") as f:
            for name, ok, detail in results:
                f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")


asyncio.run(main())
PY

  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- bash -c \
    "cd /app && PYTHONPATH=/app S97_OUT=$OUTFILE S97_SUB_BEFORE=$SUB_BEFORE python3 $DRIVER > $RUNLOG 2>&1" \
    || true

  collect "$OUTFILE" "$RUNLOG"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- \
    rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true
  return 0
}

if [ "$KEYCLOAK_BACK" = "yes" ]; then
  run_repin_case || true
else
  record_host FAIL "T-S97-004 realm-recreation re-pin  |  SETUP: no ${E2E_KC_USER} token after the T-S97-005 outage phase, so Keycloak did not come back. REFUSING to delete the admin on a cluster whose IdP is already down — that turns one outage into two and leaves nothing able to recreate the user."
fi

# ── Phase 9 — completeness gate ─────────────────────────────────────────────────
# A silently dropped case is the failure mode this repo keeps paying for: the suite
# reports green because the assertion never ran. Name the IDs that MUST appear.
# T-S97-011 is absent on purpose — see the header. Add it here with its case.
echo ""
echo "[9/9] Completeness gate…"
REQUIRED_IDS="001 002 003 004 005 006 007 008 009 010 012"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$ALL_RESULTS" | grep -q "T-S97-$id " || MISSING="$MISSING T-S97-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S97-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING"
  FAIL=$((FAIL+1))
else
  echo "PASS  T-S97-COMPLETE every gate assertion ran ($REQUIRED_IDS — none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- \
  rm -f "/tmp/s97a_driver_${RUN_TAG}.py" "/tmp/s97a_out_${RUN_TAG}.txt" "/tmp/s97a_run_${RUN_TAG}.log" \
        "/tmp/s97b_driver_${RUN_TAG}.py" "/tmp/s97b_out_${RUN_TAG}.txt" "/tmp/s97b_run_${RUN_TAG}.log" \
        "/tmp/s97c_driver_${RUN_TAG}.py" "/tmp/s97c_out_${RUN_TAG}.txt" "/tmp/s97c_run_${RUN_TAG}.log" \
  2>/dev/null || true

echo ""; echo "=== suite-97 summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -ne 0 ] && { echo "SUITE 97 FAILED"; exit 1; }
[ "$PASS" -eq 0 ] && { echo "SUITE 97 INCONCLUSIVE"; exit 1; }
echo "SUITE 97 PASSED"

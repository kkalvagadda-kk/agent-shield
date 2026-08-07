#!/usr/bin/env bash
# scripts/e2e/suite-98-rbac-role-enforcement.sh
#
# E2E Suite 98: RBAC R2 + R3 — GLOBAL ROLE + ARTIFACT-SCOPED ENFORCEMENT.
#
# STATUS: R2 SHIPPED 2026-08-06 (registry-api 0.2.263). This suite was written FIRST and
# ran RED against 0.2.261/0.2.262; it must now be GREEN and stay green.
# ------------------------------------------------------------------------------
# Every case here asserts a 403 the platform did not return before R2. That RED-first
# run is the CLAUDE.md DoD rule 7 evidence, and this repo has already paid for skipping
# it: docs/testing/manual-ui-e2e-test-plan.md G-R0-9 records a test that was red from
# the day it was written, never once passed, and went unnoticed for months because
# nothing could run it.
#
# WHY THIS SUITE HAD TO BE BUILT BEFORE R2, NOT AFTER
# ---------------------------------------------------
# 61 e2e suites mint a Keycloak token. EVERY ONE authenticates as `platform-admin`.
# Four use `agent-reviewer`, which is itself created as a *contributor*. No suite has
# ever logged in as a `consumer`, and suite-42-rbac — the RBAC suite — contains ZERO
# 403 assertions: its seven cases are structural (table exists, creator auto-grant,
# /me shape, role normalization, idempotency).
#
# So if R2 flipped `rbac.py` ENFORCE and wired require_global_role("platform-admin")
# tomorrow, the ENTIRE suite would stay green whether enforcement worked or not —
# because every caller already IS the role that passes every check. A guard that
# cannot fail is the defect this repo keeps rediscovering. This suite is the one that
# can fail.
#
# RED BASELINE, captured on the live EKS cluster 2026-08-06 against 0.2.262, immediately
# before R2's image rolled — this is the DoD rule 7 artifact, a real run and not an
# inference:
#     T-S98-001  consumer    GET  /api/v1/admin/users          -> 200   (want 403)
#     T-S98-002  contributor GET  /api/v1/admin/users          -> 200   (want 403)
#     T-S98-004  consumer    POST /api/v1/agents/              -> 409*  (want 403)
#     T-S98-006  consumer    GET  /api/v1/admin/teams-summary  -> 200   (want 403)
#     T-S98-007  consumer    GET  /api/v1/me/team              -> 404   (want 200)
#     T-S98-008  contributor GET  /api/v1/users/directory      -> 404   (want 200)
#     T-S98-003 and T-S98-005 passed, as they must — they are the over-reach guards.
# *409, not 201, and that is why T-S98-004 now uses a unique name: an EARLIER pre-R2 run
# had actually created `s98-should-not-exist`, so the uniqueness check answered before
# authorization would have and the case reported the wrong reason. See its comment.
#
# MEASURED ON THE LIVE CLUSTER, 2026-08-06, against registry-api 0.2.261 (R1 shipped):
#     e2e-consumer  ->  GET /api/v1/admin/users  ->  200
# A `consumer` — the lowest global role — can enumerate every user on the platform.
# R1 closed "no token at all". "ANY token is enough" is still wide open. That is not a
# new hole; it is §1.2/§1.3 of rbac-and-artifact-authorization.md (require_global_role
# is an orphan, ENFORCE=False) made visible by an authenticated non-admin caller,
# which nothing previously was.
#
# WHAT R2 DID
#   - deleted the `ENFORCE = False` closure-local in rbac.require_global_role — the
#     factory now enforces unconditionally (a permanently-true flag is dead config)
#   - wired require_global_role("platform-admin") onto admin.py + admin_users.py
#   - wired can_create_agent onto POST /agents/, its first call site (§1.3), and
#     deleted that handler's X-User-Sub identity fallback
#   - SPLIT /admin/teams-summary. It was read by the Studio sidebar and My Agents for
#     EVERY role, so locking the census as-is would have silently emptied "Shared With
#     Me" platform-wide. The census stays admin-only; GET /api/v1/me/team answers the
#     self-scoped question. Same reasoning produced GET /api/v1/users/directory for the
#     artifact grant picker, which a contributor must be able to use (design §2).
#
# WHAT R3 DID (2026-08-07, registry-api 0.2.264) — cases 011-016
#   - `_require_manage` on PATCH/PUT, DELETE and POST /publish: platform-admin OR
#     `agent-admin` on that artifact. All three were FULLY UNAUTHENTICATED. R2 closed a
#     read disclosure on /admin/* while these destructive writes stayed anonymous, which
#     made them the biggest remaining hole once R2 landed.
#   - quarantine (POST + DELETE) -> platform-admin ONLY, not agent-admin: it is applied
#     TO an owner, often because of what their agent did, so an owner who could lift it
#     makes it advisory.
#   - `can_deploy_to_production` on the PRODUCTION branch of deploy only — sandbox stays
#     contributor+ so the build/evaluate loop does not become admin-only.
#   - `can_use_playground` for VERIFIED users (OQ-3 resolved: contributor+).
#   All three were orphans with zero callers (§1.3); §1.3's list is now closed except
#   can_approve_hitl, which is R5.
#
# CASES
#   T-S98-001 — a CONSUMER is refused the admin surface        (GET /admin/users -> 403)
#   T-S98-002 — a CONTRIBUTOR is refused the admin surface     (GET /admin/users -> 403)
#   T-S98-003 — platform-admin is STILL allowed                (GET /admin/users -> 200)
#   T-S98-004 — a CONSUMER is refused agent creation           (POST /agents/ -> 403)
#   T-S98-005 — anonymous is still 401, not 403 (R1 unchanged) (GET /admin/users -> 401)
#   T-S98-006 — the CENSUS is admin-only          (consumer GET /admin/teams-summary -> 403)
#   T-S98-007 — the SELF-SCOPED view is not       (consumer GET /me/team -> 200)
#   T-S98-008 — the grant picker still works   (contributor GET /users/directory -> 200)
#   T-S98-009 — the directory leaks no more than a name  (no email/role/team/enabled)
#   T-S98-010 — a CONTRIBUTOR may still create an agent      (POST /agents/ -> 201)
#   T-S98-011 — ANONYMOUS cannot delete an agent             (DELETE /agents/{n} -> 401)
#   T-S98-012 — a NON-OWNER contributor cannot delete it     (DELETE -> 403)
#   T-S98-013 — a NON-OWNER contributor cannot edit it       (PATCH  -> 403)
#   T-S98-014 — quarantine is platform-admin ONLY            (contributor -> 403)
#   T-S98-015 — the OWNER may still edit their own agent     (PATCH  -> 200)
#   T-S98-016 — a CONSUMER is refused the playground         (POST /playground/runs -> 403)
#
# T-S98-003, -005, -007, -008, -010 and -015 are the guards that stop an over-broad "fix": R2/R3
# must deny the wrong role WITHOUT denying the right one, must not turn authentication
# into authorization, and must not take the self-scoped reads down with the census. A
# change that 403s everybody would pass 001/002/004/006 alone.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
CONTAINER="registry-api"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || { echo "ERROR: no Running registry-api pod in $NAMESPACE"; exit 1; }

echo "=== Suite 98: RBAC R2 + R3 role enforcement ==="
echo "  Namespace: $NAMESPACE"
echo "  Pod:       $API_POD"
echo ""

PASS=0; FAIL=0
record() {  # record <PASS|FAIL> <text>
  if [ "$1" = "PASS" ]; then echo "PASS  $2"; PASS=$((PASS+1)); else echo "FAIL  $2"; FAIL=$((FAIL+1)); fi
}

echo "[1/2] Ensuring role personas exist (created through the REAL admin API)…"
CONSUMER_TOK="$(e2e_ensure_persona "$NAMESPACE" "$API_POD" "e2e-consumer" "consumer" "$CONTAINER" || true)"
CONTRIB_TOK="$(e2e_ensure_persona  "$NAMESPACE" "$API_POD" "e2e-contributor" "contributor" "$CONTAINER" || true)"
e2e_set_token "$NAMESPACE" "$API_POD"
ADMIN_TOK="$E2E_TOKEN"

if [ -z "$CONSUMER_TOK" ] || [ -z "$CONTRIB_TOK" ] || [ -z "$ADMIN_TOK" ]; then
  record FAIL "T-S98-SETUP personas + admin token available  |  consumer=${#CONSUMER_TOK} contributor=${#CONTRIB_TOK} admin=${#ADMIN_TOK} (all must be non-empty)"
  echo ""; echo "=== Suite 98 Results: PASS=$PASS FAIL=$FAIL ==="; exit 1
fi
record PASS "T-S98-SETUP personas + admin token available  |  three distinct Keycloak identities, each created through POST /api/v1/admin/users and re-pinned to its stated global role"

echo ""
echo "[2/2] Role matrix…"

# status <token-or-empty> <method> <path> [json-body]
status() {
  local tok="$1" method="$2" path="$3" body="${4:-}"
  kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- env \
    T="$tok" M="$method" P="$path" B="$body" python3 -c '
import os, urllib.request, urllib.error
h={}
if os.environ["T"]: h["Authorization"]="Bearer "+os.environ["T"]
d=None
if os.environ["B"]:
    d=os.environ["B"].encode(); h["Content-Type"]="application/json"
req=urllib.request.Request("http://localhost:8000"+os.environ["P"], method=os.environ["M"], data=d, headers=h)
try:
    with urllib.request.urlopen(req, timeout=20) as r: print(r.status)
except urllib.error.HTTPError as e: print(e.code)
except Exception: print(0)
' 2>/dev/null | tr -d '\r\n'
}

C1="$(status "$CONSUMER_TOK" GET /api/v1/admin/users)"
[ "$C1" = "403" ] \
  && record PASS "T-S98-001 CONSUMER is refused the admin surface  |  GET /admin/users -> 403" \
  || record FAIL "T-S98-001 CONSUMER is refused the admin surface  |  GET /admin/users -> $C1 (want 403). A consumer can enumerate every user on the platform. R1 authenticated the caller; nothing yet checks WHICH caller. require_global_role is built and has zero call sites (rbac-and-artifact-authorization.md §1.3) and its ENFORCE flag is False (§1.2)."

C2="$(status "$CONTRIB_TOK" GET /api/v1/admin/users)"
[ "$C2" = "403" ] \
  && record PASS "T-S98-002 CONTRIBUTOR is refused the admin surface  |  GET /admin/users -> 403" \
  || record FAIL "T-S98-002 CONTRIBUTOR is refused the admin surface  |  GET /admin/users -> $C2 (want 403)"

C3="$(status "$ADMIN_TOK" GET /api/v1/admin/users)"
[ "$C3" = "200" ] \
  && record PASS "T-S98-003 PLATFORM-ADMIN is still allowed  |  GET /admin/users -> 200 (R2 must deny the wrong role WITHOUT denying the right one)" \
  || record FAIL "T-S98-003 PLATFORM-ADMIN is still allowed  |  GET /admin/users -> $C3 (want 200) — an over-broad R2 that 403s everybody would satisfy 001/002 and break the platform"

# UNIQUE name per run, and that is load-bearing. This case used the fixed name
# `s98-should-not-exist`, and the pre-R2 baseline run on 2026-08-06 returned **409**
# instead of the expected 200 — because an EARLIER pre-R2 run had actually created it.
# The name was already taken, so the uniqueness check answered before authorization
# would have, and the case reported the wrong reason for failing. Post-R2 the role gate
# runs first and a stale row could hide a regression the same way. A name that cannot
# pre-exist means 403 is the only passing answer, a broken build yields 201 (loud), and
# a working build writes nothing — so there is no litter to clean up either.
S98_DENIED_AGENT="s98-denied-$$-${RANDOM}"
C4="$(status "$CONSUMER_TOK" POST /api/v1/agents/ "{\"name\":\"${S98_DENIED_AGENT}\",\"description\":\"role gate probe\",\"team\":\"platform\"}")"
case "$C4" in
  403) record PASS "T-S98-004 CONSUMER is refused agent creation  |  POST /agents/ -> 403" ;;
  201) record FAIL "T-S98-004 CONSUMER is refused agent creation  |  POST /agents/ -> 201 — the agent WAS created. can_create_agent has no caller on this route." ;;
  *)   record FAIL "T-S98-004 CONSUMER is refused agent creation  |  POST /agents/ -> $C4 (want 403)." ;;
esac

C5="$(status "" GET /api/v1/admin/users)"
[ "$C5" = "401" ] \
  && record PASS "T-S98-005 ANONYMOUS is still 401, not 403  |  R1's authentication layer is unchanged by R2" \
  || record FAIL "T-S98-005 ANONYMOUS is still 401, not 403  |  GET /admin/users -> $C5 (want 401) — R2 must not turn authentication into authorization"

# ── The teams-summary split. These two cases are one decision, asserted from both
# sides: the full-org census must close AND the self-scoped read must stay open. Only
# checking the 403 would let R2 pass while "Shared With Me" was empty for every
# non-admin in the product — the exact silent-breakage class that
# docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md is about.
C6="$(status "$CONSUMER_TOK" GET /api/v1/admin/teams-summary)"
[ "$C6" = "403" ] \
  && record PASS "T-S98-006 the ORG CENSUS is admin-only  |  consumer GET /admin/teams-summary -> 403 (it lists every team, its members and its grants)" \
  || record FAIL "T-S98-006 the ORG CENSUS is admin-only  |  consumer GET /admin/teams-summary -> $C6 (want 403)"

C7="$(status "$CONSUMER_TOK" GET /api/v1/me/team)"
[ "$C7" = "200" ] \
  && record PASS "T-S98-007 the SELF-SCOPED team view stays open  |  consumer GET /me/team -> 200 (backs the sidebar's Shared With Me for every role)" \
  || record FAIL "T-S98-007 the SELF-SCOPED team view stays open  |  consumer GET /me/team -> $C7 (want 200). R2 closed the census; if this is not open, the sidebar section is empty for every non-admin and the lock-down was a silent regression."

C8="$(status "$CONTRIB_TOK" GET /api/v1/users/directory)"
[ "$C8" = "200" ] \
  && record PASS "T-S98-008 the grant picker still works for a non-admin  |  contributor GET /users/directory -> 200 (an agent-admin may delegate on their own artifact — design §2)" \
  || record FAIL "T-S98-008 the grant picker still works for a non-admin  |  contributor GET /users/directory -> $C8 (want 200)"

# The directory exists because /admin/users closed. If it returns what /admin/users
# returned, R2 moved the disclosure rather than removing it.
#
# The whole pipeline is `|| true`-guarded and the Python swallows its own errors. The
# 2026-08-06 pre-R2 baseline run proved why: against 0.2.262 this endpoint 404s,
# urlopen raised, and under `set -euo pipefail` that KILLED THE SUITE — T-S98-010 never
# ran and the run reported 8 cases instead of 10. A probe that aborts the harness hides
# every case after it, which is worse than the failure it was reporting.
LEAK="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- env T="$CONTRIB_TOK" python3 -c '
import json, os, urllib.request
banned = {"email", "role", "team", "enabled", "kc_id", "first_name", "last_name"}
try:
    req = urllib.request.Request("http://localhost:8000/api/v1/users/directory",
                                 headers={"Authorization": "Bearer " + os.environ["T"]})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.loads(r.read())
    print(",".join(sorted({k for row in rows for k in row if k in banned})) or "clean")
except Exception as exc:
    print(f"unreadable:{type(exc).__name__}")
' 2>/dev/null | tr -d "\r\n" || true)"
[ -n "$LEAK" ] || LEAK="unreadable:no-output"
[ "$LEAK" = "clean" ] \
  && record PASS "T-S98-009 the directory leaks no more than a name  |  no email / role / team / enabled field — it is a name picker, not a user export" \
  || record FAIL "T-S98-009 the directory leaks no more than a name  |  returned banned fields: $LEAK. /users/directory is readable by EVERY authenticated role; if it carries what /admin/users carried, R2 relocated the hole instead of closing it."

# The other half of T-S98-004: can_create_agent must deny a consumer WITHOUT denying
# the role whose whole purpose is creating things.
#
# 201 OR 409 both pass, and that is not a hedge — it is what makes this case leave NO
# litter. `create_agent` runs the role gate BEFORE the uniqueness check, so reaching a
# 409 proves authorization succeeded just as a 201 does. A fixed name therefore creates
# exactly one row ever: the first run creates it, every later run gets 409 off the same
# row. `DELETE /agents/{name}` is a SOFT delete (status='deprecated', name still taken),
# so a create/delete pair would not have cleaned up either — it would have left a
# deprecated row per run and then 409'd anyway. G-R0-4 is that defect in suite-53 and
# suite-71; a suite that regenerates litter makes cleaning the cluster a one-time
# illusion. A 403 here still fails, which is the whole point.
S98_AGENT="s98-contrib-create-probe"
C10="$(status "$CONTRIB_TOK" POST /api/v1/agents/ "{\"name\":\"${S98_AGENT}\",\"description\":\"suite-98 role gate probe\",\"team\":\"platform\"}")"
case "$C10" in
  201|409) record PASS "T-S98-010 a CONTRIBUTOR may still create an agent  |  POST /agents/ -> $C10 (201 first run, 409 after — both mean the role gate let them through)" ;;
  *)       record FAIL "T-S98-010 a CONTRIBUTOR may still create an agent  |  POST /agents/ -> $C10 (want 201 or 409). can_create_agent is contributor+, not admin-only — a 403 here means R2 over-reached and only platform-admin can build anything." ;;
esac

# ── R3 (2026-08-07): the artifact-scoped mutations + the deploy/playground gates ──
# These were fully UNAUTHENTICATED before R3 — anyone reaching the API could rename,
# soft-delete or quarantine any agent. R2 closed a read disclosure on /admin/* while
# these destructive writes stayed open, which made them the biggest remaining hole.
S98_R3_AGENT="s98-r3-victim"
status "$ADMIN_TOK" POST /api/v1/agents/ "{\"name\":\"${S98_R3_AGENT}\",\"description\":\"suite-98 R3 target (admin-owned)\",\"team\":\"platform\"}" >/dev/null 2>&1 || true

C11="$(status "" DELETE "/api/v1/agents/${S98_R3_AGENT}")"
[ "$C11" = "401" ] \
  && record PASS "T-S98-011 ANONYMOUS cannot delete an agent  |  DELETE /agents/{name} -> 401 (it also terminates deployments and disarms triggers)" \
  || record FAIL "T-S98-011 ANONYMOUS cannot delete an agent  |  DELETE /agents/{name} -> $C11 (want 401)"

C12="$(status "$CONTRIB_TOK" DELETE "/api/v1/agents/${S98_R3_AGENT}")"
[ "$C12" = "403" ] \
  && record PASS "T-S98-012 a NON-OWNER contributor cannot delete someone else's agent  |  -> 403 (contributor+ is not enough; needs agent-admin ON IT)" \
  || record FAIL "T-S98-012 a NON-OWNER contributor cannot delete someone else's agent  |  -> $C12 (want 403)"

C13="$(status "$CONTRIB_TOK" PATCH "/api/v1/agents/${S98_R3_AGENT}" '{"description":"hijacked"}')"
[ "$C13" = "403" ] \
  && record PASS "T-S98-013 a NON-OWNER contributor cannot edit someone else's agent  |  PATCH -> 403" \
  || record FAIL "T-S98-013 a NON-OWNER contributor cannot edit someone else's agent  |  PATCH -> $C13 (want 403)"

C14="$(status "$CONTRIB_TOK" POST "/api/v1/agents/${S98_R3_AGENT}/quarantine")"
[ "$C14" = "403" ] \
  && record PASS "T-S98-014 quarantine is platform-admin ONLY  |  contributor POST /quarantine -> 403 (an owner must not be able to lift a quarantine applied against them)" \
  || record FAIL "T-S98-014 quarantine is platform-admin ONLY  |  contributor POST /quarantine -> $C14 (want 403)"

# The over-reach guard for the whole R3 block: the OWNER must still be able to manage
# their own artifact. A change that 403s everyone passes 011-014 and breaks the product.
S98_OWNED="s98-r3-owned"
status "$CONTRIB_TOK" POST /api/v1/agents/ "{\"name\":\"${S98_OWNED}\",\"description\":\"suite-98 R3 target (contributor-owned)\",\"team\":\"platform\"}" >/dev/null 2>&1 || true
C15="$(status "$CONTRIB_TOK" PATCH "/api/v1/agents/${S98_OWNED}" '{"description":"owner edit"}')"
[ "$C15" = "200" ] \
  && record PASS "T-S98-015 the OWNER may still edit their own agent  |  PATCH -> 200 (creator auto-grant gives agent-admin)" \
  || record FAIL "T-S98-015 the OWNER may still edit their own agent  |  PATCH -> $C15 (want 200) — R3 over-reached; a contributor cannot manage what they created."

C16="$(status "$CONSUMER_TOK" POST /api/v1/playground/runs "{\"agent_name\":\"${S98_OWNED}\",\"message\":\"probe\"}")"
[ "$C16" = "403" ] \
  && record PASS "T-S98-016 a CONSUMER is refused the playground  |  POST /playground/runs -> 403 (OQ-3 resolved: contributor+)" \
  || record FAIL "T-S98-016 a CONSUMER is refused the playground  |  POST /playground/runs -> $C16 (want 403). can_use_playground had zero callers before R3."

echo ""
echo "=== Suite 98 Results: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "  R2 shipped in registry-api 0.2.263. A failure here is a REGRESSION, not the"
  echo "  pre-R2 baseline — check rbac.require_global_role is still wired onto admin.py"
  echo "  and admin_users.py, and that the teams-summary split (T-S98-006/007) held."
  exit 1
fi

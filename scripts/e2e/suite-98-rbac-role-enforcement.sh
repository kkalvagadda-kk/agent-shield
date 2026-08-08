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
# R3 RED BASELINE, captured on the live EKS cluster 2026-08-07 against 0.2.263 (R2 shipped,
# R3 not yet), immediately before R3's image rolled:
#     T-S98-011  anonymous   DELETE /api/v1/agents/{name}      -> 204   (want 401)  ** the
#                agent was ACTUALLY DELETED by an unauthenticated caller **
#     T-S98-012  contributor DELETE /api/v1/agents/{name}      -> 204   (want 403)
#     T-S98-013  contributor PATCH  /api/v1/agents/{name}      -> 200   (want 403)
#     T-S98-014  contributor POST   /api/v1/agents/{n}/quarantine -> 200 (want 403)
#     T-S98-015 passed, as it must — it is the over-reach guard.
#     T-S98-016 ALSO passed, which is why it was rewritten: see its comment. The baseline
#     is what caught it asserting nothing.
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
#   T-S98-016 — a CONSUMER is refused the playground BY THE ROLE GATE (403 naming the
#               contributor role — NOT by the pre-existing owner check, which already
#               answers 403 and made a status-only assertion pass against a pre-R3 image)
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

# status_body <token> <method> <path> [json-body]  ->  "<code>|<response body>"
# T-S98-016 needs the REASON, not just the code: two independent guards on the playground
# route both answer 403, and a case that cannot tell them apart proves neither. The R3 RED
# baseline caught exactly that — see T-S98-016's comment.
status_body() {
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
    with urllib.request.urlopen(req, timeout=20) as r: print(str(r.status)+"|"+r.read(400).decode("utf-8","replace"))
except urllib.error.HTTPError as e: print(str(e.code)+"|"+e.read(400).decode("utf-8","replace"))
except Exception as exc: print("0|"+repr(exc))
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

# ASSERTS THE REASON, not just the code — and that is the whole case.
# The R3 RED baseline on 0.2.263 caught this passing BEFORE the gate existed: the
# pre-existing owner check ("Only the agent owner can run it in the playground") already
# answers 403, because R2 stops a consumer ever OWNING an agent, so a consumer is refused
# every agent on ownership grounds. A status-only assertion was therefore GREEN against an
# image with no role gate at all — a guard that could not fail, which is the exact defect
# this suite's header is about. The role gate runs BEFORE the owner check, so post-R3 the
# consumer gets the ROLE message and this becomes a real discriminator.
#
# Worth stating plainly rather than overselling R3: for a CONSUMER, can_use_playground is
# defence in depth and a clearer error, NOT a new denial. It becomes load-bearing the
# moment non-owners may run shared or published agents — exactly when the owner check
# stops covering for it.
CB16="$(status_body "$CONSUMER_TOK" POST /api/v1/playground/runs "{\"agent_name\":\"${S98_OWNED}\",\"message\":\"probe\"}")"
C16="${CB16%%|*}"; B16="${CB16#*|}"
case "$C16|$B16" in
  403*contributor*) record PASS "T-S98-016 a CONSUMER is refused the playground BY THE ROLE GATE  |  403 naming the contributor role (OQ-3: contributor+)" ;;
  403*owner*)       record FAIL "T-S98-016 a CONSUMER is refused the playground BY THE ROLE GATE  |  403 but the reason is the OWNER check, not the role gate: $B16 — can_use_playground is unwired, or runs after the owner check." ;;
  *)               record FAIL "T-S98-016 a CONSUMER is refused the playground BY THE ROLE GATE  |  -> $C16 $B16 (want 403 naming the contributor role)" ;;
esac

# ── G-R3-2 (2026-08-07): the deployment-pinned chat endpoint had NO access check ──
# `start_chat` (/{name}/chat) enforced team + AssetGrant; `start_deployment_chat`
# (/{name}/deployments/{id}/chat) did the same job pinned to a deployment and enforced
# NOTHING — and Studio routes to THAT one from a fleet row. Reproduced on the cluster
# before the fix: a consumer in team `operations`, agent owned by `platform` ->
#     /agents/{n}/chat                  -> 403
#     /agents/{n}/deployments/{id}/chat -> 200
# Both now call the shared `_require_agent_access`. These two cases are the reason the
# extraction is the fix rather than a second copy of the check: 017 proves the hole is
# closed, 018 proves the closure did not take the legitimate path with it.
S98_XT_TOK="$(e2e_ensure_persona "$NAMESPACE" "$API_POD" "e2e-crossteam" "consumer" "$CONTAINER" || true)"
if [ -n "$S98_XT_TOK" ]; then
  # Move the persona OFF the agent's team — e2e_ensure_persona pins team=platform, and a
  # same-team caller passes via the own-team fast path, which would make 017 vacuous.
  XT_SUB="$(printf '%s' "$S98_XT_TOK" | cut -d. -f2 | python3 -c "
import base64, json, sys
raw = sys.stdin.read().strip()
print(json.loads(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))['sub'])
")"
  status "$ADMIN_TOK" PATCH "/api/v1/admin/users/${XT_SUB}" '{"team":"operations","role":"consumer"}' >/dev/null 2>&1 || true
  S98_XT_TOK="$(e2e_ensure_persona "$NAMESPACE" "$API_POD" "e2e-crossteam" "consumer" "$CONTAINER" || true)"
fi

# Find a running deployment whose agent is ACTIVE and owned by a team the persona is NOT in.
XT="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- env T="$S98_XT_TOK" python3 -c '
import json, os, urllib.request, urllib.error
B = "http://localhost:8000/api/v1"; tok = os.environ.get("T", "")
def call(p, b=None, m="GET"):
    r = urllib.request.Request(B + p, method=m,
        data=json.dumps(b).encode() if b else None,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=25) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
    except Exception: return 0, ""
me = call("/me")[1]
team = json.loads(me).get("team") if me.startswith("{") else None
s, b = call("/deployments/?status=running&limit=50")
for d in (json.loads(b).get("items", []) if s == 200 else []):
    st, ab = call("/agents/" + d["agent_name"])
    if st != 200: continue
    a = json.loads(ab)
    if a.get("status") != "active" or a.get("team") == team: continue
    c1 = call("/agents/%s/chat" % d["agent_name"], {"message": "p", "context": "production"}, "POST")[0]
    c2 = call("/agents/%s/deployments/%s/chat" % (d["agent_name"], d["id"]), {"message": "p"}, "POST")[0]
    print("%s|%s|%s|%s" % (c1, c2, d["agent_name"], team)); break
else:
    print("skip|skip|none|%s" % team)
' 2>/dev/null | tr -d '\r\n' || true)"
XT_PINNED="$(echo "$XT" | cut -d'|' -f2)"; XT_AGENT="$(echo "$XT" | cut -d'|' -f3)"; XT_TEAM="$(echo "$XT" | cut -d'|' -f4)"
case "$XT_PINNED" in
  403) record PASS "T-S98-017 the DEPLOYMENT-PINNED chat endpoint enforces access  |  cross-team caller (team=${XT_TEAM}) -> 403 on /agents/${XT_AGENT}/deployments/{id}/chat" ;;
  200) record FAIL "T-S98-017 the DEPLOYMENT-PINNED chat endpoint enforces access  |  200 — G-R3-2 is BACK. A caller in team '${XT_TEAM}' started a run on '${XT_AGENT}', which /{name}/chat refuses. Both endpoints must call _require_agent_access." ;;
  skip) record PASS "T-S98-017 SKIPPED — no running deployment owned by another team to probe (not a pass of the gate; re-run when one exists)" ;;
  *)    record FAIL "T-S98-017 the DEPLOYMENT-PINNED chat endpoint enforces access  |  got '${XT_PINNED}' (want 403)" ;;
esac

# The over-reach guard: the OWNING team must still be able to use the pinned endpoint.
# A fix that 403s everybody passes 017 and breaks every fleet-row chat in the product.
OWN="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- env T="$ADMIN_TOK" python3 -c '
import json, os, urllib.request, urllib.error
B = "http://localhost:8000/api/v1"; tok = os.environ["T"]
def call(p, b=None, m="GET"):
    r = urllib.request.Request(B + p, method=m,
        data=json.dumps(b).encode() if b else None,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=25) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
    except Exception: return 0, ""
s, b = call("/deployments/?status=running&limit=50")
for d in (json.loads(b).get("items", []) if s == 200 else []):
    st, ab = call("/agents/" + d["agent_name"])
    if st != 200 or json.loads(ab).get("status") != "active": continue
    print(call("/agents/%s/deployments/%s/chat" % (d["agent_name"], d["id"]), {"message": "p"}, "POST")[0]); break
else:
    print("skip")
' 2>/dev/null | tr -d '\r\n' || true)"
case "$OWN" in
  200|201) record PASS "T-S98-018 the pinned endpoint still WORKS for an entitled caller  |  platform-admin -> ${OWN} (the fix denies the wrong team without denying the right one)" ;;
  skip)    record PASS "T-S98-018 SKIPPED — no running deployment with an active agent to probe" ;;
  *)       record FAIL "T-S98-018 the pinned endpoint still WORKS for an entitled caller  |  -> ${OWN} (want 200/201). The G-R3-2 fix over-reached and broke fleet-row chat." ;;
esac

# ── G-R3-6 + Decision 46 step A (2026-08-07) ──────────────────────────────────
# tools.py and skills.py had ZERO authenticated routes — 7 and 5 exempt, POST/PUT/DELETE
# included. A tool's risk_level is the input to the HITL gate and OPA's risk->action rule,
# so an anonymous PUT that lowered it relaxed every control for every agent bound to that
# tool. The READS stay deliberately exempt (declarative-runner + the SDK resolver call them
# with no token) — 021 pins that so closing them later is a decision, not an accident.
S98_TOOL="s98-owner-probe"
C19="$(status "" POST /api/v1/tools/ "{\"name\":\"${S98_TOOL}-anon\",\"type\":\"http\",\"description\":\"anon probe\",\"risk_level\":\"low\"}")"
[ "$C19" = "401" ] \
  && record PASS "T-S98-019 ANONYMOUS cannot create a tool  |  POST /tools/ -> 401 (risk_level drives every downstream gate)" \
  || record FAIL "T-S98-019 ANONYMOUS cannot create a tool  |  POST /tools/ -> $C19 (want 401)"

# Decision 46: owner_team comes from the CALLER's team, never the body. The contributor is
# in team `platform`; asking for another team must be refused for a non-admin.
C20="$(status "$CONTRIB_TOK" POST /api/v1/tools/ "{\"name\":\"${S98_TOOL}-foreign\",\"type\":\"http\",\"description\":\"ownership probe\",\"risk_level\":\"low\",\"owner_team\":\"operations\"}")"
[ "$C20" = "403" ] \
  && record PASS "T-S98-020 a non-admin cannot assign tool ownership to another team  |  owner_team='operations' from a platform contributor -> 403" \
  || record FAIL "T-S98-020 a non-admin cannot assign tool ownership to another team  |  -> $C20 (want 403). A body-supplied owner_team is the same forgeable-attribution shape R2 deleted from create_agent."

# The over-reach guard + the derivation itself: a contributor CAN still create a tool, and
# it lands owned by THEIR team without them saying so.
OWNED="$(status_body "$CONTRIB_TOK" POST /api/v1/tools/ "{\"name\":\"${S98_TOOL}\",\"type\":\"http\",\"description\":\"ownership probe\",\"risk_level\":\"low\"}")"
OC="${OWNED%%|*}"; OB="${OWNED#*|}"
case "$OC|$OB" in
  201*'"owner_team":"platform"'*) record PASS "T-S98-021 a contributor creates a tool and it is owned by THEIR team  |  201, owner_team=platform, derived not supplied" ;;
  409*)                           record PASS "T-S98-021 a contributor creates a tool and it is owned by THEIR team  |  409 (already created by an earlier run — the role gate let them through)" ;;
  *)                              record FAIL "T-S98-021 a contributor creates a tool and it is owned by THEIR team  |  -> $OC $OB (want 201 with owner_team=platform). Either the gate over-reached, or owner_team is still NULL — which team_may_use_tool reads as usable by EVERY team." ;;
esac

# The exempt READS must stay open, or the runner and the SDK resolver break at startup.
C22="$(status "" GET /api/v1/tools/)"
[ "$C22" = "200" ] \
  && record PASS "T-S98-022 the tool READS stay open for in-cluster machine callers  |  anonymous GET /tools/ -> 200 (declarative-runner + SDK tool_resolver send no token; closing this is identity Phase 3)" \
  || record FAIL "T-S98-022 the tool READS stay open for in-cluster machine callers  |  anonymous GET /tools/ -> $C22 (want 200). G-R3-6 gated MUTATIONS only; gating the reads breaks every agent pod at startup."

# ── Decision 47 step B — private by default + who can SEE it (2026-08-07) ─────
# Migration 0080 flips the tools/skills publish_status default to 'private', matching
# agents and workflows. The DEFAULT alone is a one-line DDL; these cases exist because the
# flip is only correct together with two visibility changes that shipped with it, and each
# has a distinct way of being wrong:
#
#   023  the default actually took        — the DDL reached the running DB
#   024  a TEAMMATE can still see it      — visibility is TEAM-scoped, per Decision 46.
#                                           Before 0080 it was CREATOR-scoped, so a private
#                                           tool would have been invisible to the rest of
#                                           the owning team the moment the default flipped.
#   025  another TEAM cannot see it       — the over-reach guard for 024. Without it, "make
#                                           teammates see it" is satisfied by showing
#                                           everything to everyone.
#   026  an ANONYMOUS resolve still finds it — THE ONE THAT MATTERS. Agent pods hold no
#                                           token until identity Phase 3, and the SDK
#                                           tool_resolver fetches GET /tools/?name=X at
#                                           startup. Under the old published-only branch a
#                                           private tool returned zero items and the pod
#                                           died with "Tool 'X' not found in the platform
#                                           registry". A pod's authority over a tool is its
#                                           binding plus OPA Gate 3, never the catalog flag.
#   027  the same two properties for SKILLS — declarative-runner resolves those the same way
#                                           (workflow_executor.py:232) and skills got the
#                                           identical default flip, so they can fail
#                                           independently of tools.
#
# 026/027 are the regression guards for a change that would otherwise show up as every
# newly-built agent CrashLooping, with an error naming a missing tool rather than a
# visibility filter.
S98_LC="s98-lifecycle-$(date +%s)"

# These two helpers do the parsing INSIDE the pod and print one scalar. The first version
# of these cases reused `status_body`, which truncates at `r.read(400)` — a tool row is
# longer than that, so every json.loads on the bash side failed and 023/024/026 reported
# "ERR" as if the product were broken. Ship a number across the boundary, not a document.

# tool_field <token> <name> <field>  — reload one row FROM THE BACKEND and print one field.
tool_field() {
  kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- env \
    T="$1" N="$2" F="$3" python3 -c '
import os, json, urllib.request
h = {}
if os.environ["T"]: h["Authorization"] = "Bearer " + os.environ["T"]
url = "http://localhost:8000/api/v1/tools/?name=" + os.environ["N"] + "&limit=1"
try:
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
        items = json.load(r).get("items", [])
    print(items[0].get(os.environ["F"], "") if items else "NOT_VISIBLE")
except Exception as exc:
    print("ERR:" + repr(exc))
' 2>/dev/null | tr -d '\r\n'
}

# catalog_hits <token> <tools|skills> <name>  — how many rows of that EXACT name this
# caller can see. An empty token is the in-cluster machine path on purpose.
catalog_hits() {
  kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- env \
    T="$1" C="$2" N="$3" python3 -c '
import os, json, urllib.request
h = {}
if os.environ["T"]: h["Authorization"] = "Bearer " + os.environ["T"]
c, n = os.environ["C"], os.environ["N"]
# /tools/ has an exact-name filter; /skills/ does not, so page wide and match here.
url = ("http://localhost:8000/api/v1/tools/?limit=5&name=" + n) if c == "tools" \
      else "http://localhost:8000/api/v1/skills/?page_size=500"
try:
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20) as r:
        items = json.load(r).get("items", [])
    print(sum(1 for i in items if i.get("name") == n))
except Exception as exc:
    print("ERR")
' 2>/dev/null | tr -d '\r\n'
}

LC_CODE="$(status "$CONTRIB_TOK" POST /api/v1/tools/ "{\"name\":\"${S98_LC}\",\"type\":\"http\",\"description\":\"lifecycle probe\",\"risk_level\":\"low\"}")"

# 023 — save -> RELOAD FROM THE BACKEND -> assert. The POST response is not evidence on
# its own: a server_default is applied by Postgres, so reading the row back is the only
# thing that proves the DDL landed rather than the ORM echoing what it sent.
if [ "$LC_CODE" = "201" ]; then
  LC_PS="$(tool_field "$CONTRIB_TOK" "$S98_LC" publish_status)"
  case "$LC_PS" in
    private)   record PASS "T-S98-023 a new tool is PRIVATE by default  |  created, reloaded from the backend, publish_status=private (migration 0080)" ;;
    published) record FAIL "T-S98-023 a new tool is PRIVATE by default  |  reloaded as 'published'. Migration 0080 did not reach this database, so every tool anyone creates is in the shared catalog for every team the moment it exists." ;;
    *)         record FAIL "T-S98-023 a new tool is PRIVATE by default  |  reload gave '${LC_PS}'" ;;
  esac
else
  record FAIL "T-S98-023 a new tool is PRIVATE by default  |  create returned ${LC_CODE} (want 201) — cannot judge the default."
fi

# 024 — a teammate. e2e-consumer is pinned to team platform, same as e2e-contributor.
LC_MATE="$(catalog_hits "$CONSUMER_TOK" tools "$S98_LC")"
[ "$LC_MATE" = "1" ] \
  && record PASS "T-S98-024 a TEAMMATE sees the private tool  |  another platform user finds it (visibility is TEAM-scoped, Decision 46)" \
  || record FAIL "T-S98-024 a TEAMMATE sees the private tool  |  got '${LC_MATE}' (want 1). Visibility is still CREATOR-scoped, so 0080 just hid every new tool from the team that owns it."

# 025 — the over-reach guard for 024. e2e-crossteam was moved to `operations` above.
if [ -n "$S98_XT_TOK" ]; then
  LC_XT="$(catalog_hits "$S98_XT_TOK" tools "$S98_LC")"
  [ "$LC_XT" = "0" ] \
    && record PASS "T-S98-025 another TEAM does NOT see the private tool  |  an operations user finds 0 rows" \
    || record FAIL "T-S98-025 another TEAM does NOT see the private tool  |  got '${LC_XT}' (want 0). The team predicate is not filtering — 024 would then pass by showing everything to everybody."
else
  record FAIL "T-S98-025 another TEAM does NOT see the private tool  |  no cross-team persona token; 024 is unguarded without this."
fi

# 026 — THE ONE THAT MATTERS. Agent pods hold no token until identity Phase 3, and the SDK
# tool_resolver fetches GET /tools/?name=X at startup. A pod's authority over a tool is its
# binding plus OPA Gate 3, never the catalog flag.
LC_ANON="$(catalog_hits "" tools "$S98_LC")"
[ "$LC_ANON" = "1" ] \
  && record PASS "T-S98-026 an ANONYMOUS in-cluster resolve still finds a private tool  |  the SDK tool_resolver path returns it" \
  || record FAIL "T-S98-026 an ANONYMOUS in-cluster resolve still finds a private tool  |  got '${LC_ANON}' (want 1). This is the outage shape: every SDK agent bound to a tool created after 0080 dies at startup with \"Tool not found in the platform registry\"."

# 027 — skills get the identical default and the identical machine-resolve path, and can
# regress on their own: separate router, separate model, and the owning team lives in a
# differently named column (`Skill.team`, not `owner_team`).
SK_CODE="$(status "$CONTRIB_TOK" POST /api/v1/skills/ "{\"name\":\"${S98_LC}-skill\",\"description\":\"lifecycle probe\",\"team\":\"platform\"}")"
if [ "$SK_CODE" = "201" ]; then
  SK_ANON="$(catalog_hits "" skills "${S98_LC}-skill")"
  [ "$SK_ANON" = "1" ] \
    && record PASS "T-S98-027 a new SKILL stays resolvable by the tokenless runner  |  declarative-runner workflow_executor.py:232 still finds it after 0080" \
    || record FAIL "T-S98-027 a new SKILL stays resolvable by the tokenless runner  |  got '${SK_ANON}' (want 1). Every workflow binding a skill created after 0080 fails at run time."
else
  record FAIL "T-S98-027 a new SKILL stays resolvable by the tokenless runner  |  create returned ${SK_CODE} (want 201)."
fi

echo ""
echo "=== Suite 98 Results: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "  R2 shipped in registry-api 0.2.263. A failure here is a REGRESSION, not the"
  echo "  pre-R2 baseline — check rbac.require_global_role is still wired onto admin.py"
  echo "  and admin_users.py, and that the teams-summary split (T-S98-006/007) held."
  exit 1
fi

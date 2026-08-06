#!/usr/bin/env bash
# scripts/e2e/suite-98-rbac-role-enforcement.sh
#
# E2E Suite 98: RBAC R2 — GLOBAL ROLE ENFORCEMENT.
#
# ⚠ THIS SUITE IS EXPECTED TO FAIL UNTIL R2 SHIPS. That is its purpose. ⚠
# ------------------------------------------------------------------------------
# Every case here asserts a 403 that the platform does NOT yet return. Run it before
# R2 and it goes RED; run it after and it must go GREEN. CLAUDE.md DoD rule 7 asks for
# exactly this — a test that reproduces the defect BEFORE the fix — and this repo has
# already paid for skipping it: docs/testing/manual-ui-e2e-test-plan.md G-R0-9 records a
# test that was red from the day it was written, never once passed, and went unnoticed
# for months because nothing could run it.
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
# MEASURED ON THE LIVE CLUSTER, 2026-08-06, against registry-api 0.2.261 (R1 shipped):
#     e2e-consumer  ->  GET /api/v1/admin/users  ->  200
# A `consumer` — the lowest global role — can enumerate every user on the platform.
# R1 closed "no token at all". "ANY token is enough" is still wide open. That is not a
# new hole; it is §1.2/§1.3 of rbac-and-artifact-authorization.md (require_global_role
# is an orphan, ENFORCE=False) made visible by an authenticated non-admin caller,
# which nothing previously was.
#
# WHAT R2 MUST DO FOR THIS SUITE TO PASS
#   - flip `ENFORCE = False` -> True (rbac.py, inside require_global_role)
#   - wire require_global_role("platform-admin") onto admin.py + admin_users.py
#   - wire can_deploy_to_production / can_create_agent / can_use_playground
#     (all three are built and have ZERO callers — §1.3)
#
# CASES
#   T-S98-001 — a CONSUMER is refused the admin surface        (GET /admin/users -> 403)
#   T-S98-002 — a CONTRIBUTOR is refused the admin surface     (GET /admin/users -> 403)
#   T-S98-003 — platform-admin is STILL allowed                (GET /admin/users -> 200)
#   T-S98-004 — a CONSUMER is refused agent creation           (POST /agents/ -> 403)
#   T-S98-005 — anonymous is still 401, not 403 (R1 unchanged) (GET /admin/users -> 401)
#
# T-S98-003 and T-S98-005 are the guards that stop an over-broad "fix": R2 must deny the
# wrong role WITHOUT denying the right one, and must not turn authentication into
# authorization. A change that 403s everybody would pass 001/002/004 alone.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
CONTAINER="registry-api"

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || { echo "ERROR: no Running registry-api pod in $NAMESPACE"; exit 1; }

echo "=== Suite 98: RBAC R2 role enforcement (EXPECTED RED until R2 ships) ==="
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

C4="$(status "$CONSUMER_TOK" POST /api/v1/agents/ '{"name":"s98-should-not-exist","description":"role gate probe","team":"platform"}')"
case "$C4" in
  403) record PASS "T-S98-004 CONSUMER is refused agent creation  |  POST /agents/ -> 403" ;;
  *)   record FAIL "T-S98-004 CONSUMER is refused agent creation  |  POST /agents/ -> $C4 (want 403). can_create_agent exists in rbac.py and has zero callers (§1.3)." ;;
esac

C5="$(status "" GET /api/v1/admin/users)"
[ "$C5" = "401" ] \
  && record PASS "T-S98-005 ANONYMOUS is still 401, not 403  |  R1's authentication layer is unchanged by R2" \
  || record FAIL "T-S98-005 ANONYMOUS is still 401, not 403  |  GET /admin/users -> $C5 (want 401) — R2 must not turn authentication into authorization"

echo ""
echo "=== Suite 98 Results: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "  EXPECTED while R2 is unshipped. These failures ARE the R2 specification."
  echo "  They must go green when rbac.py's ENFORCE flips and require_global_role is wired."
  exit 1
fi

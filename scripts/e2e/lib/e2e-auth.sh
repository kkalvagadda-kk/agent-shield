#!/usr/bin/env bash
# scripts/e2e/lib/e2e-auth.sh — ONE definition of how a bash e2e suite authenticates.
#
# WHY THIS EXISTS
# ---------------
# Commit 76b3570 (webhook-application-identity) put `Depends(require_user)` on
# `create_trigger` (routers/triggers.py) and `create_workflow_trigger`
# (routers/composite_workflows.py). Every bash suite created triggers with
# `X-User-Sub` headers alone — that header is an audit stamp, never an
# authentication — so from that commit onward FIFTEEN suites died at setup with
#
#     401 {"detail":"Authentication required"}
#
# and the whole trigger / schedule / webhook / daemon / alerting e2e layer went
# dark: suites 19, 21, 22, 26, 27, 28, 31, 32, 33, 34, 66, 70, 71, 75, 77.
#
# It stayed dark because the failures did not name their cause. suite-28 printed
# an EMPTY setup diagnostic and simply stopped; suite-66 reported "a production
# trigger did not fire a completed run", which points at the scheduler — three
# layers below the actual problem. Silence and a misdirected message are the same
# defect class this repo keeps paying for.
#
# WHY A SHARED FILE AND NOT A SNIPPET PER SUITE
# ---------------------------------------------
# The obvious repair is to paste a token fetch into each of the fifteen drivers.
# That is fifteen copies of one decision, which is precisely the pattern
# `routers/webhook_clients.py` and `agent_endpoints.py` both carry postmortems
# about: two (here, fifteen) parallel paths, one gets fixed, the others silently
# do not. One definition, fifteen callers.
#
# USAGE
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/e2e-auth.sh"
#   TOKEN="$(e2e_token "$NAMESPACE" "$API_POD")"
#
# then interpolate it into the in-pod driver the same way suites already
# interpolate agent names:
#
#   H = {"X-User-Sub": ADMIN, "X-User-Team": "platform",
#        "Authorization": "Bearer ${TOKEN}"}
#
# The token is fetched INSIDE the pod (cluster-internal Keycloak Service DNS),
# so it needs no port-forward and no host-side Keycloak reachability.

# Resolve THIS library's directory ONCE, at source time. Do not compute it inside
# a function from `${BASH_SOURCE[0]}`: the value there is not reliably this file
# when the function is invoked from a sourcing script, and the failure mode is a
# path silently rooted at the caller's cwd (`<repo>/e2e_auth.py missing`) rather
# than an obvious error.
E2E_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Seeded by charts/agentshield/templates/realm-init-job.yaml.
E2E_KC_USER="${E2E_KC_USER:-platform-admin}"
E2E_KC_PASS="${E2E_KC_PASS:-PlatformAdmin2024}"
E2E_KC_CLIENT="${E2E_KC_CLIENT:-agentshield-studio}"
E2E_KC_URL="${E2E_KC_URL:-http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token}"

# e2e_token <namespace> <pod> [container]
# Echoes a raw access token, or echoes nothing and returns 1.
# FAILS LOUD by design: a suite that silently proceeds without a token produces
# the 401-at-setup confusion this file exists to end.
e2e_token() {
  local ns="$1" pod="$2" container="${3:-registry-api}" out
  out=$(kubectl exec -n "$ns" "$pod" -c "$container" -- python3 -c "
import json, sys, urllib.parse, urllib.request
data = urllib.parse.urlencode({
    'grant_type': 'password', 'client_id': '${E2E_KC_CLIENT}',
    'username': '${E2E_KC_USER}', 'password': '${E2E_KC_PASS}'}).encode()
try:
    req = urllib.request.Request('${E2E_KC_URL}', data=data)
    print(json.loads(urllib.request.urlopen(req, timeout=15).read())['access_token'])
except Exception as exc:
    print('E2E_TOKEN_ERROR: %s' % exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || { echo "" ; return 1; }
  [ -n "$out" ] || return 1
  printf '%s' "$out"
}

# e2e_install_pyauth <namespace> <pod> [container]
# Copy lib/e2e_auth.py into the pod at /tmp/e2e_auth.py so a detached driver can
# `from e2e_auth import BearerAuth` and refresh its own token.
#
# REQUIRED for any suite that runs longer than the 300s token lifespan — i.e.
# every detached-driver suite (66, 70, 71, 75, 77). A statically-interpolated
# token is fine ONLY for the short inline suites that finish inside 5 minutes.
# Getting this wrong does not fail loudly at the start; it fails 20 minutes in,
# on whichever case happens to run last, and looks like a feature bug.
e2e_install_pyauth() {
  local ns="$1" pod="$2" container="${3:-registry-api}" lib="$E2E_LIB_DIR/e2e_auth.py"
  [ -f "$lib" ] || { echo "FATAL: $lib missing" >&2; exit 1; }
  kubectl exec -i -n "$ns" "$pod" -c "$container" -- bash -c 'cat > /tmp/e2e_auth.py' < "$lib" \
    || { echo "FATAL: could not install e2e_auth.py into $pod" >&2; exit 1; }
  # VERIFY the module actually landed and imports. A silent no-op here does not
  # fail now — it fails twenty minutes later inside the driver, as a
  # ModuleNotFoundError that never reaches the result file, and the suite reports
  # "driver did not finish" with no output at all. Check while it is still cheap.
  kubectl exec -n "$ns" "$pod" -c "$container" -- \
    python3 -c 'import sys; sys.path.insert(0, "/tmp"); import e2e_auth' >/dev/null 2>&1 \
    || { echo "FATAL: /tmp/e2e_auth.py did not import inside $pod" >&2; exit 1; }
}

# e2e_require_token <namespace> <pod> [container]
# Aborts the suite with a message that NAMES THE CAUSE. Use this in any suite whose
# assertions depend on trigger CRUD — a missing token must read as "could not
# authenticate", never as "the feature is broken".
#
# CALL IT BARE, NOT IN A COMMAND SUBSTITUTION, when you rely on the abort:
#
#     e2e_set_token "$NAMESPACE" "$API_POD"     # good: exit propagates
#     TOK="$(e2e_require_token ...)"            # the `exit 1` below only kills the
#                                               # SUBSHELL; the suite carries on with
#                                               # TOK empty and every later call 401s
#
# That subshell subtlety is why `e2e_set_token` exists and is what the suites use.
e2e_require_token() {
  local tok
  tok=$(e2e_token "$@") || {
    echo "FATAL: could not obtain a Keycloak token for ${E2E_KC_USER} (client ${E2E_KC_CLIENT})." >&2
    echo "       Trigger CRUD requires a real JWT since 76b3570 — X-User-Sub alone returns 401." >&2
    echo "       Check the realm-init Job seeded ${E2E_KC_USER}, and that Keycloak is reachable in-cluster." >&2
    exit 1
  }
  printf '%s' "$tok"
}

# e2e_set_token <namespace> <pod> [container]
# Sets E2E_TOKEN in the CALLER'S shell and aborts the suite if it cannot.
#
# Assigns rather than echoes precisely so the failure path works: `exit 1` inside a
# command substitution exits only that subshell, so `E2E_TOKEN="$(e2e_require_token
# ...)"` silently yields an EMPTY token and the suite then fails later with a wall of
# 401s — the misdirected-failure mode this whole helper exists to end.
e2e_set_token() {
  E2E_TOKEN="$(e2e_token "$@")" || true
  if [ -z "${E2E_TOKEN:-}" ]; then
    echo "FATAL: could not obtain a Keycloak token for ${E2E_KC_USER} (client ${E2E_KC_CLIENT})." >&2
    echo "       Trigger CRUD requires a real JWT since 76b3570 — X-User-Sub alone returns 401." >&2
    echo "       Check the realm-init Job seeded ${E2E_KC_USER}, and that Keycloak is reachable in-cluster." >&2
    exit 1
  fi
}

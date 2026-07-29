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
  local ns="$1" pod="$2" container="${3:-registry-api}" lib
  lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/e2e_auth.py"
  [ -f "$lib" ] || { echo "FATAL: $lib missing" >&2; exit 1; }
  kubectl exec -i -n "$ns" "$pod" -c "$container" -- bash -c 'cat > /tmp/e2e_auth.py' < "$lib" \
    || { echo "FATAL: could not install e2e_auth.py into $pod" >&2; exit 1; }
}

# e2e_require_token <namespace> <pod> [container]
# Same, but aborts the suite with a message that NAMES THE CAUSE. Use this in
# any suite whose assertions depend on trigger CRUD — a missing token must read
# as "could not authenticate", never as "the feature is broken".
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

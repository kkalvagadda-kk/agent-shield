#!/usr/bin/env bash
# studio-e2e.sh — run Playwright browser E2E against the deployed Studio.
#
# Studio is a ClusterIP Service whose nginx proxies /api → registry-api and
# /realms → keycloak, so port-forwarding just the Studio Service gives a fully
# working app (login included). This script sets up that port-forward, runs
# Playwright, and tears the forward down.
#
# This is a SEPARATE gate from the bash API suites (scripts/e2e/run-all.sh) —
# it is not part of that run.
#
# Usage:
#   bash scripts/studio-e2e.sh                 # all specs
#   bash scripts/studio-e2e.sh e2e/workflows.spec.ts   # one spec
#   STUDIO_E2E_PASSWORD=... bash scripts/studio-e2e.sh
#
# To run only the specs covering a functional area (the usual case after a scoped
# change), select by group instead of naming files — scripts/test-manifest.txt maps
# every spec to its groups, and run-tests.sh resolves them and calls this script:
#   bash scripts/run-tests.sh --groups                       # what groups exist
#   bash scripts/run-tests.sh --layer browser --group tools
#   bash scripts/run-tests.sh --list --layer browser --group hitl   # preview only
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PORT="${STUDIO_E2E_PORT:-8080}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Gateway mode: run against the real https gateway instead of an http
# port-forward. Required because Keycloak now sets Secure session cookies —
# Playwright won't send those back over a plain-http port-forward, so SSO
# silent-auth between specs breaks. The gateway (https) keeps SSO working and
# is the more realistic path anyway. Set STUDIO_E2E_GATEWAY_URL to enable;
# defaults on when reachable.
GATEWAY_URL="${STUDIO_E2E_GATEWAY_URL:-https://agentshield.127.0.0.1.nip.io:8443}"
if curl -sk -o /dev/null -w "%{http_code}" "${GATEWAY_URL}/config.json" 2>/dev/null | grep -q 200; then
  echo "=== Studio Playwright E2E (gateway mode) ==="
  echo "  target: ${GATEWAY_URL}"
  cd "$REPO_ROOT/studio"
  PLAYWRIGHT_BASE_URL="$GATEWAY_URL" npx playwright test "$@"
  exit $?
fi

# ── EKS gateway mode ─────────────────────────────────────────────────────────
# On the EKS test cluster the default nip.io gateway above is unreachable: the
# Gateway is an INTERNAL AWS NLB, and its HTTPRoute is bound to the NLB's own DNS
# name, so nothing answers on 127.0.0.1:8443 and no Host header matches.
#
# The plain-http fallback below CANNOT substitute for it. Specs make API calls to
# the gateway on 8443, so on EKS every one of them dies with
# `apiRequestContext: connect ECONNREFUSED ::ffff:127.0.0.1:8443` — the whole
# browser layer was silently unrunnable against this cluster (gap G-R0-8).
#
# Fix: discover the route hostname, port-forward the Gateway Service to 8443, and
# run gateway mode against it. The one step this script will NOT do for you is the
# /etc/hosts entry — it needs sudo and it edits your machine, so it is named
# precisely and left to you.
if [ -z "${STUDIO_E2E_NO_EKS:-}" ]; then
  # Ask the cluster what hostname its Gateway actually serves, instead of assuming the
  # local nip.io one. deploy-eks.sh sets `global.publicUrl` to the ELB address, so on EKS
  # the route is bound to the NLB's own DNS name and the nip.io default matches nothing —
  # Envoy answers 404 (not a connection error, which is what makes it look like an outage).
  GW_HOST="$(kubectl get httproute -n "$NAMESPACE" agentshield-routes \
    -o jsonpath='{.spec.hostnames[0]}' 2>/dev/null || true)"
  if [ -n "$GW_HOST" ] && [ "$GW_HOST" != "agentshield.127.0.0.1.nip.io" ]; then
    # Try it DIRECTLY first. The NLB is internal, so its name resolves to a private VPC
    # address — but anyone who can reach the cluster API (also private) can usually reach
    # it too, and then no tunnel or /etc/hosts entry is needed at all. Verified on
    # test-cluster-964-10086: /config.json -> 200 with no port-forward.
    if curl -sk -m 10 -o /dev/null -w "%{http_code}" "https://${GW_HOST}/config.json" 2>/dev/null | grep -q 200; then
      echo "=== Studio Playwright E2E (gateway mode, direct) ==="
      echo "  target: https://${GW_HOST}"
      cd "$REPO_ROOT/studio"
      PLAYWRIGHT_BASE_URL="https://${GW_HOST}" npx playwright test "$@"
      exit $?
    fi
    # Not directly reachable (off-VPN, or a genuinely unroutable gateway). A port-forward
    # alone will NOT save you here: Playwright drives a real browser and cannot override
    # DNS the way `curl --resolve` can, so the browser would still send the wrong Host and
    # get a 404. Say so, with the exact remedy, instead of falling through to the http
    # port-forward below — that mode cannot reach the API on :8443 and every spec then
    # dies at its fixture with ECONNREFUSED, which reads as an app outage. Gap G-R0-8.
    echo "FATAL: the Gateway serves '${GW_HOST}', which is not reachable from here."
    echo "       Check you are on the network/VPN that reaches the cluster (the same one"
    echo "       serving the Kubernetes API), since the gateway is usually reachable"
    echo "       wherever the API is. If it is not, forward it and pin the name:"
    echo ""
    echo "         kubectl port-forward -n envoy-gateway-system svc/gateway-port-8443 8443:8443 &"
    echo "         sudo sh -c 'echo \"127.0.0.1  ${GW_HOST}\" >> /etc/hosts'"
    echo "         STUDIO_E2E_GATEWAY_URL=https://${GW_HOST}:8443 bash scripts/studio-e2e.sh"
    echo ""
    echo "       Set STUDIO_E2E_NO_EKS=1 to skip this and use the http port-forward, but be"
    echo "       aware specs that call the API on :8443 will fail there."
    exit 1
  fi
fi

echo "=== Studio Playwright E2E ==="
echo "[1/2] Port-forwarding svc/agentshield-studio ${PORT}:80 ..."
kubectl port-forward -n "$NAMESPACE" svc/agentshield-studio "${PORT}:80" > /tmp/studio-pf.log 2>&1 &
PF_PID=$!
cleanup() { kill "$PF_PID" 2>/dev/null || true; }
trap cleanup EXIT

# Wait for the forward to serve the SPA config endpoint.
ready=0
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:${PORT}/config.json" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[ "$ready" -eq 1 ] || { echo "FATAL: studio not reachable on :${PORT}"; cat /tmp/studio-pf.log; exit 1; }
echo "  studio reachable on http://localhost:${PORT}"

echo "[2/2] Running Playwright..."
cd "$REPO_ROOT/studio"
PLAYWRIGHT_BASE_URL="http://localhost:${PORT}" npx playwright test "$@"

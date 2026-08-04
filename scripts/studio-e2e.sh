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
  GW_HOST="$(kubectl get httproute -n "$NAMESPACE" agentshield-routes \
    -o jsonpath='{.spec.hostnames[0]}' 2>/dev/null || true)"
  GW_SVC="$(kubectl get svc -n envoy-gateway-system -o name 2>/dev/null \
    | grep -m1 'envoy-.*agentshield-gateway' || true)"
  if [ -n "$GW_HOST" ] && [ -n "$GW_SVC" ] && [ "$GW_HOST" != "agentshield.127.0.0.1.nip.io" ]; then
    if ! getent hosts "$GW_HOST" 2>/dev/null | grep -q '^127\.0\.0\.1' \
       && ! grep -qE "^[[:space:]]*127\.0\.0\.1[[:space:]]+.*${GW_HOST}" /etc/hosts 2>/dev/null; then
      echo "FATAL: the Gateway route is bound to '${GW_HOST}', which does not resolve to 127.0.0.1."
      echo "       Playwright drives a real browser, so it cannot be told to override DNS the way"
      echo "       curl --resolve can. Add this line to /etc/hosts once, then re-run:"
      echo ""
      echo "         127.0.0.1  ${GW_HOST}"
      echo ""
      echo "       (sudo sh -c 'echo \"127.0.0.1  ${GW_HOST}\" >> /etc/hosts')"
      echo "       Without it the specs fall through to a plain-http port-forward that cannot"
      echo "       reach the API on :8443, and EVERY spec fails at its fixture — see gap G-R0-8"
      echo "       in docs/testing/manual-ui-e2e-test-plan.md."
      exit 1
    fi
    echo "=== Studio Playwright E2E (EKS gateway mode) ==="
    echo "  gateway: https://${GW_HOST}:8443  (port-forward -> ${GW_SVC})"
    kubectl port-forward -n envoy-gateway-system "$GW_SVC" 8443:443 > /tmp/gateway-pf.log 2>&1 &
    GW_PID=$!
    trap 'kill "$GW_PID" 2>/dev/null || true' EXIT
    for _ in $(seq 1 30); do
      if curl -sk -o /dev/null -w "%{http_code}" "https://${GW_HOST}:8443/config.json" 2>/dev/null | grep -q 200; then
        break
      fi
      sleep 1
    done
    curl -sk -o /dev/null -w "%{http_code}" "https://${GW_HOST}:8443/config.json" 2>/dev/null | grep -q 200 \
      || { echo "FATAL: gateway port-forward did not serve /config.json"; cat /tmp/gateway-pf.log; exit 1; }
    cd "$REPO_ROOT/studio"
    PLAYWRIGHT_BASE_URL="https://${GW_HOST}:8443" npx playwright test "$@"
    exit $?
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

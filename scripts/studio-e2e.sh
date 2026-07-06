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
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PORT="${STUDIO_E2E_PORT:-8080}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

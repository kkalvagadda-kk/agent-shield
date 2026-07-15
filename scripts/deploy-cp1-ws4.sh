#!/usr/bin/env bash
# scripts/deploy-cp1-ws4.sh — WS-4 Checkpoint 1 deploy (CP1a).
#
# Thin, idempotent wrapper. It DELEGATES to scripts/deploy-cpe2e.sh and never runs bare
# helm/docker/kubectl for the deploy itself (CLAUDE.md "Deploy Script Only": a code edit
# that is not built AND deployed leaves the pod on OLD code, and every check then passes
# against the wrong bytes).
#
# Scope built here:
#   event-gateway 0.1.2 — webhook_auth.py: ONE verify_webhook_auth wrapping BOTH hooks,
#                         client-id + allowlist + HMAC, uniform-401 oracle closed,
#                         cryptography dep + AGENTSHIELD_ENCRYPTION_KEY so it can decrypt.
#   registry-api  0.2.186 — migration 0064 (webhook_clients, agent_triggers.auth_mode,
#                         agent_events.client_id), the /api/v1/triggers client router,
#                         and the born-token → client_signed-on-first-client upgrade.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "==> WS-4 CP1 deploy: event-gateway 0.1.2 + registry-api 0.2.186"
echo "    (migration 0064 applies via the registry-api alembic init container)"
echo ""

bash scripts/deploy-cpe2e.sh

echo ""
echo "==> Waiting for rollouts"
kubectl rollout status deploy/agentshield-event-gateway -n "$NAMESPACE" --timeout=5m
kubectl rollout status deploy/agentshield-registry-api  -n "$NAMESPACE" --timeout=10m

echo ""
echo "✅ CP1 deploy complete. Next:"
echo "   bash scripts/smoke-test-cp1-ws4-infra.sh && bash scripts/smoke-test-cp1-ws4-behaviour.sh"

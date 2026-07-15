#!/usr/bin/env bash
# scripts/deploy-cp1-ws2.sh
#
# WS-2 Checkpoint 1 deploy wrapper (CP1a).
#
# Scope of CP1 (Execution Models v2 · WS-2 · Phases 2–3):
#   * OPA identity floor `user_identity_ok` + deny_reason `missing_user_identity`
#     wired into `allow` (services/registry-api/opa_policy/agentshield.rego).
#   * `agent_triggers.armed_by` column (Alembic 0061) + ORM field + producers.
#   * shared `resolve_principal` helper (services/registry-api/identity.py)
#     called from BOTH entry paths — trigger `/internal/runs/start` (service
#     identity for a daemon) and interactive `/chat` (caller identity).
#
# This is a THIN, idempotent wrapper. It does NOT run bare helm/docker/kubectl for
# the deploy — the canonical build+push+helm is `scripts/deploy-cpe2e.sh` (image
# tags already bumped to registry-api 0.2.177 in BOTH deploy-cpe2e.sh and
# charts/agentshield/values.yaml). Re-running is safe: same tag => no-op image,
# helm upgrade converges, Alembic migrate init advances to head only if needed.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== WS-2 CP1 deploy (identity floor + service-identity principal) ==="
echo "  namespace: $NAMESPACE"
echo "  registry-api tag: (read from scripts/deploy-cpe2e.sh + values.yaml)"
echo ""

echo "--> Building + deploying via canonical scripts/deploy-cpe2e.sh ..."
bash scripts/deploy-cpe2e.sh

echo ""
echo "--> Waiting for registry-api rollout ..."
kubectl rollout status deploy/agentshield-registry-api -n "$NAMESPACE" --timeout=300s

echo ""
echo "=== CP1 deploy complete. Now run: ==="
echo "  bash scripts/smoke-test-cp1-ws2-infra.sh && bash scripts/smoke-test-cp1-ws2-behaviour.sh"

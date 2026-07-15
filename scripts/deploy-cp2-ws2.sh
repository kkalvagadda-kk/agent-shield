#!/usr/bin/env bash
# scripts/deploy-cp2-ws2.sh
#
# WS-2 Checkpoint 2 deploy wrapper (CP2a).
#
# Scope of CP2 (Execution Models v2 · WS-2 · Phases 4–5):
#   * async reviewer routing + audit display on a daemon run's approval
#     (routers/approvals.py: reviewer_scope + principal_display + fail-closed 403).
#   * agent_triggers.approver_role (Alembic 0062) + ORM + create/update schemas.
#   * workflow daemon identity — parent run_by = workflow service identity, threaded
#     to every member child (workflow_orchestrator.resolve_workflow_principal).
#   * Studio: ApprovalCard principal_display + inbox reviewer-role filter.
#
# THIN, idempotent wrapper. It does NOT run bare helm/docker/kubectl for the deploy —
# the canonical build+push+helm is scripts/deploy-cpe2e.sh (image tags already bumped
# to registry-api 0.2.178 + studio 0.1.135 in BOTH deploy-cpe2e.sh and
# charts/agentshield/values.yaml — this wrapper does NOT re-bump). Re-running is safe:
# same tag => no-op image, helm upgrade converges, Alembic migrate init advances to
# head (0062) only if needed.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== WS-2 CP2 deploy (async reviewer routing + workflow daemon identity) ==="
echo "  namespace: $NAMESPACE"
echo "  tags: registry-api / studio (read from scripts/deploy-cpe2e.sh + values.yaml — not re-bumped here)"
echo ""

echo "--> Building + deploying via canonical scripts/deploy-cpe2e.sh ..."
bash scripts/deploy-cpe2e.sh

echo ""
echo "--> Waiting for registry-api + studio rollouts ..."
kubectl rollout status deploy/agentshield-registry-api -n "$NAMESPACE" --timeout=300s
kubectl rollout status deploy/agentshield-studio       -n "$NAMESPACE" --timeout=300s

echo ""
echo "=== CP2 deploy complete. Now run: ==="
echo "  bash scripts/smoke-test-cp2-ws2-infra.sh && bash scripts/smoke-test-cp2-ws2-behaviour.sh"
echo "  bash scripts/studio-e2e.sh e2e/approvals-inbox.spec.ts   # Playwright (separate gate)"

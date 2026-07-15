#!/usr/bin/env bash
# scripts/deploy-cp2-ws3.sh
#
# WS-3 Checkpoint 2 deploy wrapper (CP2a).
#
# Scope of CP2 (Execution Models v2 · WS-3 · Phases 4–5):
#   * The scheduled durable daemon WORKFLOW path (parent + members carry the
#     WORKFLOW service identity; all four orchestration modes park + resume async).
#   * Failure alerting (alert_email / alert_on_failure on agent_triggers;
#     alerting.dispatch_failure_alert invoked from internal.py on status=failed).
#
# Like CP1, this is ALL existing shared code — registry-api 0.2.179,
# declarative-runner 0.1.44 — so NO image bump. THIN, idempotent VERIFY wrapper:
# confirms the already-deployed backend via `kubectl rollout status`. Runs NO bare
# helm/docker/kubectl for a deploy; delegates to scripts/deploy-cpe2e.sh ONLY on
# drift (expected tag not running).
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
EXPECT_TAG="${EXPECT_REGISTRY_API_TAG:-0.2.179}"

echo "=== WS-3 CP2 deploy (scheduled daemon WORKFLOW 4-mode + alerting — no backend bump) ==="
echo "  namespace:            $NAMESPACE"
echo "  expected registry-api tag: $EXPECT_TAG (shared workflow orchestrator + alerting)"
echo "  declarative-runner:   0.1.44 (unchanged)"
echo ""

RUNNING_IMG=$(kubectl get deploy agentshield-registry-api -n "$NAMESPACE" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
echo "  running registry-api image: ${RUNNING_IMG:-<none>}"

if echo "$RUNNING_IMG" | grep -q ":${EXPECT_TAG}$"; then
  echo "--> Expected tag already deployed — verifying rollout only (no build/deploy)."
else
  echo "--> Expected tag NOT running (drift). Delegating to canonical scripts/deploy-cpe2e.sh ..."
  bash scripts/deploy-cpe2e.sh
fi

echo ""
echo "--> Waiting for registry-api rollout ..."
kubectl rollout status deploy/agentshield-registry-api -n "$NAMESPACE" --timeout=300s
echo "--> Waiting for scheduler rollout (HA — 2 replicas) ..."
kubectl rollout status deploy/agentshield-scheduler -n "$NAMESPACE" --timeout=300s

echo ""
echo "=== CP2 deploy verified. Now run: ==="
echo "  bash scripts/smoke-test-cp2-ws3-infra.sh && bash scripts/smoke-test-cp2-ws3-behaviour.sh"

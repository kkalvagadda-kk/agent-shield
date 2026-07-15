#!/usr/bin/env bash
# scripts/deploy-cp1-ws3.sh
#
# WS-3 Checkpoint 1 deploy wrapper (CP1a).
#
# Scope of CP1 (Execution Models v2 · WS-3 · Phases 2–3):
#   * The scheduled durable daemon AGENT path — a schedule trigger fires through
#     the REAL /internal/runs/start door, dispatches durable (WS-0), commits real
#     run_steps (WS-1), stamps the daemon SERVICE identity as run_by (WS-2), parks
#     + routes async to a reviewer, and resumes to completed.
#
# WS-3 adds NO backend code: registry-api 0.2.179 ALREADY carries the shared
# WS-0/1/2 dispatch + identity path, and declarative-runner 0.1.44 is unchanged.
# So this is a THIN, idempotent VERIFY wrapper — it confirms the already-deployed
# backend via `kubectl rollout status`. It runs NO bare helm/docker/kubectl for a
# deploy; it delegates to the canonical scripts/deploy-cpe2e.sh ONLY if the
# expected registry-api tag is not already running (drift recovery). No tag bump.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
EXPECT_TAG="${EXPECT_REGISTRY_API_TAG:-0.2.179}"

echo "=== WS-3 CP1 deploy (scheduled durable daemon AGENT — no backend bump) ==="
echo "  namespace:            $NAMESPACE"
echo "  expected registry-api tag: $EXPECT_TAG (WS-0/1/2 shared dispatch+identity path)"
echo "  declarative-runner:   0.1.44 (unchanged — WS-1 already updated it)"
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
echo "=== CP1 deploy verified. Now run: ==="
echo "  bash scripts/smoke-test-cp1-ws3-infra.sh && bash scripts/smoke-test-cp1-ws3-behaviour.sh"

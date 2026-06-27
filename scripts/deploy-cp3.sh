#!/usr/bin/env bash
# deploy-cp3.sh — Checkpoint 3: enable Safety Orchestrator + Scanners
#
# Adds to an already-running CPE2E stack:
#   - safety-orchestrator:0.1.0  (Phase 9 — fan-out scanner proxy)
#   - nemo-guardrails:0.6.0      (custom — regex-based injection scanner)
#   - llm-guard:0.4.0            (ghcr.io/protectai/llm-guard-api)
#   - presidio:2.2.354           (MCR analyzer + anonymizer)
#
# Pre-conditions: deploy-cpe2e.sh has been run and the core stack is healthy.
#
# Usage: bash scripts/deploy-cp3.sh
set -euo pipefail

RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="agentshield-platform"
TIMEOUT="15m"

SAFETY_ORCH_TAG="0.1.1"
NEMO_TAG="0.6.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> AgentShield CP3 Deploy — $(date)"
echo "    Enabling: safety-orchestrator, nemo-guardrails, llm-guard, presidio"
echo ""

# ── Step 1: Build custom images ───────────────────────────────────────────────
echo "[1/4] Building custom safety images..."

echo "  → safety-orchestrator:${SAFETY_ORCH_TAG}"
docker build -t "registry.internal/agentshield/safety-orchestrator:${SAFETY_ORCH_TAG}" \
  services/safety-orchestrator/

echo "  → nemo-guardrails:${NEMO_TAG} (regex-based injection scanner)"
docker build -t "registry.internal/agentshield/nemo-guardrails:${NEMO_TAG}" \
  services/nemo-guardrails/

echo "  Images built."

# ── Step 2: Pull third-party scanner images ────────────────────────────────────
echo ""
echo "[2/4] Pulling third-party scanner images (may take a few minutes)..."
echo "  → ghcr.io/protectai/llm-guard-api:0.4.0"
docker pull ghcr.io/protectai/llm-guard-api:0.4.0 || {
  echo "  WARNING: Could not pull LLM Guard image — will deploy with IfNotPresent policy"
  echo "           LLM Guard pod will fail to start if image is not cached"
}
echo "  → mcr.microsoft.com/presidio/presidio-analyzer:2.2.354"
docker pull mcr.microsoft.com/presidio/presidio-analyzer:2.2.354 || \
  echo "  WARNING: Could not pull Presidio analyzer image"
echo "  → mcr.microsoft.com/presidio/presidio-anonymizer:2.2.354"
docker pull mcr.microsoft.com/presidio/presidio-anonymizer:2.2.354 || \
  echo "  WARNING: Could not pull Presidio anonymizer image"

# ── Step 3: Helm upgrade — enable safety stack ─────────────────────────────────
echo ""
echo "[3/4] Helm upgrade — enabling safety stack..."

# Clean up stale realm-init job if it exists
kubectl delete job "${RELEASE}-realm-init" -n "$NAMESPACE" --ignore-not-found=true

helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --timeout "$TIMEOUT" \
  --reuse-values \
  \
  --set safety-orchestrator.enabled=true \
  --set "safety-orchestrator.image.tag=${SAFETY_ORCH_TAG}" \
  --set llm-guard.enabled=true \
  --set presidio.enabled=true \
  --set nemo.enabled=true \
  --set "nemo.image.tag=${NEMO_TAG}"

# ── Step 4: Wait for rollouts ─────────────────────────────────────────────────
echo ""
echo "[4/4] Waiting for safety service rollouts..."
kubectl rollout status deployment/agentshield-safety-orchestrator -n "$NAMESPACE" --timeout=3m || \
  echo "  WARNING: safety-orchestrator rollout not ready"
kubectl rollout status deployment/agentshield-nemo -n "$NAMESPACE" --timeout=3m || \
  echo "  WARNING: nemo rollout not ready"
# LLM Guard and Presidio are heavy — give them more time
kubectl rollout status deployment/agentshield-llm-guard -n "$NAMESPACE" --timeout=5m || \
  echo "  WARNING: llm-guard rollout not ready (image may still be pulling)"
kubectl rollout status deployment/agentshield-presidio -n "$NAMESPACE" --timeout=5m || \
  echo "  WARNING: presidio rollout not ready (image may still be pulling)"

echo ""
echo "================================================================"
echo "  AgentShield CP3 Deploy — COMPLETE"
echo "================================================================"
echo ""
kubectl get pods -n "$NAMESPACE" --no-headers | grep -E "llm-guard|presidio|nemo|safety-orch" | sort
echo ""
echo "Port-forward commands:"
echo "  Safety Orchestrator: kubectl port-forward svc/agentshield-safety-orchestrator -n ${NAMESPACE} 8082:8080"
echo ""
echo "Next: bash scripts/smoke-test-cp3-scanners.sh"

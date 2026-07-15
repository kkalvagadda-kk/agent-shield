#!/usr/bin/env bash
# scripts/checkpoints/cp1-deploy.sh
#
# === Checkpoint 1a: Context Storage POC-0 deploy ===
#
# Deploys the POC-0 backbone (migration 0064 + fail-loud AsyncPostgresSaver +
# DIRECT_DATABASE_URL/AGENTSHIELD_DEPLOYMENT_ID injection + session_id threading).
#
# TAG NOTE (read this): the context-storage slice deploys TWICE (POC-0 at CP1,
# POC-1 at CP2). A committed repo can only hold one tag per service, so the repo
# is committed at the FINAL (CP2) tags:
#     registry-api 0.2.186   declarative-runner 0.1.49   deploy-controller 0.1.37
# CP1a originally consumed 0.2.185 / 0.1.48 / 0.1.37; CP2 advanced registry-api +
# declarative-runner one more patch (deploy-controller was untouched in POC-1).
# This script therefore deploys the CURRENT committed repo tags and asserts they
# are present + roll out cleanly — it does NOT re-edit tags (the T-tasks already
# bumped both scripts/deploy-cpe2e.sh and charts/agentshield/values.yaml).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
cd "$REPO_ROOT"

echo "=== Checkpoint 1: Context Storage POC-0 deploy ==="

# --- 1. assert the three touched-service tags are committed in BOTH files ------
assert_tag() {
  local label="$1" file="$2" pattern="$3"
  if ! grep -q "$pattern" "$file"; then
    echo "FAIL: expected $label tag ($pattern) not found in $file"
    exit 1
  fi
  echo "  OK: $label present in $(basename "$file")"
}
assert_tag "registry-api 0.2.186"       scripts/deploy-cpe2e.sh 'REGISTRY_API_TAG="0.2.186"'
assert_tag "declarative-runner 0.1.49"  scripts/deploy-cpe2e.sh 'DECLARATIVE_RUNNER_TAG="0.1.49"'
assert_tag "deploy-controller 0.1.37"   scripts/deploy-cpe2e.sh 'DEPLOY_CONTROLLER_TAG="0.1.37"'
assert_tag "registry-api 0.2.186"       charts/agentshield/values.yaml 'tag: "0.2.186"'
assert_tag "declarative-runner 0.1.49"  charts/agentshield/values.yaml 'declarativeRunnerTag: "0.1.49"'
assert_tag "deploy-controller 0.1.37"   charts/agentshield/values.yaml 'tag: "0.1.37"'

# --- 2. build + push the 3 images and helm-upgrade -----------------------------
# (migration 0064 runs via the registry-api alembic init-container on rollout)
echo "  running deploy-cpe2e.sh (build + push + helm upgrade)..."
bash scripts/deploy-cpe2e.sh

# --- 3. assert the platform rollouts succeeded ---------------------------------
# declarative-runner has no standalone Deployment — it is the image the controller
# provisions for agent pods, so it re-rolls out per-agent on the next deploy.
echo "  asserting rollouts..."
kubectl rollout status deployment/agentshield-registry-api      -n "$NAMESPACE" --timeout=5m
kubectl rollout status deployment/agentshield-deploy-controller -n "$NAMESPACE" --timeout=3m

echo "PASS"

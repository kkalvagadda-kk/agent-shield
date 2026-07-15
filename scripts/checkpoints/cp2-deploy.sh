#!/usr/bin/env bash
# scripts/checkpoints/cp2-deploy.sh
#
# === Checkpoint 2a: Context Storage POC-1 deploy ===
#
# POC-1 re-touched registry-api (workflow_orchestrator.py) + declarative-runner
# (orchestrator.py, main.py) AFTER the CP1 deploy, so both carry a fresh tag
# (k8s never reuses a tag). deploy-controller was NOT re-touched in POC-1.
# Assumes T015 already bumped BOTH files to the CP2 tags:
#     registry-api 0.2.186   declarative-runner 0.1.49   deploy-controller 0.1.37
# This script asserts those tags are committed, deploys, and asserts rollout.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
cd "$REPO_ROOT"

echo "=== Checkpoint 2: Context Storage POC-1 deploy ==="

assert_tag() {
  local label="$1" file="$2" pattern="$3"
  grep -q "$pattern" "$file" || { echo "FAIL: $label ($pattern) not in $file"; exit 1; }
  echo "  OK: $label present in $(basename "$file")"
}
assert_tag "registry-api 0.2.186"      scripts/deploy-cpe2e.sh 'REGISTRY_API_TAG="0.2.186"'
assert_tag "declarative-runner 0.1.49" scripts/deploy-cpe2e.sh 'DECLARATIVE_RUNNER_TAG="0.1.49"'
assert_tag "registry-api 0.2.186"      charts/agentshield/values.yaml 'tag: "0.2.186"'
assert_tag "declarative-runner 0.1.49" charts/agentshield/values.yaml 'declarativeRunnerTag: "0.1.49"'

echo "  running deploy-cpe2e.sh (build + push + helm upgrade)..."
bash scripts/deploy-cpe2e.sh

echo "  asserting rollouts..."
kubectl rollout status deployment/agentshield-registry-api      -n "$NAMESPACE" --timeout=5m
kubectl rollout status deployment/agentshield-deploy-controller -n "$NAMESPACE" --timeout=3m

echo "PASS"

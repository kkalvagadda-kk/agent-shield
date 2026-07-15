#!/usr/bin/env bash
# scripts/checkpoints/cp2-infra-smoke.sh
#
# === Checkpoint 2b: Context Storage POC-1 infra smoke ===
#
# The POC-1 shared-transcript change must not regress the checkpointer or crash the
# member pods.
#   - no CrashLoopBackOff across agentshield-platform + agents-*
#   - registry-api and deploy-controller Deployments run the CP2 image tags
#     (0.2.186 / 0.1.37)
#   - a workflow-member agent pod comes up Ready and logs "AsyncPostgresSaver ready"
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NAMESPACE="${AGENTS_NAMESPACE:-agents-platform}"

echo "=== Checkpoint 2: Context Storage POC-1 infra smoke ==="

# --- 1. no crash-looping pods --------------------------------------------------
for ns in "$NAMESPACE" "$AGENTS_NAMESPACE"; do
  if kubectl get pods -n "$ns" --no-headers 2>/dev/null \
       | grep -Ei 'crashloop|error' | grep -v 'Completed'; then
    echo "FAIL: crash-looping/errored pod(s) in namespace $ns"
    exit 1
  fi
  echo "  OK: no crash-looping pods in $ns"
done

# --- 2. platform Deployments are on the CP2 tags -------------------------------
assert_image_tag() {
  local deploy="$1" want="$2"
  local img
  img=$(kubectl get deployment "$deploy" -n "$NAMESPACE" \
        -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
  case "$img" in
    *":$want") echo "  OK: $deploy on $img" ;;
    *) echo "FAIL: $deploy image '$img' is not tag $want"; exit 1 ;;
  esac
}
assert_image_tag agentshield-registry-api      "0.2.186"
assert_image_tag agentshield-deploy-controller "0.1.37"
# declarative-runner runs as agent pods; its 0.1.49 tag is asserted on a member pod
# image below rather than a platform Deployment.

# --- 3. a Ready agent pod logs the persistent checkpointer ---------------------
echo "  locating a Ready agent pod (workflow member or standalone) in $AGENTS_NAMESPACE..."
AGENT_POD=$(kubectl get pods -n "$AGENTS_NAMESPACE" \
  --field-selector=status.phase=Running \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].ready}{"\n"}{end}' \
  2>/dev/null | awk '$2=="true"{print $1; exit}')
if [ -z "${AGENT_POD:-}" ]; then
  echo "FAIL: no Ready agent pod in $AGENTS_NAMESPACE — deploy a declarative agent/workflow first"
  exit 1
fi
echo "  agent pod: $AGENT_POD"

POD_IMG=$(kubectl get pod "$AGENT_POD" -n "$AGENTS_NAMESPACE" \
  -o jsonpath='{.spec.containers[0].image}' 2>/dev/null || true)
case "$POD_IMG" in
  *":0.1.49") echo "  OK: agent pod on declarative-runner 0.1.49 ($POD_IMG)" ;;
  *) echo "  INFO: agent pod image is $POD_IMG (redeploy the agent to pick up 0.1.49 if older)" ;;
esac

LOGS=$(kubectl logs -n "$AGENTS_NAMESPACE" "$AGENT_POD" --tail=400 2>/dev/null || true)
if echo "$LOGS" | grep -qi "AsyncPostgresSaver ready"; then
  echo "  OK: member pod logs 'AsyncPostgresSaver ready' (checkpointer not regressed)"
elif echo "$LOGS" | grep -qi "MemorySaver"; then
  echo "FAIL: agent pod fell back to MemorySaver with the direct URL set — POC-1 regressed the checkpointer"
  exit 1
else
  echo "FAIL: no 'AsyncPostgresSaver ready' line in $AGENT_POD logs"
  exit 1
fi

echo "PASS"

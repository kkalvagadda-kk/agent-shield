#!/usr/bin/env bash
# scripts/checkpoints/cp1-infra-smoke.sh
#
# === Checkpoint 1b: Context Storage POC-0 infra smoke ===
#
# The crash-loop / fail-loud landmine check. POC-0 injects DIRECT_DATABASE_URL and
# switches agent pods to a PERSISTENT AsyncPostgresSaver that RAISES (never a silent
# MemorySaver) when the URL is set but the pool fails. This script proves the pods
# came up healthy on the injected env and actually went persistent.
#
#   - no pod in agentshield-platform or agents-* is CrashLoopBackOff/Error
#   - the deploy-controller carries DIRECT_DATABASE_URL to inject
#   - a real agent pod carries DIRECT_DATABASE_URL + AGENTSHIELD_DEPLOYMENT_ID,
#     logs "AsyncPostgresSaver ready" (NOT MemorySaver), and is Ready
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NAMESPACE="${AGENTS_NAMESPACE:-agents-platform}"

echo "=== Checkpoint 1: Context Storage POC-0 infra smoke ==="

# --- 1. no crash-looping pods in the platform or agents namespaces -------------
echo "  checking for CrashLoopBackOff / Error pods..."
for ns in "$NAMESPACE" "$AGENTS_NAMESPACE"; do
  if kubectl get pods -n "$ns" --no-headers 2>/dev/null \
       | grep -Ei 'crashloop|error' | grep -v 'Completed'; then
    echo "FAIL: crash-looping/errored pod(s) in namespace $ns"
    exit 1
  fi
  echo "  OK: no crash-looping pods in $ns"
done

# --- 2. deploy-controller has the direct URL to inject -------------------------
echo "  checking deploy-controller DIRECT_DATABASE_URL..."
DC_URL=$(kubectl exec -n "$NAMESPACE" deploy/agentshield-deploy-controller -- \
  printenv DIRECT_DATABASE_URL 2>/dev/null || true)
if [ -z "$DC_URL" ]; then
  echo "FAIL: deploy-controller has no DIRECT_DATABASE_URL (T005 secret wiring missing)"
  exit 1
fi
echo "  OK: deploy-controller DIRECT_DATABASE_URL is set (${DC_URL:0:12}...)"

# --- 3. a real agent pod is persistent, not MemorySaver ------------------------
echo "  locating a Ready agent pod in $AGENTS_NAMESPACE..."
AGENT_POD=$(kubectl get pods -n "$AGENTS_NAMESPACE" \
  --field-selector=status.phase=Running \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].ready}{"\n"}{end}' \
  2>/dev/null | awk '$2=="true"{print $1; exit}')
if [ -z "${AGENT_POD:-}" ]; then
  echo "FAIL: no Ready agent pod in $AGENTS_NAMESPACE — deploy a declarative agent first"
  echo "      (POC-0's checkpointer claim can only be proven on a live agent pod)"
  exit 1
fi
echo "  agent pod: $AGENT_POD"

# 3a. injected env present on the pod
for var in DIRECT_DATABASE_URL AGENTSHIELD_DEPLOYMENT_ID; do
  val=$(kubectl exec -n "$AGENTS_NAMESPACE" "$AGENT_POD" -- printenv "$var" 2>/dev/null || true)
  if [ -z "$val" ]; then
    echo "FAIL: agent pod $AGENT_POD missing $var (T005 injection did not land)"
    exit 1
  fi
  echo "  OK: agent pod has $var"
done

# 3b. the checkpointer actually went persistent (fail-loud landmine)
LOGS=$(kubectl logs -n "$AGENTS_NAMESPACE" "$AGENT_POD" --tail=400 2>/dev/null || true)
if echo "$LOGS" | grep -qi "AsyncPostgresSaver ready"; then
  echo "  OK: agent logs show 'AsyncPostgresSaver ready' (persistent checkpointer)"
elif echo "$LOGS" | grep -qi "MemorySaver"; then
  echo "FAIL: agent using MemorySaver with DIRECT_DATABASE_URL set — T005 injection or"
  echo "      T004 construction is broken. Do NOT re-add a silent fallback; fix the pool."
  exit 1
else
  echo "FAIL: no 'AsyncPostgresSaver ready' line in $AGENT_POD logs (checkpointer unconfirmed)."
  echo "      If the pod crash-looped with 'checkpointer init failed', the fail-loud path"
  echo "      fired (T004) — fix the URL/pool per research.md §5, never silence it."
  exit 1
fi

echo "PASS"

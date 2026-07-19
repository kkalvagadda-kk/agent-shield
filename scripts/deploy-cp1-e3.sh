#!/usr/bin/env bash
# scripts/deploy-cp1-e3.sh
#
# Eval v2 E-3 (scheduled eval — job_spec datasets + side-effect assertions) — CP1
# deploy wrapper.
#
# Delegates to scripts/deploy-cpe2e.sh (the ONE build+deploy path — never bare
# helm/docker/kubectl) and then waits for the rollouts E-3 depends on. It does NOT
# bump any image tag: the tags are owned by scripts/deploy-cpe2e.sh + values.yaml and
# bumped by the implementation task (E-3 T019). Re-bumping here would mint a tag the
# chart never points at — this wrapper only builds, deploys, and proves the rollout
# landed.
#
# E-3's rollout set:
#   registry-api        — resolves mode='scheduled' from the agent's schedule trigger
#                         (_resolve_eval_mode), the compatibility launch guard
#                         (_assert_mode_compatible), and the /eval/score mode=scheduled
#                         branch (side-effect-skewed weights, detail.job_spec)
#   eval-runner         — the MODE=scheduled branch: job_spec → input_payload +
#                         trigger_type='schedule' + trigger_payload, eval_mode=record
#                         when the item asserts side effects, trigger_payload persisted
#   studio              — the scheduled job-spec editor + the job-spec evidence render
#   declarative-runner  — NOT bumped by E-3 (no sdk/agentshield_sdk/ change — see
#                         e3/tasks.md §R6). It still runs the E-2 record/mock seam that
#                         E-3 rides, so its rollout is asserted, not rebuilt. If a task
#                         ever edits the SDK, the runner image pip-installs it and MUST
#                         be bumped (a stale runner made every E-1 trajectory score 0).
#
# Usage: bash scripts/deploy-cp1-e3.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== E-3 CP1 deploy — delegating to scripts/deploy-cpe2e.sh ==="
bash "$REPO_ROOT/scripts/deploy-cpe2e.sh"

echo ""
echo "=== waiting for the E-3 rollouts ==="
# Agent pods (declarative-runner) are reconciled per-deployment by the deploy-controller,
# not by a platform Deployment, so they are not rollout-status-able here; the behaviour
# smoke test proves the scheduled branch actually runs against a real agent pod.
for d in agentshield-registry-api agentshield-studio agentshield-deploy-controller; do
  if kubectl get deploy "$d" -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "  → rollout status $d"
    kubectl rollout status "deploy/$d" -n "$NAMESPACE" --timeout=300s
  else
    echo "  → $d not present, skipping"
  fi
done

echo ""
echo "✅ E-3 CP1 deploy complete."
echo "   Next: bash scripts/smoke-test-cp1-e3-infra.sh     (images + alembic 0063 + columns)"
echo "         bash scripts/smoke-test-cp1-e3-behaviour.sh (suite-75, the no-fakes gate)"

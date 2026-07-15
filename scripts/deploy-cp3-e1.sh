#!/usr/bin/env bash
# scripts/deploy-cp3-e1.sh
#
# Eval v2 E-1 Checkpoint 3 deploy wrapper (CP3a) — the no-fakes real-durable-eval
# gate.
#
# Scope of E-1 (durable trajectory + tool-call scoring):
#   * judge.py: score_trajectory (4 match modes) + score_tool_calls (tool-name +
#     args_match dict-subset + expect_approval park check) + weighted_mean.
#   * routers/playground.py: /eval/score durable branch (response+trajectory+
#     tool_call → weighted_mean); the step-update callback persists approval_id.
#   * services/eval-runner/main.py: MODE=durable branch (real durable run → poll →
#     self-approve → project run_steps → score; fail-closed).
#   * routers/datasets.py: persists `mode`; durable-variant 422 validation.
#   * schemas.py: DurableDatasetItem (structured ExpectedTrajectory).
#   * sdk/agentshield_sdk/durable.py: tool-boundary StepUpdate.output carries
#     {tool, args}  <-- THE PRODUCER. This is baked into the AGENT RUNTIME
#     (declarative-runner image), NOT registry-api. If declarative-runner is not
#     rebuilt with this SDK, agent run_steps carry no {tool,args} and the durable
#     trajectory eval scores nothing real. See the tag note below.
#
# Image tags (read from scripts/deploy-cpe2e.sh + charts/agentshield/values.yaml —
# this wrapper does NOT re-bump):
#   registry-api 0.2.180 · eval-runner 0.1.6 · studio 0.1.137
#   declarative-runner  <-- MUST bake the E-1 SDK durable.py {tool,args} emit.
#     ⚠️  As of the E-1 build the runner tag was left at 0.1.44 (pre-E-1 SDK).
#         Bump DECLARATIVE_RUNNER_TAG (deploy-cpe2e.sh) + declarativeRunnerTag
#         (values.yaml) to a new tag and let deploy-cpe2e.sh rebuild it, or the
#         gate cannot score a real trajectory (suite-72 T-S72-001/003 will fail).
#
# THIN, idempotent wrapper. Canonical build+push+helm is scripts/deploy-cpe2e.sh
# (no bare helm/docker/kubectl for the deploy). Re-running is safe: same tag =>
# no-op image, helm upgrade converges.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== Eval v2 E-1 CP3 deploy (no-fakes durable trajectory gate) ==="
echo "  namespace: $NAMESPACE"
echo "  tags (from deploy-cpe2e.sh + values.yaml):"
grep -E 'REGISTRY_API_TAG=|EVAL_RUNNER_TAG=|STUDIO_TAG=|DECLARATIVE_RUNNER_TAG=' scripts/deploy-cpe2e.sh | sed 's/^/    /'
echo ""

echo "--> Building + deploying via canonical scripts/deploy-cpe2e.sh ..."
bash scripts/deploy-cpe2e.sh

echo ""
echo "--> Waiting for rollouts ..."
kubectl rollout status deploy/agentshield-registry-api -n "$NAMESPACE" --timeout=420s
kubectl rollout status deploy/agentshield-studio       -n "$NAMESPACE" --timeout=300s
kubectl rollout status deploy/agentshield-deploy-controller -n "$NAMESPACE" --timeout=300s || true

echo ""
echo "=== CP3 deploy complete. Now run: ==="
echo "  bash scripts/smoke-test-cp3-e1-infra.sh && bash scripts/smoke-test-cp3-e1-behaviour.sh"

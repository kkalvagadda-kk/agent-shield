#!/usr/bin/env bash
# scripts/deploy-cp1-e5.sh
#
# Eval v2 E-5 Checkpoint 1 deploy wrapper — the no-fakes workflow-run-tree
# (member-path) eval gate.
#
# Scope of E-5 (score a workflow on its REAL run tree):
#   * judge.py: _match_sequence (the shared ordered-list matcher, reused by both
#     score_trajectory and the new score_member_path) + score_member_path (member
#     names, `ordered` default → a wrong route scores <1.0).
#   * routers/playground.py: /eval/score `mode=workflow` branch — member_path +
#     response + optional per-member rubric → weighted_mean (0.4/0.4/0.2);
#     detail{expected_member_path, actual_member_path, member_diff, per_member[]}.
#   * services/eval-runner/main.py: WORKFLOW_ID branch — launch a real workflow run,
#     walk the REAL tree (GET /workflows/{id}/runs/{run_id}/tree → ordered child
#     agent_names = member_path), read each per-member child's steps
#     (GET /agent-runs/{child}/steps) → score via mode=workflow; fail-closed on an
#     incomplete tree (never scored on an empty member path).
#   * studio: EvalResultsPage WorkflowEvidence (member-path dimension + member_diff
#     + per-member panel + run_id deep-link) · DatasetsPage WorkflowItemEditor.
#
# Image tags (read from scripts/deploy-cpe2e.sh + charts/agentshield/values.yaml —
# this wrapper does NOT re-bump):
#   registry-api 0.2.181 · eval-runner 0.1.8 · studio 0.1.138
#   declarative-runner unchanged (E-1 already made agent pods emit {tool,args};
#   E-5 reuses the same run_steps producer for the per-member zoom).
#
# THIN, idempotent wrapper. Canonical build+push+helm is scripts/deploy-cpe2e.sh
# (no bare helm/docker/kubectl for the deploy). Re-running is safe: same tag =>
# no-op image, helm upgrade converges.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== Eval v2 E-5 CP1 deploy (no-fakes workflow run-tree / member-path gate) ==="
echo "  namespace: $NAMESPACE"
echo "  tags (from deploy-cpe2e.sh + values.yaml):"
grep -E 'REGISTRY_API_TAG=|EVAL_RUNNER_TAG=|STUDIO_TAG=' scripts/deploy-cpe2e.sh | sed 's/^/    /'
echo ""

echo "--> Building + deploying via canonical scripts/deploy-cpe2e.sh ..."
bash scripts/deploy-cpe2e.sh

echo ""
echo "--> Waiting for rollouts ..."
kubectl rollout status deploy/agentshield-registry-api -n "$NAMESPACE" --timeout=420s
kubectl rollout status deploy/agentshield-studio       -n "$NAMESPACE" --timeout=300s
kubectl rollout status deploy/agentshield-deploy-controller -n "$NAMESPACE" --timeout=300s || true

echo ""
echo "=== CP1 deploy complete. Now run: ==="
echo "  bash scripts/smoke-test-cp1-e5-infra.sh && bash scripts/smoke-test-cp1-e5-behaviour.sh"

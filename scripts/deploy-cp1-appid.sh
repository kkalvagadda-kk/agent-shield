#!/usr/bin/env bash
# scripts/deploy-cp1-appid.sh — Webhook Application Identity (Decision 30) Checkpoint 1 deploy.
#
# Thin, idempotent wrapper. It DELEGATES to scripts/deploy-cpe2e.sh and never runs bare
# helm/docker/kubectl for the deploy itself (matches scripts/deploy-cp1-ws4.sh's precedent —
# a code edit that is not built AND deployed leaves the pod on OLD code, and every check then
# passes against the wrong bytes). CLAUDE.md's mandatory per-change image-tag bump (never reuse
# a tag) is done in the SAME commit as this script, in deploy-cpe2e.sh + values.yaml.
#
# Scope built here: registry-api:0.2.211 only — migrations 0069/0070, Application model,
# rbac.py extensions, artifact_grants.py + applications.py routers (Phases 2-3, tasks T001-T008).
# event-gateway/studio are untouched by this checkpoint; their rollout is not waited on here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "==> Webhook Application Identity CP1 deploy: registry-api 0.2.211"
echo "    (migrations 0069/0070 apply via the registry-api alembic init container)"
echo ""

bash scripts/deploy-cpe2e.sh

echo ""
echo "==> Waiting for registry-api rollout"
kubectl rollout status deploy/agentshield-registry-api -n "$NAMESPACE" --timeout=180s

echo ""
echo "✅ CP1 deploy complete. Next:"
echo "   bash scripts/smoke-test-cp1-appid-infra.sh && bash scripts/smoke-test-cp1-appid-behaviour.sh"

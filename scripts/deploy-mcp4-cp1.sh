#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP1a — MCP Phase 4 (WS-1 CredentialProvider seam): deploy registry-api ONLY.
#
# Deploys the registry-api changes for Phases 2-3 (the pluggable credential seam,
# Decision 31):
#   - credential_provider.py — CredentialRef / CredentialProvider Protocol /
#     FernetPgProvider / (opt-in) AwsSecretsManagerProvider / get_provider()
#   - models.py — CredentialBlob + AuthConfig.credential_ref
#   - migration 0073 — credential_blobs table + auth_configs.credential_ref +
#     idempotent backfill (copies every existing Fernet blob VERBATIM — no re-encrypt)
#   - the two rewired call sites: mcp_secrets.materialize_server_secret reads creds
#     through get_provider(); routers/auth_configs.py put+set credential_ref; each with
#     an explicit legacy (null-ref → credentials_encrypted column) branch.
# The mcp-proxy is NOT part of WS-1 (the proxy OAuth read lands in Phase 4 CP3).
#
# What it does (exactly what CP1a specifies):
#   0. Bump REGISTRY_API_TAG 0.2.228 -> 0.2.229 in BOTH scripts/deploy-cpe2e.sh and
#      charts/agentshield/values.yaml (idempotent sed; re-run = no-op). This is the
#      deploy step's tag bump — it runs HERE, at deploy time, NOT committed to the repo
#      when this script was written (never reuse a tag; K8s caches images by tag).
#   1. Build the registry-api image at the tag pinned in charts/agentshield/values.yaml
#   2. helm upgrade the chart (tags baked into values.yaml — no --set)
#   3. kubectl rollout status deploy/agentshield-registry-api
#   4. kubectl exec ... alembic upgrade head  (applies 0073 + runs the backfill)
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh` rebuilds + redeploys every
# service. This is the registry-api-scoped WS-1 deploy.
set -euo pipefail

echo "=== Checkpoint MCP4-CP1: deploy registry-api (migration 0073 + credential provider seam) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="${TIMEOUT:-10m}"

PREV_REGISTRY_API_TAG="${PREV_REGISTRY_API_TAG:-0.2.228}"
EXPECTED_REGISTRY_API_TAG="${EXPECTED_REGISTRY_API_TAG:-0.2.229}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── 0. Bump the registry-api tag 0.2.228 -> 0.2.229 in BOTH files (idempotent) ──
# The WS-1 tag bump is the deploy step's job (CP1a). It runs at deploy time here so the
# repo is not mutated when the script is written. Guarded to the exact prev tag, so a
# re-run (or a repo already on 0.2.229) is a no-op. Portable sed (-i.bak works on both
# BSD/macOS and GNU); the .bak files are removed immediately after.
echo "--- [0/4] Bump REGISTRY_API_TAG ${PREV_REGISTRY_API_TAG} -> ${EXPECTED_REGISTRY_API_TAG} (deploy-cpe2e.sh + values.yaml) ..."
sed -i.bak "s/^REGISTRY_API_TAG=\"${PREV_REGISTRY_API_TAG}\"/REGISTRY_API_TAG=\"${EXPECTED_REGISTRY_API_TAG}\"/" scripts/deploy-cpe2e.sh
sed -i.bak "s/tag: \"${PREV_REGISTRY_API_TAG}\"/tag: \"${EXPECTED_REGISTRY_API_TAG}\"/" "$CHART/values.yaml"
rm -f scripts/deploy-cpe2e.sh.bak "$CHART/values.yaml.bak"

# ── Resolve the registry-api image tag from values.yaml (the source of truth) ──
read_tag() {  # $1 = top-level chart key
  local t
  t="$(yq ".[\"$1\"].image.tag" "$CHART/values.yaml")"
  if [[ -z "$t" || "$t" == "null" ]]; then
    echo "FAIL: could not read $1 image tag from $CHART/values.yaml" >&2
    exit 1
  fi
  printf '%s' "$t"
}
REGISTRY_API_TAG="$(read_tag registry-api)"
if [[ "$REGISTRY_API_TAG" != "$EXPECTED_REGISTRY_API_TAG" ]]; then
  echo "FAIL: registry-api tag in values.yaml is ${REGISTRY_API_TAG}, expected ${EXPECTED_REGISTRY_API_TAG}" >&2
  echo "      (the WS-1 bump did not take — check the current tag and re-run, or bump by hand:" >&2
  echo "       REGISTRY_API_TAG in scripts/deploy-cpe2e.sh + registry-api.image.tag in $CHART/values.yaml)." >&2
  exit 1
fi
REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}"
echo "--- registry-api image: ${REGISTRY_API_IMAGE}"

# ── 1. Build the registry-api image at the pinned tag ─────────────────────────
echo "--- [1/4] Building registry-api image ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/

# ── 2. helm upgrade (tags baked into values.yaml, no --set) ───────────────────
echo "--- [2/4] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

# ── 3. Wait for the registry-api rollout ──────────────────────────────────────
echo "--- [3/4] Waiting for registry-api rollout ..."
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

# ── 4. Apply alembic migration 0073 (upgrade head + backfill) ─────────────────
# The alembic-migrate init container already runs `alembic upgrade head` on pod start;
# this explicit exec is idempotent belt-and-suspenders so the deploy asserts 0073 landed.
echo "--- [4/4] Applying alembic migrations (upgrade head -> 0073 + backfill) ..."
kubectl exec -n "$NAMESPACE" deploy/agentshield-registry-api -c registry-api -- alembic upgrade head

echo ""
echo "Checkpoint MCP4-CP1 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp4-cp1-infra.sh && bash scripts/smoke-mcp4-cp1-behaviour.sh"
echo "PASS"

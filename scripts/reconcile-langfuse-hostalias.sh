#!/usr/bin/env bash
# scripts/reconcile-langfuse-hostalias.sh
#
# Reconcile the langfuse-web pod's hostAlias to the LIVE gateway-port-8443 ClusterIP.
#
# WHY THIS EXISTS
#   langfuse-web must reach Keycloak's OIDC endpoints at the PUBLIC issuer host
#   (agentshield.127.0.0.1.nip.io:8443) from INSIDE the cluster, where that name resolves
#   to 127.0.0.1 (loopback). A hostAlias redirects it to the in-cluster gateway-port-8443
#   Service (maps 8443 -> Gateway HTTPS, in envoy-gateway-system). That Service's ClusterIP
#   is assigned dynamically by Kubernetes and is DIFFERENT on every fresh cluster, so any IP
#   baked into charts/agentshield/values.yaml goes STALE the moment you redeploy on a new
#   cluster. langfuse-web then silently fails the SSO back-channel (server-side OIDC token
#   exchange), and Langfuse trace links show "You do not have access to this trace / Sign In"
#   even with a valid Studio login. See docs/bugs/langfuse-hostalias-stale-clusterip.md.
#
#   Never hardcode the IP: fetch the live ClusterIP after each deploy and reconcile the
#   hostAlias to match. This is the ONE place that logic lives; every install/update script
#   calls it after `helm upgrade` so no deploy path can leave the link broken.
#
# SAFE EVERYWHERE
#   * Idempotent — patches (and rolls langfuse-web) ONLY when the IP set has drifted, so a
#     no-op deploy costs nothing.
#   * REPLACES the whole hostAliases list, so a stale entry left behind by a `helm upgrade`
#     (which re-adds the values.yaml placeholder alongside a prior live patch) can't linger.
#   * Skips cleanly when langfuse is not deployed (langfuse.enabled=false — e.g. cp1/cp2/eks),
#     so it is harmless to call from every script.
#
# USAGE
#   bash scripts/reconcile-langfuse-hostalias.sh [namespace]
#   env overrides: NAMESPACE (default agentshield-platform), GATEWAY_NS (default
#   envoy-gateway-system), HOSTALIAS_NAME (default agentshield.127.0.0.1.nip.io),
#   LANGFUSE_WEB_DEPLOY (default agentshield-langfuse-web).
set -uo pipefail

NS="${1:-${NAMESPACE:-agentshield-platform}}"
GW_NS="${GATEWAY_NS:-envoy-gateway-system}"
HOST="${HOSTALIAS_NAME:-agentshield.127.0.0.1.nip.io}"
DEPLOY="${LANGFUSE_WEB_DEPLOY:-agentshield-langfuse-web}"

log() { echo "  [langfuse-hostalias] $*"; }

# langfuse deployed in this namespace? If not, nothing to reconcile.
if ! kubectl get deploy "$DEPLOY" -n "$NS" >/dev/null 2>&1; then
  log "$DEPLOY not found in $NS — langfuse not deployed, skipping."
  exit 0
fi

log "reconciling $DEPLOY hostAlias -> live $GW_NS/gateway-port-8443 ClusterIP ..."

# Fetch the live gateway-port-8443 ClusterIP (retry — it may be seconds behind a helm apply).
GW_IP=""
for _ in $(seq 1 30); do
  GW_IP=$(kubectl get svc gateway-port-8443 -n "$GW_NS" -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
  [ -n "$GW_IP" ] && [ "$GW_IP" != "None" ] && break
  sleep 2
done
if [ -z "$GW_IP" ]; then
  log "WARNING: gateway-port-8443 Service not found in $GW_NS — trace-link SSO will fail until reconciled."
  exit 0
fi

# The set of IPs the deployment currently maps HOST to (sorted+deduped), e.g. "10.96.158.172,".
CUR_IPS=$(kubectl get deploy "$DEPLOY" -n "$NS" \
  -o jsonpath='{.spec.template.spec.hostAliases[*].ip}' 2>/dev/null | tr ' ' '\n' | sort -u | grep -v '^$' | paste -sd, -)

if [ "$CUR_IPS" = "$GW_IP" ]; then
  log "already current ($GW_IP) — no change."
else
  log "hostAlias ${CUR_IPS:-<none>} -> $GW_IP (patching + rolling $DEPLOY)"
  kubectl patch deploy "$DEPLOY" -n "$NS" --type=merge \
    -p "{\"spec\":{\"template\":{\"spec\":{\"hostAliases\":[{\"ip\":\"$GW_IP\",\"hostnames\":[\"$HOST\"]}]}}}}"
  kubectl rollout status deployment/"$DEPLOY" -n "$NS" --timeout=3m || true
fi

# Self-verify the OIDC back-channel from inside the (re)rolled pod — surface breakage HERE,
# at deploy time, instead of at the next trace click.
POD=$(kubectl get pod -n "$NS" --no-headers 2>/dev/null | grep langfuse-web | grep Running | awk '{print $1}' | head -1)
if [ -n "$POD" ] && kubectl exec -n "$NS" "$POD" -c langfuse-web -- \
     wget -qO- --no-check-certificate --timeout=8 \
     "https://$HOST:8443/realms/agentshield/.well-known/openid-configuration" >/dev/null 2>&1; then
  log "verified: langfuse-web reaches Keycloak OIDC discovery (SSO back-channel OK)."
else
  log "WARNING: langfuse-web still cannot reach Keycloak OIDC discovery — trace-link SSO may fail."
fi

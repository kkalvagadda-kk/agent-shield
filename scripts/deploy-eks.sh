#!/usr/bin/env bash
# =============================================================================
# deploy-eks.sh — deploy AgentShield to a self-managed k8s cluster on AWS EC2
#                 (EBS-CSI storage, aws-load-balancer-controller), exposed via
#                 an INTERNAL AWS NLB in front of Envoy Gateway.
#
# This is the CLOUD sibling of scripts/deploy-cpe2e.sh (which is local/kind only
# and CANNOT work here: it builds `registry.internal/...` images and relies on a
# shared Docker daemon; EC2 nodes can't see local images).
#
# Usage:
#   KUBECONFIG=~/.kube/test-cluster-kube-config.yaml bash scripts/deploy-eks.sh
#   SKIP_BUILD=1 ... bash scripts/deploy-eks.sh     # reuse images already in ECR
#
# WHAT BIT US (encoded here so it never bites again) --------------------------
#  1. amd64 != AMD. `amd64` is the x86-64 ISA (runs on Intel AND AMD). Building
#     on an Apple-silicon Mac yields arm64 -> nodes fail with
#     "no match for platform in manifest". ALWAYS buildx --platform linux/amd64.
#  2. pgvector AVX-512 SIGILL. Bitnami's stock pgvector 0.8.0 is compiled
#     -march=native on an AVX-512 builder. On nodes WITHOUT AVX-512 (Broadwell
#     t2; AMD Zen1-3 c5a/c6a/t3a) `CREATE INDEX ... USING ivfflat` kills the
#     backend (signal 4: Illegal instruction) -> the whole Alembic transaction
#     rolls back -> 0 tables -> registry-api init-crashloops forever.
#     Fix: services/postgresql-pgvector (pgvector rebuilt with OPTFLAGS="").
#  3. global.imageRegistry is a BITNAMI global — setting it also repoints
#     postgres/redis. The chart uses global.appImageRegistry instead.
#  4. Vendored .tgz go stale: re-run `helm dependency update` after ANY
#     sub-chart template edit or helm silently uses the OLD packaged chart.
#  5. Envoy Gateway v1.8.2 CrashLoops on Gateway-API v1.4.1 CRDs (wants TLSRoute
#     at v1; cluster serves v1alpha2/3). v1.7.5 works.
#  6. The internal-NLB annotations must exist at Service CREATION (scheme is
#     immutable) -> EnvoyProxy CRD + GatewayClass parametersRef (in the chart).
#  7. AGENT pods don't use the namespace default SA — they run under a per-agent
#     SA (machine identity), so patching default's imagePullSecrets does NOT
#     reach them and every agent ImagePullBackOffs with "no basic auth
#     credentials". deploy-controller >=0.1.38 puts imagePullSecrets on the pod
#     spec via AGENT_IMAGE_PULL_SECRETS (chart: global.imagePullSecrets).
#     Invisible on kind, which side-loads images and needs no auth at all.
#  8. infra/ is NOT part of the chart. opa-bundle-server + the opa-sidecar-config
#     ConfigMap + playground RBAC are applied separately (step 3b). Without the
#     ConfigMap every agent pod hangs in ContainerCreating; the controller only
#     self-creates it on the PRODUCTION path, never for sandbox deploys.
#  9. Anything injected into AGENT pods must be namespace-QUALIFIED. Agents run
#     in agents-*, so a bare `agentshield-postgresql` does not resolve there and
#     the fail-loud checkpointer CrashLoopBackOffs the pod. postgres-passwords
#     therefore stores `<release>-postgresql.<ns>` — correct from every namespace
#     (registry-api included). Same reason LANGFUSE_HOST is qualified.
#
# RESTORING A BACKUP AFTERWARDS: see the note at the bottom — you MUST scale the
# DB clients to 0 first or the restore SILENTLY half-applies.
# =============================================================================
set -euo pipefail

# ── Config ───────────────────────────────────────────────────────────────────
AWS_PROFILE_NAME="${AWS_PROFILE_NAME:-kkalyan-aws-key}"
REGION="${REGION:-us-west-2}"
ACCOUNT="${ACCOUNT:-517602344783}"
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
NS="${NS:-agentshield-platform}"
RELEASE="${RELEASE:-agentshield}"
EG_VERSION="${EG_VERSION:-v1.7.5}"      # v1.8.2 is INCOMPATIBLE — see note 5
CHART="charts/agentshield"
VALUES="charts/agentshield/values-eks.yaml"
SKIP_BUILD="${SKIP_BUILD:-0}"

# Image tags (keep in sync with values-eks.yaml / values.yaml)
REGISTRY_API_TAG="0.2.224"   # 0.2.224: + HITL reactive-chat approval-status fix (_chat_thread_id keyed by session_id; cherry-picked from fix/reactive-hitl-approval-poll 922c04b). 0.2.223: fix migration 0070 down_revision 0069->0068. 0.2.222: create_grant flips webhook auth_mode->client_signed (T-SYY-002). 0.2.221: Decision 30 — matches values.yaml
DEPLOY_CONTROLLER_TAG="0.1.39"   # 0.1.39: per-provider env map (base_url→OLLAMA_BASE_URL); >=0.1.38 imagePullSecrets on agent pods (note 7)
DECLARATIVE_RUNNER_TAG="0.1.59"   # 0.1.59: matches values.yaml declarativeRunnerTag (0.1.57 SDK ChatOllama; 0.1.56 POC-3 user_directive; carries OPA bypass task #16)
STUDIO_TAG="0.1.158"   # 0.1.158: matches values.yaml (branch made no studio source change — CP3/frontend rebuilds this again after Phases 6-8)
SCHEDULER_TAG="0.1.1"
EVENT_GATEWAY_TAG="0.1.4"   # 0.1.4: Decision 30 gateway cutover — webhook_auth.py resolves applications+artifact_role_grants (not webhook_clients) — matches values.yaml
PYTHON_EXECUTOR_TAG="0.1.0"
EVAL_RUNNER_TAG="0.1.10"
MINIO_CP1_TAG="0.1.0"
PGVECTOR_TAG="17.6.0-portable"

# Platform credentials (dev defaults; match scripts/deploy-cpe2e.sh AND the
# pg_dumpall backups so a restore doesn't break auth).
PG_PASS="${PG_PASS:-DevPass2024}"
REDIS_PASS="${REDIS_PASS:-RedisPass2024}"
MINIO_USER="${MINIO_USER:-agentshield-admin}"
MINIO_PASS="${MINIO_PASS:-MinioPass2024}"
KC_ADMIN_PASS="${KC_ADMIN_PASS:-AdminPass2024}"
KC_PLATFORM_ADMIN_PASS="${KC_PLATFORM_ADMIN_PASS:-PlatformAdmin2024}"
KC_REVIEWER_PASS="${KC_REVIEWER_PASS:-Reviewer2024}"
ENCRYPTION_KEY="${ENCRYPTION_KEY:-dGVzdGtleS10ZXN0a2V5LXRlc3RrZXktdGVzdGtleTA=}"

export AWS_PROFILE="$AWS_PROFILE_NAME"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== AgentShield EKS/EC2 deploy ==="
echo "    cluster : $(kubectl config current-context)"
echo "    ecr     : ${ECR}"
echo "    ns      : ${NS}"

# ── Step 0: preflight ────────────────────────────────────────────────────────
echo ""
echo "[0/7] Preflight..."
# Retry, don't abort on the first blip: the private API endpoint (VPN) flaps, and a
# single failed /readyz should not throw away a 30-min build. ~5 min of patience.
_wait_reachable() {
  local i
  for i in $(seq 1 30); do
    kubectl get --raw='/readyz' >/dev/null 2>&1 && return 0
    echo "  cluster not reachable yet (attempt $i/30) — retry in 10s (VPN flapping?)"
    sleep 10
  done
  return 1
}
_wait_reachable || { echo "FATAL: cluster unreachable after ~5min (VPN up?)"; exit 1; }
kubectl get storageclass block-storage >/dev/null 2>&1 || echo "  WARN: no 'block-storage' StorageClass — PVCs may not bind"
kubectl get deploy -n kube-system aws-load-balancer-controller >/dev/null 2>&1 || echo "  WARN: aws-load-balancer-controller absent — the internal NLB won't provision"
# Warn loudly if nodes lack AVX-512 — we ship the portable pgvector anyway, but
# this is the #1 historical failure and worth surfacing.
echo "  note: using portable pgvector (${PGVECTOR_TAG}) — safe on nodes without AVX-512"

# ── Step 1: ECR repos + login ────────────────────────────────────────────────
echo ""
echo "[1/7] ECR login + repos..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR" >/dev/null
for r in registry-api deploy-controller declarative-runner studio scheduler event-gateway \
         python-executor eval-runner minio-cp1 postgresql-pgvector; do
  aws ecr describe-repositories --repository-names "agentshield/$r" --region "$REGION" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "agentshield/$r" --region "$REGION" >/dev/null
done
echo "  10 repos ready"

# ── Step 2: build + push (linux/amd64!) ──────────────────────────────────────
if [ "$SKIP_BUILD" = "1" ]; then
  echo ""
  echo "[2/7] SKIP_BUILD=1 — reusing images already in ECR"
else
  echo ""
  echo "[2/7] Building + pushing images (linux/amd64)..."
  docker buildx inspect eksbuilder >/dev/null 2>&1 || docker buildx create --name eksbuilder >/dev/null
  docker buildx use eksbuilder
  b() { # <svc> <tag> <context> [dockerfile]
    local svc="$1" tag="$2" ctx="$3" df="${4:-}" attempt
    echo "  -> $svc:$tag"
    # Retry build+push: the ECR push is the other thing the flapping VPN/DNS kills
    # ("no such host" / EOF mid-upload). buildx layer cache makes a retry cheap (only
    # the missing layers re-push), and we refresh the ECR token each round in case it
    # was the ~12h expiry rather than the network.
    for attempt in 1 2 3 4; do
      if [ -n "$df" ]; then
        docker buildx build --platform linux/amd64 -t "$ECR/agentshield/$svc:$tag" -f "$df" --push "$ctx" >/dev/null && return 0
      else
        docker buildx build --platform linux/amd64 -t "$ECR/agentshield/$svc:$tag" --push "$ctx" >/dev/null && return 0
      fi
      echo "     $svc:$tag build/push attempt $attempt/4 failed — refresh ECR auth + retry in 15s"
      aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR" >/dev/null 2>&1 || true
      sleep 15
    done
    echo "FATAL: $svc:$tag failed to build/push after 4 attempts (VPN/DNS?)"; return 1
  }
  b registry-api        "$REGISTRY_API_TAG"       services/registry-api/
  b deploy-controller   "$DEPLOY_CONTROLLER_TAG"  services/deploy-controller/
  b declarative-runner  "$DECLARATIVE_RUNNER_TAG" .  services/declarative-runner/Dockerfile
  b studio              "$STUDIO_TAG"             studio/
  b scheduler           "$SCHEDULER_TAG"          services/scheduler/
  b event-gateway       "$EVENT_GATEWAY_TAG"      services/event-gateway/
  b python-executor     "$PYTHON_EXECUTOR_TAG"    services/python-executor/
  b eval-runner         "$EVAL_RUNNER_TAG"        services/eval-runner/
  b minio-cp1           "$MINIO_CP1_TAG"          services/minio-cp1/
  b postgresql-pgvector "$PGVECTOR_TAG"           services/postgresql-pgvector/
fi

# ── Step 3: namespaces + ECR pull secret ─────────────────────────────────────
echo ""
echo "[3/7] Namespaces + ECR pull secret..."
for ns in "$NS" agents-platform agentshield-playground; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
done
TOKEN="$(aws ecr get-login-password --region "$REGION")"
for ns in "$NS" agents-platform agentshield-playground; do
  kubectl create secret docker-registry agentshield-ecr \
    --docker-server="$ECR" --docker-username=AWS --docker-password="$TOKEN" \
    -n "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  # studio + the raw templates do NOT propagate global.imagePullSecrets — patch
  # the default SA so every pod in the ns can pull from ECR.
  kubectl patch serviceaccount default -n "$ns" \
    -p '{"imagePullSecrets":[{"name":"agentshield-ecr"}]}' >/dev/null
done
echo "  3 namespaces + pull secret + SA patched"
echo "  NOTE: the ECR token expires in ~12h. Re-run this step to refresh it."
# NOTE: patching the *default* SA does NOT cover agent pods — they run under a
# per-agent SA (machine identity). Their pull secret comes from the controller's
# AGENT_IMAGE_PULL_SECRETS env (chart: global.imagePullSecrets), deploy-controller
# >= 0.1.38. Older controllers => agents ImagePullBackOff "no basic auth credentials".

# ── Step 3b: cluster infra the chart does NOT own ────────────────────────────
# These live in infra/ and are applied by deploy-cpe2e.sh, so they are easy to
# miss on a fresh cluster. Both are load-bearing:
#  - opa-bundle-server: nginx that serves the policy bundle registry-api builds;
#    every agent's OPA sidecar polls it.
#  - opa-sidecar-config: mounted into each agent pod's OPA sidecar. MISSING => the
#    pod hangs forever in ContainerCreating ("configmap opa-sidecar-config not
#    found"). The controller only self-creates it on the PRODUCTION path
#    (production_reconciler), never for sandbox deploys.
#  - playground-runner: ClusterRole+Binding letting registry-api drive playground
#    pods/jobs.
echo ""
echo "[3b/7] Cluster infra (OPA bundle server + sidecar config + playground RBAC)..."
kubectl apply -f infra/opa-bundle-server/configmap-nginx-conf.yaml >/dev/null
kubectl apply -f infra/opa-bundle-server/service.yaml >/dev/null
kubectl apply -f infra/opa-bundle-server/deployment.yaml >/dev/null
kubectl apply -f infra/opa-bundle-server/configmap-opa-config.yaml >/dev/null
kubectl apply -f infra/rbac/playground-runner-clusterrole.yaml >/dev/null
echo "  opa-bundle-server + opa-sidecar-config + playground-runner applied"

# ── Step 4: platform secrets ─────────────────────────────────────────────────
echo ""
echo "[4/7] Platform secrets..."
kubectl create secret generic agentshield-secrets -n "$NS" \
  --from-literal=registry-api-url="http://${RELEASE}-registry-api.${NS}:8000" \
  --from-literal=database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --from-literal=direct-database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic agentshield-encryption -n "$NS" \
  --from-literal=key="${ENCRYPTION_KEY}" \
  --from-literal=AGENTSHIELD_ENCRYPTION_KEY="${ENCRYPTION_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic postgres-passwords -n "$NS" \
  --from-literal=keycloak="${PG_PASS}" --from-literal=agentshield="${PG_PASS}" \
  --from-literal=langfuse="${PG_PASS}" --from-literal=langgraph="${PG_PASS}" \
  --from-literal=appsmith="${PG_PASS}" \
  --from-literal=registry-api-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql.${NS}:5432/agentshield" \
  --from-literal=registry-api-direct-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql.${NS}:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# The host is namespace-QUALIFIED deliberately — see note 9 in the header.
kubectl create secret generic redis-password -n "$NS" \
  --from-literal=redis-password="${REDIS_PASS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic minio-credentials -n "$NS" \
  --from-literal=root-user="${MINIO_USER}" --from-literal=root-password="${MINIO_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic keycloak-admin-password -n "$NS" \
  --from-literal=admin-password="${KC_ADMIN_PASS}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic keycloak-user-passwords -n "$NS" \
  --from-literal=platform-admin="${KC_PLATFORM_ADMIN_PASS}" \
  --from-literal=agent-reviewer="${KC_REVIEWER_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic langfuse-api-keys -n "$NS" \
  --from-literal=public-key="pk-lf-agentshield-dev-local-0001" \
  --from-literal=secret-key="sk-lf-agentshield-dev-local-0001" \
  --from-literal=nextauth-secret="agentshield-nextauth-dev-2024-sec" \
  --from-literal=salt="$(openssl rand -base64 32)" \
  --from-literal=encryption-key="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create secret generic slack-credentials -n "$NS" \
  --from-literal=bot-token="xoxb-placeholder-dev-token" \
  --from-literal=signing-secret="placeholder-signing-secret-dev" \
  --from-literal=webhook-url="https://hooks.slack.com/services/placeholder/dev" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "  9 platform secrets"

# ── Step 4b: gateway TLS ─────────────────────────────────────────────────────
# The Gateway's HTTPS listener references Secret `gateway-tls` via
# certificateRef. TLS is REQUIRED for login: Keycloak's PKCE uses Web Crypto,
# which browsers only expose in a secure context (https). Plain http => no login.
#
# Chicken-and-egg dodge: the ELB DNS isn't known until the Gateway's Service is
# created, but a wildcard SAN `*.elb.<region>.amazonaws.com` matches ANY ELB
# hostname in the region (the ELB name is a single DNS label), so we can mint the
# cert up-front. Self-signed => browser warning; click through and the secure
# context is still valid. Swap in a real cert (or ACM + DNS) for anything real.
#
# CN is a fixed short string, NOT the hostname: X.509 caps CN at 64 bytes and an
# ELB DNS name is ~77 (`openssl req` then dies with "string too long"). Identity
# lives in the SAN, which has no length cap and is the only field browsers have
# honoured since Chrome 58 / RFC 2818 deprecated CN matching.
echo ""
echo "[4b/7] Gateway TLS cert (self-signed, wildcard SAN)..."
if kubectl get secret gateway-tls -n "$NS" >/dev/null 2>&1; then
  echo "  gateway-tls already exists — keeping it (delete it to regenerate)"
else
  TLSDIR="$(mktemp -d)"
  # stderr goes to a file, not /dev/null: openssl's progress dots are stderr, so
  # blanket-suppressing it also hides real errors (that masked the CN overflow).
  if ! openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout "$TLSDIR/tls.key" -out "$TLSDIR/tls.crt" \
      -subj "/CN=AgentShield Gateway/O=AgentShield" \
      -addext "subjectAltName=DNS:*.elb.${REGION}.amazonaws.com" \
      -addext "basicConstraints=critical,CA:FALSE" \
      -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
      -addext "extendedKeyUsage=serverAuth" 2>"$TLSDIR/err"; then
    echo "ERROR: openssl failed to build the gateway cert:" >&2
    grep -i "error" "$TLSDIR/err" >&2 || cat "$TLSDIR/err" >&2
    rm -rf "$TLSDIR"; exit 1
  fi
  kubectl create secret tls gateway-tls \
    --cert="$TLSDIR/tls.crt" --key="$TLSDIR/tls.key" -n "$NS" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  rm -rf "$TLSDIR"
  echo "  gateway-tls created (self-signed, SAN *.elb.${REGION}.amazonaws.com)"
fi

# ── Step 5: Envoy Gateway controller ─────────────────────────────────────────
echo ""
echo "[5/7] Envoy Gateway controller (${EG_VERSION})..."
if helm status eg -n envoy-gateway-system >/dev/null 2>&1; then
  echo "  already installed"
else
  EG_VERSION="$EG_VERSION" bash scripts/setup-envoy-gateway.sh >/dev/null 2>&1 || true
  kubectl wait --timeout=180s -n envoy-gateway-system \
    --for=condition=Available deployment/envoy-gateway >/dev/null 2>&1 \
    || { echo "FATAL: envoy-gateway not Available. If it CrashLoops with"; \
         echo "       'no matches for kind TLSRoute in version .../v1', the EG"; \
         echo "       version is incompatible with the cluster's Gateway API"; \
         echo "       CRDs — v1.7.5 works with the v1.4.1 experimental bundle."; exit 1; }
fi
echo "  controller Available"

TMP_DEP_ERR="$(mktemp)"
trap 'rm -f "$TMP_DEP_ERR"' EXIT

# ── Step 6: deploy (two-phase for publicUrl) ─────────────────────────────────
echo ""
echo "[6/7] Helm deploy..."
# Re-vendor sub-charts. This MUST NOT be best-effort: helm renders the packaged
# .tgz in charts/, not your edited directory, so a stale .tgz means `helm upgrade`
# silently applies OLD templates. It is deceptive rather than loud — image TAGS
# still update (the old template reads .Values.image.tag), so the rollout reports
# success while your template change never shipped. Fail here instead.
if ! helm dependency update "$CHART" 2>"$TMP_DEP_ERR"; then
  echo "ERROR: helm dependency update failed — sub-chart .tgz would be STALE." >&2
  echo "       Deploying now would silently apply old templates. Common cause:" >&2
  echo "       Chart.yaml pins a sub-chart version that differs from its" >&2
  echo "       charts/<name>/Chart.yaml version (that aborts the whole update)." >&2
  cat "$TMP_DEP_ERR" >&2
  exit 1
fi
helm upgrade --install "$RELEASE" "$CHART" -f "$VALUES" -n "$NS" --timeout 15m

echo ""
echo "    waiting for the internal NLB hostname..."
ELB=""
for _ in $(seq 1 40); do
  ELB="$(kubectl get gateway -n "$NS" -o jsonpath='{.items[0].status.addresses[0].value}' 2>/dev/null || true)"
  [ -n "$ELB" ] && break
  sleep 15
done
[ -z "$ELB" ] && { echo "FATAL: Gateway never got an address (check aws-load-balancer-controller)"; exit 1; }
echo "    ELB: ${ELB}"

# Phase 2: now that the hostname exists, bake it into publicUrl so server-side
# URL generation (registry-api EVENT_GATEWAY_PUBLIC_URL, webhook URLs shown in
# Studio, Keycloak issuer) matches what the browser actually uses.
echo "    re-applying with publicUrl=https://${ELB} ..."
helm upgrade --install "$RELEASE" "$CHART" -f "$VALUES" -n "$NS" \
  --set-string "global.publicUrl=https://${ELB}" \
  --set-string "global.langfuseUrl=https://${ELB}/langfuse" \
  --timeout 15m

# ── Step 7: verify ───────────────────────────────────────────────────────────
echo ""
echo "[7/7] Waiting for rollouts..."
# registry-api is the MIGRATION GATE. A broken/blocked alembic migration leaves the OLD
# pod Running while the new one sits Pending — and a mere WARN here silently ships stale
# code (observed 2026-07-20: migration 0070's dangling down_revision '0069' KeyError left
# registry-api on the old image; every API check then passed against the wrong bytes). So
# registry-api failing to roll out is FATAL, not a warning.
if kubectl rollout status "deploy/${RELEASE}-registry-api" -n "$NS" --timeout=300s; then
  echo "  ok  registry-api"
else
  echo "FATAL: registry-api did not roll out. Most likely the alembic-migrate init" >&2
  echo "       container failed. Inspect it:" >&2
  echo "  kubectl -n ${NS} get pods -l app.kubernetes.io/name=registry-api" >&2
  echo "  kubectl -n ${NS} logs \$(kubectl -n ${NS} get pods -l app.kubernetes.io/name=registry-api --field-selector=status.phase=Pending -o name | head -1) -c alembic-migrate" >&2
  exit 1
fi
for d in studio deploy-controller scheduler event-gateway python-executor; do
  kubectl rollout status "deploy/${RELEASE}-${d}" -n "$NS" --timeout=240s >/dev/null 2>&1 \
    && echo "  ok  ${d}" || echo "  WARN ${d} not ready"
done

# Pin the platform-admin global role to the LIVE Keycloak sub (self-healing across realm
# recreations — see scripts/seed-platform-admin-role.sh header). Without this the Studio
# Admin menu silently disappears whenever the realm's admin sub changes. Non-fatal: a
# fresh cluster may still be settling Keycloak; the message tells the operator to re-run.
echo ""
echo "[7b/7] Seeding platform-admin role (live Keycloak sub)..."
bash scripts/seed-platform-admin-role.sh \
  || echo "  WARN seed-platform-admin-role failed — re-run: bash scripts/seed-platform-admin-role.sh"

echo ""
echo "=== Done ==="
echo ""
kubectl get pods -n "$NS" --no-headers | awk '{print $3}' | sort | uniq -c | sed 's/^/    /'
echo ""
echo "  Access (from the VPN — the NLB is INTERNAL, no public IP):"
echo "    https://${ELB}/"
echo "    (self-signed cert => click through the browser warning)"
echo ""
echo "  Restore a backup — IMPORTANT, read this:"
echo "    pg_dumpall --clean does DROP DATABASE, which FAILS while registry-api /"
echo "    keycloak hold connections. restore-postgres.sh runs ON_ERROR_STOP=0, so"
echo "    it SILENTLY HALF-RESTORES (some tables land, others stay empty)."
echo "    Always:"
echo "      kubectl scale deploy/${RELEASE}-registry-api deploy/${RELEASE}-keycloak \\"
echo "        deploy/${RELEASE}-scheduler deploy/${RELEASE}-event-gateway \\"
echo "        deploy/${RELEASE}-deploy-controller --replicas=0 -n ${NS}"
echo "      # verify: select count(*) from pg_stat_activity where datname in ('agentshield','keycloak');  => 0"
echo "      bash scripts/restore-postgres.sh backups/<newest>.sql.gz"
echo "      # then scale back up (registry-api=2, keycloak=1, scheduler=2, event-gateway=2, deploy-controller=1)"
echo ""

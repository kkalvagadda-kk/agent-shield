# Envoy Gateway Ingress — Design & Implementation

**Status**: IMPLEMENTED  
**Date**: 2026-07-07  
**Author**: Karthik + Claude

## Problem

All AgentShield services are ClusterIP — access requires `kubectl port-forward`. This blocks multi-user access, realistic development workflows, and EKS readiness. We need an ingress layer with path-based routing.

## Decision

**Envoy Gateway** (Gateway API v1 standard) — uses the subchart at `charts/agentshield/charts/envoy-gateway/`. Chosen over NGINX Ingress Controller for:
- Modern Gateway API standard (future-proof)
- Already has a chart slot in the repo
- Better alignment with advanced routing needs (header-based, weighted splits) coming later
- Same HTTPRoute manifests work local + EKS unchanged

## Architecture (implemented)

```
Browser → https://agentshield.127.0.0.1.nip.io:8443
         ↓
   Envoy Gateway (LoadBalancer :443, TLS termination)
         ↓ HTTPRoute path rules (hostname: agentshield.127.0.0.1.nip.io)
   /              → agentshield-studio:80        (SPA + fallback catch-all)
   /api/          → agentshield-registry-api:8000
   /realms/       → agentshield-keycloak:80      (OIDC endpoints)
   /resources/    → agentshield-keycloak:80      (KC static assets)
   /js/           → agentshield-keycloak:80      (KC JS)
   /minio/        → agentshield-minio:9001       (URLRewrite strips prefix)
   /webhooks/     → agentshield-event-gateway:8091

Browser → https://langfuse.127.0.0.1.nip.io:8443
         ↓
   Same Envoy Gateway (separate listener + HTTPRoute)
         ↓ (hostname: langfuse.127.0.0.1.nip.io)
   /              → agentshield-langfuse-web:3000 (no rewrite — subdomain)
```

Studio SPA loads from `/`, makes relative calls to `/api/` and `/realms/` — Gateway routes them directly to backends. No change to Studio's config needed (`keycloakUrl: ""` = same-origin still works).

### Why Langfuse uses a subdomain (not path prefix)

Langfuse is a Next.js app. Without `basePath` baked in at build time:
- Asset references (`/_next/static/...`) are root-relative → fail behind a prefix
- Internal API routes (`/api/trpc/`, `/api/auth/`) conflict with registry-api's `/api/`
- Server-side redirects (e.g. `/trace/{id}` → `/project/{pid}/traces/{id}`) lose the prefix

A path-prefix + URLRewrite approach was tried first and failed for all three reasons above.

## Implementation Details

### 1. Cluster Setup — Envoy Gateway Controller

Separate from app chart (cluster infrastructure):

```bash
bash scripts/setup-envoy-gateway.sh
```

Installs `oci://docker.io/envoyproxy/gateway-helm` v1.8.2 in `envoy-gateway-system` namespace. Idempotent.

### 2. Subchart Templates

`charts/agentshield/charts/envoy-gateway/templates/`:

| Template | Resource |
|----------|----------|
| `gatewayclass.yaml` | GatewayClass `agentshield` → `gateway.envoyproxy.io/gatewayclass-controller` |
| `gateway.yaml` | Gateway with 4 listeners (http/https × main + langfuse), TLS termination |
| `httproute.yaml` | Path-based rules for main hostname + catch-all for Langfuse subdomain |
| `_helpers.tpl` | Standard label helpers |

All wrapped in `{{- if .Values.enabled }}`.

### 3. TLS

Self-signed cert covering both hostnames (generated with `openssl`):
- `DNS:agentshield.127.0.0.1.nip.io`
- `DNS:langfuse.127.0.0.1.nip.io`
- `DNS:localhost`
- `IP:127.0.0.1`

Stored as K8s secret `gateway-tls`. Cert files at `certs/gateway-{cert,key}.pem`.

HTTPS required — browsers need secure context for Web Crypto API (Keycloak PKCE).

### 4. Local Access

```bash
bash scripts/gateway-proxy.sh   # port-forwards localhost:8443 → Gateway :443
```

nip.io DNS auto-resolves `*.127.0.0.1.nip.io` → 127.0.0.1. No `/etc/hosts` needed.

### 5. Values

```yaml
# charts/agentshield/values.yaml
global:
  publicUrl: "https://agentshield.127.0.0.1.nip.io:8443"
  langfuseUrl: "https://langfuse.127.0.0.1.nip.io:8443"

envoy-gateway:
  enabled: true
  gateway:
    name: agentshield-gateway
    hostname: "agentshield.127.0.0.1.nip.io"
    langfuseHostname: "langfuse.127.0.0.1.nip.io"
    tls:
      enabled: true
      secretName: gateway-tls
```

### 6. Langfuse Trace Links

Registry-api builds full direct URLs (avoids Langfuse's `/trace/{id}` short-link which redirects and loses the context):

```python
trace_url = f"{LANGFUSE_PUBLIC_URL}/project/{LANGFUSE_PROJECT_ID}/traces/{trace_id}"
# → https://langfuse.127.0.0.1.nip.io:8443/project/00000000-.../traces/abc123
```

Env vars:
- `LANGFUSE_PUBLIC_URL` — derived from `global.langfuseUrl` in Helm template
- `LANGFUSE_PROJECT_ID` — set in `registry-api.langfuseProjectId`

## Files Created/Modified

| File | Action |
|------|--------|
| `charts/agentshield/charts/envoy-gateway/Chart.yaml` | Subchart metadata |
| `charts/agentshield/charts/envoy-gateway/values.yaml` | Default values (hostnames, TLS) |
| `charts/agentshield/charts/envoy-gateway/templates/_helpers.tpl` | Label helpers |
| `charts/agentshield/charts/envoy-gateway/templates/gatewayclass.yaml` | GatewayClass resource |
| `charts/agentshield/charts/envoy-gateway/templates/gateway.yaml` | Gateway (4 listeners) |
| `charts/agentshield/charts/envoy-gateway/templates/httproute.yaml` | HTTPRoutes (main + langfuse) |
| `charts/agentshield/values.yaml` | `global.publicUrl`, `global.langfuseUrl`, envoy-gateway config |
| `charts/agentshield/charts/registry-api/templates/deployment.yaml` | `LANGFUSE_PUBLIC_URL` + `LANGFUSE_PROJECT_ID` env vars |
| `charts/agentshield/templates/keycloak-raw.yaml` | `KC_HOSTNAME` from `global.publicUrl` |
| `scripts/setup-envoy-gateway.sh` | Controller install script |
| `scripts/gateway-proxy.sh` | Local port-forward helper |
| `scripts/deploy-cpe2e.sh` | Gateway readiness check + URL output |
| `certs/gateway-cert.pem` | Self-signed TLS cert (multi-SAN) |
| `certs/gateway-key.pem` | TLS private key |

## EKS Deployment

Override three values:

```yaml
# values-eks.yaml
global:
  publicUrl: "https://agentshield.yourcompany.com"
  langfuseUrl: "https://langfuse.yourcompany.com"

envoy-gateway:
  gateway:
    hostname: "agentshield.yourcompany.com"
    langfuseHostname: "langfuse.yourcompany.com"
    tls:
      secretName: gateway-tls  # cert-manager wildcard or multi-SAN

langfuse:
  langfuse:
    nextauth:
      url: "https://langfuse.yourcompany.com"
```

Gateway gets real NLB address — no port-forward needed. Same HTTPRoutes work unchanged.

## Future Improvements (not yet implemented)

| Capability | Notes |
|-----------|-------|
| **Rate limiting** | BackendTrafficPolicy resource. Protect public endpoints (webhooks, API). |
| **Safety Orchestrator proxy hop** | Re-enable safety-orchestrator + wire input-scan path at Gateway level (ExtProc or external auth filter). |
| **WebSocket/SSE streaming verification** | Envoy handles HTTP/1.1 upgrade by default, but long-lived SSE connections need timeout tuning (idle timeout, stream timeout). |
| **Canary / weighted routing** | HTTPRoute weight splits for blue-green deploys of registry-api or studio. |
| **mTLS between Gateway and backends** | Currently plaintext inside cluster. Add BackendTLSPolicy when zero-trust is required. |
| **External auth (OAuth2 proxy)** | Gate Langfuse/MinIO console access behind platform Keycloak login at Gateway level. |
| **IP allowlisting** | ClientTrafficPolicy for restricting access to admin surfaces (MinIO, Keycloak admin). |
| **Observability** | EnvoyProxy resource for access logs, metrics export to Prometheus, trace propagation headers. |
| **Langfuse basePath rebuild** | Build custom Langfuse image with `basePath=/langfuse` to eliminate subdomain requirement. Low priority — subdomain works. |

# Envoy Gateway Ingress — Design & Implementation Plan

**Status**: PLANNED  
**Date**: 2026-07-07  
**Author**: Karthik + Claude

## Problem

All AgentShield services are ClusterIP — access requires `kubectl port-forward`. This blocks multi-user access, realistic development workflows, and EKS readiness. We need an ingress layer with path-based routing.

## Decision

**Envoy Gateway** (Gateway API v1 standard) — uses the existing stub subchart at `charts/agentshield/charts/envoy-gateway/`. Chosen over NGINX Ingress Controller for:
- Modern Gateway API standard (future-proof)
- Already has a chart slot in the repo
- Better alignment with advanced routing needs (header-based, weighted splits) coming later
- Same HTTPRoute manifests work local + EKS unchanged

## Architecture

```
Browser → http://agentshield.local
         ↓
   Envoy Gateway (LoadBalancer :80)
         ↓ HTTPRoute path rules
   /              → agentshield-studio:80        (SPA + fallback)
   /api/          → agentshield-registry-api:8000
   /realms/       → agentshield-keycloak:80      (OIDC endpoints)
   /resources/    → agentshield-keycloak:80      (KC static assets)
   /js/           → agentshield-keycloak:80      (KC JS)
   /langfuse/     → agentshield-langfuse-web:3000
   /minio/        → agentshield-minio:9001       (console)
   /webhooks/     → agentshield-event-gateway:8091
```

Studio SPA loads from `/`, makes relative calls to `/api/` and `/realms/` — Gateway routes them directly to backends. No change to Studio's `config.json` needed (`keycloakUrl: ""` = same-origin still works).

## Implementation

### 1. Cluster Setup — Envoy Gateway Controller

Separate from app chart (cluster infrastructure, like cert-manager):

```bash
helm install eg oci://docker.io/envoyproxy/gateway-helm \
  --version v1.8.2 \
  -n envoy-gateway-system --create-namespace
```

Wrapped in idempotent `scripts/setup-envoy-gateway.sh`.

### 2. Subchart Templates

Populate `charts/agentshield/charts/envoy-gateway/templates/`:

| Template | Resource |
|----------|----------|
| `gatewayclass.yaml` | GatewayClass `agentshield` → `gateway.envoyproxy.io/gatewayclass-controller` |
| `gateway.yaml` | Gateway listener on port 80, same-namespace routes |
| `httproute.yaml` | Path-based rules for all services (table above) |
| `_helpers.tpl` | Standard label helpers |

All wrapped in `{{- if .Values.enabled }}`.

### 3. Values Changes

```yaml
# charts/agentshield/values.yaml
envoy-gateway:
  enabled: true
  gateway:
    hostname: agentshield.local
```

### 4. Sub-Path Considerations

| Service | Sub-Path Support | Action |
|---------|-----------------|--------|
| Registry API | Native (`/api/` prefix built-in) | None |
| Keycloak | Native (serves from `/realms/`) | None |
| Studio | Root SPA | Catch-all `/` route |
| Langfuse | Needs `NEXTAUTH_URL` + base path config | HTTPRoute URL rewrite (strip `/langfuse/` prefix) |
| MinIO Console | `MINIO_BROWSER_REDIRECT_URL` env | Set redirect URL to `/minio/` |
| Event Gateway | Configurable prefix | Set to respond at `/webhooks/` |

### 5. Local DNS

Add to `/etc/hosts`:
```
127.0.0.1 agentshield.local
```

Docker Desktop: Gateway LoadBalancer gets `localhost` automatically.  
kind: Needs MetalLB or container port-mapping (document both).

### 6. Deploy Script Updates

`scripts/deploy-cpe2e.sh` — add Gateway readiness check + print access URL after helm upgrade.

## Files to Create/Modify

| File | Action |
|------|--------|
| `charts/agentshield/charts/envoy-gateway/Chart.yaml` | Update (remove "stub" description) |
| `charts/agentshield/charts/envoy-gateway/values.yaml` | Create |
| `charts/agentshield/charts/envoy-gateway/templates/_helpers.tpl` | Create |
| `charts/agentshield/charts/envoy-gateway/templates/gatewayclass.yaml` | Create |
| `charts/agentshield/charts/envoy-gateway/templates/gateway.yaml` | Create |
| `charts/agentshield/charts/envoy-gateway/templates/httproute.yaml` | Create |
| `charts/agentshield/values.yaml` | Enable envoy-gateway, add config |
| `scripts/setup-envoy-gateway.sh` | Create (controller install) |
| `scripts/deploy-cpe2e.sh` | Add Gateway readiness + URL output |

## Verification

1. `bash scripts/setup-envoy-gateway.sh` — controller pods running
2. `helm upgrade` — Gateway + HTTPRoute created
3. `kubectl get gateway -n agentshield-platform` → `Programmed: True`
4. `curl http://agentshield.local/api/v1/health` → 200
5. `curl http://agentshield.local/realms/agentshield/.well-known/openid-configuration` → OIDC JSON
6. Browser: `http://agentshield.local` → Studio loads, login works, API calls succeed
7. `curl -X POST http://agentshield.local/webhooks/test` → event-gateway responds

## EKS Migration (future, not blocking)

- Gateway gets real ALB/NLB address (no /etc/hosts)
- Add TLS listener + cert-manager ClusterIssuer
- Switch `gateway.hostname` to real domain
- Same HTTPRoute works unchanged — only Gateway listener config differs
- Add JWT validation at edge (SecurityPolicy resource)
- Add rate limiting (BackendTrafficPolicy)

## Out of Scope (defer)

- TLS termination (add when moving to EKS)
- Safety Orchestrator proxy hop (separate work — re-enable safety-orchestrator + wire input-scan path)
- JWT validation at Gateway edge (Keycloak + registry-api handle auth today)
- Rate limiting
- WebSocket upgrade support (needed later for SSE streaming, verify Envoy handles it by default)

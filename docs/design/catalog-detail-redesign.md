# Catalog Detail Page Redesign — Production Agent View

## Problem

The Catalog detail page (production agent view) currently shows bare "versions / deployments / runs" tabs. The sandbox agent detail page (AgentDetailPage) has a richer layout: overview, runs, memory, versions, settings. Production agents deserve the same quality of experience — especially since operators need quick access to endpoints, status, and lifecycle actions.

Additional issues:
- Suspend is DB-only — reconciler never scales pods to 0
- No terminate/delete action exists
- DeploymentsPage uses old `deployments` table (empty after environment filter fix)

## Target: Tabs

```
overview | runs | versions | settings
```

(No memory tab — production agents don't expose memory through Studio.)

---

## Overview Tab — Operational Dashboard

### Deployment Status Card

```
┌─────────────────────────────────────────────────────────────┐
│  ● Running          v2.1          ns: production-my-agent   │
│  Deployed: Jul 8 2026, 02:18 UTC                            │
│                                                             │
│  [Suspend]  [Upgrade ▾]  [Terminate]                        │
└─────────────────────────────────────────────────────────────┘
```

States: `pending` → `deploying` → `running` ⇄ `suspended` → `terminated`

### API Endpoints

| Label | Value | Notes |
|-------|-------|-------|
| **Internal (cluster)** | `http://{name}-production.{namespace}:8080` | Always available when running |
| **Chat (reactive)** | `POST /chat` | Synchronous single-turn |
| **Stream (reactive)** | `POST /chat/stream` | SSE streaming |
| **Run (durable)** | `POST /run` | Background execution |
| **External (Envoy)** | `https://agentshield.{domain}/agents/{name}/chat` | Only when Envoy Gateway deployed |

Show internal endpoint always. Show external with "(not active — Envoy Gateway not deployed)" badge when HTTPRoute exists but gateway is missing.

### Trigger Configuration

If agent has triggers (from `config_snapshot` or linked trigger records):

```
Schedule: */5 * * * *  (every 5 min)    Last fired: Jul 8, 09:15
Webhook:  POST /hooks/{name}/{token}    Events received: 42
```

### Agent Metadata

Grid: type, team, execution_shape, model, description, created_at.

---

## Runs Tab

Reuse existing catalog RunsTab (already shows production runs with status, trigger_type, latency, cost, trace links). No changes needed.

---

## Versions Tab

Existing VersionsTab: version list with config_snapshot details + Deploy button. Keep as-is.

---

## Settings Tab

- **Deployment Actions**: Upgrade (version picker), Suspend/Resume, Terminate
- **Access Grants**: Which teams can invoke this agent (from `granted_teams`)
- **Config Snapshot**: Read-only display of current deployed version's config (model, tools, execution_shape, orchestration mode)

---

## Backend Changes

### 1. Terminate action (`services/registry-api/routers/catalog.py`)

Add to the PATCH `/{artifact_id}/deployments/{did}` handler:

```python
elif body.action == "terminate":
    dep.status = "terminated"
    dep.updated_at = now
```

Update `catalogApi.ts` action type: `"upgrade" | "suspend" | "resume" | "terminate"`

### 2. Reconciler handles suspend + terminate (`services/deploy-controller/`)

**`k8s_client.py`** — add:
```python
def scale_deployment(self, namespace: str, name: str, replicas: int) -> None:
    self.apps_v1.patch_namespaced_deployment_scale(
        name=name, namespace=namespace,
        body={"spec": {"replicas": replicas}},
    )
```

**`production_reconciler.py`** — extend internal API filter:
```python
# Was: ("pending", "deploying")
# Now: ("pending", "deploying", "suspended", "terminated")
```

Branch in poll loop:
- `pending` / `deploying` → existing `reconcile_production()` (provision/upgrade pod)
- `suspended` → `k8s.scale_deployment(ns, f"{name}-production", 0)` → patch confirms
- `terminated` → `k8s.delete_deployment(ns, name)` + delete Service + delete HTTPRoute → patch confirms

### 3. Internal endpoint (`catalog.py`)

Update `list_pending_production_deployments`:
```python
.where(ProductionDeployment.status.in_(("pending", "deploying", "suspended", "terminated")))
```

Add a field to distinguish what reconciler should do (or just branch on status as-is).

---

## Frontend Changes

### `studio/src/pages/CatalogDetailPage.tsx`

Complete rewrite of tab structure:

```tsx
type Tab = "overview" | "runs" | "versions" | "settings";

// Overview tab:
// - DeploymentCard component (status badge, version, namespace, actions)
// - EndpointsCard (internal URL, chat/stream/run paths, external URL)
// - TriggersCard (cron/webhook if in config_snapshot)
// - MetadataGrid (type, team, model, execution_shape)

// Settings tab:
// - GrantedTeams list
// - ConfigSnapshot viewer (JSON or structured grid)
// - Deployment lifecycle actions (moved from inline on deployment card)
```

### `studio/src/api/catalogApi.ts`

- Add `"terminate"` to `updateDeployment` action type
- Add optional `endpoints` field to `CatalogDeployment` response (or derive client-side from namespace + name)

---

## Endpoint Derivation Logic (client-side)

```typescript
function getEndpoints(artifact: CatalogArtifact, deployment: CatalogDeployment) {
  const name = artifact.name;
  const ns = deployment.namespace || `production-${name}`;
  const base = `http://${name}-production.${ns}:8080`;

  return {
    internal: base,
    chat: `${base}/chat`,
    stream: `${base}/chat/stream`,
    run: `${base}/run`,
    external: `https://agentshield.127.0.0.1.nip.io:8443/agents/${name}/chat`,
    externalActive: false, // TODO: check if Envoy Gateway + HTTPRoute exist
  };
}
```

---

## DeploymentsPage Disposition

Two options:
1. **Remove from nav** — CatalogDetailPage now shows everything per-agent
2. **Rewire to global production view** — `GET /api/v1/catalog/deployments` listing all production_deployments across agents

Recommend: keep it but rewire. Shows fleet-wide view (all running/suspended/terminated across all agents). Defer to separate PR.

---

## Files to Modify

| File | Change |
|------|--------|
| `studio/src/pages/CatalogDetailPage.tsx` | Full redesign: 4 tabs, overview dashboard, terminate button |
| `studio/src/api/catalogApi.ts` | Add terminate action, optional endpoint fields |
| `services/registry-api/routers/catalog.py` | Terminate action + extend internal status filter |
| `services/deploy-controller/production_reconciler.py` | Handle suspended (scale 0) + terminated (delete) |
| `services/deploy-controller/k8s_client.py` | Add `scale_deployment()` method |
| `scripts/deploy-cpe2e.sh` | Bump REGISTRY_API_TAG, DEPLOY_CONTROLLER_TAG, STUDIO_TAG |
| `charts/agentshield/values.yaml` | Mirror tag bumps |

---

## Verification

1. Open Catalog → click agent → see overview/runs/versions/settings tabs
2. Overview shows: deployment status card + endpoints + metadata
3. Endpoints card shows internal URL (copyable)
4. Suspend → status "suspended" → `kubectl get deploy -n production-{name}` → 0 replicas
5. Resume → "deploying" → reconciler provisions back → "running"
6. Terminate → confirm() → "terminated" → K8s Deployment+Service gone
7. Versions tab: deploy a new version → status goes pending → reconciler picks up
8. Settings tab: shows granted_teams + config_snapshot

---

## Open Questions

1. Should we show a "Chat" button on Overview that port-forwards or proxies to the production agent? (Like playground but for production.) Or keep production agents headless?
2. External endpoint: show it grayed out with "Envoy not deployed" hint, or hide entirely until gateway exists?
3. Terminate: soft-delete (keep record, status=terminated) or hard-delete (remove from DB)?
   - Recommend soft-delete — preserves audit trail and run history.

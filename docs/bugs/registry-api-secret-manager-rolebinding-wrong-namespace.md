# registry-api `secret-manager` ClusterRoleBinding bound the wrong-namespace SA

**Found/Fixed:** 2026-07-26 — fixed by re-applying `charts/registry-api/templates/rbac.yaml`
rendered with `helm template --namespace agentshield-platform` (no image change; RBAC-only).

## Symptom

MCP Phase-4 CP1 infra smoke (`scripts/smoke-mcp4-cp1-infra.sh`) failed on the
legacy-resolve step with a Kubernetes 403 from inside the registry-api pod:

```
secrets is forbidden: User "system:serviceaccount:agentshield-platform:agentshield-registry-api"
cannot create resource "secrets" in API group "" in the namespace "agentshield-mcp"
```

`materialize_server_secret` (registry-api → per-server K8s Secret in `agentshield-mcp`)
was denied. `kubectl auth can-i create secrets` returned **no** for the registry-api SA
in **every** namespace — including `agentshield-platform` itself — even though the
`agentshield-registry-api-secret-manager` ClusterRole **and** ClusterRoleBinding both
existed on the cluster.

## Root cause

The `secret-manager` ClusterRoleBinding's subject namespace was **`default`**, not
`agentshield-platform`:

```
ServiceAccount/default/agentshield-registry-api      # live binding subject (wrong)
ServiceAccount/agentshield-platform/agentshield-registry-api   # the real SA
```

The binding template hardcodes the subject namespace as `{{ .Release.Namespace }}`.
It had been rendered ~10 days earlier by a `helm template … | kubectl apply` that
**omitted `--namespace agentshield-platform`**, so Helm defaulted `.Release.Namespace`
to `default` and baked `default` into the subject. The binding therefore granted
secrets/configmaps/jobs to a *non-existent* SA in `default`, and the real SA in
`agentshield-platform` got nothing. RBAC failed closed → 403.

This is the **same class** as the earlier "stray deployments landed in `default`" bug:
a surgical `helm template --show-only … | kubectl apply` without `--namespace` renders
every `.Release.Namespace` reference as `default`. For a Deployment the visible blast
radius is a stray object; for a ClusterRoleBinding subject it is a **silent** authz hole
— the binding object looks present and correct in `kubectl get`, and only the subject
line (rarely inspected) is wrong. It stayed invisible because no agent LLM-secret
materialization or MCP per-server Secret write happened to run on this EKS cluster in
that window.

## Fix

Re-render + apply the registry-api RBAC with the namespace pinned:

```bash
helm template agentshield charts/agentshield --namespace agentshield-platform \
  -f charts/agentshield/values.yaml -f charts/agentshield/values-eks.yaml \
  --show-only charts/registry-api/templates/rbac.yaml | kubectl apply -f -
```

Result: the ClusterRoleBinding subject becomes
`ServiceAccount/agentshield-platform/agentshield-registry-api`, and
`kubectl auth can-i create secrets` → **yes** in both `agentshield-mcp` and
`agentshield-platform`. CP1 infra + behaviour then pass fully.

**Class-fix (process, not just this object):** any surgical `helm template … |
kubectl apply` on this cluster MUST pass `--namespace agentshield-platform` so every
`.Release.Namespace` reference (Deployment namespace, RoleBinding/ClusterRoleBinding
subject namespace, etc.) renders correctly. A `--show-only` partial apply is only safe
when the namespace is pinned. Verifying a binding by its mere existence
(`kubectl get clusterrolebinding …`) is insufficient — assert the **subject** namespace,
or assert the effect with `kubectl auth can-i --as=system:serviceaccount:<ns>:<sa>`.

## Also un-broke (collateral)

The same binding governs registry-api writing LLM-provider Secrets into `agents-*`
namespaces on agent deploy. That write had been silently denied for the ~10-day window;
this fix restores it too.

## Cross-links

- Regression guard: `scripts/smoke-mcp4-cp1-infra.sh` step 4 (legacy-resolve) now
  exercises the real `materialize_server_secret` in-pod, so a recurrence of this authz
  hole fails the checkpoint instead of hiding.
- Related class: the earlier stray-`default`-namespace deploy bug (same missing
  `--namespace` on a surgical template-apply).

# ArgoCD — AgentShield Application

This directory contains the ArgoCD `Application` resource that manages the AgentShield umbrella Helm chart.

## Prerequisites

- ArgoCD 2.12+ must be installed on the cluster and the `argocd` namespace must exist.
- You need `kubectl` access to the cluster with permissions to apply resources in the `argocd` namespace.
- The `argocd` CLI is optional but useful for monitoring sync status.

## Before You Apply

Open `agentshield-app.yaml` and replace the placeholder `repoURL` with your actual git repository URL:

```yaml
repoURL: https://github.com/your-org/agent-platform.git  # <-- update this
```

## Apply the Application

```bash
kubectl apply -f infra/argocd/agentshield-app.yaml
```

ArgoCD will automatically create the `agentshield-platform` namespace and sync the Helm chart on first apply.

## Monitor Sync Status

```bash
argocd app get agentshield
argocd app sync agentshield   # trigger a manual sync if needed
argocd app logs agentshield   # stream sync logs
```

## Notes

- Automated sync is enabled with `prune` and `selfHeal` — changes pushed to `HEAD` in `charts/agentshield/` will be applied automatically.
- Secret `data` fields are excluded from diff to avoid false drift when using external-secrets or sealed-secrets.

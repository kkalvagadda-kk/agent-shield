# `helm upgrade` blocked forever by hand-applied objects that only *look* Helm-managed

**Found:** 2026-07-27, deploying studio `0.1.165` / registry-api `0.2.233` to EKS.
**Fixed:** cluster-state repair (annotation adoption) — no chart or code change.

## Symptom

`scripts/deploy-eks.sh` failed at step 6 before touching a single workload:

```
Error: UPGRADE FAILED: Unable to continue with update: Secret
"agentshield-mcp-proxy-keycloak" in namespace "agentshield-platform" exists and
cannot be imported into the current release: invalid ownership metadata;
annotation validation error: missing key "meta.helm.sh/release-name": must be
set to "agentshield"
```

Fixing that one produced the identical error for
`ClusterRole/agentshield-registry-api-tokenreview`, then for its ClusterRoleBinding.
Every deploy would have failed this way regardless of what changed in the images.

## Root cause

Helm decides "may I manage this object?" from two **annotations**:
`meta.helm.sh/release-name` and `meta.helm.sh/release-namespace`. It does *not* use
the `app.kubernetes.io/managed-by: Helm` **label**.

During the MCP Phase-2/4 checkpoint work, three objects the chart already templates
(`charts/agentshield/charts/mcp-proxy/templates/secret.yaml`,
`charts/agentshield/charts/registry-api/templates/rbac.yaml`) were created by hand
with `kubectl apply`, copying the Helm *labels* and none of the *annotations*.
`kubectl.kubernetes.io/last-applied-configuration` on the Secret is the fingerprint —
it only exists on a `kubectl apply`, and it shows `stringData: {client-secret: ""}`.

So each object looked Helm-managed to a human reading `kubectl get -l`, and looked
foreign to Helm. The label lies; the annotation is the truth. Nothing failed at
creation time — the trap only springs on the next `helm upgrade`, which may be weeks
later and by someone else, and the error names an object unrelated to their change.

## Fix

Adopt in place — never delete. Deleting is the tempting one-liner and is wrong here:
the Secret holds the mcp-proxy's Keycloak client credential, and a delete/recreate
cycle changes it out from under a running proxy.

```bash
kubectl annotate secret agentshield-mcp-proxy-keycloak -n agentshield-platform \
  meta.helm.sh/release-name=agentshield \
  meta.helm.sh/release-namespace=agentshield-platform --overwrite
```

Before adopting, confirm the chart would not clobber live data: compare the live
value against what the template renders. Here both were empty
(`.Values.keycloak.clientSecret` defaults to `""`), so adoption was provably lossless.
Had the live value been set and the template empty, adoption would have *scheduled*
the credential's destruction for the next upgrade — the check is not optional.

## Find them all at once, not one error at a time

Helm reports exactly one conflict per attempt, so fixing them serially costs a full
chart download + render per object. Sweep instead — the signature is
"carries the Helm label, missing the Helm annotation":

```bash
for kind in clusterrole clusterrolebinding secret configmap serviceaccount \
            role rolebinding service deployment; do
  case "$kind" in clusterrole|clusterrolebinding) S="";; *) S="-n agentshield-platform";; esac
  kubectl get "$kind" $S -l app.kubernetes.io/managed-by=Helm -o json | python3 -c "
import json,sys
for i in json.load(sys.stdin).get('items',[]):
    if 'meta.helm.sh/release-name' not in (i['metadata'].get('annotations') or {}):
        print(f\"{i['kind']}/{i['metadata']['name']}\")"
done
```

**Check the release before adopting each hit.** That sweep also surfaced
`ClusterRole/eg-gateway-helm-certgen:envoy-gateway-system` and its binding, which
belong to the **Envoy Gateway** release, not `agentshield`. Annotating those to
`agentshield` would hand another release's objects to ours — a worse problem than
the one being fixed. Only adopt objects your own chart actually templates
(`grep -rl <name> charts/`).

## Prevention

Anything a checkpoint or smoke script applies by hand must either (a) not be
templated by the chart, or (b) carry both `meta.helm.sh/*` annotations at creation.
No checked-in script created these — `scripts/deploy-mcp2-cp3.sh:77` only *checks*
for the Secret — so this was an ad-hoc `kubectl apply` in a terminal. That is the
practice to change, which is why this is written down rather than patched.

## Related

Folded into `docs/debugging/troubleshooting-playbook.md` as a symptom → command →
fix entry, since the error text names the object but never the cause.

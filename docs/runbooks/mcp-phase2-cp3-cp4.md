# Runbook — MCP Phase 2 CP3 (identity) + CP4/P7–P11 (runtime dispatch) checkpoints

CP1 (health loop) and CP2 (list_changed) are already green on EKS. CP3 and CP4 each
need a prereq that must be created by an operator (a Keycloak confidential client; a
fixture agent). This runbook is the exact steps.

Cluster: `~/.kube/test-cluster-kube-config.yaml`, ns `agentshield-platform`. ECR profile
`kkalyan-aws-key`, region `us-west-2`. The `agentshield-ecr` pull secret expires ~12h —
refresh it before any deploy: `TOKEN=$(aws ecr get-login-password --region us-west-2); kubectl -n agentshield-platform create secret docker-registry agentshield-ecr --docker-server=517602344783.dkr.ecr.us-west-2.amazonaws.com --docker-username=AWS --docker-password="$TOKEN" --dry-run=client -o yaml | kubectl apply -f -`.

---

## CP3 — internal-server identity (service-identity + OBO fail-closed)

### Prereq 1: a Keycloak confidential client
In the `agentshield` realm, create a client:
- **Client ID:** `agentshield-mcp-proxy` (must equal `mcp-proxy.keycloak.clientId`).
- **Client type:** confidential / "Client authentication" ON.
- **Service accounts roles / client-credentials grant:** ENABLED (this is how the proxy mints its own service-identity token; no user).
- **Standard flow / direct access:** not needed (service-account only).
- Copy the generated **client secret**.
- (If any internal MCP server will use `identity_audience`, add that audience to the token — a client scope / audience mapper — so the minted token's `aud` matches. Optional for the smoke.)

Keycloak admin console is behind the platform gateway (`/admin`), or use `kcadm.sh`
inside the keycloak pod: `kubectl -n agentshield-platform exec deploy/agentshield-keycloak -- /opt/bitnami/keycloak/bin/kcadm.sh ...` (login with the admin creds, then `create clients`).

### Prereq 2: put the client secret in the chart
Two options — pick one:
- **Inline:** set `mcp-proxy.keycloak.clientSecret=<the-secret>` at deploy time (the chart's `secret.yaml` mints `agentshield-mcp-proxy-keycloak` from it).
- **Existing secret:** create the K8s Secret yourself and set `mcp-proxy.keycloak.existingSecret=<name>` (key `client-secret`).

### Deploy (CP3 needs a full helm upgrade, NOT set-image)
CP1/CP2 used `kubectl set image` because they needed only new images. **CP3 needs the
chart** (WS-C added the Keycloak env, the read-only volume mount, and the egress
NetworkPolicy — none of which set-image applies). Do a targeted helm upgrade that
PRESERVES the current runtime overrides (else you clobber langfuse/publicUrl on the
shared cluster):

```bash
export KUBECONFIG=~/.kube/test-cluster-kube-config.yaml AWS_PROFILE=kkalyan-aws-key
cd <repo>
# capture the current runtime --sets you must keep:
#   global.publicUrl, global.langfuseUrl, envoy-gateway.gateway.langfuseHostname,
#   langfuse.langfuse.nextauth.url   (see `helm get values agentshield -n agentshield-platform`)
helm upgrade agentshield charts/agentshield -n agentshield-platform \
  -f charts/agentshield/values-eks.yaml \
  --set 'mcp-proxy.keycloak.clientSecret=<the-secret>' \
  --set global.publicUrl=https://k8s-envoygat-envoyage-6676b8bb93-7541836717beafbe.elb.us-west-2.amazonaws.com \
  --set global.langfuseUrl=https://langfuse.10.80.118.27.nip.io \
  --set envoy-gateway.gateway.langfuseHostname=langfuse.10.80.118.27.nip.io \
  --set langfuse.langfuse.nextauth.url=https://langfuse.10.80.118.27.nip.io
# NOTE: this also re-applies any post-deploy hostAlias/keycloak-issuer reconcile — verify
# login + langfuse still work after (reconcile-langfuse-hostalias.sh if the deploy used it).
```
Values.yaml already pins the Phase-2 tags (registry-api 0.2.228, mcp-proxy 0.1.3, studio 0.1.162).

### Run the smokes
```bash
NAMESPACE=agentshield-platform bash scripts/smoke-mcp2-cp3-infra.sh
NAMESPACE=agentshield-platform bash scripts/smoke-mcp2-cp3-behaviour.sh
```
Proves: client-secret file mounted (not an API read); RBAC unchanged (proxy `get secrets`
yes in `agentshield-mcp`, NO in `agentshield-platform`); `none` server byte-identical;
`service_identity` server sends a minted bearer; `on_behalf_of` → `200 is_error=true`
(empty user → "requires a user identity"; with user → "blocked on Decision 29"); an
agent with `AGENTSHIELD_USER_SUB` set emits `x-user-sub`.

---

## CP4 / P7–P11 — runtime tool-call dispatch executes

This proves a *bound* `mcp_tool` actually executes at agent run time (SDK/runner →
proxy → upstream) + the Decision-27 gate. It needs a real agent pod.

### Prereq: a fixture agent with sdk 0.2.4 + a bound mcp_tool
1. Rebuild + deploy `declarative-runner:0.1.61` (built already; `kubectl set image` the
   `deploy-controller.declarativeRunnerTag`, or helm upgrade — the controller provisions
   agent pods from that tag) so newly-created agent pods carry sdk 0.2.4.
2. Ensure the dual SA token is projected (CP3b of Phase 1): an agent pod must mount BOTH
   `/var/run/secrets/sa-token/token` (aud `agentshield-opa`) and
   `/var/run/secrets/mcp-proxy-token/token` (aud `agentshield-mcp-proxy`).
3. Register an MCP server against the in-pod stub fixture (`127.0.0.1:9999`, started via
   `kubectl exec <mcp-proxy-pod> -- sh -c 'setsid nohup python3 /app/fixtures/stub_mcp_server.py &'`),
   bind a discovered `mcp_tool` (e.g. `<server>__echo`) to a declarative agent, deploy it.

### Verify (CP3c/CP4c of the phase1 plan)
`kubectl exec` a resolve+invoke into the agent pod: `McpToolExecutor` against the fixture
returns `echo`'s real string; unreachable proxy → JSON error string (no exception);
`POST /internal/tools/call` directly — own-team executes, cross-team no-grant → 403,
missing token → 401, tool error → `200 is_error:true`. For the Decision-27 gate (CP4):
a flagged tool + a stored `PiiMapping` → the fixture receives the de-anonymized value;
internal `scan_results=false` → scan skipped; external `scan_results=false` ignored →
scan still called; a native http tool call produces an `opa_decisions` row; a blocked
verdict is NOT enforced (STUB). (safety-orchestrator 0.1.4 must be deployed for the
de-anon endpoint; it is not in the EKS build loop — add it or run CP4 on the kind path.)

---

## Note on merge with `main`
This branch registers e2e suites in `scripts/e2e/run-all.sh`. `main` has since added a
`scripts/test-manifest.txt` single-source-of-truth system. On merge, add manifest lines
for suite-84, suite-85, and `studio/e2e/mcp-servers.spec.ts` (and the Phase-4 suite-86/87).

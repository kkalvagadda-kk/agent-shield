# 013 — Playwright specs redirect to the Keycloak login on EKS (SSO silent-auth fails over the http port-forward)

**Date:** 2026-07-25 · branch `mcp-tool-source` · fixed in `studio/playwright.config.ts` + `studio/e2e/global-setup.ts` (host-resolver tunnel), no image change.

Running the Studio Playwright gate against the deployed **EKS** test cluster
(`studio 0.1.161`) never got past auth: every spec landed on the Keycloak login
form instead of the app, even though `global-setup` reported a successful login.

## Expected chain

`scripts/studio-e2e.sh` → port-forward `svc/agentshield-studio 8080:80` →
`global-setup` logs in through Keycloak once, saves `storageState` →
each spec reuses the session (cookies + keycloak-js silent check-sso) → the app
renders authenticated.

## Investigation

| # | Step | Command / evidence | Conclusion |
|---|------|--------------------|------------|
| 1 | Run the MCP spec | `bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts` → `[global-setup] authenticated as platform-admin` **then** the test fails: `getByRole('heading',{name:'MCP Servers'})` not found | Auth "succeeded" in setup but the spec is unauthenticated. |
| 2 | Read the failure snapshot | `error-context.md` page snapshot = `heading "Sign in to your account"` + username/password textboxes | The spec's page was **redirected to the Keycloak login**, not the app. |
| 3 | Why does the saved session not carry? | `studio-e2e.sh` header comment: *"Keycloak now sets **Secure** session cookies — Playwright won't send those back over a plain-**http** port-forward, so SSO silent-auth between specs breaks."* | Root layer 1: **Secure cookies + http port-forward.** `global-setup`'s own form login works in-context; the persisted KC session cookie is `Secure`, so over `http://localhost:8080` the browser drops it → keycloak-js `login-required` → redirect to login. |
| 4 | Use the script's gateway (https) mode instead | Default `STUDIO_E2E_GATEWAY_URL=https://agentshield.127.0.0.1.nip.io:8443` — a local-kind address, unreachable on EKS → the curl check fails → falls back to the http port-forward | The existing gateway-mode escape hatch doesn't fit EKS. |
| 5 | Port-forward the real Envoy gateway (`:443→:8443`) and curl it | `curl -sk https://localhost:8443/config.json` → **404**; with the ELB Host header `curl -sk -H "Host: <elb-dns>" …` → **200** | Root layer 2: the `HTTPRoute agentshield-routes` binds **only** to the ELB hostname (`kubectl get httproute -A -o …spec.hostnames`), so the gateway 404s any other Host. Playwright hitting `localhost:8443` sends the wrong Host. |

## Root cause

Two stacked constraints, neither present in the local (kind) flow the harness was
built for:
1. Keycloak sets **Secure** session cookies → they never travel over the http
   port-forward, so keycloak-js silent check-sso fails and every spec bounces to
   the login form.
2. The EKS edge is an **internal** NLB whose `HTTPRoute` matches **only** the ELB
   DNS. You can't just point Playwright at `https://localhost:8443` — the Host
   won't match and the gateway 404s. And the ELB DNS isn't locally resolvable.

## Fix

Run Playwright against the **real, portless ELB origin** (`https://<elb-dns>`) so
the Host matches the route, the URL matches the self-signed cert SAN
(`*.elb.us-west-2.amazonaws.com`), and `redirect_uri` matches what the Keycloak
client already whitelists — while **tunnelling** that origin to a local gateway
port-forward via Chromium's `--host-resolver-rules`:

```
kubectl -n envoy-gateway-system port-forward \
  svc/envoy-agentshield-platform-agentshield-gateway-<hash> 8443:443 &

cd studio
PLAYWRIGHT_BASE_URL="https://<elb-dns>" \
PLAYWRIGHT_HOST_RESOLVER_RULES="MAP <elb-dns> 127.0.0.1:8443" \
npx playwright test e2e/mcp-servers.spec.ts
```

`playwright.config.ts` and `e2e/global-setup.ts` were taught to read
`PLAYWRIGHT_HOST_RESOLVER_RULES` and pass it as a Chromium launch arg (both the
test project **and** global-setup launch browsers, so both need it). The var is
**env-gated** — unset for the default local http port-forward flow, so nothing
changes there. Over https the Secure cookies flow, keycloak-js silent-auth works,
and specs land authenticated.

This is the class-fix (not a per-spec workaround): it makes the whole Studio
Playwright suite runnable against EKS through the production-shaped origin,
without `/etc/hosts`, sudo, a privileged :443 forward, or weakening Keycloak's
Secure-cookie / SSL-required posture.

## Related

- While sweeping neighbours, `agents.spec.ts` "full lifecycle" surfaced as a
  **stale** test (unrelated to MCP): the No-code create path navigates to
  `/agents` (list) since 2026-07-09 (`CreateAgentPage.tsx` L803
  `setTimeout(() => navigate("/agents"), 800)`), but the test still expected a
  per-agent detail route, and its hard-coded 5-tab assertions predate the
  now-**dynamic** detail tab set. Trimmed to a shape-agnostic lifecycle
  (create → list-persist → open detail → delete); deep tab content stays in
  `agent-detail-modes.spec.ts`.

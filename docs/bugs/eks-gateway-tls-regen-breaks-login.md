# EKS: gateway-tls cert regenerated on every deploy → Studio login redirect "breaks"

**Found:** 2026-07-21 (EKS test-cluster, after enabling langfuse on its own subdomain)
**Fixed:** 2026-07-21 — `scripts/deploy-eks.sh` phase-2 cert step made idempotent (only re-mints when the current cert doesn't already cover the required SANs). No image change.

## Symptom

Users could no longer log into Studio through the browser: the Keycloak login page
loaded, credentials were accepted, but "the redirection after login is not working."
The browser sat on a Keycloak URL showing Chrome's **"Privacy error"** (self-signed cert
interstitial). Programmatic login (Playwright with `ignoreHTTPSErrors`) kept working, which
is what made it look like an app/redirect bug rather than a TLS one.

## Root cause

The internal NLB has no public CA, so the gateway serves a **self-signed** `gateway-tls`
cert. Browsers trust a self-signed cert **per-fingerprint** — the user clicks "Advanced →
proceed" once and Chrome remembers that exact cert.

`scripts/deploy-eks.sh` phase-2 **unconditionally re-minted** `gateway-tls` on every deploy
(it needed to add the langfuse subdomain to the SAN). Each run produced a brand-new cert
with a new fingerprint, so:

1. Every browser's previously-accepted exception became invalid → "Privacy error" again.
2. During the OAuth redirect chain (Studio → Keycloak → 302 back to Studio), the new,
   un-accepted cert makes Chrome throw the interstitial **mid-redirect** and silently
   block the 302 back to Studio. To the user that is exactly "login redirect is broken."

The cert itself was never wrong — its SAN correctly covers `*.elb.<region>.amazonaws.com`,
the exact ELB DNS, and `langfuse.<NLB-IP>.nip.io`. The defect was **regenerating it when it
didn't need to change**, throwing away browser trust each deploy.

## Fix

`deploy-eks.sh` phase-2 now reads the CURRENT `gateway-tls` cert's SAN and **skips
regeneration** when it already covers both `${ELB}` and `${LF_HOST}`. It re-mints only when
the SAN set actually needs to change (the first langfuse-adding deploy, or a changed NLB
IP). So the cert — and every browser's accepted exception — stays stable across ordinary
redeploys.

```sh
CUR_SAN="$(kubectl get secret gateway-tls -n "$NS" -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -ext subjectAltName)"
if grep -q "DNS:${LF_HOST}" <<<"$CUR_SAN" && grep -q "DNS:${ELB}" <<<"$CUR_SAN"; then
  : keep it   # preserves browser cert trust
else
  : regenerate
fi
```

## One-time user action (this deploy only)

The cert legitimately changed once (to add the langfuse SAN), so browsers must **accept the
new self-signed cert one time**: open `https://<ELB>/`, click **Advanced → Proceed**, then
log in normally. After that, both Studio login and the langfuse trace links
(`https://langfuse.<NLB-IP>.nip.io/...`, covered by the same cert's SAN) work, and future
deploys will not churn the cert again.

## Lessons

- A self-signed cert is a **trust-on-first-use** credential; regenerating it is not free —
  it invalidates every client that trusted it. Treat cert generation as create-if-needed,
  never unconditional.
- A TLS interstitial during an OAuth redirect masquerades as an application redirect bug.
  When "login redirect" fails but programmatic login (which ignores cert errors) succeeds,
  suspect the cert before the app.

Related: [langfuse-missing-s3-event-bucket.md](./langfuse-missing-s3-event-bucket.md),
[langfuse-trace-access-sso-and-membership.md](./langfuse-trace-access-sso-and-membership.md)

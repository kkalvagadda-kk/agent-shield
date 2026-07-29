# TODO: Email-form login redirects to the local dev URL instead of the EKS ELB

**Filed:** 2026-07-28 (Kalyan, during the Claude-in-Chrome journey).
**Status:** open — not yet investigated. Captured at the user's request while finishing the journey legs.

## Symptom

Signing in to Studio on the EKS cluster:

- **Username `kalyan`** → works. Lands on the deployed Studio (the EKS ELB host,
  `k8s-envoygat-envoyage-...elb.us-west-2.amazonaws.com`). This account has the `platform-admin` role.
- **Email `kalvagadda@hotmail.com`** → after auth, redirects to
  **`https://agentshield.127.0.0.1.nip.io:8443/`** — the **local dev** URL — not the EKS host. Studio is
  unreachable there, so the session dead-ends.

The user also noted they "do not see any user called platform-admin in the users" — i.e. `platform-admin`
is a **role**, held by the `kalyan` account, not a separate login. The email account may simply not have
that role (a separate, smaller issue).

## Likely cause (to confirm)

A per-account or per-client Keycloak redirect/base-URL mismatch. Hypotheses, in rough priority:

1. **Stale `redirect_uri` / "Valid redirect URIs" / "Web origins" on the Keycloak client** that the
   email account resolves to — still listing the local `agentshield.127.0.0.1.nip.io:8443` dev origin, so
   Keycloak bounces the post-login redirect there. The `kalyan` path may be hitting a different client or
   a redirect entry that *does* include the ELB host.
2. **Two realms/clients** (a dev one seeded with the localhost URLs, a cluster one with the ELB URLs) and
   the email account living in / defaulting to the dev one.
3. **Keycloak "Frontend URL" / client base URL** set to the localhost dev value for that client, so
   Keycloak builds absolute redirects against localhost regardless of the request host.

## Where to look

- Keycloak admin → realm → Clients → the Studio client → **Valid redirect URIs / Web origins / Root URL /
  Home URL**. Confirm the EKS ELB host is present and the `127.0.0.1.nip.io:8443` entries are removed (or
  scoped to a dev-only client).
- Which realm/client the email account authenticates against vs `kalyan`.
- Studio's `keycloak-js` init (`redirectUri` / `KC_HOSTNAME` / any hardcoded base URL) and the Helm values
  that seed Keycloak client redirect URIs — make sure the cluster deploy templates the ELB host, not the
  local dev host.
- Separately: grant the email account the `platform-admin` role (via `user_team_assignments`) if it is
  meant to be an admin.

## Fix shape (architecturally-correct, not a bandaid)

Redirect URIs must be **environment-templated**, not hardcoded. The cluster deploy should register the
EKS host (or a stable domain) as the client's valid redirect/web-origin, and the local dev host should
only ever appear in a dev-only client/realm. Do not "fix" this by pointing the browser at localhost or by
widening redirect URIs to a wildcard — template the correct host per environment.

## Not blocking

Login works today via the `kalyan` username, which is what the journey used. This only affects the
email-form login path.

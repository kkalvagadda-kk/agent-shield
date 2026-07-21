# Bug: Langfuse trace links show "You do not have access to this trace / Sign In" — two independent root causes

**Date:** 2026-07-20
**Status:** Fixed (cluster reconciled + verified live; durable fixes on branch `fix/langfuse-hostalias-reconcile`)
**Severity:** High — every Langfuse trace link (Traces page, eval results, playground trace drawer) is unusable; the platform's observability is dark for the affected user.

## Symptom

Clicking any Langfuse trace link opens a Langfuse tab showing:

```
Error
You do not have access to this trace.
[Sign In]
```

`langfuse-web` logs `User undefined is not a member of project 00000000-0000-0000-0001-agentshield01` on every trace-page load. Recurred after **redeploying AgentShield on a new cluster**.

This one screen has **two distinct, independent root causes**. Both had to be fixed; either one alone still shows the wall.

---

## Root cause 1 — stale hostAlias ClusterIP (the SSO back-channel was dead)

langfuse-web must reach Keycloak's OIDC endpoints at the **public issuer host**
`https://agentshield.127.0.0.1.nip.io:8443/realms/agentshield` from **inside** the cluster (the OIDC `iss` claim must match the discovery URL). That hostname resolves to `127.0.0.1` (loopback) everywhere, so a **hostAlias** on the langfuse-web pod redirects it to the in-cluster `gateway-port-8443` Service (maps 8443 → Gateway HTTPS, in `envoy-gateway-system`).

That hostAlias IP was **hardcoded** in `charts/agentshield/values.yaml` (`10.96.203.50`). On the new cluster Kubernetes assigned `gateway-port-8443` a **different** ClusterIP (`10.96.158.172`); nothing binds the two, so the hostAlias pointed at the **old cluster's dead IP**. The OIDC **back-channel** (server-side discovery + token exchange, which run inside the langfuse-web pod) resolved the issuer to a dead IP and failed, so **Sign In could never complete** and every request stayed unauthenticated (`User undefined`). A `helm upgrade` later re-added the placeholder alongside a prior live patch, leaving **two** hostAlias entries (one dead), which is untidy and can intermittently pick the dead IP.

**The design flaw:** a hardcoded ClusterIP in a hostAlias — ClusterIPs are dynamic and reassigned on every fresh cluster, and Helm can't template the runtime-assigned IP. See [`langfuse-hostalias-stale-clusterip.md`](./langfuse-hostalias-stale-clusterip.md) for the deeper write-up.

### Fix 1

`scripts/reconcile-langfuse-hostalias.sh` — a reusable helper that, after each deploy, fetches the **live** `gateway-port-8443` ClusterIP and reconciles the langfuse-web hostAlias to a **single** correct entry (idempotent; replaces the whole list so a stale entry can't linger; self-skips if langfuse isn't deployed; self-verifies the back-channel with a `wget` of the OIDC discovery URL). It is wired into **every** install/update script that runs `helm` directly (`deploy-cpe2e.sh`, `deploy-cp1/2/3.sh`, `deploy-cp1/2-eval.sh`, `deploy-eks.sh`); the wrappers that call `deploy-cpe2e.sh` inherit it. `values.yaml` now labels its hostAlias IP a **placeholder** reconciled at deploy time. (Not automatic in plain Helm — Helm renders the hostAlias at template time and can't know the ClusterIP assigned after apply. On a real deployment with a resolvable public domain, the hostAlias mechanism isn't needed at all.)

---

## Root cause 2 — the SSO user was not a Langfuse project member

Even with the back-channel fixed, Sign In lands on the same wall. Langfuse authorizes trace access by **project membership**, and account-links an SSO login to an existing Langfuse user with the **matching email**. The project seeded only **one** member — `admin@agentshield.local` (`LANGFUSE_INIT_USER_EMAIL`) — but the Keycloak `platform-admin` user's email is **`platform-admin@agentshield.local`**. Those differ, so a Keycloak SSO login as platform-admin becomes a **new, non-member** Langfuse user → "not a member of this project."

**The design flaw:** user provisioning is **single-sided**. `admin_users.py::create_user` provisions a user in **Keycloak** (identity) + `user_team_assignments` (platform authz), but **not** in Langfuse (trace authz). So only the one seeded email can ever see traces; every other platform user authenticates via SSO but has no Langfuse membership.

### Fix 2

- **Immediate (this cluster):** re-pointed `LANGFUSE_INIT_USER_EMAIL` → `platform-admin@agentshield.local` and restarted langfuse-web, so init provisioned platform-admin as a project OWNER. Verified: `platform-admin@agentshield.local | OWNER`. Then the reporting user clicked **Sign In → Sign in with Keycloak** and the trace rendered — **confirmed working end-to-end.**
- **Durable (fresh clusters):** `values.yaml` now seeds `LANGFUSE_INIT_USER_EMAIL=platform-admin@agentshield.local` so a new cluster's first admin is a member out of the box.

### Follow-up (NOT yet built — the real multi-user fix)

Aligning the seed email covers only the single admin. The architecturally correct fix is **dual provisioning**: when `admin_users.py::create_user` creates a platform user, also add them to the Langfuse project; `delete_user` should remove them. Langfuse OSS has no membership API, but has a **`membership_invitations`** table (email + org_id + org_role) that it **auto-accepts on the user's first SSO login** — the clean hook (org id `00000000-0000-0000-0000-agentshield01`). This requires registry-api to write one row to the Langfuse DB (a documented coupling, inherent to OSS Langfuse). Tracked as a follow-up; user chose to build it after the immediate unblock.

## Verification

- `wget` of the OIDC discovery URL from inside langfuse-web → returns the `issuer` JSON (back-channel OK).
- NextAuth providers endpoint offers `keycloak`; Keycloak `langfuse` client exists with correct redirect URIs.
- `platform-admin@agentshield.local | OWNER` present in `organization_memberships`.
- **Live:** reporting user signed in via Keycloak and the trace rendered.

## Lessons

1. **One error screen, two root causes.** "You do not have access / Sign In" was BOTH a dead SSO back-channel AND a missing project membership — fixing either alone still failed. Don't stop at the first plausible cause.
2. **Auth spanning two apps needs provisioning on both sides.** Keycloak identity ≠ Langfuse authorization; a user must be provisioned into both, keyed by a **matching email**, or SSO authenticates a stranger.
3. **`User undefined` = pre-auth, not a membership problem yet.** The membership issue only manifests once Sign In actually completes; the 0 sign-in callbacks in the logs showed the back-channel/click, not membership, was the first wall.

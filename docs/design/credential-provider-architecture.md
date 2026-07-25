# Pluggable Credential Provider — Architecture

**Status:** Accepted (Decision 31). Implementation deferred to **Phase 4** (MCP OAuth 2.1), which this design is a hard prerequisite for.

**Related:** `docs/decisions.md` Decision 31 (this doc is its design record), Decision 12 (K8s Secrets + RBAC — the choice this supersedes for high-value credentials), Decision 29 (on-behalf-of impersonation exchange), `docs/design/mcp-tool-source-architecture.md` §3 "Credential resolution" (path (b)) + §3b (proxy least-privilege) + §7a (FR-MCP-21) + Phase 4 (OQ-01, MCP OAuth 2.1), `docs/design/identity-propagation-architecture.md` (the RunContext/RCT propagation Decision 29 depends on), and the `todo_llm_secret_separation` backlog note (registry-api's cluster-wide secret ClusterRole).

---

## 1. Problem

### Current state (grounded in code, verified 2026-07-25)

Durable credential material lives in exactly three places today, and every one of them is a compromise made for MVP speed (Decision 12):

1. **Postgres Fernet blobs — the source of truth.** `AuthConfig.credentials_encrypted` (`services/registry-api/models.py:922`) holds an MCP server's static credential dict as a Fernet token. So do `LLMProvider.credentials_encrypted` (`models.py:1215`) and `applications.secret_encrypted` (Decision 30). All three are encrypted and decrypted by `services/registry-api/crypto.py` — one process-wide `_fernet()` (crypto.py:24-31) keyed on a **single** `AGENTSHIELD_ENCRYPTION_KEY` env var. `encrypt_json`/`decrypt_json` (crypto.py:34-44) are the only door.

2. **Per-server K8s Secret — a materialized copy.** `services/registry-api/mcp_secrets.py::materialize_server_secret` (mcp_secrets.py:104-151) decrypts the Fernet blob **once** (`decrypt_json`, line 140), composes outbound `auth_headers`, and writes the Secret `agentshield-mcp-server-{id}` into the dedicated `agentshield-mcp` namespace. The MCP Proxy reads exactly that Secret — `services/mcp-proxy/credentials.py::read_server_secret` (credentials.py:51-105) — with **no DB, no `AGENTSHIELD_ENCRYPTION_KEY`, no decryption**. This split (registry-api decrypts, proxy only `get`s) is the whole point of §3 path (b): it keeps the master key off the highest-value network target.

3. **Keycloak client secret — a file mount.** The proxy's service-identity minter reads its confidential-client secret fresh from a file on every mint — `services/mcp-proxy/keycloak_client.py::_read_client_secret` (keycloak_client.py:64-77), never via a K8s API `get` and never the master key. It is the minting material for the client-credentials grant (`mint_service_account_token`, keycloak_client.py:80-149).

### The two weaknesses

- **One master key decrypts everything.** `crypto._fernet()` reads a single `AGENTSHIELD_ENCRYPTION_KEY`. Anything that holds it — registry-api, `judge.py`, any future service that calls `decrypt_json` — can decrypt *every* `AuthConfig`, *every* `LLMProvider`, *every* application secret in the database. Rotation is manual (Decision 12 says so in as many words: "no rotation automation, no audit trail beyond K8s audit logs"), and there is no per-credential scoping: the blast radius of a leaked key is the entire credential store. The Postgres row is, by construction, a single high-value target.
- **K8s Secrets are base64-in-etcd.** The materialized `agentshield-mcp-server-{id}` Secret is not encrypted at the application layer — it is base64, protected only by etcd-encryption-at-rest (cluster-dependent, off on Docker Desktop) and namespace RBAC. Good enough for a materialized *copy* of an already-Fernet'd secret; not a place you would want to be the *only* durable home of a long-lived credential.

### The forcing function — Phase 4 OAuth 2.1 + Decision 29 OBO

Everything above stores **static** credentials that a human typed once. Two planned features break that assumption:

- **MCP OAuth 2.1 (Phase 4, `mcp-tool-source-architecture.md` Phase 4 / OQ-01).** An external server that speaks OAuth 2.1 hands back a **refresh token** per `(server, user)`. That token is long-lived, must survive restarts, must be rotatable, must be scoped to one server + one user, and must be audited on every use. It has **no durable home today** — `AuthConfig.credentials_encrypted` is a single dict per server, not a per-user token store, and the proxy's `_token_cache` (keycloak_client.py:40) is in-memory and per-audience.
- **Decision 29 on-behalf-of.** The impersonation exchange needs a Keycloak confidential-client secret with an impersonation grant — more durable minting material, and (per that decision) a credential the platform must treat as a high-value asset in its own right.

So the pressure is not "encrypt the existing blobs better." It is "we are about to acquire a class of credential (per-`(server,user)` refresh tokens + impersonation minting material) that the current two stores were never shaped to hold, rotate, scope, or audit." That is what forces a pluggable store now rather than later.

### The nuance — what actually needs a durable home

Be precise about *what* moves into the provider, because getting this wrong invites persisting things that must stay ephemeral:

| Persist (durable → provider) | Do **not** persist (ephemeral → in-memory, re-minted) |
|---|---|
| Static `AuthConfig` creds (bearer token, api_key) | Minted service-account **access** tokens (`keycloak_client._token_cache`, keycloak_client.py:40) |
| OAuth 2.1 **refresh** tokens, per `(server, user)` (Phase 4) | OAuth 2.1 **access** tokens — re-minted from the refresh token on expiry |
| Keycloak client secret / impersonation-grant secret (minting material, Decision 29) | On-behalf-of **access** tokens — minted per call, never cached (identity.py §2 matrix) |

The rule: **persist the minting material and the refresh tokens; never persist a short-lived minted access token.** Access tokens are re-derived on demand — that is already how `mint_service_account_token` works (cache-until-`exp`, re-mint after). The provider is for the long-lived secrets that seed those mints, not the mint output.

---

## 2. The `CredentialProvider` interface

A `CredentialRef` is an opaque pointer — `<scheme>://<path>` — that says *where* a secret lives, never the secret itself. A stored row keeps only the ref; the value moves to the backend the scheme names.

```python
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class CredentialRef:
    scheme: str   # "pg-fernet" | "k8s" | "vault" | "aws-sm"
    path: str     # backend-specific locator (see Backends)

    def __str__(self) -> str:            # "vault://mcp/servers/{id}"
        return f"{self.scheme}://{self.path}"

    @classmethod
    def parse(cls, s: str) -> "CredentialRef":
        scheme, _, path = s.partition("://")
        return cls(scheme=scheme, path=path)


class CredentialProvider(Protocol):
    """Backend-agnostic durable store for credential *values*. The pointer
    (CredentialRef) lives in Postgres; the value lives here."""

    async def put(self, ref: CredentialRef, value: dict) -> None:
        """Create-or-replace the secret at ref."""

    async def get(self, ref: CredentialRef) -> dict:
        """Resolve ref to its plaintext dict. Raises CredentialNotFound if absent."""

    async def rotate(self, ref: CredentialRef, value: dict) -> CredentialRef:
        """Write a new version; MAY return a new (versioned) ref. Old version
        stays resolvable for the dual-read cutover window, then is dropped."""

    async def delete(self, ref: CredentialRef) -> None:
        """Remove the secret. 404/absent is a no-op (idempotent)."""
```

**How a row changes.** Today `AuthConfig` carries the value inline (`credentials_encrypted`). Under this design it carries a `credential_ref` string instead (e.g. `pg-fernet://auth-configs/{id}` or `aws-sm://agentshield/mcp/servers/{id}`), and the value is behind the provider. The Fernet column is retained during migration (dual-read, §6) and dropped only after backfill. The `MCPServer.auth_config_id` FK (models.py:994) is unchanged — the indirection is entirely inside how an `AuthConfig`'s value is stored, not in the graph of who references it.

Ref shapes by scheme:

| Scheme | Example ref | Resolves to |
|---|---|---|
| `pg-fernet` | `pg-fernet://auth-configs/{id}` | Fernet blob in Postgres (today's store, behind the seam) |
| `k8s` | `k8s://agentshield-mcp/agentshield-mcp-server-{id}` | K8s Secret (today's materialized copy) |
| `vault` | `vault://mcp/servers/{id}` (KV v2 path) | Vault KV entry |
| `aws-sm` | `aws-sm://agentshield/mcp/servers/{id}` | AWS Secrets Manager secret id/ARN |

---

## 3. Backends

Four backends implement the one Protocol. `FernetPgProvider` **is today's behaviour** behind the seam, so dev/default keeps working with no external dependency.

| Backend | Encryption at rest | Rotation | Access scoping | Audit |
|---|---|---|---|---|
| **FernetPgProvider** (dev/default) | Fernet (single `AGENTSHIELD_ENCRYPTION_KEY`, crypto.py) — value stays in Postgres | Manual, single master key (today) | Whoever holds the master key reads all — no per-secret scope | Postgres/K8s audit logs only (Decision 12) |
| **K8sSecretProvider** | base64 in etcd (etcd-encryption-at-rest if the cluster enables it) | Manual (`kubectl`/`upsert_secret`) | K8s RBAC — namespace + verb (`get` on `agentshield-mcp`, rbac.yaml) | K8s API audit log |
| **VaultProvider** | Vault transit / KV, per-secret keys | Native leases + rotation policies; dynamic secrets possible | Vault policy per path (`mcp/servers/{id}`), short-lived token per reader | Vault audit device — per-read, per-identity |
| **AwsSecretsManagerProvider** | KMS CMK per secret | Native rotation (Lambda / scheduled), versioned | IAM policy per secret ARN, resolved via **IRSA** (pod SA → IAM role) | CloudTrail — per-`GetSecretValue`, per-role |

The two external backends (Vault, ASM) are the ones that actually fix the weaknesses in §1: per-secret keys instead of one master key, native rotation instead of manual, per-path/per-ARN scoping instead of "holds the key → reads everything," and a real per-read audit trail keyed to a caller identity.

---

## 4. How it threads through MCP

Two call sites change; both go from "touch the value directly" to "resolve a ref through the provider." Nothing about the tool-call contract (§3c of the MCP arch doc) or the discover flow changes shape.

- **`registry-api` — `mcp_secrets.materialize_server_secret` (mcp_secrets.py:104-151).** Today it calls `decrypt_json(auth_config.credentials_encrypted)` (line 140) directly. Under this design it calls `provider.get(CredentialRef.parse(auth_config.credential_ref))`. For the `pg-fernet` backend that is byte-identical to today (the provider *is* the Fernet decrypt). For `vault`/`aws-sm` the value comes from the external store — registry-api no longer needs to hold the master key to compose the per-server Secret. Writes on register/`PUT`-with-auth-change go through `provider.put`; server DELETE goes through `provider.delete` alongside `delete_server_secret`.

- **`mcp-proxy` — `credentials.read_server_secret` (credentials.py:51-105).** Today it reads the materialized K8s Secret. Under this design the behaviour is **backend-dependent, and that is the containment win (§5)**:
  - `pg-fernet` / `k8s` backends: unchanged — the proxy still reads the materialized `agentshield-mcp-server-{id}` Secret, because it has neither the DB nor the master key and cannot resolve a `pg-fernet://` ref itself. Materialization stays.
  - `vault` / `aws-sm` backends: the proxy resolves the ref **itself**, with its own scoped read identity (a Vault token limited to `mcp/servers/*`, or an IRSA role limited to `agentshield/mcp/servers/*` ARNs). The per-server K8s Secret materialization is **eliminated** for these backends — one fewer copy of the secret exists, and the read is scoped per-path instead of namespace-wide.

- **Service-identity minting material + (Phase 4) OAuth refresh tokens.** The Keycloak client secret the proxy reads from a file (keycloak_client.py:64-77) becomes a `vault://`/`aws-sm://` ref the proxy resolves with the same scoped identity — no file mount to manage. Phase-4 OAuth refresh tokens are stored per `(server, user)` under e.g. `aws-sm://agentshield/mcp/oauth/{server_id}/{user_sub}`; the proxy resolves the ref, exchanges the refresh token for a fresh **access** token, uses it, and **never persists the access token** (§1 nuance restated). Decision 29's impersonation-grant secret is stored and resolved the same way.

**Ephemeral-vs-durable, restated at the call site:** `provider.get` only ever returns durable material — a static cred dict, a refresh token, or a client secret. The access token that a mint or a refresh-exchange produces stays in the in-memory cache (`_token_cache`, keycloak_client.py:40) or is minted per call (OBO, identity.py:54-70) and is re-derived on expiry. The provider is never asked to store a minted access token.

---

## 5. Proxy containment

The proxy is, by the MCP arch doc's own words (§3b), "the highest-value credential target in the mesh": it can drive every registered external server with stored credentials. Its current containment is the RBAC in `charts/agentshield/charts/mcp-proxy/templates/rbac.yaml`:

- ClusterRole: `tokenreviews: create` only (the auth-delegator half, nothing more).
- Role in `{{ .Values.secretsNamespace }}` (= `agentshield-mcp`): `secrets: get` only — **namespace-wide** across every `agentshield-mcp-server-*` Secret, but provably unable to reach `agentshield-platform` (where the master `AGENTSHIELD_ENCRYPTION_KEY` Secret lives).

That namespace-wide `secrets: get` is the weak edge: a proxy compromise reads *every* server's materialized credential, because RBAC's finest grain for `get` on Secrets is the namespace, not the individual object.

An external-store backend is **strictly better** on exactly this axis:

- **Vault:** the proxy's token carries a policy scoped to `mcp/servers/{id}` (or a template bounded to the servers it is actually serving). A compromise reads only what the policy names, and every read is in Vault's audit device keyed to the proxy's identity.
- **AWS Secrets Manager via IRSA:** the proxy pod's ServiceAccount maps to an IAM role whose policy allows `secretsmanager:GetSecretValue` on `arn:…:secret:agentshield/mcp/servers/*` and nothing else. Per-secret ARN conditions can tighten it further. Every `GetSecretValue` is a CloudTrail event tied to that role.

Both preserve the load-bearing invariant this whole credential path exists to protect: **the proxy never holds the master `AGENTSHIELD_ENCRYPTION_KEY`, never touches the DB.** A scoped Vault token / IRSA role is *more* contained than namespace-wide `secrets: get`, not less — it replaces "read every secret in the namespace" with "read exactly the paths your policy names," and adds a per-read audit trail the K8s Secret path never had. The proxy's ClusterRole (`tokenreviews: create`) is untouched; only the credential-read half moves from K8s RBAC to a scoped external-store identity.

---

## 6. Migration

Fernet → external is a dual-read cutover, per credential class, with dev untouched:

1. **Seam first (no behaviour change).** Introduce `CredentialProvider` + `CredentialRef`; ship `FernetPgProvider` as the default. Route `mcp_secrets.materialize_server_secret` and the proxy read through the provider. At this step every ref is `pg-fernet://…` and the system is byte-identical to today — this is the "no orphan, thin vertical slice" step.
2. **Dual-read.** Add the chosen external backend (`aws-sm` recommended, §8). A resolver reads the ref's scheme: `pg-fernet://` → Fernet (old), `aws-sm://` → ASM (new). Both are live at once.
3. **Backfill + rotate.** For each credential, `provider.put` the value into the external store, flip the row's `credential_ref` to the new scheme, and — critically — **rotate** the underlying secret (issue a new token / re-key), so a value that ever sat under the single master key is retired, not merely re-pointed. Rotation is the point of the migration, not a nicety.
4. **Drop the old column.** After backfill completes and the dual-read window closes, drop `credentials_encrypted` (guarded, idempotent migration — preserve on the way down). The master key survives only for whatever dev/default still runs `pg-fernet`.

**Dev-mode is unaffected.** `FernetPgProvider` stays the default; a local checkout needs no Vault, no AWS, no IRSA — exactly one `AGENTSHIELD_ENCRYPTION_KEY` env var, as today. The external backends are opt-in via config, selected per deployment.

---

## 7. Phasing

- **Now (Phase 4 prerequisite, this decision).** Land the seam (`CredentialProvider`, `CredentialRef`, `FernetPgProvider`) with the two MCP call sites routed through it, byte-identical to today. This is the thin slice that unblocks everything below without changing behaviour.
- **Phase 4 — MCP OAuth 2.1 (`mcp-tool-source-architecture.md` Phase 4 / OQ-01).** The refresh-token store lands on top of the seam. This is the feature that *requires* an external backend to exist — a per-`(server,user)` refresh token in a single-master-key Fernet blob is exactly the anti-pattern §1 describes. OAuth 2.1 and the first external backend ship together.
- **Decision 29 (on-behalf-of).** When `identity-propagation-architecture.md` Phase 0–2 lands and Decision 29's impersonation exchange is built, its Keycloak impersonation-client secret is stored/resolved through the same provider — one place for minting material, audited and rotatable. The OBO stub (`identity.py::mint_on_behalf_of_token`, identity.py:54-70) is filled in against a provider-resolved client secret, not a new file mount.
- **`todo_llm_secret_separation` (related, not blocked-on).** Moving `LLMProvider` secret handling out of registry-api (that backlog note) becomes cleaner once secrets live behind a provider — deploy-controller can resolve a ref with its own scoped identity instead of registry-api holding a cluster-wide secret ClusterRole. Not a dependency; a beneficiary.

---

## 8. Open questions

1. **Which external store first — Vault or ASM?** **Recommendation: AWS Secrets Manager via IRSA.** The platform already runs on EKS (ECR `us-west-2`, IRSA available), so ASM is a *managed* dependency reached with an IAM role the pod already can assume — no new stateful service to run, patch, seal/unseal, and back up. Vault is more capable (dynamic secrets, transit, finer leasing) but is precisely the "another complex stateful service" Decision 12 rejected for MVP; standing it up is its own project. Default to ASM+IRSA; keep `VaultProvider` a first-class backend for on-prem/non-AWS deployments and for teams that already run Vault. The `CredentialProvider` seam means this is a config choice, not a rewrite.
2. **Scoping granularity — per-server vs per-team.** Refs are per-server today (`mcp/servers/{id}`). Phase-4 OAuth refresh tokens are inherently per-`(server, user)`. Is there a per-team tier in between (one credential shared across a team's servers), and does the ref path encode team? Decide before the ref scheme is frozen, since it is baked into IAM/Vault policy paths.
3. **Rotation triggers.** Native rotation (ASM Lambda / Vault lease) covers time-based rotation. What triggers an *event*-based rotation — a suspected proxy compromise, a departing team member (for OBO), a manual "rotate everywhere" button (the reuse win Decision 30 built for `applications`)? The `rotate(ref, value) -> CredentialRef` signature supports versioned rotation; the *policy* for when it fires is unspecified here.
4. **Cross-service provider ownership.** Does `registry-api`, `deploy-controller`, or a small shared library own the provider client? Ties into `todo_llm_secret_separation` — if deploy-controller resolves refs directly, registry-api sheds its cluster-wide secret ClusterRole.

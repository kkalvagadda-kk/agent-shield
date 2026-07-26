# Research — MCP as a Tool Source, Phase 4

**Scope:** WS-1 CredentialProvider (Decision 31, the hard prerequisite) + WS-2 MCP OAuth 2.1 for external servers (OQ-01). WS-3 (`resources`/`prompts`, OQ-02) is scope-checked out (Part E).
**Companion artifacts (same dir):** `plan.md`, `data-model.md`, `tasks.md`, `quickstart.md`, `contracts/registry-api-oauth-phase4.md`, `contracts/mcp-proxy-oauth-phase4.md`, `contracts/studio-mcp-oauth-phase4.md`.

Grounding date: 2026-07-25. Every code claim below was read from the shipped Phase-1/2 tree on branch `mcp-tool-source` (registry-api `0.2.228`, studio `0.1.162`, mcp-proxy `0.1.3`, alembic head `0072`, e2e ceiling `suite-85`).

---

## Part A — FR / OQ reconciliation (brief labels vs. the requirements + design docs)

| Workstream | Requirement of record | Where it lives today | Note |
|---|---|---|---|
| WS-1 — CredentialProvider seam | **Decision 31** + `docs/design/credential-provider-architecture.md` | Design accepted, **implementation deferred to Phase 4** (design §7). Nothing built yet. | The hard prerequisite: OAuth refresh tokens need a durable per-`(server,user)` home the current stores were never shaped for (Decision 31 "new pressure"). |
| WS-2 — MCP OAuth 2.1 | **OQ-01** (`requirements.md` §10/§11 "Phase 4"; `mcp-tool-source-architecture.md` §Phase-4) | Deferred-intentional gap (arch doc §7 gap ledger row "MCP OAuth 2.1 … Phase 4 (OQ-01)"). | External-server concept only; internal servers use the identity-mode matrix (§3b), which OAuth is orthogonal to (Part C, C7). |
| WS-3 — `resources`/`prompts` | **OQ-02** (`requirements.md` §10 "resources & prompts") | Deferred-intentional gap. | Scope-checked to its **own later plan** (Part E) — a distinct MCP primitive set with its own binding/execution/governance model, orthogonal to OAuth. |

**Naming correction the brief anticipated:** Decision 31's design doc calls the dev backend `FernetPgProvider` and says its ref is `pg-fernet://auth-configs/{id}` resolving to "today's store." The *shipped* code stores the value in `AuthConfig.credentials_encrypted` (a single column). Phase 4 needs a home for a **second** credential class (OAuth refresh tokens, which are not auth-configs). So the seam introduces a generic `credential_blobs` key-value table as `FernetPgProvider`'s storage, and the auth-config value is **relocated** column→`credential_blobs` during the WS-1 backfill. "Byte-identical to today" (Decision 31 / design §6 step 1) is honored at the level that matters — the composed upstream `auth_headers` the proxy receives, and the Fernet crypto used — not the physical byte location of the ciphertext. See C1 and `data-model.md §1`.

---

## Part B — Grounding corrections (design intent said X; the shipped code says Y)

1. **The proxy already has a per-`(server, user)` session cache and threads `x-user-sub`.** `session_cache.py` keys on the composite `(server_id, user_sub)` and `main.py::tools_call` already accepts `x_user_sub` and passes it to `identity.resolve_headers(... user_sub=..., is_data_plane=True)`. So OAuth's per-user token dimension is **already plumbed** — Phase 4 reuses the existing composite key rather than inventing pooling (design §3c "forward-compatible" note is confirmed true in code).

2. **`identity.resolve_headers` is a clean, pure selection seam with an explicit `is_data_plane` context.** It switches on `connection.identity_mode ∈ {none, service_identity, on_behalf_of}`. OAuth is an **external** concept and an external server is always `identity_mode="none"` (enforced by `MCPServerCreate/Update` validators, `models.py:1008-1014`). So OAuth is a genuinely orthogonal dimension: Phase 4 adds a **first-checked** `external_auth_mode == "oauth"` branch that runs *before* the identity_mode switch, and the existing `none/service_identity/on_behalf_of` paths stay byte-identical (C7). This is an explicit named dimension, not a priority fallthrough.

3. **The proxy holds NO DB and NO master key, and that is enforced by RBAC, not convention.** `charts/agentshield/charts/mcp-proxy/templates/rbac.yaml` grants exactly `tokenreviews: create` (ClusterRole) + `secrets: get` in `agentshield-mcp` (Role). `config.py` has a module docstring that says adding a DB URL or `AGENTSHIELD_ENCRYPTION_KEY` "violates least-privilege — stop." So the OAuth design **cannot** store or refresh tokens in the proxy: the interactive dance + refresh-with-rotation live in registry-api; the proxy only *reads a current access token*. This is the load-bearing invariant of Part C, C3.

4. **The one shipped proxy→registry-api call (`authorize-tool-call`) is unauthenticated on purpose because it returns only a boolean.** `internal_mcp.py` and `authz.py` confirm: NetworkPolicy-trusted, no TokenReview, "returns only a boolean — never a secret." The Phase-4 OAuth token-read endpoint **returns a bearer access token**, so it CANNOT reuse that unauthenticated posture — it must authenticate the caller is the proxy (C4). This is the one place Phase 4 breaks the "internal endpoints are unauthenticated" pattern, deliberately.

5. **registry-api's credential-write path is `crypto.encrypt_json` → column, plus a materialized K8s Secret in `agentshield-platform`.** `auth_configs.py::create/update` and `applications.py` both do `encrypt_json(...)` then `upsert_secret(...)`. `auth_configs.py::get_auth_config_secret_ref` already contains the comment "where an external secret store (Vault/ASM) will plug in: swap the `decrypt_json` read for the store's read" — the seam the design predicted exists in code and is the exact WS-1 rewire point.

6. **The mcp SDK's `OAuthClientProvider` does not fit a two-request server-side web flow.** Verified via the SDK docs (context7 `/modelcontextprotocol/python-sdk`): `OAuthClientProvider` is an `httpx.Auth` hook attached to a live transport that triggers the whole flow (discovery → DCR → PKCE → code exchange → refresh) automatically on a `401`, driven by `redirect_handler` + `callback_handler` async callbacks that must both resolve *within one coroutine*. A platform authorize→browser→callback spans **two independent HTTP requests** (and possibly two registry-api replicas). So Phase 4 **reuses the SDK's OAuth *mechanics* conceptually** but **hand-rolls** the two-request orchestration in registry-api with plain `httpx` + a Fernet-signed `state` (C5, C6). The proxy needs **zero** new mcp-SDK OAuth code — it already accepts `headers` on `mcp_client.connect_and_initialize(url, headers)`.

7. **registry-api has no `mcp` dependency today; the proxy does.** `services/mcp-proxy/requirements.txt` pins `mcp>=1.2,<2.0`; registry-api does not import `mcp` at all. Pulling the whole SDK (and its httpx pin) into registry-api just for a few Pydantic auth models is a heavy, avoidable coupling — the OAuth metadata/token JSON shapes are simple and spec-defined, so WS-2 hand-rolls minimal Pydantic models in `mcp_oauth.py` (C6, alternatives-rejected).

---

## Part C — Decisions

Each: **Decision / Rationale / Alternatives rejected / Assumptions.**

### C1 — WS-1 storage: generic `credential_blobs` KV as `FernetPgProvider`, value relocated from the `auth_configs` column
**Decision.** `FernetPgProvider` stores every credential *value* as a Fernet blob in a new generic table `credential_blobs (path PK, value_encrypted, timestamps)`, keyed by the `CredentialRef.path`. The provider is backend-agnostic and never special-cases a path. The WS-1 migration (`0073`) creates the table, adds `auth_configs.credential_ref`, and **backfills** each existing `auth_configs.credentials_encrypted` into a `credential_blobs` row at path `auth-configs/{id}` with `credential_ref='pg-fernet://credential-blobs/auth-configs/{id}'`. The legacy `credentials_encrypted` column is **retained** for dual-read and dropped only in a later phase (ledgered).
**Rationale.** OAuth refresh tokens are a *second* credential class that has no column of its own; a generic KV gives both classes one clean home in the dev/default backend with no external dependency, exactly matching Decision 31's "dev unaffected — one env var, no Vault/AWS." One storage location = no path-dispatch inside the provider (avoids the "priority fallthrough" the constitution forbids).
**Alternatives rejected.**
- *Keep the auth-config value in its column; special-case `auth-configs/*` refs inside the provider* — makes `FernetPgProvider` branch on `ref.path`, a type/priority sniff the constitution's "no bandaid" rule rejects; and still leaves OAuth tokens homeless.
- *A JSONB column on `mcp_oauth_grants` holding the refresh token directly* — puts a long-lived rotating secret back in a single-master-key blob, the exact anti-pattern Decision 31 exists to end.
**Assumptions.** The existing `crypto._fernet()` + `AGENTSHIELD_ENCRYPTION_KEY` remain the dev encryptor (byte-compatible). The backfill is idempotent (`INSERT … WHERE NOT EXISTS`) and preserves data.

### C2 — WS-1 first external backend: AWS Secrets Manager via IRSA
**Decision.** Ship `AwsSecretsManagerProvider` (boto3, IRSA) as the recommended production backend, config-selected via `CREDENTIAL_PROVIDER_BACKEND` (default `pg-fernet`). Refs are `aws-sm://{prefix}/{path}` resolving to a Secrets Manager secret id; `put`/`rotate` use `CreateSecret`/`PutSecretValue`, `get` uses `GetSecretValue`, `delete` uses `DeleteSecret` (with `ForceDeleteWithoutRecovery` off — recovery window preserves data). `VaultProvider` is **not** built this phase (kept a first-class future backend).
**Rationale.** The platform already runs on EKS (ECR `us-west-2`, IRSA available — `project_eks_test_cluster` memory). ASM is a *managed* dependency reached with an IAM role the pod already assumes — no new stateful service to run/patch/seal, which is exactly what Decision 12 rejected and Decision 31 §8 OQ-1 recommends. Per-secret KMS keys + native rotation + CloudTrail per-`GetSecretValue` audit directly fix §1's "one master key decrypts everything."
**Alternatives rejected.**
- *Vault first* — more capable (dynamic secrets, transit) but is "another complex stateful service" (Decision 12); standing it up is its own project.
- *K8sSecretProvider as the external backend* — base64-in-etcd with only namespace-grain RBAC is not a better home for a long-lived rotating refresh token (Decision 31 §1 weakness #2).
**Assumptions.** registry-api's pod ServiceAccount can be annotated with `eks.amazonaws.com/role-arn`; the IAM role policy scopes `secretsmanager:*` to `arn:…:secret:{prefix}/*`. Dev/CI never sets `CREDENTIAL_PROVIDER_BACKEND=aws-sm`, so no AWS is needed locally.

### C3 — Where the OAuth dance runs: registry-api obtains + stores + refreshes; the proxy only reads a current access token *(HEADLINE)*
**Decision.** The interactive authorization-code + PKCE dance, the refresh token storage (via WS-1's provider), **and** the refresh-token→access-token exchange (with rotation) **all run in registry-api**. The proxy never stores a token, never holds the master key, never talks to the upstream authorization server. On a data-plane call to an OAuth server the proxy asks registry-api `POST /api/v1/internal/mcp/oauth/access-token {server_id, user_sub}` for a **fresh access token**, presents it as the upstream `Authorization: Bearer`, and caches it in-memory per `(server_id, user_sub)` until its `exp` (never persisting it — same rule as the service-identity `_token_cache`).
**Rationale.** Three independent forces all point at registry-api: (a) the interactive redirect+consent+callback needs a browser and an authenticated user session — only registry-api sits behind Studio's login and has one; (b) the refresh token must be **durably stored** and the proxy holds no DB and no master key (Part B #3); (c) OAuth 2.1 **mandates refresh-token rotation** — each refresh returns a *new* refresh token and invalidates the old — so the exchange needs a **single writer** to persist the rotated token without a multi-replica race. registry-api owns the store, so it is the single writer; the proxy is a read-only, in-memory cache of the short-lived *access* token. This preserves every invariant: proxy-no-DB, proxy-no-master-key, proxy-read-only.
**Alternatives rejected.**
- *Proxy does its own refresh exchange against the AS* — it would have to **write back** the rotated refresh token (breaking read-only containment) and would race across replicas on the rotating token; and it would need egress to every upstream AS.
- *registry-api materializes a per-`(server,user)` K8s Secret with the current access token on a timer* — access tokens live minutes; a timer either over-refreshes or serves stale tokens, and materializing per-user Secrets multiplies the very copies WS-1 is trying to reduce. The pull model is lazy, single-writer, and cache-correct.
**Assumptions.** The proxy→registry-api hop is already NetworkPolicy-open (used today for `authorize-tool-call`); adding a second internal endpoint is a config + a token, not new topology.

### C4 — Authenticating the token-read endpoint: proxy SA token + TokenReview (not the unauthenticated internal pattern)
**Decision.** `POST /api/v1/internal/mcp/oauth/access-token` **returns a bearer access token**, so unlike `authorize-tool-call`/`list-changed` it is **not** unauthenticated. The proxy presents a projected ServiceAccount token (audience `agentshield-registry-api`) mounted at a new path; registry-api verifies it via K8s `TokenReview` and requires the subject to equal the mcp-proxy SA (`system:serviceaccount:{ns}:{release}-mcp-proxy`), else `403`. This adds a narrow `tokenreviews: create` ClusterRole to registry-api (the same single verb the proxy already holds).
**Rationale.** The other two internal endpoints are safe unauthenticated precisely because they leak nothing (a boolean, counters). A token-minting endpoint that echoed a user's OAuth access token to any pod that could reach it would be a credential-harvest confused-deputy. TokenReview is the platform's one real cryptographic service-identity primitive (design §3b) — reuse it rather than invent a scheme.
**Alternatives rejected.**
- *Shared HMAC secret mounted in both services (RCT-style)* — invents the identity-propagation "RCT" internal token that is Proposed-but-unbuilt (`identity-propagation-architecture.md`); reusing TokenReview needs no new scheme.
- *Leave it unauthenticated behind NetworkPolicy* — NetworkPolicy is L3/L4, unenforced on Docker Desktop, and cannot gate on identity; a token endpoint needs the SA proof.
**Assumptions.** registry-api may hold `tokenreviews: create` (a ClusterRole with exactly that verb; no secrets, no pods). The proxy deployment can project a second SA token (audience `agentshield-registry-api`) alongside its existing OPA/mcp-proxy tokens.

### C5 — Cross-request state for the dance: a Fernet-signed, short-TTL `state`, no pending table
**Decision.** The `state` parameter carried through the upstream authorization redirect is `crypto.encrypt_json({server_id, user_sub, code_verifier, nonce, exp})` (a Fernet token). The `/oauth/callback` endpoint decrypts it, checks `exp`, recovers the PKCE `code_verifier`, and exchanges the code. No `mcp_oauth_pending` table.
**Rationale.** authorize and callback are two independent requests that may hit different registry-api replicas; an in-memory pending map would break under >1 replica. Fernet-signing the state makes it self-contained and any-replica-verifiable using the master key registry-api already holds — no shared store, no cleanup job. PKCE (S256) + the upstream's one-time authorization code already bound the flow; the encrypted state adds confidentiality + expiry.
**Alternatives rejected.**
- *An `mcp_oauth_pending` DB table* — a third table plus a TTL sweeper for a value that is dead in ~60s; unnecessary given a signable state.
- *Plaintext `state` + server-side verifier store* — needs the pending table again and puts the verifier on the wire.
**Assumptions.** State TTL is short (default 600s, `MCP_OAUTH_STATE_TTL_SECONDS`). Replay within the TTL is not separately hardened (a nonce is embedded but not checked against a store) — ledgered as low-impact because the upstream one-time code + PKCE defeat a replayed state.

### C6 — OAuth mechanics: hand-rolled in `mcp_oauth.py`, minimal Pydantic models, plain httpx
**Decision.** `services/registry-api/mcp_oauth.py` implements the mechanics with `httpx` and small local Pydantic models: `discover_oauth_metadata(server_url)` (fetch `.well-known/oauth-protected-resource` → follow to the authorization server's `.well-known/oauth-authorization-server` / `openid-configuration`), `register_client(...)` (RFC 7591 dynamic client registration when the AS advertises `registration_endpoint`), `build_authorization_url(...)` (PKCE S256 challenge + Fernet `state`), `exchange_code(...)`, and `refresh_access_token(...)` (`grant_type=refresh_token`, capturing a rotated `refresh_token` if returned). Do **not** add the `mcp` SDK to registry-api.
**Rationale.** Part B #6/#7 — the SDK's provider can't span two requests and the SDK is a heavy dep for registry-api. The mechanics are a handful of well-specified JSON round-trips; hand-rolling them is less code than adapting the provider's callback model and keeps registry-api's dependency surface clean.
**Alternatives rejected.**
- *Drive `mcp.client.auth.OAuthClientProvider` with a coroutine parked on an asyncio future keyed by state* — fragile across replicas/restarts (the parked coroutine lives on one replica; the callback may land on another).
- *Vendor `mcp.shared.auth` models only* — still pulls the SDK's transitive deps; the models are ~5 simple shapes we can declare locally.
**Assumptions.** Target servers implement the MCP authorization spec's discovery (`.well-known/oauth-protected-resource`) or a standard OIDC discovery document; DCR is used when advertised, else a pre-registered `client_id`/`client_secret` is supplied via the server's `AuthConfig` (fallback path, ledgered).

### C7 — OAuth vs. the `identity_mode` matrix: orthogonal, first-checked `external_auth_mode` branch
**Decision.** Add `MCPServer.external_auth_mode ∈ {static, oauth}` (default `static`), surfaced to the proxy inside the per-server Secret's `connection` JSON. `identity.resolve_headers` gains a **first** check: `if connection.external_auth_mode == "oauth"` → the OAuth path (fetch a fresh access token via `oauth_tokens.get_oauth_access_token(server_id, user_sub)`, merge over static headers); **else** the existing `identity_mode` switch runs unchanged. An external OAuth server keeps `identity_mode="none"`, so the two dimensions never collide.
**Rationale.** OAuth is an external-server upstream-auth concept; `on_behalf_of`/`service_identity` are internal-server identity concepts (Decision 29). Keeping them separate named dimensions — rather than adding an `identity_mode="oauth"` value — means the `none/service_identity/on_behalf_of` matrix stays byte-identical and the constitution's "make illegal states unrepresentable / explicit context not priority fallthrough" holds: an external server (`is_external=true`) may set `external_auth_mode`; an internal server may set `identity_mode`; validators keep them from crossing.
**Alternatives rejected.**
- *Add `identity_mode="oauth"`* — conflates an external upstream-token concept into the internal-identity enum and would force the OBO/service-identity code path to branch on external-vs-internal; a category error.
- *Reuse the static `auth_headers` slot* — OAuth tokens are per-user and expire; a static header map can't carry them.
**Assumptions.** For OAuth on the **data plane**, `x-user-sub` is required (fail-closed to a `200 is_error` "re-authorize" body if absent — same posture as `on_behalf_of`). For the **admin plane** (discover/health), registry-api supplies the authorizing user's sub in the discover request (C9).

### C8 — Token scoping: per-`(server, user)`
**Decision.** OAuth grants and refresh tokens are scoped per-`(server_id, user_sub)`: table `mcp_oauth_grants` PK `(server_id, user_sub)`; refresh-token ref path `mcp-oauth-refresh/{server_id}/{user_sub}`; access-token proxy cache keyed `(server_id, user_sub)`. The DCR-registered client is per-server (`mcp_servers.oauth_client_ref`, path `mcp-oauth-client/{server_id}`).
**Rationale.** OAuth consent is inherently a user↔server grant — the upstream issues the token to the human who consented; two users of the same server hold different tokens with different scopes. This matches the design doc (Decision 31 §8 OQ-2: "Phase-4 OAuth refresh tokens are inherently per-`(server, user)`") and reuses the proxy's existing composite `(server_id, user_sub)` session key (Part B #1).
**Alternatives rejected.**
- *Per-`(server, team)` shared grant* — a shared refresh token means one member's consent (and scopes, and revocation) silently acts for the whole team; wrong trust model, and the upstream AS issues per-user anyway. (A team tier can be added later behind the same ref scheme — the client registration is already per-server.)
**Assumptions.** `user_sub` is the Keycloak subject already threaded as `x-user-sub`. A daemon/service-triggered agent reaching an OAuth server with no `user_sub` fails closed (C7).

### C9 — Discovery of an OAuth server: triggered post-authorize as the authorizing user
**Decision.** Discovery/health of an OAuth server needs a token too (the AS gates `tools/list`). registry-api triggers `discover` immediately after a successful `/oauth/callback`, passing the authorizing user's sub. `McpDiscoverRequest` gains an optional `user_sub`; when set for an `external_auth_mode=oauth` server the proxy fetches that user's access token for the admin-plane connect. The periodic health loop probes an OAuth server as the **most-recently-authorized** user (the grant with the freshest `updated_at`); if no `authorized` grant exists, the server is reported `status=needs_auth` without a proxy hop.
**Rationale.** There is no single "server token" for an OAuth server; a real token belongs to a user. Binding discovery to the just-authorized user is the natural moment a valid token exists. The health loop degrades gracefully to `needs_auth` rather than erroring when nobody has authorized.
**Alternatives rejected.**
- *A dedicated "service" grant for discovery* — would require the platform itself to hold a machine grant on the upstream, which most OAuth MCP servers don't offer; and re-introduces a shared token.
**Assumptions.** `MCPServer.status` gains no new value; a new `needs_auth` surfaces via the OAuth-status endpoint + `health_detail.last_error`, while `status` stays `connected|disconnected|error` (data-model §4).

### C10 — Decision 29 (OBO) vs. the OAuth user-token: two different mechanisms, no interaction at runtime
**Decision.** Treat them as fully separate. Decision 29 on-behalf-of is an **internal-server** Keycloak *impersonation* exchange (the platform mints a token *as* the user against **our** Keycloak); it never carries a re-presentable upstream token and is blocked on `identity-propagation-architecture.md` (still a `STUB` that raises — `identity.py:54-70`). WS-2 OAuth is an **external-server** flow where the *user themselves* consents at the upstream AS and the upstream issues the token. They share only the `x-user-sub` plumbing and the per-`(server,user)` cache key; they never call each other. WS-1's CredentialProvider is the one place they converge in the *future*: both the OBO impersonation-client secret (Decision 29) and OAuth refresh tokens (WS-2) become provider-stored minting material — but WS-2 does not implement or unblock Decision 29.
**Rationale.** Conflating them (e.g. routing OBO through the OAuth path) would try to re-present a token that, by design, never survives a hop (§7a). Keeping them separate keeps each fail-closed independently.
**Alternatives rejected.** *Unify under one "user identity" resolver* — the two produce tokens by different grants against different authorization servers; a single resolver would hide that and risk presenting the wrong credential.
**Assumptions.** The OBO stub stays a stub in Phase 4 (unchanged). WS-1 makes Decision 29 *cheaper* later (a provider-resolved impersonation secret instead of a new file mount) but that is out of Phase-4 scope.

### C11 — Migration/dual-read cutover (WS-1)
**Decision.** Per design §6: (1) seam — `0073` creates `credential_blobs` + `auth_configs.credential_ref`, backfills, routes `mcp_secrets.materialize_server_secret` + `auth_configs.py` writes through `get_provider()`, all on `pg-fernet` (behavior-preserving); (2) dual-read — the resolver reads `credential_ref`'s scheme (`pg-fernet://` → `credential_blobs`; `aws-sm://` → ASM); a legacy row with a null `credential_ref` but a non-null `credentials_encrypted` reads the legacy column (explicit legacy branch at the call site, not in the provider); (3) backfill+rotate to ASM is an **opt-in ops runbook** (not automated this phase); (4) dropping `credentials_encrypted` is **deferred** (a later migration) and ledgered.
**Rationale.** Thin vertical slice first (no behavior change, no orphan), external backend behind a config flag, rotation as a deliberate ops action, column-drop only after a dual-read window — exactly the design's staged cutover, keeping dev untouched.
**Alternatives rejected.** *Big-bang move + drop the column in one migration* — no dual-read window, irreversible, and would break any un-migrated deployment.
**Assumptions.** The legacy read branch is removed (and the column dropped) in a follow-up once all deployments are cut over.

### C12 — Environment knobs (all env-driven; defaults keep dev on `pg-fernet`, no AWS, OAuth-capable)

| Knob | Default | Where | Reasoning |
|---|---|---|---|
| `CREDENTIAL_PROVIDER_BACKEND` | `pg-fernet` | registry-api | Selects the provider. Dev/default stays Fernet-in-Postgres — no external store. |
| `AWS_SECRETS_MANAGER_PREFIX` | `agentshield` | registry-api | ASM secret-name prefix → the ref path + IAM ARN scope. |
| `AWS_REGION` | `us-west-2` | registry-api | ASM client region (matches ECR). |
| `MCP_OAUTH_CALLBACK_URL` | `""` (must set to enable OAuth) | registry-api | The public redirect URI registered with upstream ASes (`…/api/v1/mcp-servers/oauth/callback`). Empty → OAuth authorize returns `409 oauth_not_configured`. |
| `STUDIO_BASE_URL` | `""` | registry-api | Where `/oauth/callback` 302-redirects the browser back to (`{STUDIO_BASE_URL}/mcp-servers/{id}?oauth=…`). |
| `MCP_OAUTH_STATE_TTL_SECONDS` | `600` | registry-api | Fernet-`state` expiry (C5). |
| `MCP_PROXY_SA_AUDIENCE` | `agentshield-registry-api` | registry-api | Audience the token-read endpoint requires on the proxy's TokenReview'd SA token (C4). |
| `REGISTRY_API_OAUTH_TOKEN_URL` | `http://…registry-api…:8000/api/v1/internal/mcp/oauth/access-token` | mcp-proxy | The endpoint the proxy pulls a fresh access token from (C3). Reuses the existing `REGISTRY_API_URL` base. |
| `MCP_PROXY_REGISTRY_API_TOKEN_PATH` | `/var/run/secrets/registry-api/token` | mcp-proxy | Projected SA token (audience `agentshield-registry-api`) the proxy presents to the token-read endpoint (C4). Read fresh per call (rotates hourly). |
| `MCP_OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS` | `30` | mcp-proxy | Re-fetch a cached access token this long before its `exp` (mirrors `KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS`). |

---

## Part D — Blast radius / regression map (for the mandatory sweep)

WS-1 rewires the **credential read/write path** shared by every MCP server and every AuthConfig, so the sweep must prove the *unchanged* paths still work byte-identically:

- **`scripts/e2e/suite-84-mcp-tools.sh`** — Phase-1 register/discover/authorize + bundle field. Must stay green: the `pg-fernet` seam must compose identical `auth_headers`.
- **`scripts/e2e/suite-85-mcp-phase2.sh`** — health/list_changed/identity (`none`/`service_identity`). Must stay green: `resolve_headers` gains an OAuth branch but the three existing modes are untouched.
- **AuthConfig-consuming paths** — any suite that creates an `AuthConfig` and deploys a tool bound to it (`suite-81-deploy-tool-autograt.sh`, HTTP-tool suites): the `auth_configs.py` write now routes through the provider; the materialized `agentshield-platform` Secret + `get_auth_config_secret_ref` re-materialize path must be unchanged.
- **New suites:** `suite-86-credential-provider.sh` (WS-1) proves put/get/rotate/delete round-trips + the pg-fernet byte-identity + the legacy dual-read branch; `suite-87-mcp-oauth.sh` (WS-2) proves authorize→callback→grant, the internal access-token refresh+rotation, and the proxy fail-closed `needs_auth`.
- **Studio:** `studio/src/pages/McpServerDetailPage.test.tsx` + `studio/src/pages/McpServersPage.test.tsx` (Vitest) and `studio/e2e/mcp-servers.spec.ts` (Playwright) — extended for the OAuth panel + authorize journey; the existing register/health assertions must stay green.
- **Cross-service:** registry-api gains `tokenreviews: create` — confirm no existing RBAC test asserts the old (absent) grant; confirm the proxy's new projected token volume doesn't disturb the OPA/mcp-proxy token mounts.

---

## Part E — Scope check: WS-3 (`resources`/`prompts`) is deferred to its own plan

**Decision.** Keep Phase 4 = **WS-1 + WS-2**. `resources`/`prompts` (OQ-02) is recommended as a **separate later plan** (call it Phase 5).
**Why it's a large independent subsystem, not a Phase-4 add-on.** `tools` is the only MCP primitive the platform models today (`Tool` rows, `governed_tool` dispatch, the tool picker, OPA per-tool policy). `resources` and `prompts` are *different primitives* with their own wire methods (`resources/list`, `resources/read`, `resources/subscribe`, `prompts/list`, `prompts/get`), their own registry/binding model (a resource is not a callable tool — it is fetched content; a prompt is a templated message set), their own governance questions (is a resource read OPA-authorized? output-scanned as untrusted input? how does a prompt interact with the system prompt?), and their own Studio surfaces. None of that shares code with OAuth or the CredentialProvider. Bundling it would violate the planner scope-check (two unrelated subsystems in one plan) and the "vertical slice" rule.
**Sketch (for the future plan, not built here).** New proxy endpoints `POST /internal/resources/list|read` + `POST /internal/prompts/list|get` (same auth/error-to-200 shape as `tools/call`); new `MCPResource`/`MCPPrompt` models discovered alongside tools; a governance decision on whether a resource read is a governed action; Studio detail-page tabs for Resources/Prompts. Tracked in the gap ledger (`plan.md`) as **deferred (intentional)**.

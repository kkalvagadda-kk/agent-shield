# Implementation Plan — MCP as a Tool Source, Phase 4

**Status:** Ready to implement.
**Scope:** WS-1 CredentialProvider (Decision 31 — the hard prerequisite, built first) + WS-2 MCP OAuth 2.1 for external servers (OQ-01). WS-3 (`resources`/`prompts`, OQ-02) is scope-checked **out** to its own later plan (see Scope Check).
**Inputs:** `docs/decisions.md` (Decision 31, 29), `docs/design/credential-provider-architecture.md`, `docs/design/mcp-tool-source-architecture.md` §3/§3b/§7a, `docs/design/identity-propagation-architecture.md`, `docs/design/todo/mcp-tools-for-agents-requirements.md` §9/§11, the shipped Phase-1/2 code.
**Companion artifacts (same dir):** `research.md`, `data-model.md`, `tasks.md`, `quickstart.md`, `contracts/registry-api-oauth-phase4.md`, `contracts/mcp-proxy-oauth-phase4.md`, `contracts/studio-mcp-oauth-phase4.md`.

> **Grounding note (reason from the running product):** every interface below was reconciled against the shipped tree (registry-api `0.2.228`, studio `0.1.162`, mcp-proxy `0.1.3`, alembic head `0072`, e2e ceiling `suite-85`). Where the design doc and the code diverge, this plan follows the **code** and flags it in `research.md` (Part B). Two such corrections drive the whole plan: the SDK's `OAuthClientProvider` cannot span a two-request web flow (→ hand-roll the dance in registry-api), and the proxy is RBAC-fenced off the DB and master key (→ the proxy never stores or refreshes a token; it reads a fresh access token from registry-api).

---

## Scope Check — one plan, two tightly-sequenced workstreams (WS-3 deferred)

WS-1 (CredentialProvider) is a **hard prerequisite** for WS-2: OAuth introduces per-`(server,user)` refresh tokens that need a durable, rotatable, scoped home the current single-master-key Fernet blob was never shaped for (Decision 31). So WS-1 lands first (behavior-preserving seam), then WS-2 builds the OAuth store on top of it. **WS-3 (`resources`/`prompts`) is a separate MCP primitive subsystem** (its own wire methods, binding model, governance questions, Studio surfaces) with zero shared code with OAuth or the provider — bundling it would break the scope-check and the vertical-slice rule. **Recommendation: defer WS-3 to its own plan (Phase 5).** It is sketched in `research.md` Part E and ledgered below.

---

## Goal

1. **WS-1:** introduce a `CredentialProvider` seam (`put`/`get`/`rotate`/`delete` over a `CredentialRef` pointer), with `FernetPgProvider` (dev/default, behavior-preserving — Postgres stays the store) and `AwsSecretsManagerProvider` (prod, IRSA, opt-in). Rewire the two MCP credential call sites (`mcp_secrets.materialize_server_secret`, `auth_configs.py` writes/re-materialize) through it. Only the ref lands in Postgres; the value moves behind the provider. Default = byte-identical to today.
2. **WS-2:** let an external MCP server that advertises OAuth 2.1 be **authorized** by a user through the standard authorization-code + PKCE dance (run in registry-api), store the resulting refresh token per `(server,user)` via WS-1's provider, and have the proxy present a **fresh access token** (refreshed-with-rotation on expiry) as the upstream `Authorization` — fail-closed to a `200 is_error` "re-authorize" state when no usable token exists. Studio gains an **Authorize** step + a Connected/Needs-authorization badge.

---

## Architecture

```
WS-1 — CREDENTIAL PROVIDER SEAM (registry-api only; proxy read path unchanged for pg-fernet)

  auth_configs.py / mcp_secrets.py                 credential_provider.get_provider()
  ───────────────────────────────                 ─────────────────────────────────
   write cred ─┐                                    ┌─ FernetPgProvider  → credential_blobs (Fernet, Postgres)   [DEFAULT/dev]
   read  cred ─┼──►  CredentialRef("<scheme>://…")─►┤
   rotate     ─┘         (pointer in Postgres)      └─ AwsSecretsManagerProvider → AWS Secrets Manager (IRSA)    [prod, opt-in]
                                                        value NEVER in Postgres — only the ref
  Only the two MCP call sites change; the per-server K8s Secret + the proxy's read of it are UNCHANGED (byte-identical).


WS-2 — MCP OAuth 2.1 (dance in registry-api; proxy reads a fresh access token; proxy holds no DB/key)

  Studio                    registry-api                              upstream Authorization Server
  ──────                    ────────────                              ─────────────────────────────
  Authorize ─► POST /oauth/authorize ─► discover(.well-known)+DCR+PKCE
     ◄── authorization_url ───────────────────────────────────────►  consent screen (browser)
  browser ◄──────────── 302 ?oauth=connected ◄── GET /oauth/callback ◄── code + state
                             │  exchange code → {access, REFRESH}
                             │  provider.put(refresh)  ── mcp_oauth_grants(status=authorized)
                             ▼
  agent pod                mcp-proxy                                  registry-api                    upstream MCP server
  ─────────                ─────────                                  ────────────                    ──────────────────
  tools/call ─► /internal/tools/call ─► resolve_headers(external_auth_mode="oauth", user_sub)
                             │  oauth_tokens.get_oauth_access_token(server,user)
                             │─── POST /internal/mcp/oauth/access-token (proxy SA token, TokenReview'd) ─►│
                             │                                        refresh-with-rotation (single writer)│
                             │◄────────────── {status:authorized, access_token, expires_at} ──────────────│
                             │  cache in-memory per (server,user) until exp; NEVER persist
                             ▼  Authorization: Bearer <access> ───────────────────────────────────────────► tools/call
  needs_auth / error  ─────► 200 is_error "re-authorize"  (fail-closed — never a silent unauth call, never 5xx)
```

**Invariants (all preserved, verified against code):**
- The proxy holds **no DB, no `AGENTSHIELD_ENCRYPTION_KEY`, no refresh token** — RBAC-fenced (`mcp-proxy/rbac.yaml`), and the OAuth token-read is a *pull* of a short-lived access token it caches in memory only.
- `identity_mode ∈ {none, service_identity, on_behalf_of}` stays **byte-identical** for non-OAuth servers; OAuth is a first-checked orthogonal `external_auth_mode` branch (external servers are always `identity_mode="none"`).
- **Fail-closed:** expired/absent/revoked OAuth token → `200 is_error=true`; refresh failure → a "re-authorize" state, never a crash, never a downgrade to static/service creds.
- Dev/default needs no AWS and no upstream AS beyond a stub: `FernetPgProvider` is the default backend; OAuth is exercised against a stub server + stub AS.

---

## Tech Stack

- **registry-api:** FastAPI + SQLAlchemy async + Alembic; `httpx` (OAuth mechanics); `cryptography.fernet` (existing `crypto.py`, reused for the value store + the signed `state`); **`boto3`** (new — ASM backend only, lazily imported). No `mcp` SDK dependency added (`research.md` C6).
- **mcp-proxy:** FastAPI + `httpx` (existing); **no new library** — OAuth is an outbound `httpx` pull + an in-memory cache. `mcp>=1.2,<2.0` pin unchanged (no new OAuth SDK code).
- **Studio:** React + React Query + axios (existing `mcpServersApi.ts`); `react-router-dom` `useSearchParams` for the callback landing.
- **Infra:** Helm (registry-api + mcp-proxy subcharts), K8s projected ServiceAccount tokens + TokenReview (existing primitive), EKS IRSA (existing) for the ASM backend.

---

## Constitution Check (against this worktree's `CLAUDE.md`)

| # | Principle | Status | How this plan satisfies it |
|---|---|---|---|
| 1 | Real user journey proven (Playwright, not just an endpoint) | **PASS (planned)** | T016 extends `studio/e2e/mcp-servers.spec.ts`: register External+OAuth → detail → click **Authorize** → assert `POST …/oauth/authorize` fires + returns `authorization_url` (the redirect wiring; the upstream consent is a third-party page, same boundary the bash suites accept). |
| 2 | Save → reload → assert survived | **PASS (planned)** | T016: after a stubbed `?oauth=connected` callback, reload the detail page and assert the badge reads **Connected** (persisted in `mcp_oauth_grants`). T017/T018 bash suites reload the grant/blob rows from the DB after write. |
| 3 | No orphan code | **PASS (planned)** | Every new symbol has a caller in the same task: `get_provider()` ← `mcp_secrets`/`auth_configs` (T004); `mcp_oauth.*` ← `routers/mcp_oauth.py` (T008); `oauth/access-token` ← proxy `oauth_tokens` (T010/T011); `startMcpOAuth`/`getMcpOAuthStatus`/`disconnectMcpOAuth` ← the detail page (T015). T020 greps each. |
| 4 | Vertical slices, not horizontal layers | **PASS (planned)** | WS-1 is one thin behavior-preserving slice (T002-T005) proven at CP1 before WS-2. WS-2 wires one path (authorize→store→proxy-read→tool-call) end-to-end and proves it at CP3 before Studio (P9). |
| 5 | Honest gap ledger | **PASS** | See Gap Ledger — column-drop, proxy-side ASM read, DCR-fallback, state-replay, WS-3 all tagged deferred vs debt. |
| 6 | Reason from the running product | **PASS** | `research.md` Part B lists 7 code-vs-design corrections; the plan follows the code. |
| 7 | Bug-fix regression-test-first | **N/A (no bug fix in scope)** | Feature work; the CP checkpoints + T020 regression sweep guard the shared credential path. |
| 8 | Document bug + debugging session | **N/A (no bug fix in scope)** | No bug postmortem required; the artifacts here are the record. |

**Deliberate, justified scope note (Complexity Tracking):** WS-1 relocates the auth-config credential value from a column to `credential_blobs` (not a pure "add a pointer" change) — justified because a *second* credential class (OAuth refresh tokens) needs a home and a generic KV avoids a per-path branch in the provider (Complexity Tracking, row 1).

---

## File Structure

### New — registry-api (`services/registry-api/`)
| File | C/M | Task | Responsibility |
|---|---|---|---|
| `credential_provider.py` | Create | T002,T005 | `CredentialRef`, `CredentialProvider` Protocol, `CredentialNotFound`, `FernetPgProvider`, `AwsSecretsManagerProvider`, `get_provider()` factory. |
| `mcp_oauth.py` | Create | T007 | OAuth mechanics: discovery, DCR, authorization-URL+PKCE+Fernet-state, code exchange, refresh-with-rotation; minimal Pydantic metadata/token models. |
| `routers/mcp_oauth.py` | Create | T008 | `POST /mcp-servers/{id}/oauth/authorize`, `GET /mcp-servers/oauth/callback`, `GET /mcp-servers/{id}/oauth/status`, `DELETE /mcp-servers/{id}/oauth`. |
| `alembic/versions/0073_credential_blobs_and_credential_ref.py` | Create | T003 | `credential_blobs` table + `auth_configs.credential_ref` + backfill (idempotent). |
| `alembic/versions/0074_mcp_oauth_grants.py` | Create | T006 | `mcp_servers.external_auth_mode`/`oauth_client_ref` + `mcp_oauth_grants` table (idempotent). |

### Modified — registry-api
| File | Task(s) | Change |
|---|---|---|
| `models.py` | T003, T006 | Add `CredentialBlob`; `AuthConfig.credential_ref`; `MCPServer.external_auth_mode`/`oauth_client_ref`; `MCPOAuthGrant`. |
| `mcp_secrets.py` | T004, T009 | `materialize_server_secret` reads creds via `get_provider()`; writes `external_auth_mode` into the `connection` JSON. |
| `routers/auth_configs.py` | T004 | create/update/`secret-ref` route credential read+write through `get_provider()` (dual-read legacy branch). |
| `routers/internal_mcp.py` | T010 | Add `POST /internal/mcp/oauth/access-token` (TokenReview'd, subject-pinned, refresh-with-rotation). |
| `routers/mcp_servers.py` | T006, T008 | Validators for `external_auth_mode`; DELETE revokes grants + deletes refs. |
| `schemas.py` | T006 | `MCPServerCreate/Update/Response` gain `external_auth_mode` + cross-field validator. |
| `mcp_proxy_client.py` | T009 | `discover_server(server_id, user_sub=None)` threads `user_sub` to the proxy. |
| `mcp_health.py` | T009 | For an OAuth server, probe as the most-recently-authorized user; `needs_auth` if none. |
| `config.py` | T002, T008, T010 | `CREDENTIAL_PROVIDER_BACKEND`, `AWS_SECRETS_MANAGER_PREFIX`, `AWS_REGION`, `MCP_OAUTH_CALLBACK_URL`, `STUDIO_BASE_URL`, `MCP_OAUTH_STATE_TTL_SECONDS`, `MCP_PROXY_SA_AUDIENCE`. |
| `main.py` | T008 | Register the `mcp_oauth` router. |
| `requirements.txt` | T005 | Add `boto3` (ASM backend). |

### New — mcp-proxy (`services/mcp-proxy/`)
| File | C/M | Task | Responsibility |
|---|---|---|---|
| `oauth_tokens.py` | Create | T011 | `get_oauth_access_token(server,user)` (pull+cache), `invalidate`, `OAuthUserRequired`/`OAuthAuthorizationRequired`/`OAuthTokenUnavailable`. |

### Modified — mcp-proxy
| File | Task(s) | Change |
|---|---|---|
| `identity.py` | T012 | First-checked OAuth branch in `resolve_headers`; re-export the OAuth exceptions. |
| `credentials.py` | T012 | `ServerConnection.external_auth_mode` + parse from the Secret. |
| `main.py` | T013 | `tools_call`/`discover` OAuth `except` arms + `user_sub`; evict-retry `oauth_tokens.invalidate`. |
| `schemas.py` | T013 | `McpDiscoverRequest.user_sub: str | None = None`. |
| `config.py` | T011 | `REGISTRY_API_OAUTH_TOKEN_URL`, `MCP_PROXY_REGISTRY_API_TOKEN_PATH`, `MCP_OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS`. |

### Modified — Studio (`studio/`)
| File | Task(s) | Change |
|---|---|---|
| `src/api/mcpServersApi.ts` | T015 | `startMcpOAuth`, `getMcpOAuthStatus`, `disconnectMcpOAuth`, `McpOAuthStatus`, `external_auth_mode` on payload/response types. |
| `src/pages/McpServerDetailPage.tsx` | T015 | OAuth Connection panel (Authorize/Connected/Disconnect) + `?oauth=` callback landing. |
| `src/pages/McpServersPage.tsx` | T015 | Register-modal OAuth toggle (External only). |
| `src/pages/McpServerDetailPage.test.tsx` | T015 | Vitest: panel states + callback toast + status query. |
| `src/pages/McpServersPage.test.tsx` | T015 | Vitest: OAuth toggle → `external_auth_mode:"oauth"` payload. |
| `e2e/mcp-servers.spec.ts` | T016 | Playwright: authorize journey + save→reload→assert. |

### New / Modified — charts, infra, scripts, e2e, docs
| File | C/M | Task | Change / Responsibility |
|---|---|---|---|
| `charts/agentshield/charts/registry-api/templates/rbac.yaml` | Modify | T010 | Add a `tokenreviews: create` ClusterRole + binding for registry-api (C4). |
| `charts/agentshield/charts/registry-api/templates/serviceaccount.yaml` | Modify | T005 | Optional IRSA `eks.amazonaws.com/role-arn` annotation (values-gated, off by default). |
| `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml` | Modify | T014 | Projected SA-token volume (audience `agentshield-registry-api`) + the three OAuth env vars. |
| `charts/agentshield/values.yaml` | Modify | T005,T014,T019 | `CREDENTIAL_PROVIDER_BACKEND` env (default pg-fernet), OAuth env, IRSA toggle, tag bumps. |
| `charts/agentshield/values-eks.yaml` | Modify | T005,T014,T019 | Same for EKS + the real IRSA role ARN + tag bumps. |
| `scripts/deploy-cpe2e.sh` | Modify | T019 | Bump `REGISTRY_API_TAG`→`0.2.229`, `MCP_PROXY_TAG`→`0.1.4`, `STUDIO_TAG`→`0.1.163` (verify-then-bump). |
| `scripts/deploy-eks.sh` | Modify | T019 | Mirror the tag bumps for EKS. |
| `scripts/e2e/fixtures/oauth_mcp_server.py` | Create | T018 | Stub OAuth-protected MCP server + minimal AS (`.well-known`, `/authorize`, `/token`, `tools/list` gated on a bearer). |
| `scripts/e2e/suite-86-credential-provider.sh` | Create | T017 | WS-1: provider put/get/rotate/delete round-trip, pg-fernet byte-identity, dual-read legacy branch. |
| `scripts/e2e/suite-87-mcp-oauth.sh` | Create | T018 | WS-2: authorize→callback→grant, internal access-token refresh+rotation, proxy fail-closed. |
| `scripts/e2e/run-all.sh` | Modify | T017,T018 | Register suites 86 + 87. |
| `docs/decisions.md` | Modify | T019 | Decision 31 status → Implemented (WS-1); note Phase-4 OAuth. |
| `docs/design/credential-provider-architecture.md` | Modify | T019 | Status → Implemented (seam + FernetPg + ASM); note deferred column-drop. |
| `docs/design/mcp-tool-source-architecture.md` | Modify | T019 | Gap-ledger rows OAuth (OQ-01) → resolved; resources/prompts (OQ-02) → deferred to Phase 5. |
| `docs/design/todo/mcp-tools-for-agents-requirements.md` | Modify | T019 | §11 Phase 4 status → OAuth shipped; resources/prompts deferred. |
| `docs/testing/manual-ui-e2e-test-plan.md` | Modify | T020 | Known-gaps: proxy-side ASM read, column-drop, WS-3, state-replay. |

Every file above appears in exactly one task's Files list, and every file in a task's Files list appears here.

---

## Key Interfaces

Exact signatures every task must match.

```python
# services/registry-api/credential_provider.py — T002 (FernetPg), T005 (ASM + factory)
from dataclasses import dataclass
from typing import Protocol

class CredentialNotFound(Exception):
    """No secret at the given ref (get/rotate on an absent ref). Callers map to 404/heal."""

@dataclass(frozen=True)
class CredentialRef:
    scheme: str          # "pg-fernet" | "aws-sm"
    path: str            # e.g. "credential-blobs/auth-configs/{id}"  |  "agentshield/mcp-oauth-refresh/{sid}/{sub}"
    def __str__(self) -> str: ...
    @classmethod
    def parse(cls, s: str) -> "CredentialRef": ...

class CredentialProvider(Protocol):
    async def put(self, ref: CredentialRef, value: dict) -> None: ...
    async def get(self, ref: CredentialRef) -> dict: ...        # raises CredentialNotFound
    async def rotate(self, ref: CredentialRef, value: dict) -> CredentialRef: ...  # may return a new ref
    async def delete(self, ref: CredentialRef) -> None: ...     # idempotent (absent = no-op)

class FernetPgProvider:   # value → credential_blobs (Fernet, existing crypto). Dev/default.
    def __init__(self, session_factory): ...
class AwsSecretsManagerProvider:  # value → AWS Secrets Manager via boto3/IRSA. Prod, opt-in.
    def __init__(self, prefix: str, region: str): ...

def get_provider() -> CredentialProvider:
    """Config-selected singleton keyed on config.CREDENTIAL_PROVIDER_BACKEND
    ('pg-fernet' default | 'aws-sm'). FernetPg binds the async session factory;
    ASM binds prefix+region (boto3 lazily imported so dev needs no AWS libs at import)."""
```
```python
# services/registry-api/mcp_oauth.py — T007  (plain httpx; NO mcp SDK dependency)
async def discover_oauth_metadata(server_url: str) -> "OAuthMetadata":
    """GET {server_url}/.well-known/oauth-protected-resource → follow to the AS's
    .well-known/oauth-authorization-server (or openid-configuration). Returns the merged
    metadata (authorization_endpoint, token_endpoint, registration_endpoint?,
    revocation_endpoint?, scopes_supported, issuer). Raises OAuthDiscoveryError."""
async def register_client(meta, redirect_uri: str) -> "OAuthClientInfo":  # RFC 7591 DCR
async def build_authorization_url(meta, client, *, redirect_uri, state, code_challenge,
                                  scope, resource) -> str:                 # PKCE S256
async def exchange_code(meta, client, *, code, code_verifier, redirect_uri) -> "OAuthTokenResponse"
async def refresh_access_token(meta, client, *, refresh_token) -> "OAuthTokenResponse"
# OAuthTokenResponse: access_token, token_type, expires_in, refresh_token|None, scope|None
def make_state(payload: dict) -> str:   # crypto.encrypt_json({server_id,user_sub,code_verifier,nonce,exp})
def read_state(state: str) -> dict:     # crypto.decrypt_json + exp check → raises OAuthStateError
```
```python
# services/registry-api/routers/internal_mcp.py — T010  (added alongside authorize-tool-call)
class OAuthAccessTokenRequest(BaseModel):
    server_id: uuid.UUID
    user_sub: str = Field(..., min_length=1)
class OAuthAccessTokenResponse(BaseModel):
    status: str                        # "authorized" | "needs_auth" | "error"
    access_token: str | None = None    # only when authorized
    expires_at: datetime | None = None
    detail: str | None = None
@router.post("/oauth/access-token", response_model=OAuthAccessTokenResponse)
async def oauth_access_token(body, authorization: str | None = Header(None), db=Depends(_get_db)):
    """TokenReview the proxy SA token (audience MCP_PROXY_SA_AUDIENCE); subject MUST equal
    the mcp-proxy SA (else 403). Load grant FOR UPDATE; needs_auth/error → 200 no-token;
    else provider.get(refresh) → refresh_access_token → rotate+persist a new refresh token
    → 200 {authorized, access_token, expires_at}. Refresh failure → status='error', 200."""
```
```python
# services/mcp-proxy/oauth_tokens.py — T011
_access_cache: dict[tuple[str, str], tuple[str, int]] = {}   # (server,user) -> (token, exp). NEVER persisted.
class OAuthUserRequired(Exception): ...
class OAuthAuthorizationRequired(Exception): ...
class OAuthTokenUnavailable(Exception): ...
async def get_oauth_access_token(server_id: str, user_sub: str) -> str: ...  # pull from registry-api + cache
def invalidate(server_id: str, user_sub: str) -> None: ...
```
```python
# services/mcp-proxy/identity.py — T012  (first-checked branch; matrix unchanged otherwise)
async def resolve_headers(connection, *, user_sub=None, is_data_plane: bool) -> dict[str, str]:
    if connection.external_auth_mode == "oauth":
        if not user_sub:
            raise oauth_tokens.OAuthUserRequired(...)          # data + admin: fail closed
        token = await oauth_tokens.get_oauth_access_token(str(connection_server_id), user_sub)
        return {**connection.auth_headers, "Authorization": f"Bearer {token}"}
    # ... existing identity_mode matrix, byte-identical ...
```
```python
# services/mcp-proxy/credentials.py — T012
@dataclass
class ServerConnection:
    ...                                   # unchanged fields
    external_auth_mode: str = "static"    # "static" | "oauth"; default keeps Phase-2 behavior
# read_server_secret: external_auth_mode = connection.get("external_auth_mode") or "static"
```
> **Note on `connection_server_id`:** `resolve_headers` needs the `server_id` to key the token pull. `ServerConnection` does not carry it today; T012 adds `server_id: str` to `ServerConnection` (populated by `read_server_secret` from the Secret name / a new `connection.server_id` key) so `resolve_headers` can call `get_oauth_access_token(server_id, user_sub)` without a new argument threading through every caller. Listed under T012's files.

---

## Tasks

**Baseline (verify-then-bump — never reuse a claimed tag/number; re-verify at build time per `quickstart.md`):** registry-api `0.2.228`→`0.2.229`; mcp-proxy `0.1.3`→`0.1.4`; studio `0.1.162`→`0.1.163`. Alembic head `0072`→ add `0073`,`0074`. e2e ceiling `suite-85`→ add `86`,`87`. Bump the tag in **both** `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml` (+ `values-eks.yaml`/`deploy-eks.sh`) in the same commit.

### Phase overview

| Phase | Name | Tasks | Delivers / Proves |
|---|---|---|---|
| P1 | Setup & baseline verification `[P]` | T001 | Tags/head/suite ceiling re-verified; no code. |
| P2 | WS-1 CredentialProvider seam | T002–T004 | `CredentialProvider`+`FernetPgProvider`; migration `0073`; two MCP call sites rewired; **pg-fernet byte-identical**. |
| P3 | WS-1 AWS Secrets Manager backend | T005 | `AwsSecretsManagerProvider` (IRSA, opt-in); `boto3`; chart IRSA annotation. |
| **CP1** | **Checkpoint — Credential provider seam** | CP1a–CP1c | **Deferred.** pg-fernet round-trip = byte-identical creds; legacy dual-read; ASM optional path. |
| P4 | WS-2 data model + OAuth mechanics | T006–T007 | Migration `0074`; `MCPOAuthGrant`; `mcp_oauth.py` (discovery/DCR/PKCE/exchange/refresh). |
| P5 | WS-2 registry-api dance endpoints | T008–T009 | authorize/callback/status/disconnect; discover-as-authorizing-user. |
| P6 | WS-2 internal token endpoint | T010 | `/internal/mcp/oauth/access-token` (TokenReview'd) + registry-api `tokenreviews:create`. |
| **CP2** | **Checkpoint — Authorize flow + refresh** | CP2a–CP2c | **Deferred.** authorize→callback→`authorized`; access-token endpoint refreshes+rotates. |
| P7 | WS-2 proxy token-read | T011–T012 | `oauth_tokens.py`; `resolve_headers` OAuth branch; `ServerConnection.external_auth_mode`. |
| P8 | WS-2 proxy plumbing + chart | T013–T014 | proxy `tools/call`/`discover` arms + `user_sub`; projected token; RBAC; NetworkPolicy reuse. |
| **CP3** | **Checkpoint — End-to-end OAuth tool call** | CP3a–CP3c | **Deferred.** authorized user's tool call presents a fresh bearer; expired/absent → `200 is_error`. |
| P9 | WS-2 Studio | T015–T016 | API methods; OAuth panel; register toggle; Vitest; Playwright authorize journey. |
| **CP4** | **Checkpoint — Studio authorize journey** | CP4a–CP4b | **Deferred.** register→authorize→Connected badge; save→reload→assert. |
| P10 | Testing, regression, docs, tags | T017–T020 | suite-86/87; tag bumps; docs/gap-ledger; regression sweep. |
| **CP5** | **Checkpoint — Full Phase-4 e2e + regression** | CP5a–CP5c | **Deferred.** 86+87 green; 84+85 + AuthConfig suites green; Vitest+Playwright green. |

Detailed per-task field blocks (`**Files** / **Interface contract** / **Dependencies** / **Acceptance** / **Test cases**) live in `tasks.md`, which also carries the phased checklist and the CP checkpoint scripts. A condensed view:

- **T001** `[P]` — verify baseline (tags/head/suites); no code. *(quickstart runbook)*
- **T002** — `credential_provider.py`: `CredentialRef`+`CredentialProvider`+`CredentialNotFound`+`FernetPgProvider`+`get_provider()`(pg-fernet only) + config knobs. `T-S86-001..003`.
- **T003** — `models.py` `CredentialBlob`+`AuthConfig.credential_ref`; migration `0073` (+backfill). `T-S86-004`.
- **T004** — rewire `mcp_secrets.materialize_server_secret` + `auth_configs.py` (create/update/secret-ref) through `get_provider()` (dual-read legacy branch). `T-S86-005..007`; regression `suite-84`.
- **T005** — `AwsSecretsManagerProvider` + `get_provider()` backend switch + `boto3` + chart IRSA SA annotation (opt-in). `T-S86-008` (skipped unless `CREDENTIAL_PROVIDER_BACKEND=aws-sm`).
- **CP1** — deploy registry-api; prove byte-identity + dual-read.
- **T006** — migration `0074`; `models.py` `MCPServer.external_auth_mode`/`oauth_client_ref` + `MCPOAuthGrant`; `schemas.py` `external_auth_mode` + validator; `mcp_servers.py` validator. `T-S87-001..002`.
- **T007** — `mcp_oauth.py` mechanics + minimal Pydantic models + Fernet `state`. `T-S87-003` (unit-ish via stub AS).
- **T008** — `routers/mcp_oauth.py` (authorize/callback/status/disconnect); register in `main.py`; `mcp_servers.py` DELETE revoke. `T-S87-004..007`.
- **T009** — `mcp_secrets` writes `external_auth_mode`; `mcp_proxy_client.discover_server(user_sub)`; `mcp_health` most-recently-authorized. `T-S87-008`.
- **T010** — `internal_mcp.py` `oauth/access-token` (TokenReview+subject-pin+rotate); registry-api `tokenreviews:create` RBAC; config `MCP_PROXY_SA_AUDIENCE`. `T-S87-009..011`.
- **CP2** — deploy registry-api; prove authorize→`authorized` + refresh+rotation.
- **T011** — proxy `oauth_tokens.py` (cache+pull+exceptions+invalidate) + config. `T-S87-012`.
- **T012** — proxy `credentials.ServerConnection.external_auth_mode`+`server_id`; `identity.resolve_headers` OAuth branch. `T-S87-013..014`.
- **T013** — proxy `main.py` OAuth arms + `user_sub`; `schemas.McpDiscoverRequest.user_sub`; evict-retry invalidate. `T-S87-015`.
- **T014** — chart: proxy projected `agentshield-registry-api` token + OAuth env; values/values-eks wiring. *(deploy)*
- **CP3** — deploy proxy+registry-api; prove end-to-end tool call + fail-closed.
- **T015** — Studio API methods + detail OAuth panel + callback landing + register toggle + Vitest. `Vitest`.
- **T016** — `studio/e2e/mcp-servers.spec.ts` authorize journey + save→reload→assert. `Playwright`.
- **CP4** — deploy studio; prove the authorize journey.
- **T017** — `suite-86-credential-provider.sh` + register. `T-S86-001..008`.
- **T018** — `suite-87-mcp-oauth.sh` + `fixtures/oauth_mcp_server.py` + register. `T-S87-001..015`.
- **T019** — tag bumps (deploy-cpe2e/values/values-eks/deploy-eks) + design/decision doc status updates.
- **T020** — regression sweep (84/85 + AuthConfig + Vitest + Playwright) + gap-ledger finalization. **Blast radius (mandatory mapping):** the credential read/write path (every AuthConfig + every MCP server), `resolve_headers` (all identity modes), the proxy→registry-api hop, registry-api RBAC. See `research.md` Part D.
- **CP5** — full Phase-4 e2e + regression.

---

## Complexity Tracking

| Item | Why it's here (not a shortcut) |
|---|---|
| Value relocation column→`credential_blobs` in WS-1 (T003/T004) | A generic KV is needed so OAuth refresh tokens have a dev home without AWS; a per-path branch inside the provider would be the priority-fallthrough the constitution forbids. Behavior (composed headers, crypto) is byte-preserved; only ciphertext location moves. |
| The token-read endpoint TokenReviews its caller (T010) — unlike the other two internal endpoints | It returns a bearer access token; leaving it unauthenticated would be a credential-harvest confused-deputy. TokenReview reuses the platform's one real service-identity primitive rather than inventing a scheme (`research.md` C4). |
| Refresh-with-rotation is a single writer in registry-api (T010) | OAuth 2.1 rotates the refresh token on every refresh; a multi-writer (proxy replicas) would race and orphan the token. Centralizing in registry-api (which owns the store) is the correct concurrency model, and keeps the proxy read-only (`research.md` C3). |
| Hand-rolled OAuth mechanics instead of the mcp SDK's `OAuthClientProvider` (T007) | The SDK provider can't span the authorize→browser→callback two-request boundary or a multi-replica registry-api; and it would pull the whole mcp SDK into registry-api (which has no such dep). The mechanics are a few well-specified JSON round-trips (`research.md` C6). |

No other deviations. WS-3 is explicitly out (Scope Check).

---

## Execution Notes

- **Order is load-bearing:** WS-1 (P2-P3, CP1) MUST land and prove byte-identity before WS-2 touches the OAuth store. Within WS-2, prove the registry-api side (CP2) and the proxy side (CP3) before Studio (P9).
- **Deferred deploys:** as in Phase 2, CP scripts are **written this run, executed by the user when ready to deploy** — no build/deploy is triggered by the planning/implementation agent.
- **Dev needs no AWS and no real OAuth server:** `CREDENTIAL_PROVIDER_BACKEND=pg-fernet` (default) and the `fixtures/oauth_mcp_server.py` stub (a minimal AS + a bearer-gated MCP server) exercise the whole flow locally (`quickstart.md`).
- **`boto3` import is lazy:** `AwsSecretsManagerProvider` imports `boto3` inside `__init__`/first use, so a dev/CI checkout on `pg-fernet` never needs the library installed.
- **TypeScript/Python gates:** `cd studio && npm run typecheck && npm run test`; `python3 -c "import ast; ast.parse(...)"` on each new .py + `sqlalchemy.orm.configure_mappers()` after the model additions (T003/T006).

---

## Gap Ledger

| Gap | Tag | Note |
|---|---|---|
| `resources`/`prompts` passthrough (WS-3 / OQ-02) | **deferred (intentional)** — own plan | A distinct MCP primitive subsystem; recommended as Phase 5 (`research.md` Part E). |
| Drop `auth_configs.credentials_encrypted` after dual-read cutover | **deferred (intentional)** | A later migration once all deployments are on a `credential_ref`; column retained this phase for dual-read (`research.md` C11). |
| Proxy-side external-store read for **static** creds (drop per-server K8s Secret materialization for `aws-sm`) | **deferred (intentional)** | Not needed by WS-2 (OAuth tokens come from registry-api, not the Secret). The containment win in Decision 31 §5; a follow-up. |
| `LLMProvider` / `applications` credentials moved behind the provider | **not-yet-wired (debt)** | WS-1 rewires only the two MCP call sites (design §4); other classes migrate later. |
| DCR fallback to a pre-registered `client_id`/`client_secret` via `AuthConfig` | **not-yet-wired (debt), low-impact** | Implemented as a path but only lightly tested against the stub; real pre-registered-client servers unverified. |
| OAuth `state` replay within its TTL | **deferred (intentional), low-impact** | A nonce is embedded but not checked against a store; the upstream one-time code + PKCE defeat a replayed state (`research.md` C5). |
| Per-scope selection UI in Studio | **deferred (intentional)** | Scopes come from server metadata; a scope-picker is a future enhancement (`contracts/studio-mcp-oauth-phase4.md` §5). |
| `VaultProvider` | **deferred (intentional)** | First-class future backend; ASM first (Decision 31 §8, `research.md` C2). |
| Decision 29 on-behalf-of exchange | **not-yet-wired (debt), blocked externally** | Stays a STUB (`identity.py`), blocked on `identity-propagation-architecture.md`; WS-1 makes it cheaper later but does not build it (`research.md` C10). |

# Data Model — MCP as a Tool Source, Phase 4

**Scope:** WS-1 CredentialProvider seam + WS-2 MCP OAuth 2.1.
**Companion artifacts (same dir):** `plan.md`, `research.md`, `tasks.md`, `quickstart.md`, `contracts/`.

**Headline:** Phase 4 adds **two** sequential migrations — `0073` (WS-1, one new table + one column) and `0074` (WS-2, two columns + one table). Both are additive, guarded, idempotent, and data-preserving. Alembic head today is `0072`. No column is dropped in Phase 4 (the legacy `auth_configs.credentials_encrypted` drop is deferred — §6).

---

## 1. WS-1 — `credential_blobs` + `auth_configs.credential_ref` (migration `0073`)

The `CredentialRef` (`research.md` C1) is a pointer `<scheme>://<path>`. The **pointer** lives in Postgres; the **value** lives behind the provider. For the `pg-fernet` (dev/default) backend the value lives in the new generic KV table `credential_blobs`; for `aws-sm` it lives in AWS Secrets Manager (nothing in Postgres but the ref).

### 1a. `CredentialRef` (not a table — a value object)
```python
# services/registry-api/credential_provider.py
@dataclass(frozen=True)
class CredentialRef:
    scheme: str          # "pg-fernet" | "aws-sm"   (extensible: "vault", "k8s")
    path: str            # backend-agnostic locator, e.g. "credential-blobs/auth-configs/{id}"
                         #                              or "mcp-oauth-refresh/{server_id}/{user_sub}"
    def __str__(self) -> str: return f"{self.scheme}://{self.path}"
    @classmethod
    def parse(cls, s: str) -> "CredentialRef":
        scheme, _, path = s.partition("://"); return cls(scheme=scheme, path=path)
```

Ref shapes used in Phase 4:

| Ref | Credential class | Backend resolution |
|---|---|---|
| `pg-fernet://credential-blobs/auth-configs/{id}` | Static `AuthConfig` creds (WS-1) | `credential_blobs` row `path='auth-configs/{id}'` |
| `pg-fernet://credential-blobs/mcp-oauth-refresh/{server_id}/{user_sub}` | OAuth refresh token (WS-2, dev) | `credential_blobs` row |
| `pg-fernet://credential-blobs/mcp-oauth-client/{server_id}` | DCR client registration (WS-2, dev) | `credential_blobs` row |
| `aws-sm://agentshield/auth-configs/{id}` | Static creds (WS-1, prod) | ASM secret |
| `aws-sm://agentshield/mcp-oauth-refresh/{server_id}/{user_sub}` | OAuth refresh token (WS-2, prod) | ASM secret |
| `aws-sm://agentshield/mcp-oauth-client/{server_id}` | DCR client (WS-2, prod) | ASM secret |

Note the `pg-fernet` path is always prefixed `credential-blobs/` (the table name) so the provider is a pure KV over `credential_blobs.path` with no per-path special-casing; the `aws-sm` path omits it (the ASM prefix stands in).

### 1b. `credential_blobs` table (new — `models.py::CredentialBlob`)

| Column | Type | Notes |
|---|---|---|
| `path` | `String(512)` PK | The `CredentialRef.path` (e.g. `auth-configs/{id}`). Unique. |
| `value_encrypted` | `Text NOT NULL` | Fernet token of the JSON value (`crypto.encrypt_json`). |
| `created_at` | `timestamptz NOT NULL default now()` | |
| `updated_at` | `timestamptz NOT NULL default now()` | `rotate`/`put` bumps it. |

### 1c. `auth_configs.credential_ref` (new column)

| Column | Type | Notes |
|---|---|---|
| `credential_ref` | `String(512) NULL` | The pointer (e.g. `pg-fernet://credential-blobs/auth-configs/{id}`). Null on a legacy row → the call site reads the legacy `credentials_encrypted` column (dual-read, `research.md` C11). `credentials_encrypted` is **retained** this phase. |

### 1d. Migration `0073` DDL (idempotent, data-preserving)
```python
# services/registry-api/alembic/versions/0073_credential_blobs_and_credential_ref.py
revision = "0073"; down_revision = "0072"

def upgrade():
    op.execute("""
      CREATE TABLE IF NOT EXISTS credential_blobs (
        path             VARCHAR(512) PRIMARY KEY,
        value_encrypted  TEXT NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
      );
    """)
    op.execute("ALTER TABLE auth_configs ADD COLUMN IF NOT EXISTS credential_ref VARCHAR(512);")
    # Backfill: copy each existing Fernet blob into credential_blobs + set the ref.
    # Guarded (WHERE NOT EXISTS) so re-running is a no-op; the ciphertext is copied
    # verbatim (same AGENTSHIELD_ENCRYPTION_KEY), so the value is byte-preserved.
    op.execute("""
      INSERT INTO credential_blobs (path, value_encrypted)
      SELECT 'auth-configs/' || id::text, credentials_encrypted
      FROM auth_configs
      WHERE credentials_encrypted IS NOT NULL
        AND NOT EXISTS (
          SELECT 1 FROM credential_blobs cb
          WHERE cb.path = 'auth-configs/' || auth_configs.id::text
        );
    """)
    op.execute("""
      UPDATE auth_configs
      SET credential_ref = 'pg-fernet://credential-blobs/auth-configs/' || id::text
      WHERE credentials_encrypted IS NOT NULL AND credential_ref IS NULL;
    """)

def downgrade():
    # Preserve data on the way down: the column drop is safe (values remain in the
    # legacy credentials_encrypted column, untouched); the table is dropped last.
    op.execute("ALTER TABLE auth_configs DROP COLUMN IF EXISTS credential_ref;")
    op.execute("DROP TABLE IF EXISTS credential_blobs;")
```

**Not touched:** `credentials_encrypted` stays (dual-read window). `LLMProvider.credentials_encrypted` and `applications.secret_encrypted` are **out of scope** — WS-1 rewires only the two MCP call sites (design §4); other credential classes migrate in later phases (ledgered).

---

## 2. WS-2 — `mcp_servers` OAuth columns + `mcp_oauth_grants` (migration `0074`)

### 2a. `mcp_servers` new columns

| Column | Type | Notes |
|---|---|---|
| `external_auth_mode` | `String(16) NOT NULL default 'static'` | `'static' | 'oauth'`. Only meaningful when `is_external=true` (a `MCPServerCreate/Update` validator rejects `external_auth_mode='oauth'` with `is_external=false`). CHECK constraint `ck_mcp_servers_external_auth_mode`. |
| `oauth_client_ref` | `String(512) NULL` | `CredentialRef` to the DCR-registered client (`{client_id, client_secret?}`), per-server. Null until the first authorize registers a client. |

Surfaced to the proxy inside the per-server Secret `connection` JSON (§3) so the proxy learns "this server is OAuth" with no DB.

### 2b. `mcp_oauth_grants` table (new — `models.py::MCPOAuthGrant`)

Per-`(server, user)` grant record (`research.md` C8). Holds the **pointer** to the refresh token, never the token.

| Column | Type | Notes |
|---|---|---|
| `server_id` | `UUID` FK→`mcp_servers.id` | PK part 1. |
| `user_sub` | `String(255)` | PK part 2. The Keycloak subject that consented. |
| `credential_ref` | `String(512) NULL` | Pointer to the refresh token (`…/mcp-oauth-refresh/{server_id}/{user_sub}`). Null in `needs_auth`. |
| `status` | `String(16) NOT NULL default 'needs_auth'` | `needs_auth | authorized | error` (state machine §4). CHECK `ck_mcp_oauth_grants_status`. |
| `scopes` | `Text NULL` | Space-delimited granted scopes (echoed from the token response). |
| `token_expires_at` | `timestamptz NULL` | Access-token expiry last observed (advisory; the proxy caches by this). |
| `last_error` | `Text NULL` | Last refresh/exchange failure reason (surfaces the "re-authorize" state). |
| `created_at` / `updated_at` | `timestamptz NOT NULL default now()` | `updated_at` = last successful refresh / authorize (drives the health-loop "most-recently-authorized" pick, C9). |

Primary key `(server_id, user_sub)`; index on `server_id` for the health-loop lookup. `ON DELETE CASCADE` from `mcp_servers` so deleting a server cleans up grants (the refresh tokens behind the refs are deleted by the router, not the FK — §5).

### 2c. Migration `0074` DDL (idempotent)
```python
# services/registry-api/alembic/versions/0074_mcp_oauth_grants.py
revision = "0074"; down_revision = "0073"

def upgrade():
    op.execute("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS external_auth_mode VARCHAR(16) NOT NULL DEFAULT 'static';")
    op.execute("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_client_ref VARCHAR(512);")
    op.execute("""
      DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_mcp_servers_external_auth_mode') THEN
          ALTER TABLE mcp_servers ADD CONSTRAINT ck_mcp_servers_external_auth_mode
            CHECK (external_auth_mode IN ('static','oauth'));
        END IF;
      END $$;
    """)
    op.execute("""
      CREATE TABLE IF NOT EXISTS mcp_oauth_grants (
        server_id        UUID NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
        user_sub         VARCHAR(255) NOT NULL,
        credential_ref   VARCHAR(512),
        status           VARCHAR(16) NOT NULL DEFAULT 'needs_auth',
        scopes           TEXT,
        token_expires_at TIMESTAMPTZ,
        last_error       TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (server_id, user_sub),
        CONSTRAINT ck_mcp_oauth_grants_status CHECK (status IN ('needs_auth','authorized','error'))
      );
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_mcp_oauth_grants_server ON mcp_oauth_grants(server_id);")

def downgrade():
    op.execute("DROP TABLE IF EXISTS mcp_oauth_grants;")
    op.execute("ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS ck_mcp_servers_external_auth_mode;")
    op.execute("ALTER TABLE mcp_servers DROP COLUMN IF EXISTS oauth_client_ref;")
    op.execute("ALTER TABLE mcp_servers DROP COLUMN IF EXISTS external_auth_mode;")
```

---

## 3. Per-server Secret `connection` blob — one new key (no DDL)

`mcp_secrets.materialize_server_secret` (mcp_secrets.py:116-130) already writes `identity_mode` + `identity_audience`. Phase 4 adds **one** key so the proxy learns a server is OAuth without a DB:

```jsonc
// BEFORE (Phase 2)                          // AFTER (Phase 4)
{ "server_url": "...", "transport": "...",   { "server_url": "...", "transport": "...",
  "transport_config": {...},                   "transport_config": {...},
  "is_external": true,                         "is_external": true,
  "owner_team": "...",                         "owner_team": "...",
  "identity_mode": "none",                     "identity_mode": "none",
  "identity_audience": null }                  "identity_audience": null,
                                               "external_auth_mode": "oauth" }   // NEW
```

A Secret materialized before Phase 4 lacks the key → `credentials.read_server_secret` defaults `external_auth_mode="static"` → **byte-identical Phase-2 behavior** (the OAuth branch in `resolve_headers` is never taken). For an OAuth server the `auth_headers` value stays `{}` (there is no static AuthConfig); the bearer is fetched per-call from registry-api (§4 of `contracts/mcp-proxy-oauth-phase4.md`).

`ServerConnection` (`credentials.py:34-48`) gains one field:
```python
@dataclass
class ServerConnection:
    ...                                        # unchanged Phase-1/2 fields
    identity_mode: str = "none"
    identity_audience: str | None = None
    external_auth_mode: str = "static"         # NEW — "static" | "oauth"; default keeps P2 behavior
    server_id: str = ""                         # NEW — set by read_server_secret from its server_id arg
                                                #       (already known; no new Secret key) so resolve_headers
                                                #       can key the OAuth token pull get_oauth_access_token(server_id, user_sub)
```

---

## 4. Token lifecycle / state machine (`mcp_oauth_grants.status`)

```
                    POST /mcp-servers/{id}/oauth/authorize
   (no grant row)  ─────────────────────────────────────────►  needs_auth
        │                                                          │
        │  (row created lazily on first authorize, status=needs_auth)
        │                                                          │
        │              GET /oauth/callback  (code→token OK,        │
        │              refresh token stored via provider)         ▼
        │        ┌───────────────────────────────────────►  authorized
        │        │                                             │   ▲
        │        │  proxy pulls access-token; registry-api     │   │ next authorize / next
        │        │  refresh exchange succeeds (rotates RT)     │   │ successful refresh
        │        │        (updated_at bumped) ─────────────────┘   │  (error → authorized)
        │        │                                                 │
        │        │  refresh exchange fails (RT revoked/expired,    │
        │        │  invalid_grant) OR callback token exchange fails ▼
        └────────┴──────────────────────────────────────────►  error
                                                                   │
                        re-authorize (user consents again) ────────┘  → authorized
```

- **`needs_auth`** — a grant is intended (row exists, or absent) but no usable refresh token. The proxy fails closed: `200 is_error` "server requires (re-)authorization." Studio shows a **Needs authorization** badge + an **Authorize** button.
- **`authorized`** — a refresh token is stored; the internal access-token endpoint can mint a fresh access token on demand. Studio shows **Connected**.
- **`error`** — the last refresh/exchange failed (e.g. `invalid_grant` = revoked/expired refresh token). `last_error` set. Proxy fails closed (same body as `needs_auth`). Studio shows **Needs authorization** (with the error reason) + Authorize.
- **Transient "refreshing"** is *not* a persisted status — it exists only inside the internal endpoint's exchange call (holds no lock beyond the request; a concurrent second pull either waits on the row `SELECT … FOR UPDATE` or re-reads the just-rotated token). Modeled in code, not in the enum.

`MCPServer.status` is **unchanged** (`connected|disconnected|error`); OAuth authorization state is the *grant's* concern, surfaced via `GET /oauth/status`, not `MCPServer.status`.

---

## 5. Delete / revoke lifecycle

- **Server DELETE** (`routers/mcp_servers.py::delete_mcp_server`): after the existing checks, for an `external_auth_mode='oauth'` server it (a) `provider.delete` each grant's refresh-token ref, (b) `provider.delete` the `oauth_client_ref`, then the `ON DELETE CASCADE` removes `mcp_oauth_grants`. `delete_server_secret` still runs.
- **Disconnect** (`DELETE /mcp-servers/{id}/oauth`, one user): revoke at the AS `revocation_endpoint` if advertised (best-effort), `provider.delete` that user's refresh-token ref, set the grant `status='needs_auth'` (or delete the row). Idempotent.

---

## 6. What Phase 4 does NOT change (guardrails)

- **No column dropped.** `auth_configs.credentials_encrypted` stays for the dual-read window; its drop is a **later** migration (`research.md` C11, gap ledger).
- **The proxy still holds no DB and no master key.** WS-1 leaves the proxy reading the materialized per-server Secret for `pg-fernet`/`k8s` backends (design §4, unchanged). The proxy-side external read (dropping materialization for `aws-sm`) is a **deferred enhancement** — not needed by WS-2, which pulls OAuth tokens from registry-api.
- **`identity_mode` matrix untouched.** `none/service_identity/on_behalf_of` behave exactly as Phase 2; the OBO stub stays a stub (`research.md` C10).
- **`MCPServer.status` enum unchanged.** No `needs_auth` value added to it (§4).
- **`LLMProvider` / `applications` credentials unchanged.** Out of WS-1 scope.

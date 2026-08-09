"""
AgentShield Registry API — application settings.

All values are read from environment variables (case-insensitive).
A `.env` file in the working directory is loaded automatically when
present, but environment variables always take precedence.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ------------------------------------------------------------------ #
    # Database                                                             #
    # ------------------------------------------------------------------ #
    # Primary async connection string — goes through PgBouncer.
    # Format: postgresql+asyncpg://agentshield_user:<pw>@pgbouncer.agentshield-platform:5432/agentshield
    database_url: str

    # Direct (bypass PgBouncer) connection string — required for
    # LISTEN/NOTIFY and Alembic autogenerate.
    # Format: postgresql+psycopg://agentshield_user:<pw>@postgres-primary:5432/agentshield
    direct_database_url: str

    # ------------------------------------------------------------------ #
    # Keycloak / Auth                                                      #
    # ------------------------------------------------------------------ #
    keycloak_url: str  # e.g. http://keycloak:80
    keycloak_realm: str = "agentshield"
    keycloak_client_id: str = "registry-api"
    keycloak_client_secret: str = ""

    # ------------------------------------------------------------------ #
    # Langfuse observability                                               #
    # ------------------------------------------------------------------ #
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://langfuse-web:3000"

    # ------------------------------------------------------------------ #
    # Notifications                                                        #
    # ------------------------------------------------------------------ #
    slack_webhook_url: str = ""

    # ------------------------------------------------------------------ #
    # Kubernetes                                                           #
    # ------------------------------------------------------------------ #
    # Namespace prefix used when creating agent namespaces.
    # Resulting namespace: "{kubernetes_namespace_prefix}-{team}"
    kubernetes_namespace_prefix: str = "agents"

    # ------------------------------------------------------------------ #
    # Encryption                                                           #
    # ------------------------------------------------------------------ #
    # Fernet key for encrypting LLM provider credentials at rest.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    agentshield_encryption_key: str = ""

    # ------------------------------------------------------------------ #
    # Credential provider (Decision 31 — pluggable credential store)      #
    # ------------------------------------------------------------------ #
    # Selects the CredentialProvider backend (credential_provider.get_provider).
    #   "pg-fernet" (default) — value stays in Postgres, Fernet-encrypted with
    #                           AGENTSHIELD_ENCRYPTION_KEY (behavior-preserving).
    #   "aws-sm"              — AWS Secrets Manager via IRSA (opt-in, lands in P3).
    credential_provider_backend: str = "pg-fernet"
    # Prefix for AWS Secrets Manager secret ids (only used by the "aws-sm" backend).
    aws_secrets_manager_prefix: str = ""
    # AWS region for the "aws-sm" backend (only used by that backend).
    aws_region: str = ""

    # ------------------------------------------------------------------ #
    # MCP Proxy (MCP-as-tool-source)                                       #
    # ------------------------------------------------------------------ #
    # In-cluster URL of the MCP Proxy service. registry-api calls its
    # POST /internal/discover on MCP-server register / sync (mcp_proxy_client).
    mcp_proxy_url: str = "http://agentshield-mcp-proxy.agentshield-platform:8080"
    # Path to the projected K8s ServiceAccount token (audience
    # `agentshield-mcp-proxy`) registry-api presents as a Bearer to the proxy.
    # The token ROTATES — mcp_proxy_client re-reads it from disk on every call.
    mcp_proxy_sa_token_path: str = "/var/run/secrets/mcp-proxy-token/mcp-proxy-token"

    # ------------------------------------------------------------------ #
    # MCP health-check loop (Phase 2, WS-A / FR-MCP-22)                    #
    # ------------------------------------------------------------------ #
    # registry-api owns a background sweep (mcp_health.py) that periodically
    # probes every registered MCP server via the proxy's POST /internal/health
    # and folds the verdict into mcp_servers.status / health_detail. Single-
    # flighted across replicas by a Postgres advisory lock (mirrors the
    # scheduler's HA primitive); the proxy never writes the DB.
    mcp_health_check_enabled: bool = True
    # Seconds between sweeps (the loop's asyncio.sleep interval).
    mcp_health_check_interval_seconds: int = 60
    # Consecutive failed probes before a server flips status -> 'error'.
    mcp_health_failure_threshold: int = 3
    # Max concurrent per-server probes within one sweep (asyncio.Semaphore).
    mcp_health_check_concurrency: int = 8
    # Cap on how many sweeps a hard-down server is skipped (exponential backoff).
    mcp_health_max_backoff_cycles: int = 10

    # ------------------------------------------------------------------ #
    # platform-admin bootstrap (Decision 40, phase R0)                     #
    # ------------------------------------------------------------------ #
    # registry-api code — not the Helm chart — creates the sole auto-created
    # user. Disable ONLY for a deploy that provisions the admin some other way;
    # with it off, a fresh install has no admin and no Admin menu.
    platform_admin_bootstrap_enabled: bool = True
    # From the pre-existing keycloak-user-passwords Secret, key `platform-admin`.
    # Empty -> bootstrap fails loudly (/ready red) rather than minting an
    # unknown-password admin.
    platform_admin_password: str = ""
    # Retry cadence while Keycloak is not yet up. /ready stays red until success.
    platform_admin_bootstrap_retry_seconds: int = 30

    # ------------------------------------------------------------------ #
    # MCP list_changed re-sync (Phase 2, WS-B / FR-MCP-07)                 #
    # ------------------------------------------------------------------ #
    # When an upstream server announces notifications/tools/list_changed, the
    # proxy's subscription manager POSTs /api/v1/internal/mcp/list-changed and
    # registry-api re-runs discovery. This is the coalesce window: a second
    # re-sync for the same server within this many seconds is deduped (returns
    # coalesced=true without re-discovering) — the cross-replica / burst guard
    # (mcp_discovery._last_resync / _resync_locks).
    mcp_list_changed_min_resync_interval_seconds: int = 10

    # ------------------------------------------------------------------ #
    # MCP OAuth 2.1 for external servers (Phase 4 WS-2)                    #
    # ------------------------------------------------------------------ #
    # The redirect URI registered with every upstream authorization server and
    # sent as `redirect_uri` in the authorize + token-exchange requests. This is
    # the public URL of the callback endpoint
    # (GET /api/v1/mcp-servers/oauth/callback). Empty → the authorize endpoint
    # 409s `oauth_not_configured` (OAuth cannot run without a registered redirect).
    mcp_oauth_callback_url: str = ""
    # Base URL of Studio (the SPA) the callback 302-redirects the browser back to,
    # e.g. "https://studio.example.com". The callback lands on
    # `{studio_base_url}/mcp-servers/{id}?oauth=connected|denied|invalid_state|error`.
    # NEVER carries the token/code — only the `?oauth=` outcome flag.
    studio_base_url: str = ""
    # TTL (seconds) of the Fernet-encrypted `state` that carries {server_id,
    # user_sub, code_verifier} across the upstream redirect. Kept short — the value
    # is dead ~60s after the redirect. Passed to mcp_oauth.make_state(..., ttl).
    mcp_oauth_state_ttl_seconds: int = 600
    # ── Internal OAuth access-token endpoint auth (Phase 4 WS-2, T010) ──
    # POST /api/v1/internal/mcp/oauth/access-token is the ONE internal MCP endpoint
    # that authenticates its caller — it hands out a live OAuth *access token* (a
    # bearer), so it TokenReviews the proxy's projected SA token and pins the subject.
    #
    # `mcp_proxy_sa_audience` — the audience the mcp-proxy's projected SA token carries
    #   when it calls registry-api (the RECIPIENT identity = registry-api, mirroring how
    #   registry-api's token to the proxy carries audience `agentshield-mcp-proxy`). The
    #   endpoint runs TokenReview with spec.audiences=[this] and requires it in
    #   status.audiences — a token minted for a different audience fails (401). This is
    #   the value the proxy's projected-token volume (T014) is minted with; the proxy
    #   verifies the symmetric direction with MCP_PROXY_AUDIENCE=agentshield-mcp-proxy.
    mcp_proxy_sa_audience: str = "agentshield-registry-api"
    # `mcp_proxy_sa_subject` — the ONLY caller allowed a bearer from that endpoint. The
    #   TokenReview'd subject (status.user.username) MUST equal this exact SA subject,
    #   else 403. Derived as system:serviceaccount:{release-namespace}:{release}-mcp-proxy
    #   (default release `agentshield` in ns `agentshield-platform`). This is the mirror
    #   of the proxy's REGISTRY_API_SA_SUBJECT pin.
    mcp_proxy_sa_subject: str = (
        "system:serviceaccount:agentshield-platform:agentshield-mcp-proxy"
    )

    # ------------------------------------------------------------------ #
    # Server                                                               #
    # ------------------------------------------------------------------ #
    port: int = 8000
    log_level: str = "INFO"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Module-level singleton — import this everywhere.
settings = Settings()

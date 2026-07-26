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

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

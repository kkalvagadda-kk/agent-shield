"""0074 — MCP OAuth 2.1 for external servers: mcp_servers OAuth columns + mcp_oauth_grants.

WS-2 (Phase 4). Lets an external MCP server that advertises OAuth 2.1 be authorized by a
user through the authorization-code + PKCE dance (run in registry-api); the resulting
refresh token is stored per ``(server, user)`` behind the CredentialProvider (WS-1), and
this migration adds its durable anchors:

  * ``mcp_servers.external_auth_mode`` (VARCHAR(16) NOT NULL DEFAULT 'static';
    'static' | 'oauth') — only meaningful when ``is_external=true`` (a
    MCPServerCreate/Update validator + the router reject ``'oauth'`` with
    ``is_external=false``). Guarded by CHECK ``ck_mcp_servers_external_auth_mode``.
  * ``mcp_servers.oauth_client_ref`` (VARCHAR(512) NULL) — the CredentialRef pointer to
    the DCR-registered client credentials ``{client_id, client_secret?}``, per server.
    Null until the first authorize registers a client.
  * ``mcp_oauth_grants`` — the per-``(server_id, user_sub)`` grant record; holds the
    POINTER to the refresh token (``credential_ref``), never the token itself.

Idempotent + data-preserving:
  * every ``ADD COLUMN`` is ``IF NOT EXISTS``; the CHECK is added inside a guarded
    ``DO $$`` block (added only if absent); ``CREATE TABLE`` / ``CREATE INDEX`` are
    ``IF NOT EXISTS`` — safe to re-run against a partially-applied DB.
  * Downgrade drops the table then the constraint then the columns (additive only, so
    no data is destroyed beyond the OAuth grants themselves).

Data model: docs/plan/mcp-tool-source-phase4/data-model.md §2c.
"""
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. mcp_servers OAuth columns (additive, guarded).
    op.execute(
        "ALTER TABLE mcp_servers "
        "ADD COLUMN IF NOT EXISTS external_auth_mode VARCHAR(16) NOT NULL DEFAULT 'static';"
    )
    op.execute(
        "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_client_ref VARCHAR(512);"
    )

    # 2. CHECK constraint on external_auth_mode (added only if not already present).
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_mcp_servers_external_auth_mode'
          ) THEN
            ALTER TABLE mcp_servers ADD CONSTRAINT ck_mcp_servers_external_auth_mode
              CHECK (external_auth_mode IN ('static', 'oauth'));
          END IF;
        END $$;
        """
    )

    # 3. Per-(server, user) grant record. Holds the ref to the refresh token, not the token.
    op.execute(
        """
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
          CONSTRAINT ck_mcp_oauth_grants_status
            CHECK (status IN ('needs_auth', 'authorized', 'error'))
        );
        """
    )
    # Index for the health-loop "most-recently-authorized user" lookup (C9).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mcp_oauth_grants_server "
        "ON mcp_oauth_grants(server_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_oauth_grants;")
    op.execute(
        "ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS ck_mcp_servers_external_auth_mode;"
    )
    op.execute("ALTER TABLE mcp_servers DROP COLUMN IF EXISTS oauth_client_ref;")
    op.execute("ALTER TABLE mcp_servers DROP COLUMN IF EXISTS external_auth_mode;")

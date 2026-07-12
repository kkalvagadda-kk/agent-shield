"""Add credentials_encrypted to auth_configs.

Tool credential values (e.g. the Serper API key) were stored ONLY in a K8s
Secret referenced by auth_configs.k8s_secret_ref — never in Postgres. So a
pg_dump backup does NOT capture them, and on a cluster wipe the K8s secret is
lost while the DB restore brings back only the (now-dangling) reference. This
column stores the credentials Fernet-encrypted in the DB (source of truth, like
llm_providers.credentials_encrypted), so backups capture them and they can be
re-materialized into the K8s secret after a restore. Nullable for legacy rows.
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'auth_configs'
                  AND column_name = 'credentials_encrypted'
            ) THEN
                ALTER TABLE auth_configs
                    ADD COLUMN credentials_encrypted text NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'auth_configs'
                  AND column_name = 'credentials_encrypted'
            ) THEN
                ALTER TABLE auth_configs DROP COLUMN credentials_encrypted;
            END IF;
        END $$;
        """
    )

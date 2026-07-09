"""Create artifact_role_grants table for RBAC scoped roles.

Implements §4.2 of the RBAC design spec. Also normalizes legacy role values
in user_team_assignments (admin→platform-admin, operator→contributor).

Revision ID: 0044
Revises: 0043
"""
from alembic import op

revision = "0044"
down_revision = "0043"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS artifact_role_grants (
        id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        artifact_type   VARCHAR(32) NOT NULL,
        artifact_id     UUID        NOT NULL,
        role            VARCHAR(32) NOT NULL,
        grantee_type    VARCHAR(16) NOT NULL,
        grantee_id      TEXT        NOT NULL,
        granted_by      TEXT        NOT NULL,
        granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        revoked_at      TIMESTAMPTZ NULL,
        CONSTRAINT ck_arg_artifact_type CHECK (artifact_type IN ('agent','workflow')),
        CONSTRAINT ck_arg_role CHECK (role IN ('agent-admin','approver')),
        CONSTRAINT ck_arg_grantee_type CHECK (grantee_type IN ('user','team'))
    )
    """)

    # Primary permission-check index
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_arg_lookup
        ON artifact_role_grants(artifact_id, grantee_type, grantee_id, role)
        WHERE revoked_at IS NULL
    """)

    # "What roles does this user/team have?" query
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_arg_grantee
        ON artifact_role_grants(grantee_type, grantee_id)
        WHERE revoked_at IS NULL
    """)

    # Prevent duplicate active grants
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS uq_arg_active_grant
        ON artifact_role_grants(artifact_id, role, grantee_type, grantee_id)
        WHERE revoked_at IS NULL
    """)

    # Normalize legacy role values
    op.execute("""
    UPDATE user_team_assignments SET role = 'platform-admin' WHERE role = 'admin'
    """)
    op.execute("""
    UPDATE user_team_assignments SET role = 'contributor' WHERE role = 'operator'
    """)


def downgrade() -> None:
    # Revert role names
    op.execute("UPDATE user_team_assignments SET role = 'admin' WHERE role = 'platform-admin'")
    op.execute("UPDATE user_team_assignments SET role = 'operator' WHERE role = 'contributor'")
    op.execute("DROP INDEX IF EXISTS uq_arg_active_grant")
    op.execute("DROP INDEX IF EXISTS idx_arg_grantee")
    op.execute("DROP INDEX IF EXISTS idx_arg_lookup")
    op.execute("DROP TABLE IF EXISTS artifact_role_grants")

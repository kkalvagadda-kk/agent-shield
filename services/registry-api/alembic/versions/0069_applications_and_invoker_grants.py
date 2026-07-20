"""Create applications table; widen artifact_role_grants for the 'invoker' role
and 'application' grantee type (Decision 30).

Additive only — does not touch the live gateway auth path (that cutover is a
separate code change, not a migration) and does not move any existing data
(migration 0070 does the webhook_clients backfill).

Revision ID: 0069
Revises: 0068
"""
from alembic import op

revision = "0069"
down_revision = "0068"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        team_name        VARCHAR(255) NOT NULL,
        name             VARCHAR(128) NOT NULL,
        secret_encrypted TEXT        NOT NULL,
        enabled          BOOLEAN     NOT NULL DEFAULT true,
        created_by       VARCHAR(255) NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        rotated_at       TIMESTAMPTZ NULL,
        CONSTRAINT uq_applications_team_name UNIQUE (team_name, name)
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_applications_team ON applications(team_name)
    """)

    # --- Widen artifact_role_grants (migration 0044) -----------------------
    # Postgres has no "ADD CONSTRAINT IF NOT EXISTS" for CHECK constraints, so
    # guard with a catalog lookup instead (idempotent re-run safe, matching
    # this repo's migration convention of IF [NOT] EXISTS guards).
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_grantee_type'
        ) THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_grantee_type;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_grantee_type
            CHECK (grantee_type IN ('user', 'team', 'application'));
    END $$;
    """)

    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_role'
        ) THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_role;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_role
            CHECK (role IN ('agent-admin', 'approver', 'invoker'));
    END $$;
    """)


def downgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_role') THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_role;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_role
            CHECK (role IN ('agent-admin', 'approver'));
    END $$;
    """)
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_grantee_type') THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_grantee_type;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_grantee_type
            CHECK (grantee_type IN ('user', 'team'));
    END $$;
    """)
    op.execute("DROP INDEX IF EXISTS idx_applications_team")
    op.execute("DROP TABLE IF EXISTS applications")

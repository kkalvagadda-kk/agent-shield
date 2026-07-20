"""Create applications table; widen artifact_role_grants for the 'invoker' role
and 'application' grantee type (Decision 30).

Additive only — does not touch the live gateway auth path (that cutover is a
separate code change, not a migration) and does not move any existing data
(migration 0071 does the webhook_clients backfill).

Revision ID kept at 0070 (renamed from 0069 to avoid colliding with the concurrent,
UNRELATED "0069_mcp_server_fields.py" from the MCP-tools workstream, which also claims
revision "0069" — same ID string, different DDL — and would silently shadow this one).

down_revision is 0068, the REAL parent that exists in this branch's tree. An earlier
renumber set it to "0069" on the assumption that the MCP branch's 0069 would already be
applied ahead of us; that file is NOT part of this (webhook-application-identity) branch,
so on any database where it hasn't landed, alembic raises `KeyError: '0069'` and the
whole upgrade aborts (observed at CP deploy 2026-07-20 against a DB at head 0064). This
migration has NO data dependency on the MCP fields — it only creates `applications` and
widens `artifact_role_grants` (from 0044) — so it correctly chains off 0068. When the two
branches later meet on main, 0068 has two children (this 0070 and MCP's 0069); reconcile
with a standard `alembic merge` at that point, not by coupling them here.

Revision ID: 0070
Revises: 0068
"""
from alembic import op

revision = "0070"
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

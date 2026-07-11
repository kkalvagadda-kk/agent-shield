"""Add session_id + requester provenance to playground_runs.

- session_id: conversation grouping key (scopes the sandbox self-approve panel
  to a conversation; forward-proof for persisted-conversation history).
- requested_by_username / requested_by_team: captured from the JWT at chat
  start so the HITL console shows a username (not a raw sub) and the requester's
  own team.
"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='playground_runs' AND column_name='session_id') THEN
                ALTER TABLE playground_runs ADD COLUMN session_id varchar(256) NULL;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='playground_runs' AND column_name='requested_by_username') THEN
                ALTER TABLE playground_runs ADD COLUMN requested_by_username varchar(256) NULL;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='playground_runs' AND column_name='requested_by_team') THEN
                ALTER TABLE playground_runs ADD COLUMN requested_by_team varchar(128) NULL;
            END IF;
        END $$;
        """
    )
    # Index for the session-scoped approvals query (join thread_id -> run, filter by session).
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_playground_runs_session_id "
        "ON playground_runs (session_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_playground_runs_session_id")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='playground_runs' AND column_name='requested_by_team') THEN
                ALTER TABLE playground_runs DROP COLUMN requested_by_team;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='playground_runs' AND column_name='requested_by_username') THEN
                ALTER TABLE playground_runs DROP COLUMN requested_by_username;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='playground_runs' AND column_name='session_id') THEN
                ALTER TABLE playground_runs DROP COLUMN session_id;
            END IF;
        END $$;
        """
    )

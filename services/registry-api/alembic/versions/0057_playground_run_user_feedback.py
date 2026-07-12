"""Add user_feedback to playground_runs.

Thumbs-up/down feedback (POST /playground/runs/{id}/feedback) was pushed ONLY to
Langfuse as a score — there was no local column, so the observability dashboard
could not show a feedback ratio without a live Langfuse call. This column stores
the thumbs score (1 up / -1 down / NULL none) locally so the dashboard's
feedback panel aggregates from Postgres like its other panels. Nullable for
legacy rows.
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_runs'
                  AND column_name = 'user_feedback'
            ) THEN
                ALTER TABLE playground_runs
                    ADD COLUMN user_feedback smallint NULL;
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
                WHERE table_name = 'playground_runs'
                  AND column_name = 'user_feedback'
            ) THEN
                ALTER TABLE playground_runs DROP COLUMN user_feedback;
            END IF;
        END $$;
        """
    )

"""Add reasoning to approvals (the LLM's stated why for the tool call).

Surfaced on every HITL approval surface so reviewers/self-approvers see WHY the
agent wants the tool, not just the tool + args. Best-effort, nullable.
"""

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='approvals' AND column_name='reasoning') THEN
                ALTER TABLE approvals ADD COLUMN reasoning text NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='approvals' AND column_name='reasoning') THEN
                ALTER TABLE approvals DROP COLUMN reasoning;
            END IF;
        END $$;
        """
    )

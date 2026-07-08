"""Add judge_score column to agent_runs for unified dashboard queries.

Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"


def upgrade() -> None:
    op.execute("""
    ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS judge_score FLOAT NULL
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_agent_runs_judge_score
        ON agent_runs(judge_score)
        WHERE judge_score IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_judge_score")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS judge_score")

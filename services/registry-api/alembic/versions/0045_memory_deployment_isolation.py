"""Add deployment_id to agent_memory for per-deployment isolation.

Revision ID: 0045
Revises: 0044
"""
from alembic import op
import sqlalchemy as sa

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE agent_memory
        ADD COLUMN IF NOT EXISTS deployment_id UUID NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_memory_deployment
        ON agent_memory (agent_name, deployment_id)
        WHERE deployment_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memory_deployment")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS deployment_id")

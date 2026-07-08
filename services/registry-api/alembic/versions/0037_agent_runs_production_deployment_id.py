"""Add production_deployment_id FK to agent_runs for run isolation.

Revision ID: 0037
Revises: 0036
"""
from alembic import op
import sqlalchemy as sa

revision = "0037"
down_revision = "0036"


def upgrade() -> None:
    op.execute("""
    ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS production_deployment_id UUID
        REFERENCES production_deployments(id)
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_runs_prod_deployment
        ON agent_runs(production_deployment_id)
        WHERE production_deployment_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_prod_deployment")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS production_deployment_id")

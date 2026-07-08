"""Expand production_deployments status check constraint to include
suspending, terminating, terminated states.

Revision ID: 0038
Revises: 0037
"""
from alembic import op

revision = "0038"
down_revision = "0037"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE production_deployments
        DROP CONSTRAINT IF EXISTS production_deployments_status_check;
    """)
    op.execute("""
        ALTER TABLE production_deployments
        ADD CONSTRAINT production_deployments_status_check
        CHECK (status IN ('pending', 'deploying', 'running', 'suspending', 'suspended', 'terminating', 'terminated', 'failed'));
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE production_deployments
        DROP CONSTRAINT IF EXISTS production_deployments_status_check;
    """)
    op.execute("""
        ALTER TABLE production_deployments
        ADD CONSTRAINT production_deployments_status_check
        CHECK (status IN ('pending', 'deploying', 'running', 'suspended', 'failed'));
    """)

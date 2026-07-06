"""add execution_shape + memory_enabled to agents

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "execution_shape",
            sa.String(16),
            nullable=False,
            server_default="reactive",
        ),
    )
    op.create_check_constraint(
        "ck_agents_execution_shape",
        "agents",
        "execution_shape IN ('reactive', 'durable')",
    )
    op.add_column(
        "agents",
        sa.Column(
            "memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_constraint("ck_agents_execution_shape", "agents", type_="check")
    op.drop_column("agents", "execution_shape")
    op.drop_column("agents", "memory_enabled")

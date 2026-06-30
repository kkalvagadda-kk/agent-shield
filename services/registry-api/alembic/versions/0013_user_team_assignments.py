"""user_team_assignments

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-30
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_team_assignments",
        sa.Column("user_sub", sa.String(255), primary_key=True),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(64), nullable=False, server_default="operator"),
        sa.Column("assigned_by", sa.String(255), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_uta_team_name", "user_team_assignments", ["team_name"])


def downgrade() -> None:
    op.drop_table("user_team_assignments")

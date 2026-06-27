"""add python tool type

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # Add python_code column to tools table
    op.add_column("tools", sa.Column("python_code", sa.Text, nullable=True))

    # Drop the old CHECK constraint that excludes 'python'
    op.drop_constraint("ck_tools_type", "tools", type_="check")

    # Re-create with 'python' included
    op.create_check_constraint(
        "ck_tools_type",
        "tools",
        "type IN ('native','http','mcp_tool','python')",
    )


def downgrade():
    op.drop_constraint("ck_tools_type", "tools", type_="check")
    op.create_check_constraint(
        "ck_tools_type",
        "tools",
        "type IN ('native','http','mcp_tool')",
    )
    op.drop_column("tools", "python_code")

"""agents_created_by_not_null

Backfill NULL created_by to 'system' and add NOT NULL + default constraint.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE agents SET created_by = 'system' WHERE created_by IS NULL")
    op.alter_column(
        "agents",
        "created_by",
        existing_type=sa.String(256),
        nullable=False,
        server_default="system",
    )


def downgrade() -> None:
    op.alter_column(
        "agents",
        "created_by",
        existing_type=sa.String(256),
        nullable=True,
        server_default=None,
    )

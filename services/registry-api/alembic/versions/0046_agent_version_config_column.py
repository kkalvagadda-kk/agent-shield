"""Add config JSONB column to agent_versions for metadata snapshot.

Revision ID: 0046
Revises: 0045
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_versions",
        sa.Column("config", JSONB, nullable=True, server_default=None),
        schema=None,
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "config")

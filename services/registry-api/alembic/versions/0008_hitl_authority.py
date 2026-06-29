"""
0008_hitl_authority — add context column to approvals

Adds `context` column (playground vs production) to the approvals table,
enabling scoped HITL approval routing: playground approvals use self-approval,
production approvals use the approval_authority registry.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column("context", sa.Text(), nullable=False, server_default="production"),
    )


def downgrade() -> None:
    op.drop_column("approvals", "context")

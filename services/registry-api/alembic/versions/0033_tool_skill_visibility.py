"""Add visibility columns to tools and skills

Revision ID: 0033
Revises: 0032

Adds created_by and publish_status to tools table, publish_status to skills
table. Enables consistent visibility filtering: published OR created_by == caller.
Idempotent (IF NOT EXISTS guards).
"""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # --- tools: add created_by ---
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='tools' AND column_name='created_by'"
    ))
    if not result.fetchone():
        op.add_column("tools", sa.Column("created_by", sa.String(256), nullable=True))

    # --- tools: add publish_status ---
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='tools' AND column_name='publish_status'"
    ))
    if not result.fetchone():
        op.add_column("tools", sa.Column(
            "publish_status", sa.String(32), nullable=False, server_default="published"
        ))

    # --- skills: add publish_status ---
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='skills' AND column_name='publish_status'"
    ))
    if not result.fetchone():
        op.add_column("skills", sa.Column(
            "publish_status", sa.String(32), nullable=False, server_default="published"
        ))


def downgrade() -> None:
    op.drop_column("skills", "publish_status")
    op.drop_column("tools", "publish_status")
    op.drop_column("tools", "created_by")

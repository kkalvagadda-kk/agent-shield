"""Add source_version_id to publish_requests for version-pinning."""
from alembic import op
import sqlalchemy as sa

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publish_requests",
        sa.Column("source_version_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publish_requests", "source_version_id")

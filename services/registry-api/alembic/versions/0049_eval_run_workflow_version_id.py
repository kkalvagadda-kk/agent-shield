"""Add workflow_version_id to eval_runs for workflow eval publish path."""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eval_runs",
        sa.Column("workflow_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_eval_runs_workflow_version",
        "eval_runs",
        "workflow_versions",
        ["workflow_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_eval_runs_workflow_version", "eval_runs", type_="foreignkey")
    op.drop_column("eval_runs", "workflow_version_id")

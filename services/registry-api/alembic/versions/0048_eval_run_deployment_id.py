"""Add deployment_id columns to eval_runs for deployment-targeted evaluation."""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eval_runs",
        sa.Column("sandbox_deployment_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "eval_runs",
        sa.Column("workflow_deployment_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_eval_runs_sandbox_deployment",
        "eval_runs",
        "deployments",
        ["sandbox_deployment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_eval_runs_workflow_deployment",
        "eval_runs",
        "workflow_deployments",
        ["workflow_deployment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_eval_runs_workflow_deployment", "eval_runs", type_="foreignkey")
    op.drop_constraint("fk_eval_runs_sandbox_deployment", "eval_runs", type_="foreignkey")
    op.drop_column("eval_runs", "workflow_deployment_id")
    op.drop_column("eval_runs", "sandbox_deployment_id")

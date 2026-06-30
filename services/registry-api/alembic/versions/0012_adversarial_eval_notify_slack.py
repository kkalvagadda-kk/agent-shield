"""Add adversarial_eval_passed to agent_versions + notify_slack to approvals.

Revision: 0012
Down: 0011

adversarial_eval_passed (agent_versions):
  High-risk agents must pass an adversarial evaluation before deploy.
  Default false so existing versions are not blocked; operators opt in
  by patching the version after running their adversarial eval suite.

notify_slack (approvals):
  Playground approvals should not page on-call reviewers via Slack.
  Default true so existing production approvals continue to notify.
  Set to false automatically when context='playground'.
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_versions",
        sa.Column(
            "adversarial_eval_passed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "approvals",
        sa.Column(
            "notify_slack",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "playground_runs",
        sa.Column("judge_score", sa.Numeric(4, 3), nullable=True),
    )
    op.add_column(
        "playground_runs",
        sa.Column("judge_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "playground_runs",
        sa.Column("judge_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playground_runs", "judge_reason")
    op.drop_column("playground_runs", "judge_status")
    op.drop_column("playground_runs", "judge_score")
    op.drop_column("approvals", "notify_slack")
    op.drop_column("agent_versions", "adversarial_eval_passed")

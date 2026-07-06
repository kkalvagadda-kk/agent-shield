"""deployments_env_add_sandbox

Add 'sandbox' to the deployments.environment CHECK constraint (Decision 20 / T-10).
Enables ungated sandbox deploys for the playground evaluation loop.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-03
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_deployments_env", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_env",
        "deployments",
        "environment IN ('production','staging','canary','sandbox')",
    )


def downgrade() -> None:
    # NOTE: fails if any deployment rows still have environment='sandbox'.
    # In a dev cluster, delete/repoint those rows before downgrading.
    op.drop_constraint("ck_deployments_env", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_env",
        "deployments",
        "environment IN ('production','staging','canary')",
    )

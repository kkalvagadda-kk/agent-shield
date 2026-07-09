"""Expand production_deployments status CHECK constraint."""

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "production_deployments_status_check", "production_deployments", type_="check"
    )
    op.create_check_constraint(
        "production_deployments_status_check",
        "production_deployments",
        "status IN ('pending','deploying','running','suspended','failed','terminating','terminated','rolled_back','gate_failed')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "production_deployments_status_check", "production_deployments", type_="check"
    )
    op.create_check_constraint(
        "production_deployments_status_check",
        "production_deployments",
        "status IN ('pending','deploying','running','suspended','failed')",
    )

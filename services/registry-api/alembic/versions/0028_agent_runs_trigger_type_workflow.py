"""Allow trigger_type='workflow' on agent_runs (Decision 22 run tree)

Revision ID: 0028
Revises: 0027

Composite-workflow parent/child runs use trigger_type='workflow'. Extend the
ck_agent_runs_trigger_type CHECK to include it. Idempotent.
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_ALLOWED = "'manual','api','schedule','webhook','workflow'"
_OLD = "'manual','api','schedule','webhook'"


def upgrade() -> None:
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS ck_agent_runs_trigger_type")
    op.execute(
        f"ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_trigger_type "
        f"CHECK (trigger_type IN ({_ALLOWED}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS ck_agent_runs_trigger_type")
    op.execute(
        f"ALTER TABLE agent_runs ADD CONSTRAINT ck_agent_runs_trigger_type "
        f"CHECK (trigger_type IN ({_OLD}))"
    )

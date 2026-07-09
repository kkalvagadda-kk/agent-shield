"""Agent sandbox deployment lifecycle: expand status enum + suspended_at + ttl_hours.

Gives sandbox deployments the same Suspend/Resume/Terminate lifecycle the
production (catalog) deployments already have, plus a configurable TTL for
auto-cleanup.

Revision ID: 0041
Revises: 0040
"""
from alembic import op

revision = "0041"
down_revision = "0040"

_OLD = "('pending','deploying','running','failed','rolled_back','terminated','gate_failed')"
_NEW = (
    "('pending','deploying','running','failed','rolled_back','terminated',"
    "'gate_failed','suspending','suspended','terminating')"
)


def upgrade() -> None:
    op.execute("ALTER TABLE deployments DROP CONSTRAINT IF EXISTS ck_deployments_status")
    op.execute(f"ALTER TABLE deployments ADD CONSTRAINT ck_deployments_status CHECK (status IN {_NEW})")
    op.execute("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ")
    op.execute("ALTER TABLE deployments ADD COLUMN IF NOT EXISTS ttl_hours INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS ttl_hours")
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS suspended_at")
    op.execute("ALTER TABLE deployments DROP CONSTRAINT IF EXISTS ck_deployments_status")
    op.execute(f"ALTER TABLE deployments ADD CONSTRAINT ck_deployments_status CHECK (status IN {_OLD})")

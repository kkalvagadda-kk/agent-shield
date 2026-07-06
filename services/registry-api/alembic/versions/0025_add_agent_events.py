"""Add agent_events table (Phase 9 — event gateway)

Revision ID: 0025
Revises: 0024

Records every inbound webhook the Event Gateway processes: matched (a run was
dispatched), filtered (valid token but filter didn't match — logged, no run),
or rejected (bad token / replay / rate-limit). run_id is a soft reference to
agent_runs (nullable; set only for matched events). Idempotent CREATE.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_events (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trigger_id   UUID REFERENCES agent_triggers(id) ON DELETE SET NULL,
            agent_name   VARCHAR(256) NOT NULL,
            status       VARCHAR(16) NOT NULL,
            filter_reason TEXT,
            payload      JSONB,
            run_id       UUID,
            source_ip    INET,
            received_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_events_status
                CHECK (status IN ('matched','filtered','rejected'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_trigger_received "
        "ON agent_events (trigger_id, received_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_agent_received "
        "ON agent_events (agent_name, received_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_events")

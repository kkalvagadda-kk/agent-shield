"""Add composite workflows + workflow_members; extend triggers/runs (Decision 22)

Revision ID: 0027
Revises: 0026

Creates the NEW composite-executable `workflows` table (a collection of member
agents) and `workflow_members`. Adds a nullable `workflow_id` FK to
`agent_triggers` and `agent_runs` so a trigger/run can target EITHER an agent OR
a composite workflow — enforced on triggers by a `num_nonnulls(...) = 1` CHECK.
Idempotent (IF NOT EXISTS / guarded).
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── composite workflows ──────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflows (
          id              UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
          name            VARCHAR(256) NOT NULL,
          team            VARCHAR(128) NOT NULL,
          description     TEXT,
          execution_shape VARCHAR(16)  NOT NULL DEFAULT 'durable'
                          CONSTRAINT ck_workflows_execution_shape CHECK (execution_shape IN ('reactive','durable')),
          memory_enabled  BOOLEAN NOT NULL DEFAULT false,
          orchestration   VARCHAR(32)  NOT NULL DEFAULT 'sequential'
                          CONSTRAINT ck_workflows_orchestration CHECK (orchestration IN ('sequential','supervisor','handoff')),
          status          VARCHAR(32)  NOT NULL DEFAULT 'draft'
                          CONSTRAINT ck_workflows_status CHECK (status IN ('draft','published','archived')),
          publish_status  VARCHAR(32)  NOT NULL DEFAULT 'private',
          created_by      VARCHAR(256),
          created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_workflows_team ON workflows(team)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_name_team ON workflows(name, team)")

    # ── workflow_members ─────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_members (
          workflow_id  UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
          agent_id     UUID NOT NULL REFERENCES agents(id),
          role         VARCHAR(64),
          position     INTEGER,
          routing      JSONB NOT NULL DEFAULT '{}',
          added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (workflow_id, agent_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_workflow_members_agent_id ON workflow_members(agent_id)")

    # ── agent_triggers.workflow_id (+ exactly-one target CHECK) ──────────────
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE")
    # A trigger may now target a workflow instead of an agent → agent_id must be nullable.
    op.execute("ALTER TABLE agent_triggers ALTER COLUMN agent_id DROP NOT NULL")
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_agent_triggers_target') THEN
            ALTER TABLE agent_triggers
              ADD CONSTRAINT ck_agent_triggers_target
              CHECK (num_nonnulls(agent_id, workflow_id) = 1);
          END IF;
        END $$;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_triggers_workflow ON agent_triggers(workflow_id) WHERE workflow_id IS NOT NULL")

    # ── agent_runs.workflow_id ───────────────────────────────────────────────
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS workflow_id UUID REFERENCES workflows(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow_id ON agent_runs(workflow_id) WHERE workflow_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_workflow_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS workflow_id")
    op.execute("DROP INDEX IF EXISTS idx_agent_triggers_workflow")
    op.execute("ALTER TABLE agent_triggers DROP CONSTRAINT IF EXISTS ck_agent_triggers_target")
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS workflow_id")
    # Restore NOT NULL on agent_id (safe: with workflow_id gone, all remaining
    # triggers are agent triggers with agent_id set).
    op.execute("ALTER TABLE agent_triggers ALTER COLUMN agent_id SET NOT NULL")
    op.execute("DROP TABLE IF EXISTS workflow_members")
    op.execute("DROP TABLE IF EXISTS workflows")

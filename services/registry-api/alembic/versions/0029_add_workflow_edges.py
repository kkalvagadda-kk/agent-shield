"""Add workflow_edges + allow orchestration='conditional' (Decision 22 — full orchestration)

Revision ID: 0029
Revises: 0028

Composite workflows gain a real edge graph: `workflow_edges(source_agent_id →
target_agent_id, condition)` drives conditional-routing / supervisor / handoff
orchestration (edges are a cross-member construct, so they live in their own
table, not in workflow_members.routing). Also relaxes the workflows
orchestration CHECK to allow the new 'conditional' mode. Idempotent
(IF NOT EXISTS / guarded / DROP+ADD constraint).
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_ORCH_NEW = "'sequential','supervisor','handoff','conditional'"
_ORCH_OLD = "'sequential','supervisor','handoff'"


def upgrade() -> None:
    # ── workflow_edges ───────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_edges (
          id               UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
          workflow_id      UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
          source_agent_id  UUID NOT NULL REFERENCES agents(id),
          target_agent_id  UUID NOT NULL REFERENCES agents(id),
          condition        TEXT,
          position         INTEGER,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_workflow_edges_workflow_id ON workflow_edges(workflow_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_workflow_edges_source ON workflow_edges(source_agent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_workflow_edges_target ON workflow_edges(target_agent_id)")
    # Prevent duplicate source→target edges within a workflow.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_edges_src_tgt "
        "ON workflow_edges(workflow_id, source_agent_id, target_agent_id)"
    )

    # ── relax orchestration CHECK to add 'conditional' ───────────────────────
    op.execute("ALTER TABLE workflows DROP CONSTRAINT IF EXISTS ck_workflows_orchestration")
    op.execute(
        f"ALTER TABLE workflows ADD CONSTRAINT ck_workflows_orchestration "
        f"CHECK (orchestration IN ({_ORCH_NEW}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workflows DROP CONSTRAINT IF EXISTS ck_workflows_orchestration")
    op.execute(
        f"ALTER TABLE workflows ADD CONSTRAINT ck_workflows_orchestration "
        f"CHECK (orchestration IN ({_ORCH_OLD}))"
    )
    op.execute("DROP INDEX IF EXISTS uq_workflow_edges_src_tgt")
    op.execute("DROP INDEX IF EXISTS idx_workflow_edges_target")
    op.execute("DROP INDEX IF EXISTS idx_workflow_edges_source")
    op.execute("DROP INDEX IF EXISTS idx_workflow_edges_workflow_id")
    op.execute("DROP TABLE IF EXISTS workflow_edges")

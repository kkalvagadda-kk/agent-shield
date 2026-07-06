"""Rename workflows → agent_graphs (Decision 22 — free the name for composite workflows)

Revision ID: 0026
Revises: 0025

The OLD `workflows` / `workflow_versions` tables are a single declarative agent's
canvas graph. Decision 22 redefines "Workflow" as a COMPOSITE of agents, so this
migration renames the old canvas concept to `agent_graphs` / `agent_graph_versions`
(freeing the name `workflows` for the new composite table created in 0027).

Pure rename — no row data changes. Renames tables, the `workflow_id` columns,
indexes, and FK constraints. Idempotent where Postgres supports IF EXISTS;
column/constraint renames are guarded with DO blocks so a partial re-run is safe.
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Tables ───────────────────────────────────────────────────────────────
    op.execute("ALTER TABLE IF EXISTS workflows RENAME TO agent_graphs")
    op.execute("ALTER TABLE IF EXISTS workflow_versions RENAME TO agent_graph_versions")

    # ── Columns (guarded — RENAME COLUMN has no IF EXISTS) ───────────────────
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name='agent_versions' AND column_name='workflow_id') THEN
            ALTER TABLE agent_versions RENAME COLUMN workflow_id TO agent_graph_id;
          END IF;
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name='agent_graph_versions' AND column_name='workflow_id') THEN
            ALTER TABLE agent_graph_versions RENAME COLUMN workflow_id TO agent_graph_id;
          END IF;
        END $$;
        """
    )

    # ── Indexes ──────────────────────────────────────────────────────────────
    op.execute("ALTER INDEX IF EXISTS idx_workflows_team RENAME TO idx_agent_graphs_team")
    op.execute("ALTER INDEX IF EXISTS idx_workflows_status RENAME TO idx_agent_graphs_status")
    op.execute("ALTER INDEX IF EXISTS idx_workflow_versions_workflow_id RENAME TO idx_agent_graph_versions_agent_graph_id")
    op.execute("ALTER INDEX IF EXISTS uq_workflow_versions RENAME TO uq_agent_graph_versions")

    # ── FK constraints (guarded — RENAME CONSTRAINT has no IF EXISTS) ────────
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_versions_workflow_id_fkey') THEN
            ALTER TABLE agent_versions RENAME CONSTRAINT agent_versions_workflow_id_fkey TO agent_versions_agent_graph_id_fkey;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='workflow_versions_workflow_id_fkey') THEN
            ALTER TABLE agent_graph_versions RENAME CONSTRAINT workflow_versions_workflow_id_fkey TO agent_graph_versions_agent_graph_id_fkey;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_versions_agent_graph_id_fkey') THEN
            ALTER TABLE agent_versions RENAME CONSTRAINT agent_versions_agent_graph_id_fkey TO agent_versions_workflow_id_fkey;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname='agent_graph_versions_agent_graph_id_fkey') THEN
            ALTER TABLE agent_graph_versions RENAME CONSTRAINT agent_graph_versions_agent_graph_id_fkey TO workflow_versions_workflow_id_fkey;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER INDEX IF EXISTS uq_agent_graph_versions RENAME TO uq_workflow_versions")
    op.execute("ALTER INDEX IF EXISTS idx_agent_graph_versions_agent_graph_id RENAME TO idx_workflow_versions_workflow_id")
    op.execute("ALTER INDEX IF EXISTS idx_agent_graphs_status RENAME TO idx_workflows_status")
    op.execute("ALTER INDEX IF EXISTS idx_agent_graphs_team RENAME TO idx_workflows_team")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name='agent_graph_versions' AND column_name='agent_graph_id') THEN
            ALTER TABLE agent_graph_versions RENAME COLUMN agent_graph_id TO workflow_id;
          END IF;
          IF EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_name='agent_versions' AND column_name='agent_graph_id') THEN
            ALTER TABLE agent_versions RENAME COLUMN agent_graph_id TO workflow_id;
          END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE IF EXISTS agent_graph_versions RENAME TO workflow_versions")
    op.execute("ALTER TABLE IF EXISTS agent_graphs RENAME TO workflows")

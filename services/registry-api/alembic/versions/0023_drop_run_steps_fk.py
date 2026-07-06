"""Drop run_steps.run_id FK to agent_runs (make run_id polymorphic)

Revision ID: 0023
Revises: 0022

run_steps tracks steps for durable runs that originate from BOTH agent_runs
(production) and playground_runs (playground). A single FK to agent_runs.id
rejects playground run ids with a ForeignKeyViolationError. Drop the FK so
run_id is a polymorphic soft reference; the index on run_id is retained.
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def _fk_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint WHERE conname = :n "
                "AND conrelid = 'run_steps'::regclass"
            ),
            {"n": name},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if _fk_exists(conn, "run_steps_run_id_fkey"):
        op.drop_constraint("run_steps_run_id_fkey", "run_steps", type_="foreignkey")


def downgrade() -> None:
    conn = op.get_bind()
    if not _fk_exists(conn, "run_steps_run_id_fkey"):
        op.create_foreign_key(
            "run_steps_run_id_fkey",
            "run_steps",
            "agent_runs",
            ["run_id"],
            ["id"],
            ondelete="CASCADE",
        )

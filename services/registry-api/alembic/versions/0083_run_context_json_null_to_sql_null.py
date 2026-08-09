"""Normalize JSON `null` run_context anchors to SQL NULL.

Revision ID: 0083
Revises: 0082
Create Date: 2026-08-09

WHY
---
`0082` added `run_context JSONB`. SQLAlchemy's `JSONB` type defaults to
`none_as_null=False`, so assigning Python `None` serialized to JSON `null` rather than
SQL NULL. `workflow_orchestrator` does exactly that: a member child's anchor is
`inherit_anchor(parent.run_context, ...)`, which returns `None` when the parent has no
anchor.

The result is a row for which **`run_context IS NOT NULL` is TRUE while there is no
identity**. Measured on the test cluster before this fix: **52 rows in `agent_runs`**.

Behaviour was never wrong — `run_context_anchor.rehydrate` deserializes JSON `null` to
Python `None` and its `if not claims` guard treats it as absent, so no run ever gained a
fabricated identity. What was wrong is the DATA: an identity column that answers "yes, I
have one" when it does not. Every future audit query, every `count(*) FILTER (WHERE
run_context IS NOT NULL)`, and any operator asking "which runs carry identity" would have
built a wrong conclusion on it. That is the same defect class as a permission-bearing
field inventing its most permissive value when absent.

The model now declares `JSONB(none_as_null=True)` so new writes cannot recreate this. This
migration cleans up the rows written before that.

IDEMPOTENT AND SAFE
-------------------
Touches only rows whose JSON type is literally `'null'`. A real anchor is a JSON object and
`jsonb_typeof(...) = 'object'`, so it is never matched. Running twice affects zero rows the
second time. Guarded on the column existing so it is a no-op on a database that has not
taken 0082.

NOT REVERSIBLE, DELIBERATELY. The downgrade is a no-op: SQL NULL and JSON `null` both mean
"no anchor" to every reader, and writing JSON `null` back would restore a lie for no gain.
"""
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None

_TABLES = ("playground_runs", "agent_runs")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = '{table}' AND column_name = 'run_context'
                ) THEN
                    UPDATE {table}
                       SET run_context = NULL
                     WHERE run_context IS NOT NULL
                       AND jsonb_typeof(run_context) = 'null';
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    # Intentionally a no-op — see the module docstring. Both representations mean "no
    # anchor"; only one of them tells the truth to `IS NOT NULL`.
    pass

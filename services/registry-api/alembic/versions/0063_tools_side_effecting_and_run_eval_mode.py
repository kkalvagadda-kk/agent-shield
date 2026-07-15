"""Eval v2 E-2 T001 — `tools.side_effecting` + `playground_runs.eval_mode`.

Two additive columns that make the record/mock seam possible:

1. `tools.side_effecting` — classifies which tools must be intercepted under
   `eval_mode=record`. Served by `ToolResponse`, resolved onto the tool callable
   by the SDK (`tool_resolver` → `.side_effecting`), and read by the ONE delivery
   edge (`graph_builder.governed_tool` step 3).

   **Fail-closed backfill:** a tool is read-only ONLY when it is provably so — an
   HTTP tool whose method is GET/HEAD. Everything else (POST/PUT/PATCH/DELETE,
   python, native, mcp_tool, an HTTP tool with no method at all) backfills to
   `true`, i.e. side-effecting, and is therefore mocked rather than invoked under
   eval. The same rule is applied to NEW tools at the API door by
   `routers/tools.py::infer_side_effecting` — this SQL is its snapshot (a
   migration must not import app code that will drift under it).

2. `playground_runs.eval_mode` — PERSISTED, not transient. A durable run is
   dispatched fire-and-forget and a parked HITL step resumes via a SEPARATE POST
   to the runner (`/resume/{thread_id}` for the console decide, `/resume/{thread_id}/stream`
   for the eval-runner's self-approve). The resume re-drives the graph and
   re-crosses the delivery seam, so `eval_mode` MUST survive the checkpoint —
   the resume dispatch reads it back off this column rather than re-deriving it.

Idempotent: the ADDs are guarded, and the `side_effecting` backfill runs ONLY on
the transition that creates the column, so a re-run can never clobber an operator's
explicit override back to the inferred value. Data-preserving; up/down/up round-trips.
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # tools.side_effecting — add + fail-closed backfill in ONE guarded transition so
    # the backfill cannot re-run over hand-set overrides.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'tools' AND column_name = 'side_effecting'
            ) THEN
                ALTER TABLE tools
                    ADD COLUMN side_effecting BOOLEAN NOT NULL DEFAULT false;
                -- Read-only iff provably read-only (HTTP GET/HEAD). Everything else
                -- — incl. an unclassifiable tool — is side-effecting (fail-closed).
                UPDATE tools SET side_effecting = true
                WHERE NOT (
                    type = 'http'
                    AND upper(coalesce(http_method, '')) IN ('GET', 'HEAD')
                );
            END IF;
        END $$;
        """
    )

    # playground_runs.eval_mode — existing rows take the 'live' default (no eval ran
    # on them), so no backfill statement is needed.
    op.execute(
        "ALTER TABLE playground_runs "
        "ADD COLUMN IF NOT EXISTS eval_mode VARCHAR(16) NOT NULL DEFAULT 'live'"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_playground_runs_eval_mode'
            ) THEN
                ALTER TABLE playground_runs
                    ADD CONSTRAINT ck_playground_runs_eval_mode
                    CHECK (eval_mode IN ('live', 'record'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE playground_runs DROP CONSTRAINT IF EXISTS ck_playground_runs_eval_mode"
    )
    op.execute("ALTER TABLE playground_runs DROP COLUMN IF EXISTS eval_mode")
    op.execute("ALTER TABLE tools DROP COLUMN IF EXISTS side_effecting")

"""Durable identity anchor on the run rows — identity P1, read back by P1.5.

Revision ID: 0082
Revises: 0081
Create Date: 2026-08-08

WHY
---
Identity P1 mints a signed RunContext (the "RCT") at the edge and threads it to the runner
as a header. The token has a 900-second TTL, and that TTL is deliberately short: it rides
synchronous internal hops that complete in seconds, so a leaked token stops being useful
almost immediately.

A HITL approval can sit for hours. Carrying the token across the pause would force the TTL
up to "long enough for the slowest human", throwing away the only property the short TTL
buys. So identity is persisted HERE and the token is re-minted from this column at resume:
the token is transport, this row is the system of record.

Without it, every post-approval OPA re-check sees `user_id=""`. The identity floor has been
live and denying since WS-2 (`opa_policy/agentshield.rego:22,101-108`, AND-ed into `allow`
at `:116`), so that is a real denial of resumed runs, not a future risk — the same class as
`docs/bugs/opa-user-identity-floor-denies-tools-missing-x-user-sub.md`.

WHY A JSONB COLUMN AND NOT REUSE `user_id` / `run_by`
------------------------------------------------------
Both columns already exist and re-deriving a RunContext from them was the cheaper option.
It is wrong three ways, and each one fails silently:

  1. `agent_runs.run_by` holds a SERVICE subject for a daemon run
     (`serviceaccount:scheduler`). Minting `user_sub=run_by` would hand a daemon a
     fabricated human identity and walk it straight through the `user_delegated` arm of the
     identity floor — an authorization escalation produced by a convenience.
  2. `user_team` is on neither row. Re-deriving it would re-query the CURRENT team, so a
     user who changed teams during the pause resumes with the wrong one, silently changing
     which grants Decision 45 intersects against.
  3. `origin`, `actor_chain` and `is_service_call` have no columns at all, so a resumed run
     would lose its lineage and look like a root run.

WHY BOTH TABLES
---------------
`playground_runs` anchors the sandbox path; `agent_runs` anchors production and workflow
members. `routers/approvals.py` already discriminates a paused thread across exactly these
two, so anchoring one and not the other would make resume identity work in the sandbox and
fail in production — the environment split this platform has repeatedly been bitten by.

Nullable, no backfill, no default. A run that started before this migration genuinely has
no anchor, and `run_context_anchor.rehydrate` returns None for it: no identity, fail-closed,
exactly the pre-P1.5 behaviour. Writing `'{}'` instead would be an ASSERTION that the run
has no user, which is a different and false claim.
"""
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

_TABLES = ("playground_runs", "agent_runs")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = '{table}')
                   AND NOT EXISTS (
                       SELECT 1 FROM information_schema.columns
                       WHERE table_name = '{table}' AND column_name = 'run_context'
                   ) THEN
                    ALTER TABLE {table} ADD COLUMN run_context JSONB;
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = '{table}' AND column_name = 'run_context'
                ) THEN
                    ALTER TABLE {table} DROP COLUMN run_context;
                END IF;
            END $$;
            """
        )

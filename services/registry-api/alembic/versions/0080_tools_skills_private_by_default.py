"""Tools and skills are PRIVATE by default, like agents and workflows.

Revision ID: 0080
Revises: 0079
Create Date: 2026-08-07

WHY
---
Decision 47: a tool follows the same lifecycle as an agent — "drafts are yours until you
share". `agents.publish_status` and `composite_workflows.publish_status` already default to
'private'. `tools.publish_status` and `skills.publish_status` defaulted to 'published', so
every tool anyone created was immediately in the shared catalog for every team, with no
review and no decision by its owner.

Paired with Decision 46 (0.2.267), which made `owner_team` derive from the creator's team
instead of coming from the request body where it defaulted to NULL. Ownership without a
private default is half the model: knowing who owns a tool changes nothing if the tool is
public the moment it exists.

NO BACKFILL — DELIBERATE
------------------------
This migration touches ZERO rows. The ~174 existing tools and skills stay 'published' and
remain the shared library they have been all along.

Backfilling them to 'private' would stand up a correct-looking model on top of a live
outage: every SDK agent resolves its tools by name through GET /api/v1/tools/, and every
declarative workflow resolves them by id. Flipping 174 rows private would strand every
agent whose tool bindings cross a team boundary today — and cross-team bindings are the
normal case here, because until 0.2.267 `owner_team` was NULL on 65 of them, which
`tool_access.team_may_use_tool` reads as "usable by every team".

So the default changes forward-looking only. Existing rows are grandfathered as the shared
library; new rows are private to their owning team until an owner or a publish cascade
shares them. If those 174 should later be reviewed and demoted, that is a data decision
with its own blast radius, taken deliberately — not a side effect of a DDL change.

WHAT ELSE HAD TO CHANGE IN THE SAME COMMIT
------------------------------------------
A default flip on its own would have broken agent startup. `list_tools`/`list_skills`
filtered visibility to `publish_status == 'published' OR created_by == <caller>`:

  * The SDK tool_resolver calls GET /api/v1/tools/?name=X ANONYMOUSLY (agent pods hold no
    token until identity Phase 3). An anonymous caller got the published-only branch, so a
    newly created private tool returned zero items and the pod died at startup with
    "Tool 'X' not found in the platform registry".
  * Visibility was CREATOR-scoped while Decision 46 makes the TEAM the owner, so a
    contributor's new tool would have been invisible to their own teammates.

Both are fixed by `catalog_visibility.py` in this commit. The migration is inert without it.

Idempotent: ALTER COLUMN ... SET DEFAULT is safe to re-run and is a no-op when the default
already matches. Guarded on table+column existence so a partial install cannot fail here.
Data-preserving: no UPDATE, no row touched.
"""
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


# (table, column) pairs that adopt the agent lifecycle's private-by-default.
_TARGETS = (("tools", "publish_status"), ("skills", "publish_status"))


def _set_default(table: str, column: str, value: str) -> None:
    """SET DEFAULT, guarded on the column existing.

    Guarded rather than bare so this migration cannot be the thing that fails an install
    that legitimately predates one of these tables. An ALTER on a missing table aborts the
    whole transaction and takes every later migration with it.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = '{column}'
            ) THEN
                ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT '{value}';
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    for table, column in _TARGETS:
        _set_default(table, column, "private")


def downgrade() -> None:
    # Restores the previous default only. It does NOT re-publish rows created while the
    # private default was in force — those were private by their owner's intent, and a
    # downgrade of a DDL default has no business changing what anyone can see.
    for table, column in _TARGETS:
        _set_default(table, column, "published")

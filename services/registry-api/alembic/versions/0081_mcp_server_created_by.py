"""Record who registered an MCP server, so its discovered tools have a creator.

Revision ID: 0081
Revises: 0080
Create Date: 2026-08-08

WHY
---
Migration 0080 made tools private by default. Catalog visibility is CREATOR-scoped
(`published OR created_by == caller`), matching agents and workflows — Decision 47's
"drafts are yours until you share".

`mcp_discovery.py` creates `Tool` rows for every tool an upstream MCP server exposes, and it
had no `created_by` to give them. A private tool with a NULL creator matches neither arm of
the predicate: **invisible to everyone, permanently.** That is the same defect shape as
`docs/bugs/mcp-discovered-tools-invisible-after-private-default.md` — a writer that does not
set the column the filter reads — found this time by auditing the writers BEFORE shipping,
which is the lesson that bug doc records.

`mcp_servers` had no `created_by` either, so there was nothing to propagate. This adds it.

WHY THE REGISTRANT IS THE RIGHT CREATOR
---------------------------------------
An MCP tool has no draft phase — you cannot edit it into shape, it is whatever upstream
declares. The authoring act is **registering the server**, so the person who did that is the
one whose draft these tools are. They see them; the team sees them once published, by the
same cascade as any other tool.

The alternative — born `published` — was rejected: registering a server for your own team
would then expose its whole tool surface org-wide with no review, which is precisely what
0080 exists to stop.

Nullable, no backfill: the ~70 existing `mcp_tool` rows are already `published` (they
predate 0080) and stay visible regardless. Inventing a creator for them would attribute
someone else's registration to whoever ran the migration.
"""
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'mcp_servers')
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'mcp_servers' AND column_name = 'created_by'
               ) THEN
                ALTER TABLE mcp_servers ADD COLUMN created_by VARCHAR(256);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'mcp_servers' AND column_name = 'created_by'
            ) THEN
                ALTER TABLE mcp_servers DROP COLUMN created_by;
            END IF;
        END $$;
        """
    )

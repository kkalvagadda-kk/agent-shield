"""Rename the read-only global role `viewer` to `consumer` in
user_team_assignments (docs/design/todo/rbac-design.md §2.1).

Completes the role-name normalization migration 0044 started (admin →
platform-admin, operator → contributor); `viewer` was left unchanged then and
is renamed here so all three canonical global roles use the same vocabulary as
the design doc.

Idempotent — the UPDATE matches only rows still holding the legacy value, so a
re-run is a no-op. Lowercase `consumer` is deliberate: every other role value
(platform-admin, contributor) is lowercase, and role comparisons in
rbac.ROLE_HIERARCHY, Keycloak realm roles, and Studio's ROLE_LEVEL are all
case-sensitive.

In-flight JWTs and un-migrated rows keep working regardless: rbac._LEGACY_MAP
maps viewer → consumer on read, so this migration is a data-cleanliness step
rather than a correctness prerequisite.

Numbered 0075, not 0072. `main` and the `mcp-tool-source` branch each forked a
0072 off parent 0071, and the MCP chain (0072 → 0073 → 0074) is the one already
applied — the live database is stamped 0074. Alembic tracks applied revisions by
ID string alone, so re-claiming 0072 here would make Alembic treat this
migration as "already applied" and silently skip its UPDATE. Same collision, and
same resolution, as ec48337: the unapplied chain renumbers off the real head.

Revision ID: 0075
Revises: 0074
"""
from alembic import op

revision = "0075"
down_revision = "0074"


def upgrade() -> None:
    op.execute("""
    UPDATE user_team_assignments SET role = 'consumer' WHERE role = 'viewer'
    """)


def downgrade() -> None:
    op.execute("""
    UPDATE user_team_assignments SET role = 'viewer' WHERE role = 'consumer'
    """)

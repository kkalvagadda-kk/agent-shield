"""Drop user_team_assignments.role's server_default.

Revision ID: 0079
Revises: 0078
Create Date: 2026-08-04

WHY
---
Migration 0013 created the column with server_default="operator". 0044 migrated the
DATA operator -> contributor and 0075 finished viewer -> consumer, but NEITHER touched
the default — so every insert that omitted `role` silently re-introduced the exact
legacy value those two migrations existed to remove. Decision 41, producer 2.

The column stays NOT NULL. After this migration an insert that omits `role` fails with
a NOT NULL violation instead of inventing one. Dropping NOT NULL as well would only
replace an invented role with a null one, which every reader would then have to guess
about — the point is that the writer must STATE it. Every in-repo inserter was fixed
first (FR-7, ordered before this migration on purpose):
    services/registry-api/routers/admin_users.py:96   states it
    scripts/e2e/suite-53-cost-tracking.sh:50          fixed
    scripts/e2e/suite-48-feedback-dashboard.sh:49     fixed
    scripts/e2e/suite-71-scheduled-e2e.sh:325         already states it (reviewer scope)

Idempotent: ALTER COLUMN ... DROP DEFAULT is a no-op when no default exists.
Data-preserving: touches no row.
"""
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE user_team_assignments ALTER COLUMN role DROP DEFAULT")


def downgrade() -> None:
    op.execute("ALTER TABLE user_team_assignments ALTER COLUMN role SET DEFAULT 'operator'")

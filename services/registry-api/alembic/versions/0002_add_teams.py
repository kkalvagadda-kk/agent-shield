"""Add teams table, team_id FK on agents, quarantined status

Creates the teams table (first-class entity for multi-team governance),
adds team_id FK on agents, and extends the agents.status CHECK constraint
to include 'quarantined' (needed for the emergency quarantine endpoint).

Tables changed:
  + teams             — new (name, namespace, keycloak_role_id)
  ~ agents.team_id    — new nullable FK → teams.id
  ~ agents.status     — CHECK constraint updated to add 'quarantined'

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create teams table ──────────────────────────────────────────────
    op.create_table(
        "teams",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("namespace", sa.String(128), nullable=False),
        sa.Column("keycloak_role_id", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="uq_teams_name"),
    )
    op.create_index("idx_teams_name", "teams", ["name"])
    op.create_index("idx_teams_keycloak_role_id", "teams", ["keycloak_role_id"])

    # ── 2. Add team_id FK to agents ───────────────────────────────────────
    op.add_column(
        "agents",
        sa.Column(
            "team_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id"),
            nullable=True,
        ),
    )
    op.create_index("idx_agents_team_id", "agents", ["team_id"])

    # ── 3. Extend agents.status CHECK to include 'quarantined' ───────────
    # Drop the old constraint first, then recreate with the new set.
    op.drop_constraint("ck_agents_status", "agents", type_="check")
    op.create_check_constraint(
        "ck_agents_status",
        "agents",
        "status IN ('active','archived','deprecated','quarantined')",
    )


def downgrade() -> None:
    # Restore old CHECK constraint
    op.drop_constraint("ck_agents_status", "agents", type_="check")
    op.create_check_constraint(
        "ck_agents_status",
        "agents",
        "status IN ('active','archived','deprecated')",
    )

    # Remove team_id FK
    op.drop_index("idx_agents_team_id", "agents")
    op.drop_column("agents", "team_id")

    # Drop teams table
    op.drop_index("idx_teams_keycloak_role_id", "teams")
    op.drop_index("idx_teams_name", "teams")
    op.drop_table("teams")

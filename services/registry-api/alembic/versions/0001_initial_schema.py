"""Initial schema — Phase 1 tables

Creates all AgentShield Phase 1 tables with indexes, CHECK constraints,
UNIQUE constraints, and the pgcrypto extension required for
gen_random_uuid().

Tables created (in FK dependency order):
  1. auth_configs        — no FKs
  2. mcp_servers         — FK → auth_configs
  3. agents              — no FKs
  4. tools               — FK → auth_configs, mcp_servers
  5. agent_versions      — FK → agents, workflows
  6. workflows           — no FKs (created before agent_versions)
  7. workflow_versions   — FK → workflows
  8. deployments         — FK → agents, agent_versions
  9. opa_decisions       — no FKs (created before approvals)
 10. approvals           — FK → agents, opa_decisions
 11. agent_policies      — FK → agents, agent_versions
 12. pii_mappings        — no FKs
 13. agent_tools         — FK → agents, tools (many-to-many join)

Revision ID: 0001
Revises:
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

# ---------------------------------------------------------------------------
# Revision identifiers — used by Alembic.
# ---------------------------------------------------------------------------
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Shorthand for the timezone-aware timestamp type used on every table.
_TSTZ = sa.TIMESTAMP(timezone=True)
_UUID = sa.dialects.postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # pgcrypto — provides gen_random_uuid() used as the default PK value.
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # 1. auth_configs
    # ------------------------------------------------------------------
    op.create_table(
        "auth_configs",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("k8s_secret_ref", sa.String(512), nullable=True),
        sa.Column("owner_team", sa.String(128), nullable=True),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "type IN ('api_key','oauth2','bearer','mtls')",
            name="ck_auth_configs_type",
        ),
    )

    # ------------------------------------------------------------------
    # 2. mcp_servers
    # ------------------------------------------------------------------
    op.create_table(
        "mcp_servers",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("server_url", sa.String(1024), nullable=False),
        sa.Column(
            "transport",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'streamable_http'"),
        ),
        sa.Column(
            "auth_config_id",
            _UUID,
            sa.ForeignKey("auth_configs.id"),
            nullable=True,
        ),
        sa.Column("owner_team", sa.String(128), nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'disconnected'"),
        ),
        sa.Column("last_synced_at", _TSTZ, nullable=True),
        sa.Column(
            "discovered_tool_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "transport IN ('streamable_http','stdio')",
            name="ck_mcp_servers_transport",
        ),
        sa.CheckConstraint(
            "status IN ('connected','disconnected','error')",
            name="ck_mcp_servers_status",
        ),
    )

    # ------------------------------------------------------------------
    # 3. agents
    # ------------------------------------------------------------------
    op.create_table(
        "agents",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "agent_type",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'sdk'"),
        ),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.CheckConstraint(
            "status IN ('active','archived','deprecated')",
            name="ck_agents_status",
        ),
        sa.CheckConstraint(
            "agent_type IN ('sdk','declarative')",
            name="ck_agents_type",
        ),
    )
    op.create_index("idx_agents_team", "agents", ["team"])
    op.create_index("idx_agents_status", "agents", ["status"])
    op.create_index("idx_agents_name", "agents", ["name"])

    # ------------------------------------------------------------------
    # 4. tools
    # ------------------------------------------------------------------
    op.create_table(
        "tools",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(256), nullable=False, unique=True),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column("tags", ARRAY(sa.String), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("input_schema", JSONB, nullable=True),
        sa.Column("output_schema", JSONB, nullable=True),
        sa.Column(
            "risk_level",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'low'"),
        ),
        sa.Column(
            "auth_config_id",
            _UUID,
            sa.ForeignKey("auth_configs.id"),
            nullable=True,
        ),
        sa.Column("owner_team", sa.String(128), nullable=True),
        sa.Column(
            "version",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        # HTTP tool fields
        sa.Column("http_method", sa.String(16), nullable=True),
        sa.Column("http_url", sa.String(2048), nullable=True),
        sa.Column("http_headers", JSONB, nullable=True),
        sa.Column("http_body_template", sa.Text, nullable=True),
        sa.Column("http_timeout_ms", sa.Integer, nullable=True),
        # MCP tool fields
        sa.Column(
            "mcp_server_id",
            _UUID,
            sa.ForeignKey("mcp_servers.id"),
            nullable=True,
        ),
        sa.Column("mcp_tool_name", sa.String(256), nullable=True),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "type IN ('native','http','mcp_tool')",
            name="ck_tools_type",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low','medium','high','critical')",
            name="ck_tools_risk_level",
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive','deprecated')",
            name="ck_tools_status",
        ),
    )
    # Partial index: only active tools — used by tool-lookup queries.
    op.create_index(
        "idx_tools_type_risk_active",
        "tools",
        ["type", "risk_level"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # ------------------------------------------------------------------
    # 5. workflows  (must precede agent_versions due to FK)
    # ------------------------------------------------------------------
    op.create_table(
        "workflows",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_workflows_status",
        ),
    )
    op.create_index("idx_workflows_team", "workflows", ["team"])
    op.create_index("idx_workflows_status", "workflows", ["status"])

    # ------------------------------------------------------------------
    # 6. workflow_versions
    # ------------------------------------------------------------------
    op.create_table(
        "workflow_versions",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workflow_id",
            _UUID,
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("change_summary", sa.Text, nullable=True),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.UniqueConstraint(
            "workflow_id", "version_number", name="uq_workflow_versions"
        ),
    )
    op.create_index(
        "idx_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"]
    )

    # ------------------------------------------------------------------
    # 7. agent_versions
    # ------------------------------------------------------------------
    op.create_table(
        "agent_versions",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id",
            _UUID,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("image_tag", sa.String(512), nullable=True),
        sa.Column(
            "workflow_id",
            _UUID,
            sa.ForeignKey("workflows.id"),
            nullable=True,
        ),
        sa.Column(
            "tools",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "eval_passed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("git_sha", sa.String(64), nullable=True),
        sa.Column("git_branch", sa.String(256), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.UniqueConstraint(
            "agent_id", "version_number", name="uq_agent_versions"
        ),
        sa.CheckConstraint(
            "status IN ('pending','eval_passed','eval_failed','deployed','retired')",
            name="ck_agent_versions_status",
        ),
    )
    op.create_index("idx_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_index("idx_agent_versions_status", "agent_versions", ["status"])
    # Composite index used for "latest passing version" queries.
    op.create_index(
        "idx_agent_versions_eval", "agent_versions", ["agent_id", "eval_passed"]
    )
    # Descending created_at per agent — covering index for history queries.
    op.create_index(
        "idx_agent_versions_agent_created_desc",
        "agent_versions",
        ["agent_id", "created_at"],
        postgresql_ops={"created_at": "DESC NULLS LAST"},
    )

    # ------------------------------------------------------------------
    # 8. deployments
    # ------------------------------------------------------------------
    op.create_table(
        "deployments",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id",
            _UUID,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "version_id",
            _UUID,
            sa.ForeignKey("agent_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "environment",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'production'"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "replicas",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("canary_percent", sa.Integer, nullable=True),
        sa.Column("k8s_namespace", sa.String(128), nullable=False),
        sa.Column("k8s_deployment_name", sa.String(256), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "deployed_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("terminated_at", _TSTZ, nullable=True),
        sa.Column("deployed_by", sa.String(256), nullable=True),
        sa.Column(
            "previous_version_id",
            _UUID,
            sa.ForeignKey("agent_versions.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "environment IN ('production','staging','canary')",
            name="ck_deployments_env",
        ),
        sa.CheckConstraint(
            "status IN ('pending','deploying','running','failed','rolled_back','terminated')",
            name="ck_deployments_status",
        ),
        sa.CheckConstraint(
            "canary_percent BETWEEN 0 AND 100",
            name="ck_canary_percent",
        ),
    )
    op.create_index("idx_deployments_agent_id", "deployments", ["agent_id"])
    op.create_index("idx_deployments_status", "deployments", ["status"])
    op.create_index(
        "idx_deployments_agent_status", "deployments", ["agent_id", "status"]
    )
    op.create_index(
        "idx_deployments_deployed_at_desc",
        "deployments",
        ["deployed_at"],
        postgresql_ops={"deployed_at": "DESC NULLS LAST"},
    )
    # Partial index: only active deployments — used by the deploy controller.
    op.create_index(
        "idx_deployments_agent_active",
        "deployments",
        ["agent_id"],
        postgresql_where=sa.text("status IN ('deploying','running')"),
    )

    # ------------------------------------------------------------------
    # 9. opa_decisions  (must precede approvals — FK from approvals.opa_decision_id)
    # ------------------------------------------------------------------
    op.create_table(
        "opa_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("deny_reason", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("decision IN ('allow', 'deny', 'require_approval')", name="ck_opa_decisions_decision"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_opa_decisions_agent", "opa_decisions", ["agent_name", "decided_at"])
    op.create_index("idx_opa_decisions_decision_time", "opa_decisions", ["decision", "decided_at"])
    op.create_index("idx_opa_decisions_thread", "opa_decisions", ["thread_id"])

    # ------------------------------------------------------------------
    # 10. approvals
    # ------------------------------------------------------------------
    op.create_table(
        "approvals",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id",
            _UUID,
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        # Denormalized — kept even if the agent row is later soft-deleted.
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(256), nullable=False),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column(
            "tool_args",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "risk_level",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'high'"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("reviewer_id", sa.String(256), nullable=True),
        sa.Column("reviewer_notes", sa.Text, nullable=True),
        sa.Column("trace_id", sa.String(256), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("opa_decision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("opa_decisions.id"), nullable=True),
        sa.Column("decision_at", _TSTZ, nullable=True),
        sa.Column("expires_at", _TSTZ, nullable=False),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        # Optimistic locking — first UPDATE with matching version wins.
        sa.Column(
            "version",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.CheckConstraint(
            "risk_level IN ('high','critical')",
            name="ck_approvals_risk",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','timed_out')",
            name="ck_approvals_status",
        ),
    )
    op.create_index("idx_approvals_agent_id", "approvals", ["agent_id"])
    op.create_index("idx_approvals_status", "approvals", ["status"])
    op.create_index("idx_approvals_thread_id", "approvals", ["thread_id"])
    op.create_index(
        "idx_approvals_created_at_desc", "approvals", ["created_at"]
    )
    # Partial index on expires_at — only pending rows matter for TTL checks.
    op.create_index(
        "idx_approvals_expires_at_pending",
        "approvals",
        ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ------------------------------------------------------------------
    # 11. agent_policies
    # ------------------------------------------------------------------
    op.create_table(
        "agent_policies",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # unique=True enforces one policy row per agent.
        sa.Column(
            "agent_id",
            _UUID,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("rego_policy", sa.Text, nullable=False),
        sa.Column(
            "tool_allowlist",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "risk_map",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("configmap_name", sa.String(256), nullable=True),
        sa.Column(
            "version",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "generated_at",
            _TSTZ,
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "generated_from_version_id",
            _UUID,
            sa.ForeignKey("agent_versions.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_agent_policies_agent_id", "agent_policies", ["agent_id"]
    )

    # ------------------------------------------------------------------
    # 12. pii_mappings
    # ------------------------------------------------------------------
    op.create_table(
        "pii_mappings",
        sa.Column(
            "id",
            _UUID,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", sa.String(256), nullable=False),
        sa.Column("agent_name", sa.String(128), nullable=False),
        # original_text is encrypted at the application layer before INSERT.
        sa.Column("original_text", sa.Text, nullable=False),
        sa.Column("anonymized_text", sa.Text, nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column(
            "created_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("expires_at", _TSTZ, nullable=False),
    )
    op.create_index("idx_pii_mappings_session_id", "pii_mappings", ["session_id"])
    op.create_index("idx_pii_mappings_expires_at", "pii_mappings", ["expires_at"])

    # ------------------------------------------------------------------
    # 13. agent_tools  (many-to-many join table)
    # ------------------------------------------------------------------
    op.create_table(
        "agent_tools",
        sa.Column(
            "agent_id",
            _UUID,
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tool_id",
            _UUID,
            sa.ForeignKey("tools.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("added_by", sa.String(256), nullable=True),
        sa.Column(
            "added_at", _TSTZ, nullable=False, server_default=sa.text("now()")
        ),
    )
    op.create_index("idx_agent_tools_tool_id", "agent_tools", ["tool_id"])


def downgrade() -> None:
    # Drop in reverse dependency order to avoid FK violations.
    op.drop_table("agent_tools")
    op.drop_table("pii_mappings")
    op.drop_table("agent_policies")
    op.drop_table("approvals")
    op.drop_table("opa_decisions")
    op.drop_table("deployments")
    op.drop_table("agent_versions")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
    op.drop_table("tools")
    op.drop_table("agents")
    op.drop_table("mcp_servers")
    op.drop_table("auth_configs")

    op.execute("DROP EXTENSION IF EXISTS pgcrypto")

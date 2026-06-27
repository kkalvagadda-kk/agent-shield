"""
AgentShield Registry API — SQLAlchemy 2.0 ORM models.

All tables live in the `agentshield` database.  Column names, types, CHECK
constraints, and indexes are kept in exact sync with
docs/plan/data-model.md and the Alembic migration in
alembic/versions/0001_initial_schema.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# ---------------------------------------------------------------------------
# Helper type aliases
# ---------------------------------------------------------------------------
_UUID = UUID(as_uuid=True)
_NOW = text("now()")
_GEN_UUID = text("gen_random_uuid()")
_TSTZ = TIMESTAMP(timezone=True)


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------
class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        Index("idx_teams_name", "name"),
        Index("idx_teams_keycloak_role_id", "keycloak_role_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    keycloak_role_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    # Relationships
    agents: Mapped[list[Agent]] = relationship(
        "Agent", back_populates="team_obj", foreign_keys="Agent.team_id"
    )


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------
class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','archived','deprecated','quarantined')",
            name="ck_agents_status",
        ),
        CheckConstraint(
            "agent_type IN ('sdk','declarative')",
            name="ck_agents_type",
        ),
        Index("idx_agents_team", "team"),
        Index("idx_agents_status", "status"),
        Index("idx_agents_name", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    team: Mapped[str] = mapped_column(String(128), nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("teams.id"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    agent_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'sdk'")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'")
    )

    # Relationships
    team_obj: Mapped[Team | None] = relationship(
        "Team", back_populates="agents", foreign_keys=[team_id]
    )
    versions: Mapped[list[AgentVersion]] = relationship(
        "AgentVersion", back_populates="agent", cascade="all, delete-orphan"
    )
    deployments: Mapped[list[Deployment]] = relationship(
        "Deployment",
        back_populates="agent",
        cascade="all, delete-orphan",
        foreign_keys="Deployment.agent_id",
    )
    approvals: Mapped[list[Approval]] = relationship(
        "Approval", back_populates="agent"
    )
    policy: Mapped[AgentPolicy | None] = relationship(
        "AgentPolicy", back_populates="agent", uselist=False, cascade="all, delete-orphan"
    )
    agent_tools: Mapped[list[AgentTool]] = relationship(
        "AgentTool", back_populates="agent", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# workflows  (declared before agent_versions to satisfy FK ordering)
# ---------------------------------------------------------------------------
class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_workflows_status",
        ),
        Index("idx_workflows_team", "team"),
        Index("idx_workflows_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    team: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'draft'")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'")
    )

    # Relationships
    versions: Mapped[list[WorkflowVersion]] = relationship(
        "WorkflowVersion", back_populates="workflow", cascade="all, delete-orphan"
    )
    agent_versions: Mapped[list[AgentVersion]] = relationship(
        "AgentVersion", back_populates="workflow"
    )


# ---------------------------------------------------------------------------
# workflow_versions
# ---------------------------------------------------------------------------
class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version_number", name="uq_workflow_versions"),
        Index("idx_workflow_versions_workflow_id", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Relationships
    workflow: Mapped[Workflow] = relationship("Workflow", back_populates="versions")


# ---------------------------------------------------------------------------
# agent_versions
# ---------------------------------------------------------------------------
class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version_number", name="uq_agent_versions"),
        CheckConstraint(
            "status IN ('pending','eval_passed','eval_failed','deployed','retired')",
            name="ck_agent_versions_status",
        ),
        Index("idx_agent_versions_agent_id", "agent_id"),
        Index("idx_agent_versions_status", "status"),
        # Composite index used for "latest passing version" queries
        Index("idx_agent_versions_eval", "agent_id", "eval_passed"),
        # Descending created_at per agent — covering index for history queries
        Index(
            "idx_agent_versions_agent_created_desc",
            "agent_id",
            "created_at",
            postgresql_ops={"created_at": "DESC NULLS LAST"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_tag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("workflows.id"),
        nullable=True,
    )
    tools: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    eval_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(256), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Relationships
    agent: Mapped[Agent] = relationship("Agent", back_populates="versions")
    workflow: Mapped[Workflow | None] = relationship(
        "Workflow", back_populates="agent_versions"
    )
    deployments: Mapped[list[Deployment]] = relationship(
        "Deployment",
        back_populates="version",
        foreign_keys="Deployment.version_id",
    )
    policy_snapshots: Mapped[list[AgentPolicy]] = relationship(
        "AgentPolicy",
        back_populates="generated_from_version",
        foreign_keys="AgentPolicy.generated_from_version_id",
    )


# ---------------------------------------------------------------------------
# deployments
# ---------------------------------------------------------------------------
class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint(
            "environment IN ('production','staging','canary')",
            name="ck_deployments_env",
        ),
        CheckConstraint(
            "status IN ('pending','deploying','running','failed','rolled_back','terminated')",
            name="ck_deployments_status",
        ),
        CheckConstraint(
            "canary_percent BETWEEN 0 AND 100",
            name="ck_canary_percent",
        ),
        Index("idx_deployments_agent_id", "agent_id"),
        Index("idx_deployments_status", "status"),
        Index("idx_deployments_agent_status", "agent_id", "status"),
        Index(
            "idx_deployments_deployed_at_desc",
            "deployed_at",
            postgresql_ops={"deployed_at": "DESC NULLS LAST"},
        ),
        # Partial index: active deployments only — used by deploy controller
        Index(
            "idx_deployments_agent_active",
            "agent_id",
            postgresql_where=text("status IN ('deploying','running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("agent_versions.id"),
        nullable=False,
    )
    environment: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'production'")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    replicas: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    canary_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    k8s_namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    k8s_deployment_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    terminated_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    deployed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("agent_versions.id"),
        nullable=True,
    )

    # Relationships
    agent: Mapped[Agent] = relationship(
        "Agent",
        back_populates="deployments",
        foreign_keys=[agent_id],
    )
    version: Mapped[AgentVersion] = relationship(
        "AgentVersion",
        back_populates="deployments",
        foreign_keys=[version_id],
    )
    previous_version: Mapped[AgentVersion | None] = relationship(
        "AgentVersion",
        foreign_keys=[previous_version_id],
    )


# ---------------------------------------------------------------------------
# opa_decisions
# ---------------------------------------------------------------------------
class OPADecision(Base):
    __tablename__ = "opa_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)  # allow | deny | require_approval
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    deny_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    trace_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "decision IN ('allow', 'deny', 'require_approval')",
            name="ck_opa_decisions_decision",
        ),
        Index("idx_opa_decisions_agent", "agent_name", "decided_at"),
        Index("idx_opa_decisions_decision_time", "decision", "decided_at"),
    )


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------
class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "risk_level IN ('high','critical')",
            name="ck_approvals_risk",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','timed_out')",
            name="ck_approvals_status",
        ),
        Index("idx_approvals_agent_id", "agent_id"),
        Index("idx_approvals_status", "status"),
        Index("idx_approvals_thread_id", "thread_id"),
        Index("idx_approvals_created_at_desc", "created_at"),
        # Partial index on expires_at — only rows still pending matter for TTL
        Index(
            "idx_approvals_expires_at_pending",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("agents.id"),
        nullable=False,
    )
    # Denormalized — kept even if the agent row is later soft-deleted / renamed
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    team: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(256), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(256), nullable=False)
    tool_args: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    risk_level: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'high'")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    reviewer_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decision_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_TSTZ, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    # Optimistic locking version — first UPDATE with matching version wins
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Links to PII mapping for reviewer de-anonymization
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID, nullable=True)
    # FK to the OPA decision that triggered this approval request
    opa_decision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey("opa_decisions.id"),
        nullable=True,
    )

    # Relationships
    agent: Mapped[Agent] = relationship("Agent", back_populates="approvals")
    opa_decision: Mapped[Optional[OPADecision]] = relationship("OPADecision")


# ---------------------------------------------------------------------------
# agent_policies
# ---------------------------------------------------------------------------
class AgentPolicy(Base):
    __tablename__ = "agent_policies"
    __table_args__ = (
        Index("idx_agent_policies_agent_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rego_policy: Mapped[str] = mapped_column(Text, nullable=False)
    tool_allowlist: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    risk_map: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    configmap_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    generated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    generated_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("agent_versions.id"),
        nullable=True,
    )

    # Relationships
    agent: Mapped[Agent] = relationship("Agent", back_populates="policy")
    generated_from_version: Mapped[AgentVersion | None] = relationship(
        "AgentVersion",
        back_populates="policy_snapshots",
        foreign_keys=[generated_from_version_id],
    )


# ---------------------------------------------------------------------------
# pii_mappings
# ---------------------------------------------------------------------------
class PiiMapping(Base):
    __tablename__ = "pii_mappings"
    __table_args__ = (
        Index("idx_pii_mappings_session_id", "session_id"),
        Index("idx_pii_mappings_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    session_id: Mapped[str] = mapped_column(String(256), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # original_text is encrypted at the application layer before INSERT
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    anonymized_text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    expires_at: Mapped[datetime] = mapped_column(_TSTZ, nullable=False)


# ---------------------------------------------------------------------------
# auth_configs  (referenced by Tool and MCPServer)
# ---------------------------------------------------------------------------
class AuthConfig(Base):
    __tablename__ = "auth_configs"
    __table_args__ = (
        CheckConstraint(
            "type IN ('api_key','oauth2','bearer','mtls')",
            name="ck_auth_configs_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Reference to K8s Secret; never echoed in API responses
    k8s_secret_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    owner_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    # Relationships
    tools: Mapped[list[Tool]] = relationship(
        "Tool",
        back_populates="auth_config",
        foreign_keys="Tool.auth_config_id",
    )
    mcp_servers: Mapped[list[MCPServer]] = relationship(
        "MCPServer", back_populates="auth_config"
    )


# ---------------------------------------------------------------------------
# mcp_servers  (declared before Tool to satisfy FK ordering)
# ---------------------------------------------------------------------------
class MCPServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        CheckConstraint(
            "transport IN ('streamable_http','stdio')",
            name="ck_mcp_servers_transport",
        ),
        CheckConstraint(
            "status IN ('connected','disconnected','error')",
            name="ck_mcp_servers_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'streamable_http'")
    )
    auth_config_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("auth_configs.id"),
        nullable=True,
    )
    owner_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'disconnected'")
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    discovered_tool_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    # Relationships
    auth_config: Mapped[AuthConfig | None] = relationship(
        "AuthConfig", back_populates="mcp_servers"
    )
    tools: Mapped[list[Tool]] = relationship(
        "Tool", back_populates="mcp_server", foreign_keys="Tool.mcp_server_id"
    )


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
class Tool(Base):
    __tablename__ = "tools"
    __table_args__ = (
        CheckConstraint(
            "type IN ('native','http','mcp_tool')",
            name="ck_tools_type",
        ),
        CheckConstraint(
            "risk_level IN ('low','medium','high','critical')",
            name="ck_tools_risk_level",
        ),
        CheckConstraint(
            "status IN ('active','inactive','deprecated')",
            name="ck_tools_status",
        ),
        Index("idx_tools_type_risk_active", "type", "risk_level",
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'low'")
    )
    auth_config_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("auth_configs.id"),
        nullable=True,
    )
    owner_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'active'")
    )
    # HTTP tool fields
    http_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    http_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    http_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    http_body_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # MCP tool fields
    mcp_server_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("mcp_servers.id"),
        nullable=True,
    )
    mcp_tool_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    # Relationships
    auth_config: Mapped[AuthConfig | None] = relationship(
        "AuthConfig",
        back_populates="tools",
        foreign_keys=[auth_config_id],
    )
    mcp_server: Mapped[MCPServer | None] = relationship(
        "MCPServer",
        back_populates="tools",
        foreign_keys=[mcp_server_id],
    )
    agent_tools: Mapped[list[AgentTool]] = relationship(
        "AgentTool", back_populates="tool", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# agent_tools  (many-to-many join table)
# ---------------------------------------------------------------------------
class AgentTool(Base):
    __tablename__ = "agent_tools"
    __table_args__ = (
        Index("idx_agent_tools_tool_id", "tool_id"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("tools.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    # Relationships
    agent: Mapped[Agent] = relationship("Agent", back_populates="agent_tools")
    tool: Mapped[Tool] = relationship("Tool", back_populates="agent_tools")


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------
class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("name", "team", name="uq_skills_name_team"),
        Index("idx_skills_team", "team"),
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, server_default=_GEN_UUID)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    team: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_ids: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(_TSTZ, nullable=False, server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(_TSTZ, nullable=False, server_default=_NOW)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)


# ---------------------------------------------------------------------------
# Explicit __all__ so Alembic env.py can do `from models import Base`
# and pick up all mapped tables via Base.metadata.
# ---------------------------------------------------------------------------
__all__ = [
    "Base",
    "Team",
    "Agent",
    "AgentVersion",
    "Deployment",
    "OPADecision",
    "Approval",
    "AgentPolicy",
    "Workflow",
    "WorkflowVersion",
    "PiiMapping",
    "AuthConfig",
    "MCPServer",
    "Tool",
    "AgentTool",
    "Skill",
]

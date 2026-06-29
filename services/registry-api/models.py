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
    llm_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    llm_provider: Mapped[Optional["LLMProvider"]] = relationship(
        "LLMProvider", back_populates="agents", foreign_keys=[llm_provider_id]
    )

    # Authorization model fields (Phase 9.1)
    # agent_class determines which OPA flow applies at runtime
    agent_class: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    # publish_status tracks authoring lifecycle; separate from operational status
    publish_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'private'")
    )

    identities: Mapped[list["AgentIdentity"]] = relationship(
        "AgentIdentity", back_populates="agent", cascade="all, delete-orphan",
        foreign_keys="AgentIdentity.agent_name",
        primaryjoin="Agent.name == AgentIdentity.agent_name",
    )


# ---------------------------------------------------------------------------
# agent_identities  (machine identity provisioned at first deploy)
# ---------------------------------------------------------------------------
class AgentIdentity(Base):
    __tablename__ = "agent_identities"
    __table_args__ = (
        Index("idx_agent_identities_agent_name", "agent_name"),
        # Only one active (non-revoked) identity per SA subject
        Index(
            "idx_agent_identities_sa_subject_active",
            "sa_subject",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_name: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agents.name", ondelete="CASCADE"),
        nullable=False,
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Full K8s SA subject: system:serviceaccount:{namespace}:{sa-name}
    sa_subject: Mapped[str] = mapped_column(String(512), nullable=False)
    sa_namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    provisioned_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    revoked_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)

    agent: Mapped[Agent] = relationship(
        "Agent", back_populates="identities",
        foreign_keys=[agent_name],
        primaryjoin="AgentIdentity.agent_name == Agent.name",
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
            "status IN ('pending','deploying','running','failed','rolled_back','terminated','gate_failed')",
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
    llm_secret_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    llm_env_keys: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    llm_provider_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_provider_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
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
    # Context: 'production' (default, routed via approval_authority) or 'playground' (self-approve)
    context: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'production'")
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
            "type IN ('native','http','mcp_tool','python')",
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
    # Python tool fields
    python_code: Mapped[str | None] = mapped_column(Text, nullable=True)
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
# llm_providers
# ---------------------------------------------------------------------------
class LLMProvider(Base):
    __tablename__ = "llm_providers"
    __table_args__ = (
        UniqueConstraint("name", "team", name="uq_llm_providers_name_team"),
        CheckConstraint(
            "provider IN ('anthropic','bedrock')",
            name="ck_llm_providers_provider",
        ),
        Index("idx_llm_providers_team", "team"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    default_model: Mapped[str] = mapped_column(String(256), nullable=False)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    team: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    # Relationships
    agents: Mapped[list[Agent]] = relationship(
        "Agent", back_populates="llm_provider", foreign_keys="Agent.llm_provider_id"
    )


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
# publish_requests  (Phase 9.2 — asset lifecycle)
# ---------------------------------------------------------------------------
class PublishRequest(Base):
    __tablename__ = "publish_requests"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('tool','agent','skill','workflow')",
            name="ck_publish_requests_asset_type",
        ),
        CheckConstraint(
            "status IN ('pending_review','approved','rejected')",
            name="ck_publish_requests_status",
        ),
        CheckConstraint(
            "highest_risk_level IN ('low','medium','high')",
            name="ck_publish_requests_risk_level",
        ),
        Index("idx_publish_requests_asset", "asset_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_by: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending_review'")
    )
    highest_risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    dependency_declaration: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# asset_grants  (Phase 9.2 — asset lifecycle)
# ---------------------------------------------------------------------------
class AssetGrant(Base):
    __tablename__ = "asset_grants"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('tool','agent','skill','workflow')",
            name="ck_asset_grants_asset_type",
        ),
        Index("idx_asset_grants_lookup", "asset_id", "grantee_team", "revoked_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    grantee_team: Mapped[str] = mapped_column(Text, nullable=False)
    granted_by: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    expires_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)


# ---------------------------------------------------------------------------
# grant_audit  (Phase 9.2 — append-only audit log)
# ---------------------------------------------------------------------------
class GrantAudit(Base):
    __tablename__ = "grant_audit"
    __table_args__ = (
        CheckConstraint(
            "action IN ('created','revoked','expired')",
            name="ck_grant_audit_action",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    admin_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    grantee_team: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )


# ---------------------------------------------------------------------------
# approval_authority  (Phase 9.2 — per-resource approver registry)
# ---------------------------------------------------------------------------
class ApprovalAuthority(Base):
    __tablename__ = "approval_authority"
    __table_args__ = (
        CheckConstraint(
            "resource_type IN ('agent','tool','skill')",
            name="ck_approval_authority_resource_type",
        ),
        CheckConstraint(
            "approver_user_id IS NOT NULL OR approver_role IS NOT NULL",
            name="ck_approval_authority_approver",
        ),
        Index("idx_approval_authority_resource", "resource_type", "resource_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    approver_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    approver_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_by: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    revoked_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)


# ---------------------------------------------------------------------------
# playground_runs  (Phase 10.1 — per-user agent test runs)
# ---------------------------------------------------------------------------
class PlaygroundRun(Base):
    __tablename__ = "playground_runs"
    __table_args__ = (
        Index("idx_playground_runs_user_id", "user_id"),
        Index("idx_playground_runs_agent", "agent_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("agent_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    context: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'playground'")
    )
    sandbox: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    input_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'running'")
    )


# ---------------------------------------------------------------------------
# playground_datasets  (Phase 10.1 — named collections of test items)
# ---------------------------------------------------------------------------
class PlaygroundDataset(Base):
    __tablename__ = "playground_datasets"
    __table_args__ = (
        Index("idx_playground_datasets_owner", "owner_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    owner_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    items: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )


# ---------------------------------------------------------------------------
# eval_runs  (Phase 10.3 — evaluation run metadata)
# ---------------------------------------------------------------------------
class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("idx_eval_runs_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, nullable=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("playground_datasets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    total_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )


# ---------------------------------------------------------------------------
# eval_run_results  (Phase 10.3 — per-item evaluation results)
# ---------------------------------------------------------------------------
class EvalRunResult(Base):
    __tablename__ = "eval_run_results"
    __table_args__ = (
        Index("idx_eval_run_results_eval_run_id", "eval_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    eval_run_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    dataset_item_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    input_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_score: Mapped[float | None] = mapped_column(nullable=True)
    judge_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )


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
    "LLMProvider",
    "Skill",
    "PublishRequest",
    "AssetGrant",
    "GrantAudit",
    "ApprovalAuthority",
    "PlaygroundRun",
    "PlaygroundDataset",
    "EvalRun",
    "EvalRunResult",
]

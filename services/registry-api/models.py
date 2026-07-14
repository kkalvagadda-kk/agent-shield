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
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

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
        CheckConstraint(
            "agent_class IN ('user_delegated','daemon')",
            name="ck_agents_agent_class",
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
    created_by: Mapped[str] = mapped_column(
        String(256), nullable=False, server_default=text("'system'")
    )
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

    # Execution shape: reactive (ephemeral, in-request, synchronous — no cross-time
    # persistence) or durable (checkpointed — parks + resumes across time, survives restart).
    execution_shape: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'reactive'")
    )
    # Whether cross-session memory is enabled for this agent
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # Authorization model fields (Phase 9.1)
    # agent_class determines which OPA flow applies at runtime.
    # NOT NULL + server_default + CHECK: an executable's class can never be absent or garbage,
    # so the deploy-time coalesce is deletable (WS-0 M3) and OPA's class-based flow can trust it.
    agent_class: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'user_delegated'")
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
    # Sandbox deployment this identity belongs to (FK sandbox `deployments`).
    # Production identities use `production_deployment_id` instead — the two are
    # mutually exclusive per row (see sandbox-production-parity-architecture.md).
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Production deployment this identity belongs to. Production deployments live
    # in `production_deployments` (a different table), so they need their own FK
    # column — without it, production SA subjects never enter the OPA bundle and
    # every production tool call fails closed (agent_unauthenticated).
    production_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("production_deployments.id", ondelete="SET NULL"),
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
class AgentGraph(Base):
    # Renamed from Workflow (Decision 22): this is a SINGLE declarative agent's
    # canvas graph. The name "workflows" now belongs to the composite executable
    # (CompositeWorkflow below).
    __tablename__ = "agent_graphs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_agent_graphs_status",
        ),
        Index("idx_agent_graphs_team", "team"),
        Index("idx_agent_graphs_status", "status"),
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
    versions: Mapped[list["AgentGraphVersion"]] = relationship(
        "AgentGraphVersion", back_populates="agent_graph", cascade="all, delete-orphan"
    )
    agent_versions: Mapped[list["AgentVersion"]] = relationship(
        "AgentVersion", back_populates="agent_graph"
    )


# ---------------------------------------------------------------------------
# agent_graph_versions  (renamed from workflow_versions — Decision 22)
# ---------------------------------------------------------------------------
class AgentGraphVersion(Base):
    __tablename__ = "agent_graph_versions"
    __table_args__ = (
        UniqueConstraint("agent_graph_id", "version_number", name="uq_agent_graph_versions"),
        Index("idx_agent_graph_versions_agent_graph_id", "agent_graph_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_graph_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("agent_graphs.id", ondelete="CASCADE"),
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
    agent_graph: Mapped[AgentGraph] = relationship("AgentGraph", back_populates="versions")


# ---------------------------------------------------------------------------
# workflows (COMPOSITE executable) + workflow_members — Decision 22 (NEW)
# ---------------------------------------------------------------------------
class CompositeWorkflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint(
            "execution_shape IN ('reactive','durable')",
            name="ck_workflows_execution_shape",
        ),
        CheckConstraint(
            "agent_class IN ('user_delegated','daemon')",
            name="ck_workflows_agent_class",
        ),
        CheckConstraint(
            "orchestration IN ('sequential','supervisor','handoff','conditional')",
            name="ck_workflows_orchestration",
        ),
        CheckConstraint(
            "status IN ('draft','published','archived')",
            name="ck_workflows_status",
        ),
        Index("idx_workflows_team", "team"),
        Index("idx_workflows_status", "status"),
        UniqueConstraint("name", "team", name="uq_workflows_name_team"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    team: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_shape: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'durable'")
    )
    # agent_class: the workflow run's authority (D1). user_delegated → invoking user;
    # daemon → the workflow's service identity. Members inherit via actor_chain (WS-2).
    agent_class: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'user_delegated'")
    )
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    orchestration: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'sequential'")
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'draft'")
    )
    publish_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'private'")
    )
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    members: Mapped[list["WorkflowMember"]] = relationship(
        "WorkflowMember", back_populates="workflow", cascade="all, delete-orphan"
    )
    edges: Mapped[list["WorkflowEdge"]] = relationship(
        "WorkflowEdge", back_populates="workflow", cascade="all, delete-orphan"
    )


class WorkflowMember(Base):
    __tablename__ = "workflow_members"
    __table_args__ = (
        Index("idx_workflow_members_agent_id", "agent_id"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("workflows.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("agents.id"), primary_key=True
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    routing: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    added_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    workflow: Mapped[CompositeWorkflow] = relationship(
        "CompositeWorkflow", back_populates="members"
    )
    agent: Mapped["Agent"] = relationship("Agent")


class WorkflowEdge(Base):
    """A directed edge between two member agents of a composite workflow.

    Edges are a cross-member construct (source → target), so they live in their
    own table rather than in `workflow_members.routing`. `condition` drives
    conditional-routing / handoff orchestration; blank/NULL = default (fallback)
    edge. See `workflow_orchestrator.evaluate_condition` for the DSL.
    """

    __tablename__ = "workflow_edges"
    __table_args__ = (
        Index("idx_workflow_edges_workflow_id", "workflow_id"),
        Index("idx_workflow_edges_source", "source_agent_id"),
        Index("idx_workflow_edges_target", "target_agent_id"),
        UniqueConstraint(
            "workflow_id", "source_agent_id", "target_agent_id",
            name="uq_workflow_edges_src_tgt",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    source_agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("agents.id"), nullable=False
    )
    target_agent_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("agents.id"), nullable=False
    )
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    workflow: Mapped[CompositeWorkflow] = relationship(
        "CompositeWorkflow", back_populates="edges"
    )


# ---------------------------------------------------------------------------
# workflow_versions (snapshot of a workflow's composition — Decision 22b)
# ---------------------------------------------------------------------------
class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version_number", name="uq_workflow_versions"),
        Index("idx_workflow_versions_workflow_id", "workflow_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, server_default=_GEN_UUID)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    members: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    edges: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    orchestration: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'sequential'"))
    execution_shape: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'durable'"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    eval_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(_TSTZ, nullable=False, server_default=_NOW)
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    workflow: Mapped[CompositeWorkflow] = relationship("CompositeWorkflow", backref="versions")


# ---------------------------------------------------------------------------
# workflow_deployments (logical deployment of a workflow version)
# ---------------------------------------------------------------------------
class WorkflowDeployment(Base):
    __tablename__ = "workflow_deployments"
    __table_args__ = (
        CheckConstraint(
            "environment IN ('production','staging','canary','sandbox')",
            name="ck_workflow_deployments_env",
        ),
        CheckConstraint(
            "status IN ('pending','deploying','running','failed','rolled_back','terminated','gate_failed','suspending','suspended','terminating')",
            name="ck_workflow_deployments_status",
        ),
        Index("idx_workflow_deployments_workflow_id", "workflow_id"),
        Index("idx_workflow_deployments_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, server_default=_GEN_UUID)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("workflow_versions.id"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    environment: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'sandbox'"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    replicas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    ttl_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deployed_at: Mapped[datetime] = mapped_column(_TSTZ, nullable=False, server_default=_NOW)
    suspended_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("workflow_versions.id"), nullable=True
    )

    workflow: Mapped[CompositeWorkflow] = relationship("CompositeWorkflow")
    version: Mapped[WorkflowVersion] = relationship("WorkflowVersion", foreign_keys=[version_id])


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
    agent_graph_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("agent_graphs.id"),
        nullable=True,
    )
    tools: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    eval_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    adversarial_eval_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_branch: Mapped[str | None] = mapped_column(String(256), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, server_default=None
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Relationships
    agent: Mapped[Agent] = relationship("Agent", back_populates="versions")
    agent_graph: Mapped["AgentGraph | None"] = relationship(
        "AgentGraph", back_populates="agent_versions"
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
            "environment IN ('production','staging','canary','sandbox')",
            name="ck_deployments_env",
        ),
        CheckConstraint(
            "status IN ('pending','deploying','running','failed','rolled_back','terminated','gate_failed','suspending','suspended','terminating')",
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
    # Human-facing deployment name (e.g. "simple-qa-bd28") — the primary
    # identifier in the deployment-overview UX. Nullable for legacy rows.
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    terminated_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    # Optional auto-terminate window (hours since deployed_at); NULL = never.
    ttl_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    # The LLM's stated reason for the tool call (best-effort; may be null).
    # Surfaced to the reviewer as the "why" on every approval surface.
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Playground approvals must not trigger Slack/on-call notifications.
    # Set automatically to false when context='playground' at creation time.
    notify_slack: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
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
    # Reference to K8s Secret; never echoed in API responses. The K8s secret is a
    # runtime materialization — the DURABLE source of truth is credentials_encrypted
    # below, so a Postgres backup captures the credential (K8s secrets are NOT in
    # the backup and are wiped on cluster loss). Mirrors LLMProvider.credentials_encrypted.
    k8s_secret_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Fernet-encrypted credentials dict (AGENTSHIELD_ENCRYPTION_KEY). Nullable for
    # legacy rows created before this column existed. Never echoed in API responses.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    publish_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'published'")
    )
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
    publish_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'published'")
    )
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
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)


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
    # Sandbox deployment this run targeted (deployment-pinned chat). Lets the HITL
    # console show which deployment/environment a pending approval came from.
    # FK to the sandbox `deployments` table — production runs use
    # `production_deployment_id` (a different table); never cross them.
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Production deployment this run targeted. Production deployments live in
    # `production_deployments` (published-artifact model), not `deployments`, so
    # they need their own FK column — mirrors AgentRun.production_deployment_id.
    production_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("production_deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Conversation grouping key (many per-turn runs share one session). Used to
    # scope the sandbox self-approve panel to a conversation and, later, to
    # reload persisted conversations.
    session_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Requester provenance captured from the JWT at chat start, surfaced in the
    # HITL console (username instead of raw sub; the requester's own team).
    requested_by_username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    requested_by_team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'playground'")
    )
    sandbox: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    input_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_shape: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'reactive'")
    )
    input_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trigger_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trigger_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'running'")
    )
    judge_score: Mapped[float | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    judge_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    judge_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Human thumbs feedback: 1 = up, -1 = down, NULL = none given. Written by
    # POST /playground/runs/{id}/feedback (alongside the Langfuse score push) so
    # the observability dashboard's feedback-ratio panel can aggregate locally
    # instead of doing a live Langfuse call.
    user_feedback: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# playground_datasets  (Phase 10.1 — named collections of test items)
# ---------------------------------------------------------------------------
class PlaygroundDataset(Base):
    __tablename__ = "playground_datasets"
    __table_args__ = (
        Index("idx_playground_datasets_owner", "owner_user_id"),
        CheckConstraint(
            "mode IN ('reactive','durable','scheduled','webhook','workflow')",
            name="ck_playground_datasets_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    owner_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    items: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    # Eval v2 E-0: authoring discriminator — which per-mode item schema this
    # dataset's rows follow. Default 'reactive' (back-compat).
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'reactive'")
    )
    # Lets the item schema evolve without a data migration.
    schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
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
        CheckConstraint(
            "mode IN ('reactive','durable','scheduled','webhook','workflow')",
            name="ck_eval_runs_mode",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, nullable=True
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
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
    sandbox_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("deployments.id", ondelete="SET NULL"), nullable=True
    )
    workflow_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("workflow_deployments.id", ondelete="SET NULL"), nullable=True
    )
    workflow_version_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("workflow_versions.id", ondelete="SET NULL"), nullable=True
    )
    # Eval v2 E-0: interpretation discriminator — resolved from the executable
    # at launch; validated == dataset.mode. Default 'reactive' (back-compat).
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'reactive'")
    )
    # Optional per-dimension weights for the composite score.
    dimension_weights: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Optional per-run override of the global EVAL_PASS_THRESHOLD.
    pass_threshold: Mapped[float | None] = mapped_column(
        Numeric(4, 3), nullable=True
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
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Eval v2 E-0: composite-score evidence store (all nullable; old rows read
    # as response-only). `judge_score` above stays the composite (gate input).
    dimension_scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    eval_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trigger_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    matched: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Soft FK -> playground_runs.id (no DB constraint): deep-link to the run tree.
    run_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )


# ---------------------------------------------------------------------------
# agent_runs — central invocation primitive for all agent calls
# ---------------------------------------------------------------------------
class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','blocked','awaiting_approval','cancelled')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "context IN ('production','playground')",
            name="ck_agent_runs_context",
        ),
        CheckConstraint(
            "trigger_type IN ('manual','api','schedule','webhook','workflow')",
            name="ck_agent_runs_trigger_type",
        ),
        Index("ix_agent_runs_agent_name", "agent_name"),
        Index("ix_agent_runs_session_id", "session_id"),
        Index("ix_agent_runs_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_name: Mapped[str] = mapped_column(String(256), nullable=False)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    input: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(nullable=True)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="running"
    )
    context: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="production"
    )
    started_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    completed_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    # Orchestration fields (Phase 1 execution-modes)
    trigger_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True, server_default=text("'manual'")
    )
    run_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("agent_runs.id"), nullable=True
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    trigger_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    # Set only on a PARENT composite-workflow run (Decision 22). Child agent
    # runs within a workflow leave this NULL and set parent_run_id instead.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True
    )
    trigger_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Durable orchestrator checkpoint for a PARENT composite-workflow run. Set
    # when a member pauses for HITL approval (parent → 'awaiting_approval') so the
    # run tree can resume and advance after the approval is decided. Shape:
    # {mode, order[], next_index, team, workflow_id}. NULL when not paused.
    orchestrator_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    production_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("production_deployments.id", ondelete="SET NULL"), nullable=True
    )
    # Scopes a playground run to the sandbox deployment that produced it
    # (mirror of production_deployment_id for the sandbox context).
    sandbox_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("deployments.id"), nullable=True
    )
    workflow_deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("workflow_deployments.id"), nullable=True
    )
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    # run_steps.run_id has no DB-level FK (it's a polymorphic reference to either
    # agent_runs or playground_runs — see migration 0023), so the join condition
    # must be spelled out with an explicit foreign() annotation.
    steps: Mapped[list["RunStep"]] = relationship(
        "RunStep",
        back_populates="run",
        primaryjoin="AgentRun.id == foreign(RunStep.run_id)",
        cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# run_steps  (durable run step tracking)
# ---------------------------------------------------------------------------
class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_number", name="uq_run_steps_run_step"),
        CheckConstraint(
            "status IN ('pending','running','completed','failed','awaiting_approval','cancelled')",
            name="ck_run_steps_status",
        ),
        Index("idx_run_steps_run_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    # Polymorphic reference: run_id may point at either agent_runs.id (production
    # durable runs) or playground_runs.id (playground durable runs). No DB-level
    # FK is enforced because it cannot reference two tables; integrity is handled
    # in the application layer. Kept indexed for lookup performance.
    run_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        nullable=False,
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'pending'")
    )
    started_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("approvals.id"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["AgentRun"] = relationship(
        "AgentRun",
        back_populates="steps",
        primaryjoin="AgentRun.id == foreign(RunStep.run_id)",
    )
    approval: Mapped[Optional["Approval"]] = relationship("Approval")


# ---------------------------------------------------------------------------
# agent_triggers  (schedule + webhook trigger config)
# ---------------------------------------------------------------------------
class AgentTrigger(Base):
    __tablename__ = "agent_triggers"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('schedule','webhook')",
            name="ck_agent_triggers_type",
        ),
        Index("idx_agent_triggers_agent", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
    )
    # A trigger targets EITHER an agent OR a composite workflow (Decision 22).
    # DB CHECK ck_agent_triggers_target enforces exactly one of the two is set.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True
    )
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timezone: Mapped[str | None] = mapped_column(
        String(50), nullable=True, server_default=text("'UTC'")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filter_conditions: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    # Per-schedule job parameters (Decision 24 follow-on): the JSON "job spec"
    # fed to the agent as its input on each fire. Lets one deployed agent serve
    # many scheduled jobs with different params. Schedule triggers only; NULL for
    # webhooks (their payload is the inbound event).
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Failure alerting (Phase 8): notify alert_email when a run for this
    # trigger completes with status=failed. alert_on_failure gates it.
    alert_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alert_on_failure: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    agent: Mapped["Agent"] = relationship("Agent")


# ---------------------------------------------------------------------------
# agent_events — inbound webhook log (Phase 9 event gateway)
# ---------------------------------------------------------------------------

class AgentEvent(Base):
    __tablename__ = "agent_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('matched','filtered','rejected')",
            name="ck_agent_events_status",
        ),
        Index("idx_agent_events_trigger_received", "trigger_id", "received_at"),
        Index("idx_agent_events_agent_received", "agent_name", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    # trigger_id is a soft FK (ON DELETE SET NULL) so events survive trigger deletion
    trigger_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("agent_triggers.id", ondelete="SET NULL"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    filter_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # run_id references agent_runs but stays FK-free (matched events only)
    run_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    # workflow_id is set when the event is matched to a workflow trigger (nullable)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    source_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    trigger: Mapped["AgentTrigger"] = relationship("AgentTrigger")


# ---------------------------------------------------------------------------
# agent_memory — conversation history + semantic search
# ---------------------------------------------------------------------------

class AgentMemory(Base):
    __tablename__ = "agent_memory"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','system','tool')",
            name="ck_agent_memory_role",
        ),
        Index("ix_agent_memory_thread_msg", "thread_id", "message_index"),
        Index("ix_agent_memory_agent_team", "agent_name", "team"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    agent_name: Mapped[str] = mapped_column(String(256), nullable=False)
    team: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(256), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_index: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    expires_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)


# ---------------------------------------------------------------------------
# published_artifacts / published_versions / production_deployments
# (Decision: Production Artifact Isolation)
# ---------------------------------------------------------------------------
class PublishedArtifact(Base):
    __tablename__ = "published_artifacts"
    __table_args__ = (
        CheckConstraint(
            "type IN ('agent','workflow','tool','skill')",
            name="ck_published_artifacts_type",
        ),
        UniqueConstraint("name", "type", name="uq_published_artifacts_name_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    team: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    versions: Mapped[list["PublishedVersion"]] = relationship(
        back_populates="artifact", order_by="PublishedVersion.promoted_at"
    )
    deployments: Mapped[list["ProductionDeployment"]] = relationship(
        back_populates="artifact"
    )


class PublishedVersion(Base):
    __tablename__ = "published_versions"
    __table_args__ = (
        Index("idx_published_versions_artifact", "artifact_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("published_artifacts.id"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'")
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, nullable=True)
    promoted_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    promoted_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    artifact: Mapped["PublishedArtifact"] = relationship(back_populates="versions")


class ProductionDeployment(Base):
    __tablename__ = "production_deployments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','deploying','running','suspended','failed','terminating','terminated','rolled_back','gate_failed')",
            name="production_deployments_status_check",
        ),
        Index("idx_production_deployments_artifact", "artifact_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("published_artifacts.id"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("published_versions.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    namespace: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )

    artifact: Mapped["PublishedArtifact"] = relationship(back_populates="deployments")
    version: Mapped["PublishedVersion"] = relationship()


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
    "AgentGraph",
    "AgentGraphVersion",
    "CompositeWorkflow",
    "WorkflowMember",
    "WorkflowEdge",
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
    "AgentRun",
    "RunStep",
    "AgentTrigger",
    "AgentEvent",
    "AgentMemory",
    "PublishedArtifact",
    "PublishedVersion",
    "ProductionDeployment",
]

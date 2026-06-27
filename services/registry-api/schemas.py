"""
AgentShield Registry API — Pydantic v2 request / response schemas.

These schemas map 1-to-1 with the OpenAPI contract in
docs/plan/contracts/registry-api.yaml.  All response schemas carry
`model_config = ConfigDict(from_attributes=True)` so they can be
constructed directly from SQLAlchemy ORM instances.

Naming convention
-----------------
  <Resource>Create   — POST / PUT request body
  <Resource>Update   — PATCH request body (all fields optional)
  <Resource>Response — serialised representation returned to callers
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Generic pagination wrapper
# ---------------------------------------------------------------------------
T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Wrapper used for collection endpoints that support limit/offset."""

    items: list[T]
    total: int

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    database: str | None = None  # "connected" | "disconnected"
    version: str | None = None


# ---------------------------------------------------------------------------
# ToolDefinition  (embedded inside AgentVersion)
# ---------------------------------------------------------------------------
class ToolDefinition(BaseModel):
    """Lightweight tool reference stored in agent_versions.tools JSONB."""

    name: str
    risk: str = Field(..., pattern="^(low|high)$")
    description: str | None = None


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
_AGENT_NAME_PATTERN = r"^[a-z0-9-]+$"


class AgentCreate(BaseModel):
    name: str = Field(..., max_length=128, pattern=_AGENT_NAME_PATTERN)
    team: str = Field(..., max_length=128)
    description: str | None = None
    agent_type: str = Field("sdk", pattern="^(sdk|declarative)$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    description: str | None = None
    status: str | None = Field(None, pattern="^(active|archived|deprecated)$")
    metadata: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    team: str
    description: str | None
    status: str
    agent_type: str
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _remap_metadata(cls, data: Any) -> Any:
        """ORM column is `metadata_`; expose it as `metadata` in the response."""
        if hasattr(data, "metadata_"):
            # ORM object — create a plain dict representation
            return {
                "id": data.id,
                "name": data.name,
                "team": data.team,
                "description": data.description,
                "status": data.status,
                "agent_type": data.agent_type,
                "created_at": data.created_at,
                "updated_at": data.updated_at,
                "created_by": data.created_by,
                "metadata": data.metadata_,
            }
        return data


# ---------------------------------------------------------------------------
# AgentVersion
# ---------------------------------------------------------------------------
class AgentVersionCreate(BaseModel):
    image_tag: str | None = Field(None, max_length=512)
    workflow_id: uuid.UUID | None = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    eval_passed: bool = False
    git_sha: str | None = Field(None, max_length=64)
    git_branch: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_agent_type_fields(self) -> "AgentVersionCreate":
        if self.image_tag is None and self.workflow_id is None:
            # Allow for now — router will enforce based on parent agent type
            pass
        return self


class AgentVersionPatch(BaseModel):
    eval_passed: bool | None = None
    status: str | None = Field(
        None,
        pattern="^(pending|eval_passed|eval_failed|deployed|retired)$",
    )
    notes: str | None = None


class AgentVersionResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    version_number: int
    image_tag: str | None
    workflow_id: uuid.UUID | None
    tools: list[ToolDefinition]
    eval_passed: bool
    git_sha: str | None
    git_branch: str | None
    notes: str | None
    status: str
    created_at: datetime
    created_by: str | None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------
class DeploymentCreate(BaseModel):
    version_id: uuid.UUID
    replicas: int = Field(1, ge=1, le=10)
    environment: str = Field("production", pattern="^(production|staging)$")


class RollbackRequest(BaseModel):
    target_version_id: uuid.UUID | None = None


class DeploymentResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: Optional[str] = None
    version_id: uuid.UUID
    environment: str
    status: str
    replicas: int
    canary_percent: int | None
    k8s_namespace: str
    k8s_deployment_name: str | None
    error_message: str | None
    deployed_at: datetime
    terminated_at: datetime | None
    deployed_by: str | None
    previous_version_id: uuid.UUID | None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# OPADecision
# ---------------------------------------------------------------------------
class OPADecisionCreate(BaseModel):
    agent_name: str
    tool_name: str
    decision: Literal["allow", "deny", "require_approval"]
    policy_version: str
    input_snapshot: dict = {}
    deny_reason: Optional[str] = None
    thread_id: Optional[str] = None
    trace_id: Optional[str] = None


class OPADecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_name: str
    tool_name: str
    decision: str
    policy_version: str
    deny_reason: Optional[str]
    thread_id: Optional[str]
    trace_id: Optional[str]
    decided_at: datetime


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------
class ApprovalCreate(BaseModel):
    agent_id: uuid.UUID
    agent_name: str = Field(..., max_length=128)
    team: str = Field(..., max_length=128)
    thread_id: str = Field(..., max_length=256)
    tool_name: str = Field(..., max_length=256)
    tool_args: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = Field(..., pattern="^(high|critical)$")
    trace_id: str | None = None
    timeout_seconds: int = Field(
        1800,
        description="Auto-reject after this many seconds (default 30 min)",
    )
    session_id: Optional[uuid.UUID] = None
    opa_decision_id: Optional[uuid.UUID] = None


class ApprovalDecision(BaseModel):
    """PATCH /approvals/{id} — reviewer submits approve/reject."""

    decision: str = Field(..., pattern="^(approved|rejected)$")
    reviewer_id: str
    reviewer_notes: str | None = None
    version: int = Field(
        ...,
        description="Optimistic lock version; get from GET /approvals/{id}",
    )


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str
    team: str
    thread_id: str
    tool_name: str
    tool_args: dict[str, Any]
    risk_level: str
    status: str
    reviewer_id: str | None
    reviewer_notes: str | None
    trace_id: str | None
    decision_at: datetime | None
    expires_at: datetime
    created_at: datetime
    version: int
    session_id: Optional[uuid.UUID] = None
    opa_decision_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------
class WorkflowCreate(BaseModel):
    name: str = Field(..., max_length=256)
    team: str = Field(..., max_length=128)
    description: str | None = None
    definition: dict[str, Any]
    change_summary: str | None = None


class WorkflowUpdate(BaseModel):
    name: str | None = Field(None, max_length=256)
    team: str | None = Field(None, max_length=128)
    description: str | None = None
    definition: dict[str, Any] | None = None
    change_summary: str | None = None


class WorkflowResponse(BaseModel):
    id: uuid.UUID
    name: str
    team: str
    description: str | None
    status: str
    current_version_number: int | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str | None

    model_config = ConfigDict(from_attributes=True)


class WorkflowVersionResponse(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    version_number: int
    definition: dict[str, Any]
    change_summary: str | None
    created_at: datetime
    created_by: str | None

    model_config = ConfigDict(from_attributes=True)


class WorkflowWithDefinitionResponse(WorkflowResponse):
    """Extended response returned by GET /workflows/{id} — includes current version."""

    current_definition: WorkflowVersionResponse | None = None


class WorkflowDeployRequest(BaseModel):
    version_number: int | None = Field(
        None,
        description="Specific version to deploy; defaults to latest",
    )
    replicas: int = Field(1, ge=1, le=10)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
class ToolCreate(BaseModel):
    name: str = Field(..., max_length=256)
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    type: str = Field(..., pattern="^(native|http|mcp_tool)$")
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    risk_level: str = Field("low", pattern="^(low|medium|high|critical)$")
    auth_config_id: uuid.UUID | None = None
    owner_team: str | None = None
    # HTTP-specific
    http_method: str | None = None
    http_url: str | None = None
    http_headers: dict[str, Any] | None = None
    http_body_template: str | None = None
    http_timeout_ms: int | None = None
    # MCP-specific
    mcp_server_id: uuid.UUID | None = None
    mcp_tool_name: str | None = None


class ToolUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    risk_level: str | None = Field(None, pattern="^(low|medium|high|critical)$")
    status: str | None = Field(None, pattern="^(active|inactive|deprecated)$")
    auth_config_id: uuid.UUID | None = None
    http_method: str | None = None
    http_url: str | None = None
    http_headers: dict[str, Any] | None = None
    http_body_template: str | None = None
    http_timeout_ms: int | None = None
    mcp_server_id: uuid.UUID | None = None
    mcp_tool_name: str | None = None


class ToolResponse(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str | None
    description: str | None
    category: str | None
    tags: list[str] | None
    type: str
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    risk_level: str
    auth_config_id: uuid.UUID | None
    owner_team: str | None
    version: int
    status: str
    http_method: str | None
    http_url: str | None
    http_headers: dict[str, Any] | None
    http_body_template: str | None
    http_timeout_ms: int | None
    mcp_server_id: uuid.UUID | None
    mcp_tool_name: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ToolTestRequest(BaseModel):
    """Trigger a test invocation of a registered tool."""

    input: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(5000, ge=100, le=30_000)


class ToolTestResponse(BaseModel):
    success: bool
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int


# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------
class AuthConfigCreate(BaseModel):
    name: str = Field(..., max_length=256)
    type: str = Field(..., pattern="^(api_key|oauth2|bearer|mtls)$")
    k8s_secret_ref: str | None = None
    owner_team: str | None = None


class AuthConfigUpdate(BaseModel):
    name: str | None = None
    type: str | None = Field(None, pattern="^(api_key|oauth2|bearer|mtls)$")
    k8s_secret_ref: str | None = None
    owner_team: str | None = None


class AuthConfigResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    # k8s_secret_ref is deliberately excluded — never returned to callers
    owner_team: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# MCPServer
# ---------------------------------------------------------------------------
class MCPServerCreate(BaseModel):
    name: str = Field(..., max_length=256)
    description: str | None = None
    server_url: str
    transport: str = Field("streamable_http", pattern="^(streamable_http|stdio)$")
    auth_config_id: uuid.UUID | None = None
    owner_team: str | None = None


class MCPServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    server_url: str
    transport: str
    auth_config_id: uuid.UUID | None
    owner_team: str | None
    status: str
    last_synced_at: datetime | None
    discovered_tool_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# AgentTool  (POST /agents/{name}/tools)
# ---------------------------------------------------------------------------
class AgentToolBind(BaseModel):
    """Bind an existing Tool record to an Agent."""

    tool_id: uuid.UUID
    added_by: str | None = None


class AgentToolResponse(BaseModel):
    agent_id: uuid.UUID
    tool_id: uuid.UUID
    added_by: str | None
    added_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------
class TeamCreate(BaseModel):
    name: str = Field(..., max_length=128)
    namespace: str = Field(..., max_length=128)
    keycloak_role_id: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = None


class TeamUpdate(BaseModel):
    namespace: Optional[str] = Field(None, max_length=128)
    keycloak_role_id: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = None


class TeamResponse(BaseModel):
    id: uuid.UUID
    name: str
    namespace: str
    keycloak_role_id: Optional[str]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------
class SkillCreate(BaseModel):
    name: str = Field(..., max_length=256)
    team: str = Field(..., max_length=128)
    description: str | None = None
    tool_ids: list[str] = Field(default_factory=list)


class SkillUpdate(BaseModel):
    name: str | None = Field(None, max_length=256)
    description: str | None = None
    tool_ids: list[str] | None = None
    status: str | None = None


class SkillResponse(BaseModel):
    id: uuid.UUID
    name: str
    team: str
    description: str | None
    tool_ids: list[str]
    status: str
    created_at: datetime
    created_by: str | None
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# LLM Provider
# ---------------------------------------------------------------------------
class LLMCredentials(BaseModel):
    """Credentials payload — encrypted at rest, never returned in responses."""
    api_key: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str | None = None


class LLMProviderCreate(BaseModel):
    name: str = Field(..., max_length=128)
    provider: Literal["anthropic", "bedrock"]
    default_model: str = Field(..., max_length=256)
    credentials: LLMCredentials
    team: str = Field(..., max_length=128)


class LLMProviderUpdate(BaseModel):
    name: str | None = Field(None, max_length=128)
    default_model: str | None = Field(None, max_length=256)
    credentials: LLMCredentials | None = None


class LLMProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    default_model: str
    team: str
    created_at: datetime
    updated_at: datetime
    # credentials_encrypted is intentionally excluded — never returned
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Error  (matches ErrorResponse in OpenAPI spec)
# ---------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    field: str | None = None


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------
__all__ = [
    "PaginatedResponse",
    "HealthResponse",
    "ToolDefinition",
    # Agent
    "AgentCreate",
    "AgentUpdate",
    "AgentResponse",
    # AgentVersion
    "AgentVersionCreate",
    "AgentVersionPatch",
    "AgentVersionResponse",
    # Deployment
    "DeploymentCreate",
    "RollbackRequest",
    "DeploymentResponse",
    # OPADecision
    "OPADecisionCreate",
    "OPADecisionResponse",
    # Approval
    "ApprovalCreate",
    "ApprovalDecision",
    "ApprovalResponse",
    # Workflow
    "WorkflowCreate",
    "WorkflowUpdate",
    "WorkflowResponse",
    "WorkflowVersionResponse",
    "WorkflowWithDefinitionResponse",
    "WorkflowDeployRequest",
    # Tool
    "ToolCreate",
    "ToolUpdate",
    "ToolResponse",
    "ToolTestRequest",
    "ToolTestResponse",
    # AuthConfig
    "AuthConfigCreate",
    "AuthConfigUpdate",
    "AuthConfigResponse",
    # MCPServer
    "MCPServerCreate",
    "MCPServerResponse",
    # AgentTool
    "AgentToolBind",
    "AgentToolResponse",
    # Team
    "TeamCreate",
    "TeamUpdate",
    "TeamResponse",
    # Skill
    "SkillCreate",
    "SkillUpdate",
    "SkillResponse",
    # LLMProvider
    "LLMCredentials",
    "LLMProviderCreate",
    "LLMProviderUpdate",
    "LLMProviderResponse",
    # Error
    "ErrorResponse",
]

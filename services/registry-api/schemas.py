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

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, Optional, TypeVar, Union

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
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
    risk: str = Field(..., pattern="^(low|medium|high|critical)$")
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
    # Defaulted-required (not Optional): a missing value persists an explicit default,
    # never NULL — so the deploy-time coalesce is deletable (WS-0 M3).
    agent_class: str = Field("user_delegated", pattern="^(daemon|user_delegated)$")
    execution_shape: str = Field("reactive", pattern="^(reactive|durable)$")
    memory_enabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] | None = Field(None, description="Platform tool names to bind at creation")


class AgentUpdate(BaseModel):
    description: str | None = None
    status: str | None = Field(None, pattern="^(active|archived|deprecated)$")
    agent_class: str | None = Field(None, pattern="^(daemon|user_delegated)$")
    execution_shape: str | None = Field(None, pattern="^(reactive|durable)$")
    memory_enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    team: str
    description: str | None
    status: str
    agent_type: str
    agent_class: str
    execution_shape: str
    memory_enabled: bool
    publish_status: str
    created_at: datetime
    updated_at: datetime
    created_by: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Computed in list_agents / get_agent — not an ORM column.
    latest_version_number: int | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _remap_metadata(cls, data: Any) -> Any:
        """ORM column is `metadata_`; expose it as `metadata` in the response."""
        if hasattr(data, "metadata_"):
            return {
                "id": data.id,
                "name": data.name,
                "team": data.team,
                "description": data.description,
                "status": data.status,
                "agent_type": data.agent_type,
                "agent_class": data.agent_class,
                "execution_shape": data.execution_shape,
                "memory_enabled": data.memory_enabled,
                "publish_status": data.publish_status,
                "created_at": data.created_at,
                "updated_at": data.updated_at,
                "created_by": data.created_by,
                "metadata": data.metadata_,
            }
        return data


# ---------------------------------------------------------------------------
# AgentIdentity
# ---------------------------------------------------------------------------
class AgentIdentityCreate(BaseModel):
    sa_subject: str = Field(..., max_length=512)
    sa_namespace: str = Field(..., max_length=256)
    # Exactly one of these is set: sandbox deployments use deployment_id (FK
    # deployments); production deployments use production_deployment_id.
    deployment_id: uuid.UUID | None = None
    production_deployment_id: uuid.UUID | None = None


class AgentIdentityResponse(BaseModel):
    id: uuid.UUID
    agent_name: str
    deployment_id: uuid.UUID | None
    production_deployment_id: uuid.UUID | None = None
    sa_subject: str
    sa_namespace: str
    provisioned_at: datetime
    revoked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# AgentVersion
# ---------------------------------------------------------------------------
class AgentVersionCreate(BaseModel):
    image_tag: str | None = Field(None, max_length=512)
    agent_graph_id: uuid.UUID | None = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    eval_passed: bool = False
    adversarial_eval_passed: bool = False
    git_sha: str | None = Field(None, max_length=64)
    git_branch: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _validate_agent_type_fields(self) -> "AgentVersionCreate":
        if self.image_tag is None and self.agent_graph_id is None:
            # Allow for now — router will enforce based on parent agent type
            pass
        return self


class AgentVersionPatch(BaseModel):
    eval_passed: bool | None = None
    adversarial_eval_passed: bool | None = None
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
    agent_graph_id: uuid.UUID | None
    tools: list[ToolDefinition]
    config: dict | None = None
    eval_passed: bool
    adversarial_eval_passed: bool
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
    version_id: uuid.UUID | None = None
    replicas: int = Field(1, ge=1, le=10)
    environment: str = Field("production", pattern="^(production|staging|sandbox)$")
    # Optional custom deployment name; auto-generated ("{agent}-{suffix}") if omitted.
    name: str | None = Field(None, max_length=256)
    # Optional auto-terminate window in hours (sandbox cleanup); NULL = never.
    ttl_hours: int | None = Field(None, ge=1, le=8760)


class DeploymentActionRequest(BaseModel):
    """Lifecycle action on a running deployment (mirror of the catalog action)."""
    action: Literal["suspend", "resume", "terminate", "upgrade"]
    version_id: uuid.UUID | None = None  # required for upgrade


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
    suspended_at: datetime | None = None
    deployed_by: str | None
    previous_version_id: uuid.UUID | None
    llm_secret_name: str | None = None
    llm_env_keys: list[str] | None = None
    llm_provider_type: str | None = None
    llm_provider_model: str | None = None
    name: str | None = None
    ttl_hours: int | None = None

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
    context: str = Field("production", pattern="^(production|playground|sandbox)$")
    reasoning: str | None = None


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
    reasoning: str | None = None
    trace_id: str | None
    decision_at: datetime | None
    expires_at: datetime
    created_at: datetime
    version: int
    session_id: Optional[uuid.UUID] = None
    opa_decision_id: Optional[uuid.UUID] = None
    context: str = "production"
    notify_slack: bool = True
    # Provenance enrichment (populated by list_approvals via thread_id → run join).
    # Lets the HITL console show who requested the tool and on which deployment.
    requested_by: str | None = None          # username (falls back to sub for old rows)
    requested_by_team: str | None = None      # the requester's own team
    deployment_name: str | None = None
    environment: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ApprovalInboxItem(BaseModel):
    id: uuid.UUID
    agent_name: str
    team: str
    step_name: str | None = None
    tool_name: str
    risk_level: str
    tool_args: dict[str, Any]
    thread_context_snippet: str | None = None
    sla_remaining_seconds: int
    created_at: datetime
    context: str = "production"


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Agent Graph (renamed from Workflow — Decision 22: a single agent's canvas graph)
# ---------------------------------------------------------------------------
class AgentGraphCreate(BaseModel):
    name: str = Field(..., max_length=256)
    team: str = Field(..., max_length=128)
    description: str | None = None
    definition: dict[str, Any]
    change_summary: str | None = None


class AgentGraphUpdate(BaseModel):
    name: str | None = Field(None, max_length=256)
    team: str | None = Field(None, max_length=128)
    description: str | None = None
    definition: dict[str, Any] | None = None
    change_summary: str | None = None


class AgentGraphResponse(BaseModel):
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


class AgentGraphVersionResponse(BaseModel):
    id: uuid.UUID
    agent_graph_id: uuid.UUID
    version_number: int
    definition: dict[str, Any]
    change_summary: str | None
    created_at: datetime
    created_by: str | None

    model_config = ConfigDict(from_attributes=True)


class AgentGraphWithDefinitionResponse(AgentGraphResponse):
    """Extended response returned by GET /agent-graphs/{id} — includes current version."""

    current_definition: AgentGraphVersionResponse | None = None


class AgentGraphDeployRequest(BaseModel):
    version_number: int | None = Field(
        None,
        description="Specific version to deploy; defaults to latest",
    )
    replicas: int = Field(1, ge=1, le=10)


# ---------------------------------------------------------------------------
# Composite Workflow (NEW — Decision 22: a collection of member agents)
# ---------------------------------------------------------------------------
class CompositeWorkflowCreate(BaseModel):
    name: str = Field(..., max_length=256)
    team: str = Field(..., max_length=128)
    description: str | None = None
    execution_shape: str = Field("durable", pattern="^(reactive|durable)$")
    orchestration: str = Field("sequential", pattern="^(sequential|supervisor|handoff|conditional)$")
    agent_class: str = Field("user_delegated", pattern="^(daemon|user_delegated)$")
    memory_enabled: bool = False


class CompositeWorkflowUpdate(BaseModel):
    description: str | None = None
    execution_shape: str | None = Field(None, pattern="^(reactive|durable)$")
    orchestration: str | None = Field(None, pattern="^(sequential|supervisor|handoff|conditional)$")
    agent_class: str | None = Field(None, pattern="^(daemon|user_delegated)$")
    memory_enabled: bool | None = None
    status: str | None = Field(None, pattern="^(draft|published|archived)$")


class CompositeWorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    team: str
    description: str | None = None
    execution_shape: str
    orchestration: str
    memory_enabled: bool
    status: str
    publish_status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    agent_class: str
    # S2 save-time author warning (best-effort, non-blocking). [] for durable workflows.
    warnings: list[str] = Field(default_factory=list)


class WorkflowMemberCreate(BaseModel):
    agent_id: uuid.UUID
    role: str | None = Field(None, max_length=64)
    position: int | None = None
    routing: dict[str, Any] = Field(default_factory=dict)


class WorkflowMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workflow_id: uuid.UUID
    agent_id: uuid.UUID
    agent_name: str | None = None  # denormalized for display
    role: str | None = None
    position: int | None = None
    routing: dict[str, Any] = Field(default_factory=dict)
    added_at: datetime


class WorkflowEdgeCreate(BaseModel):
    source_agent_id: uuid.UUID
    target_agent_id: uuid.UUID
    condition: str | None = None
    position: int | None = None


class WorkflowEdgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    source_agent_id: uuid.UUID
    target_agent_id: uuid.UUID
    condition: str | None = None
    position: int | None = None
    created_at: datetime


class CompositeWorkflowWithMembersResponse(CompositeWorkflowResponse):
    members: list[WorkflowMemberResponse] = Field(default_factory=list)
    edges: list[WorkflowEdgeResponse] = Field(default_factory=list)


class WorkflowRunCreate(BaseModel):
    input_payload: dict[str, Any] | None = None
    input_message: str | None = None
    trigger_type: str = "manual"
    run_by: str


class WorkflowRunStartResponse(BaseModel):
    workflow_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    warning: str | None = None


class WorkflowRunTreeResponse(BaseModel):
    parent: "AgentRunResponse"
    children: list["AgentRunResponse"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Workflow Versions
# ---------------------------------------------------------------------------
class WorkflowVersionCreate(BaseModel):
    eval_passed: bool = False
    notes: str | None = None


class WorkflowVersionPatch(BaseModel):
    eval_passed: bool | None = None
    notes: str | None = None


class WorkflowVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version_number: int
    members: list[Any] = Field(default_factory=list)
    edges: list[Any] = Field(default_factory=list)
    orchestration: str
    execution_shape: str
    config: dict[str, Any] = Field(default_factory=dict)
    eval_passed: bool
    created_at: datetime
    created_by: str | None = None


# ---------------------------------------------------------------------------
# Workflow Deployments
# ---------------------------------------------------------------------------
class WorkflowDeploymentCreate(BaseModel):
    version_id: uuid.UUID
    environment: str = Field("sandbox", pattern="^(production|staging|canary|sandbox)$")
    replicas: int = Field(1, ge=1, le=10)
    ttl_hours: int | None = None
    name: str | None = None


class WorkflowDeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    version_id: uuid.UUID
    name: str | None = None
    workflow_name: str | None = None
    environment: str
    status: str
    replicas: int
    ttl_hours: int | None = None
    deployed_at: datetime
    suspended_at: datetime | None = None
    terminated_at: datetime | None = None
    error_message: str | None = None
    deployed_by: str | None = None
    previous_version_id: uuid.UUID | None = None


class WorkflowDeploymentActionRequest(BaseModel):
    action: Literal["suspend", "resume", "terminate", "upgrade"]
    version_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
class ToolCreate(BaseModel):
    name: str = Field(..., max_length=256)
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    type: str = Field(..., pattern="^(native|http|mcp_tool|python)$")
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
    # Python-specific
    python_code: str | None = None


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
    python_code: str | None = None


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
    python_code: str | None
    created_by: str | None = None
    publish_status: str = "published"
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

# Fingerprints of HTTP/httpx error strings and stack traces that must NEVER be
# persisted as a secret value. httpx.HTTPStatusError.__str__() renders as e.g.
#   "Client error '403 Forbidden' for url 'https://google.serper.dev/search'"
# so if a caller ever feeds a raise_for_status() error (or a JSON error body)
# into the credential value, we reject it at the API boundary instead of
# encrypting it and mounting it as X-API-KEY.
_HTTP_ERROR_FINGERPRINT = re.compile(
    r"(Client error '|Server error '|' for url '|HTTPStatusError|"
    r"Traceback \(most recent call last\)|\bForbidden\b|\bUnauthorized\b)"
)

# Real API keys / bearer tokens / OAuth secrets are single-line and short.
# Only mTLS material (PEM cert/key) is legitimately long and multi-line, so the
# single-line + short-length rule is scoped to the inline credential types.
_MAX_INLINE_CREDENTIAL_LEN = 1024
_MAX_PEM_CREDENTIAL_LEN = 16384
_INLINE_CREDENTIAL_TYPES = {"api_key", "bearer", "oauth2"}
# A credential key becomes an environment variable in the agent pod (injected
# via `envFrom`). K8s silently drops env vars whose names aren't valid, so a
# hyphenated key like "serper-dev" would never reach the agent. Enforce a valid
# env-var name at the boundary — the UI is only one entry path (API/seed bypass).
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_credential_values(
    credentials: dict[str, str] | None, config_type: str | None
) -> dict[str, str] | None:
    """Reject values that are obviously not credentials.

    Makes the illegal state — an HTTP error body / stack trace stored as a
    secret — unrepresentable at the API boundary, so it can never be encrypted
    or written to a K8s Secret. Raises ValueError (surfaced as HTTP 422).
    """
    if not credentials:
        return credentials
    # mTLS PEM material is multi-line and large; every other type is a short,
    # single-line token. Unknown/None types default to the permissive path so a
    # partial update (type not resent) never falsely rejects an mTLS rotation —
    # the error-fingerprint check below still applies to all types.
    inline = config_type in _INLINE_CREDENTIAL_TYPES
    max_len = _MAX_INLINE_CREDENTIAL_LEN if inline else _MAX_PEM_CREDENTIAL_LEN
    for key, value in credentials.items():
        if not _ENV_VAR_NAME_RE.match(key or ""):
            raise ValueError(
                f"credential key '{key}' is not a valid environment variable name "
                "(letters, digits, underscores; not starting with a digit) — "
                "it would be silently dropped when injected into the agent"
            )
        if not value or not value.strip():
            raise ValueError(f"credential '{key}' value must not be empty")
        if _HTTP_ERROR_FINGERPRINT.search(value):
            raise ValueError(
                f"credential '{key}' looks like an HTTP error message, not a secret value"
            )
        if len(value) > max_len:
            raise ValueError(
                f"credential '{key}' value is too long ({len(value)} chars) to be a valid secret"
            )
        if inline and ("\n" in value or "\r" in value):
            raise ValueError(
                f"credential '{key}' value must be a single line (embedded newline found)"
            )
    return credentials


class AuthConfigCreate(BaseModel):
    name: str = Field(..., max_length=256)
    type: str = Field(..., pattern="^(api_key|oauth2|bearer|mtls)$")
    credentials: dict[str, str] | None = Field(None, description="Key-value credential pairs; stored as K8s Secret, never in DB")
    k8s_secret_ref: str | None = None
    owner_team: str | None = None

    @model_validator(mode="after")
    def _reject_invalid_credentials(self) -> "AuthConfigCreate":
        validate_credential_values(self.credentials, self.type)
        return self


class AuthConfigUpdate(BaseModel):
    name: str | None = None
    type: str | None = Field(None, pattern="^(api_key|oauth2|bearer|mtls)$")
    credentials: dict[str, str] | None = Field(None, description="Key-value credential pairs; updates existing K8s Secret")
    k8s_secret_ref: str | None = None
    owner_team: str | None = None

    @model_validator(mode="after")
    def _reject_invalid_credentials(self) -> "AuthConfigUpdate":
        validate_credential_values(self.credentials, self.type)
        return self


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
    publish_status: str = "published"
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
# Publish Request  (Phase 9.2 — asset lifecycle)
# ---------------------------------------------------------------------------
class PublishRequestCreate(BaseModel):
    asset_id: uuid.UUID
    asset_type: str  # tool/agent/skill/workflow
    highest_risk_level: str  # low/medium/high
    dependency_declaration: dict = {}


class PublishRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    asset_id: uuid.UUID
    asset_type: str
    submitted_by: str
    submitted_at: datetime
    status: str
    highest_risk_level: str
    dependency_declaration: dict
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    review_notes: Optional[str]
    source_version_id: Optional[uuid.UUID] = None
    last_eval_score: Optional[float] = None
    last_eval_run_id: Optional[uuid.UUID] = None
    asset_name: Optional[str] = None
    asset_team: Optional[str] = None


class PublishRequestApprove(BaseModel):
    grantee_teams: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class PublishRequestReject(BaseModel):
    notes: str = ""


# ---------------------------------------------------------------------------
# Asset Grant  (Phase 9.2)
# ---------------------------------------------------------------------------
class AssetGrantCreate(BaseModel):
    asset_id: uuid.UUID
    asset_type: str
    grantee_team: str
    expires_at: Optional[datetime] = None


class AssetGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    asset_id: uuid.UUID
    asset_type: str
    grantee_team: str
    granted_by: str
    granted_at: datetime
    expires_at: Optional[datetime]
    revoked_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Approval Authority  (Phase 9.2)
# ---------------------------------------------------------------------------
class GrantAuditResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    admin_id: str
    action: str
    asset_id: uuid.UUID
    grantee_team: str
    timestamp: datetime


class ApprovalAuthorityCreate(BaseModel):
    resource_type: str  # agent/tool/skill
    resource_id: str
    approver_user_id: Optional[str] = None
    approver_role: Optional[str] = None


class ApprovalAuthorityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    resource_type: str
    resource_id: str
    approver_user_id: Optional[str]
    approver_role: Optional[str]
    granted_by: str
    granted_at: datetime
    revoked_at: Optional[datetime]


# ---------------------------------------------------------------------------
# Agent publish endpoint body  (Phase 9.2)
# ---------------------------------------------------------------------------
class AgentPublishRequest(BaseModel):
    dependency_declaration: dict = {}
    version_id: Optional[uuid.UUID] = None


# ---------------------------------------------------------------------------
# Playground Run  (Phase 10.1)
# ---------------------------------------------------------------------------
class PlaygroundRunCreate(BaseModel):
    agent_name: str
    agent_version_id: Optional[uuid.UUID] = None
    input_message: Optional[str] = None
    execution_shape: str = Field("reactive", pattern="^(reactive|durable)$")
    input_payload: Optional[dict[str, Any]] = None
    trigger_type: Optional[str] = None
    trigger_payload: Optional[dict[str, Any]] = None


class PlaygroundRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    agent_name: str
    agent_version_id: Optional[uuid.UUID]
    context: str
    sandbox: bool
    input_message: Optional[str]
    execution_shape: str
    input_payload: Optional[dict[str, Any]] = None
    trigger_type: Optional[str] = None
    trigger_payload: Optional[dict[str, Any]] = None
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    judge_score: Optional[float] = None
    judge_status: Optional[str] = None
    judge_reason: Optional[str] = None
    output_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Playground Dataset  (Phase 10.3; Eval v2 E-0 — mode discriminator)
# ---------------------------------------------------------------------------
# The five eval families (== playground_datasets.mode / eval_runs.mode). A
# projection of the execution cube onto the eval-relevant families + workflow.
DatasetMode = Literal["reactive", "durable", "scheduled", "webhook", "workflow"]


class _DatasetItemBase(BaseModel):
    """Common envelope shared by every DatasetItem variant (data-model §2.0).

    `extra='allow'` keeps forward-compat: variants declared here for E-1..E-5
    (durable/scheduled/webhook/workflow) may carry mode-specific fields that
    E-0 does not yet interpret — they must not fail validation today.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: Optional[str] = None
    notes: Optional[str] = None
    rubric: Optional[str] = None
    weight: Optional[float] = None
    tags: Optional[list[str]] = None


class ReactiveDatasetItem(_DatasetItemBase):
    """Response-correctness item — back-compat with today's `{input, expected_output}`.

    `input` (today's key) is accepted as an alias for `input_message`; a missing
    `kind` defaults to 'reactive'. Storage is NOT rewritten (validate-only), so
    the existing runner that reads `item["input"]` keeps working unchanged.
    """

    kind: Literal["reactive"] = "reactive"
    input_message: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("input_message", "input"),
    )
    expected_output: Optional[str] = None  # optional if a rubric is present


class DurableDatasetItem(_DatasetItemBase):
    """Trajectory + tool-call + HITL-arg review (data-model §2.2; scored WS-1)."""

    kind: Literal["durable"] = "durable"
    input_payload: Optional[dict[str, Any]] = None
    expected_output: Optional[str] = None
    expected_trajectory: Optional[dict[str, Any]] = None


class ScheduledDatasetItem(_DatasetItemBase):
    """Job-spec run → side-effect verification (data-model §2.3; scored WS-3)."""

    kind: Literal["scheduled"] = "scheduled"
    job_spec: Optional[dict[str, Any]] = None
    expected_output: Optional[str] = None
    expected_trajectory: Optional[dict[str, Any]] = None
    expected_side_effects: Optional[list[dict[str, Any]]] = None


class WebhookDatasetItem(_DatasetItemBase):
    """Filter match/miss + action correctness + injection robustness (§2.4; WS-4)."""

    kind: Literal["webhook"] = "webhook"
    trigger_payload: Optional[dict[str, Any]] = None
    expected_match: Optional[bool] = None
    expected_filter_reason: Optional[str] = None
    expected_output: Optional[str] = None
    expected_trajectory: Optional[dict[str, Any]] = None
    expected_side_effects: Optional[list[dict[str, Any]]] = None
    injection_probe: Optional[dict[str, Any]] = None


class WorkflowDatasetItem(_DatasetItemBase):
    """Run-tree / per-member eval (data-model §2.5; scored WS-5)."""

    kind: Literal["workflow"] = "workflow"
    input_message: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("input_message", "input"),
    )
    input_payload: Optional[dict[str, Any]] = None
    expected_output: Optional[str] = None
    expected_member_path: Optional[list[str]] = None
    per_member: Optional[dict[str, Any]] = None


def _dataset_item_discriminator(value: Any) -> str:
    """Route by `kind`, defaulting a missing/empty discriminator to 'reactive'.

    This is the kind-defaulting validator that makes today's `kind`-less rows
    read back as reactive (full back-compat).
    """

    if isinstance(value, dict):
        return value.get("kind") or "reactive"
    return getattr(value, "kind", None) or "reactive"


# Discriminated union keyed by `kind` (== dataset.mode), with a callable
# discriminator so a legacy item that omits `kind` still routes to reactive.
DatasetItem = Annotated[
    Union[
        Annotated[ReactiveDatasetItem, Tag("reactive")],
        Annotated[DurableDatasetItem, Tag("durable")],
        Annotated[ScheduledDatasetItem, Tag("scheduled")],
        Annotated[WebhookDatasetItem, Tag("webhook")],
        Annotated[WorkflowDatasetItem, Tag("workflow")],
    ],
    Discriminator(_dataset_item_discriminator),
]

_DATASET_ITEM_ADAPTER: TypeAdapter = TypeAdapter(DatasetItem)


def _validate_dataset_items(items: list[Any], mode: Optional[str]) -> None:
    """Validate each item against the dataset `mode` (validate-only, no rewrite).

    - A missing per-item `kind` defaults to `mode` (or 'reactive' when `mode`
      is unknown, e.g. a partial PATCH that doesn't restate the mode).
    - An explicit per-item `kind` that disagrees with `mode` is rejected — an
      illegal `{mode, item-kind}` pair is unrepresentable at the door (no
      runtime key-sniffing later). Raises ValueError on any invalid item.
    """

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"dataset item {idx} must be a JSON object")
        declared = item.get("kind")
        if mode is not None and declared and declared != mode:
            raise ValueError(
                f"dataset item {idx} kind '{declared}' does not match dataset mode '{mode}'"
            )
        effective_kind = declared or mode or "reactive"
        candidate = {**item, "kind": effective_kind}
        try:
            _DATASET_ITEM_ADAPTER.validate_python(candidate)
        except Exception as exc:  # noqa: BLE001 — re-raise as a clean validation error
            raise ValueError(f"dataset item {idx} is invalid for mode '{effective_kind}': {exc}")


class PlaygroundDatasetCreate(BaseModel):
    name: str = Field(..., max_length=256)
    mode: DatasetMode = "reactive"
    schema_version: int = 1
    items: list[Any] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_items(self) -> "PlaygroundDatasetCreate":
        _validate_dataset_items(self.items, self.mode)
        return self


class PlaygroundDatasetUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=256)
    mode: Optional[DatasetMode] = None
    items: Optional[list[Any]] = None

    @model_validator(mode="after")
    def _check_items(self) -> "PlaygroundDatasetUpdate":
        if self.items is not None:
            _validate_dataset_items(self.items, self.mode)
        return self


class PlaygroundDatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_user_id: str
    name: str
    mode: str = "reactive"
    schema_version: int = 1
    items: list[Any]
    created_at: datetime


# ---------------------------------------------------------------------------
# Eval Run  (Phase 10.3)
# ---------------------------------------------------------------------------
class EvalRunCreate(BaseModel):
    agent_name: Optional[str] = None
    agent_version_id: Optional[uuid.UUID] = None
    workflow_id: Optional[uuid.UUID] = None
    workflow_version_id: Optional[uuid.UUID] = None
    dataset_id: uuid.UUID
    sandbox_deployment_id: Optional[uuid.UUID] = None
    workflow_deployment_id: Optional[uuid.UUID] = None


class EvalRunResultCreate(BaseModel):
    dataset_item_idx: int
    input_message: Optional[str] = None
    expected_output: Optional[str] = None
    response: Optional[str] = None
    judge_score: Optional[float] = None
    judge_reasoning: Optional[str] = None
    passed: Optional[bool] = None
    # Eval v2 E-0: composite-score evidence (all optional; reactive fills only
    # dimension_scores={"response": x}).
    dimension_scores: Optional[dict[str, Any]] = None
    eval_detail: Optional[dict[str, Any]] = None
    trigger_payload: Optional[dict[str, Any]] = None
    matched: Optional[bool] = None
    run_id: Optional[uuid.UUID] = None


class EvalRunResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    eval_run_id: uuid.UUID
    dataset_item_idx: int
    input_message: Optional[str]
    expected_output: Optional[str]
    response: Optional[str]
    judge_score: Optional[float]
    judge_reasoning: Optional[str]
    passed: Optional[bool]
    langfuse_trace_id: Optional[str]
    trace_url: Optional[str] = None
    dimension_scores: Optional[dict[str, Any]] = None
    eval_detail: Optional[dict[str, Any]] = None
    trigger_payload: Optional[dict[str, Any]] = None
    matched: Optional[bool] = None
    run_id: Optional[uuid.UUID] = None
    created_at: datetime


class EvalRunStatusUpdate(BaseModel):
    status: str
    total_items: Optional[int] = None
    passed_count: Optional[int] = None
    failed_count: Optional[int] = None
    overall_score: Optional[float] = None


class EvalRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    agent_name: str
    agent_version_id: Optional[uuid.UUID]
    workflow_id: Optional[uuid.UUID]
    dataset_id: uuid.UUID
    status: str
    total_items: Optional[int]
    passed_count: Optional[int]
    failed_count: Optional[int]
    overall_score: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    sandbox_deployment_id: Optional[uuid.UUID] = None
    workflow_deployment_id: Optional[uuid.UUID] = None
    workflow_version_id: Optional[uuid.UUID] = None
    # Eval v2 E-0: interpretation mode + composite inputs.
    mode: str = "reactive"
    dimension_weights: Optional[dict[str, Any]] = None
    pass_threshold: Optional[float] = None


# ---------------------------------------------------------------------------
# Eval score door  (Eval v2 E-0 — POST /playground/eval/score)
# ---------------------------------------------------------------------------
class EvalScoreRequest(BaseModel):
    """One scoring door. `mode` selects the scorer branch; E-0 wires reactive.

    The reactive branch scores `response` against the item's expected/rubric and
    returns `dimension_scores={"response": x}` with `composite == x` — byte
    identical to today's `judge_for_eval`.
    """

    mode: DatasetMode = "reactive"
    item: dict[str, Any] = Field(default_factory=dict)
    run_id: Optional[uuid.UUID] = None
    input: Optional[str] = None
    response: Optional[str] = None


class EvalScoreResponse(BaseModel):
    composite: float
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# AgentRun — central invocation primitive
# ---------------------------------------------------------------------------
class AgentRunCreate(BaseModel):
    agent_name: str
    agent_version_id: uuid.UUID | None = None
    session_id: str | None = None
    user_id: str | None = None
    input: str | None = None
    langfuse_trace_id: str | None = None
    context: Literal["production", "playground"] = "production"
    trigger_type: Literal["manual", "api", "schedule", "webhook"] | None = "manual"
    run_by: str | None = None
    team: str | None = None
    thread_id: str | None = None
    # Run-isolation columns — scope a run to the deployment that produced it.
    production_deployment_id: uuid.UUID | None = None
    sandbox_deployment_id: uuid.UUID | None = None
    workflow_deployment_id: uuid.UUID | None = None


class InternalRunStartRequest(BaseModel):
    """Payload for POST /api/v1/internal/runs/start — called by the scheduler /
    event gateway (cluster-internal only) to start a triggered run. Targets
    EITHER an agent (agent_name) OR a composite workflow (workflow_id) — exactly
    one must be provided (Decision 22)."""
    agent_name: str | None = None
    workflow_id: uuid.UUID | None = None
    trigger_type: Literal["schedule", "webhook", "manual", "api"] = "schedule"
    trigger_id: uuid.UUID | None = None
    trigger_payload: dict[str, Any] | None = None
    run_by: str

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "InternalRunStartRequest":
        if bool(self.agent_name) == bool(self.workflow_id):
            raise ValueError("exactly one of agent_name or workflow_id must be set")
        return self


class AgentRunUpdate(BaseModel):
    output: str | None = None
    langfuse_trace_id: str | None = None
    cost_usd: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    status: Literal["queued", "running", "completed", "failed", "blocked", "awaiting_approval", "cancelled"] | None = None
    error_message: str | None = None


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_name: str
    agent_version_id: uuid.UUID | None = None
    session_id: str | None = None
    user_id: str | None = None
    langfuse_trace_id: str | None = None
    cost_usd: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    judge_score: float | None = None
    status: str
    context: str
    input: str | None = None
    output: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    trigger_type: str | None = None
    run_by: str | None = None
    team: str | None = None
    thread_id: str | None = None
    parent_run_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None
    trigger_payload: dict[str, Any] | None = None
    error_message: str | None = None
    trace_url: str | None = None
    production_deployment_id: uuid.UUID | None = None
    sandbox_deployment_id: uuid.UUID | None = None
    workflow_deployment_id: uuid.UUID | None = None
    judge_score: float | None = None


class AgentStatsResponse(BaseModel):
    run_count: int = 0
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    error_rate: float = 0.0
    total_cost_usd: float = 0.0


class AgentHealthResponse(BaseModel):
    """Mode-aware health signals for an agent (Phase 8).

    `mode` selects which block of signals is populated; the rest stay null.
      reactive     : p95_latency_ms, error_rate, runs_24h, cost_24h
      durable      : awaiting_approval_count, failed_24h, avg_duration_ms
      scheduled    : last_run_status, next_fire_at, missed_fires
      event-driven : match_rate_24h, rejected_count_24h
    `health` is a rolled-up traffic-light: healthy | degraded | failing.
    """
    agent_name: str
    mode: str  # reactive | durable | scheduled | event-driven
    health: str = "healthy"  # healthy | degraded | failing

    # reactive
    p95_latency_ms: int | None = None
    error_rate: float | None = None
    runs_24h: int | None = None
    cost_24h: float | None = None

    # durable
    awaiting_approval_count: int | None = None
    failed_24h: int | None = None
    avg_duration_ms: int | None = None

    # scheduled
    last_run_status: str | None = None
    next_fire_at: datetime | None = None
    missed_fires: int | None = None

    # event-driven
    match_rate_24h: float | None = None
    rejected_count_24h: int | None = None


# ---------------------------------------------------------------------------
# RunStep
# ---------------------------------------------------------------------------
class RunStepCreate(BaseModel):
    step_number: int
    name: str
    status: str = "pending"
    output: dict[str, Any] | None = None
    approval_id: str | None = None
    error_message: str | None = None


class RunStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    step_number: int
    name: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output: dict[str, Any] | None = None
    approval_id: uuid.UUID | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# AgentTrigger
# ---------------------------------------------------------------------------
class AgentTriggerCreate(BaseModel):
    trigger_type: str = Field(..., pattern="^(schedule|webhook)$")
    cron_expression: str | None = Field(None, max_length=100)
    timezone: str = "UTC"
    enabled: bool = True
    filter_conditions: dict[str, Any] | list[dict[str, Any]] | None = None
    # Per-schedule job parameters fed to the agent as input on each fire.
    input_payload: dict[str, Any] | None = None
    alert_email: str | None = Field(None, max_length=255)
    alert_on_failure: bool = True

    @model_validator(mode="after")
    def _validate_trigger_fields(self) -> "AgentTriggerCreate":
        if self.trigger_type == "schedule" and not self.cron_expression:
            raise ValueError("cron_expression is required for schedule triggers")
        return self


class AgentTriggerUpdate(BaseModel):
    cron_expression: str | None = None
    timezone: str | None = None
    enabled: bool | None = None
    filter_conditions: dict[str, Any] | list[dict[str, Any]] | None = None
    input_payload: dict[str, Any] | None = None
    alert_email: str | None = Field(None, max_length=255)
    alert_on_failure: bool | None = None


class AgentTriggerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    trigger_type: str
    cron_expression: str | None = None
    timezone: str | None = None
    enabled: bool
    filter_conditions: dict[str, Any] | list[dict[str, Any]] | None = None
    input_payload: dict[str, Any] | None = None
    alert_email: str | None = None
    alert_on_failure: bool = True
    # Plaintext webhook token — populated ONLY in the create response (shown once);
    # never persisted (only sha256 is) and absent from list/get responses.
    token: str | None = None
    # Full public webhook URL (/hooks/{agent}/{token}) — populated alongside `token`
    # in the create response only, so the UI can show a copyable URL once.
    webhook_url: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentEventResponse(BaseModel):
    """One inbound webhook the Event Gateway processed (Phase 9)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trigger_id: uuid.UUID | None = None
    agent_name: str
    status: str  # matched | filtered | rejected
    filter_reason: str | None = None
    payload: dict[str, Any] | None = None
    run_id: uuid.UUID | None = None
    source_ip: str | None = None
    received_at: datetime

    @field_validator("source_ip", mode="before")
    @classmethod
    def _coerce_ip(cls, v: Any) -> str | None:
        # INET columns deserialize to ipaddress.IPv4Address/IPv6Address; the
        # response contract is a plain string.
        return str(v) if v is not None else None


class WorkflowTriggerResponse(BaseModel):
    """Trigger response for composite-workflow triggers (workflow_id set, agent_id NULL)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    trigger_type: str
    cron_expression: str | None = None
    timezone: str | None = None
    enabled: bool
    filter_conditions: dict[str, Any] | list[dict[str, Any]] | None = None
    input_payload: dict[str, Any] | None = None
    alert_email: str | None = None
    alert_on_failure: bool = True
    # Plaintext webhook token — populated ONLY in the create/rotate-token response
    # (shown once, never persisted — only sha256 is stored).
    token: str | None = None
    # Full public webhook URL (/hooks/workflow/{name}/{token}) — populated alongside
    # `token` in the create/rotate-token response only.
    webhook_url: str | None = None
    created_at: datetime
    updated_at: datetime


class RotateTokenResponse(BaseModel):
    """Returned once when a webhook trigger's token is rotated — plaintext token
    is shown only here and never persisted (only its sha256 is stored)."""
    trigger_id: uuid.UUID
    token: str
    webhook_url: str


# Error  (matches ErrorResponse in OpenAPI spec)
# ---------------------------------------------------------------------------
class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    field: str | None = None


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class MemoryMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system|tool)$")
    content: str


class MemorySaveTurnRequest(BaseModel):
    thread_id: str
    messages: list[MemoryMessage]
    session_id: str | None = None
    user_id: str | None = None
    deployment_id: str | None = None


class MemorySearchRequest(BaseModel):
    query: str
    top_k: int = Field(5, ge=1, le=50)
    deployment_id: str | None = None


class AgentMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: str
    role: str
    content: str
    message_index: int
    created_at: datetime
    user_id: str | None = None
    session_id: str | None = None
    deployment_id: uuid.UUID | None = None


class MemorySearchResult(BaseModel):
    content: str
    similarity_score: float
    role: str
    thread_id: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Catalog (Production Artifact Isolation)
# ---------------------------------------------------------------------------
class CatalogVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    artifact_id: uuid.UUID
    version_label: str
    config_snapshot: dict[str, Any] = {}
    source_version_id: uuid.UUID | None = None
    # The source agent's version_number this publish came from (v2 in the catalog
    # may be "from agent v16"). Populated by the catalog detail endpoint; the
    # published label is a per-artifact publish counter, decoupled from this.
    source_version_number: int | None = None
    promoted_at: datetime
    promoted_by: str | None = None
    notes: str | None = None


class CatalogDeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    artifact_id: uuid.UUID
    version_id: uuid.UUID
    version_label: str | None = None
    status: str
    namespace: str | None = None
    deployed_at: datetime | None = None
    suspended_at: datetime | None = None
    updated_at: datetime


class CatalogArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    description: str | None = None
    source_id: uuid.UUID | None = None
    team: str
    created_at: datetime
    updated_at: datetime
    latest_version: str | None = None
    deployment_count: int = 0


class MemberTopologyEntry(BaseModel):
    agent_name: str
    agent_id: str
    agent_version_id: str | None = None
    role: str | None = None
    position: int | None = None
    has_production_deployment: bool = False


class CatalogDetailResponse(BaseModel):
    artifact: CatalogArtifactResponse
    versions: list[CatalogVersionResponse] = []
    deployments: list[CatalogDeploymentResponse] = []
    granted_teams: list[str] = []
    member_topology: list[MemberTopologyEntry] = []


class CatalogDeployRequest(BaseModel):
    version_id: uuid.UUID


class CatalogDeploymentUpdateRequest(BaseModel):
    action: str  # "upgrade" | "suspend" | "resume"
    version_id: uuid.UUID | None = None  # required for upgrade


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
    "AgentGraphCreate",
    "AgentGraphUpdate",
    "AgentGraphResponse",
    "AgentGraphVersionResponse",
    "AgentGraphWithDefinitionResponse",
    "AgentGraphDeployRequest",
    "CompositeWorkflowCreate",
    "CompositeWorkflowUpdate",
    "CompositeWorkflowResponse",
    "CompositeWorkflowWithMembersResponse",
    "WorkflowMemberCreate",
    "WorkflowMemberResponse",
    "WorkflowEdgeCreate",
    "WorkflowEdgeResponse",
    "WorkflowRunCreate",
    "WorkflowRunStartResponse",
    "WorkflowRunTreeResponse",
    # WorkflowVersion
    "WorkflowVersionCreate",
    "WorkflowVersionResponse",
    # WorkflowDeployment
    "WorkflowDeploymentCreate",
    "WorkflowDeploymentResponse",
    "WorkflowDeploymentActionRequest",
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
    # Publish Request (Phase 9.2)
    "PublishRequestCreate",
    "PublishRequestResponse",
    "PublishRequestApprove",
    "PublishRequestReject",
    # Asset Grant (Phase 9.2)
    "AssetGrantCreate",
    "AssetGrantResponse",
    # Approval Authority (Phase 9.2)
    "GrantAuditResponse",
    "ApprovalAuthorityCreate",
    "ApprovalAuthorityResponse",
    # Agent publish (Phase 9.2)
    "AgentPublishRequest",
    # Playground Run (Phase 10.1)
    "PlaygroundRunCreate",
    "PlaygroundRunResponse",
    # Playground Dataset (Phase 10.3; Eval v2 E-0 discriminated union)
    "PlaygroundDatasetCreate",
    "PlaygroundDatasetUpdate",
    "PlaygroundDatasetResponse",
    "DatasetItem",
    "ReactiveDatasetItem",
    "DurableDatasetItem",
    "ScheduledDatasetItem",
    "WebhookDatasetItem",
    "WorkflowDatasetItem",
    # Eval Run (Phase 10.3)
    "EvalRunCreate",
    "EvalRunResultCreate",
    "EvalRunResultResponse",
    "EvalRunStatusUpdate",
    "EvalRunResponse",
    # Eval score door (Eval v2 E-0)
    "EvalScoreRequest",
    "EvalScoreResponse",
    # Agent Run (observability primitive)
    "AgentRunCreate",
    "AgentRunUpdate",
    "AgentRunResponse",
    # RunStep
    "RunStepResponse",
    # AgentTrigger / WorkflowTrigger
    "AgentTriggerCreate",
    "AgentTriggerUpdate",
    "AgentTriggerResponse",
    "WorkflowTriggerResponse",
    # Memory
    "MemorySaveTurnRequest",
    "MemorySearchRequest",
    "AgentMemoryResponse",
    "MemorySearchResult",
    # Catalog (production artifacts)
    "CatalogArtifactResponse",
    "CatalogVersionResponse",
    "CatalogDeploymentResponse",
    "CatalogDetailResponse",
    "MemberTopologyEntry",
    "CatalogDeployRequest",
    "CatalogDeploymentUpdateRequest",
    # Error
    "ErrorResponse",
]

# Resolve the forward reference WorkflowRunTreeResponse → AgentRunResponse.
WorkflowRunTreeResponse.model_rebuild()

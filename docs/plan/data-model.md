# AgentShield Phase 1 — Data Model

**Database:** `agentshield` (one of 5 databases on the shared Postgres 16 cluster)
**ORM:** SQLAlchemy 2.0 (async) with Alembic for migrations
**Migration tool:** Alembic 1.14
**Connection string format:** `postgresql+asyncpg://agentshield_user:<password>@pgbouncer.agentshield-platform:5432/agentshield`

---

## Entity Relationship Overview

```
agents (1) ─────────────── (*) agent_versions
  │                                 │
  │                                 └──── (*) deployments
  │
  ├── (*) approvals ◄──────────────────── (1) opa_decisions
  │
  └── (1) agent_policies

opa_decisions (1) ─────── (0..1) approvals  ← every require_approval decision links to an approval row

workflows (1) ─────────── (*) workflow_versions
     │
     └── (*) deployments (via workflow_version_id)

pii_mappings (standalone — keyed by session/request)
pii_mappings.session_id ──► approvals.session_id  ← reviewer UI uses this for de-anonymization
```

---

## Table Definitions

### `agents`

Represents a registered AI agent. One agent can have many versions and deployments.

```sql
CREATE TABLE agents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(128) NOT NULL UNIQUE,        -- URL-safe name, e.g. "order-agent"
    team        VARCHAR(128) NOT NULL,               -- Team namespace, e.g. "commerce"
    description TEXT,
    status      VARCHAR(32) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived', 'deprecated')),
    agent_type  VARCHAR(32) NOT NULL DEFAULT 'sdk'
                    CHECK (agent_type IN ('sdk', 'declarative')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  VARCHAR(256),                        -- email of creator
    metadata    JSONB NOT NULL DEFAULT '{}'          -- arbitrary tags/labels
);

CREATE INDEX idx_agents_team ON agents (team);
CREATE INDEX idx_agents_status ON agents (status);
CREATE INDEX idx_agents_name ON agents (name);
```

### `agent_versions`

A specific build/definition of an agent. For SDK agents: an image tag. For declarative agents: a workflow JSON snapshot.

```sql
CREATE TABLE agent_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,               -- 1, 2, 3, ... auto-incremented per agent
    image_tag       VARCHAR(512),                   -- null for declarative agents
    workflow_id     UUID REFERENCES workflows(id),  -- null for SDK agents
    tools           JSONB NOT NULL DEFAULT '[]',    -- [{"name": "lookup_order", "risk": "low"}, ...]
    eval_passed     BOOLEAN NOT NULL DEFAULT false,
    git_sha         VARCHAR(64),
    git_branch      VARCHAR(256),
    notes           TEXT,
    status          VARCHAR(32) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'eval_passed', 'eval_failed', 'deployed', 'retired')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      VARCHAR(256),
    UNIQUE (agent_id, version_number)
);

CREATE INDEX idx_agent_versions_agent_id ON agent_versions (agent_id);
CREATE INDEX idx_agent_versions_status ON agent_versions (status);
CREATE INDEX idx_agent_versions_eval_passed ON agent_versions (agent_id, eval_passed);
```

### `deployments`

An active or historical deployment of an agent version to the cluster.

```sql
CREATE TABLE deployments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version_id          UUID NOT NULL REFERENCES agent_versions(id),
    environment         VARCHAR(64) NOT NULL DEFAULT 'production'
                            CHECK (environment IN ('production', 'staging', 'canary')),
    status              VARCHAR(32) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'deploying', 'running', 'failed', 'rolled_back', 'terminated')),
    replicas            INTEGER NOT NULL DEFAULT 1,
    canary_percent      INTEGER CHECK (canary_percent BETWEEN 0 AND 100),  -- null = full rollout
    k8s_namespace       VARCHAR(128) NOT NULL,
    k8s_deployment_name VARCHAR(256),
    error_message       TEXT,                       -- populated on failed deployments
    deployed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminated_at       TIMESTAMPTZ,
    deployed_by         VARCHAR(256),
    previous_version_id UUID REFERENCES agent_versions(id)  -- for rollback tracking
);

CREATE INDEX idx_deployments_agent_id ON deployments (agent_id);
CREATE INDEX idx_deployments_status ON deployments (status);
CREATE INDEX idx_deployments_agent_status ON deployments (agent_id, status);
CREATE INDEX idx_deployments_deployed_at ON deployments (deployed_at DESC);
```

### `opa_decisions`

Audit log of every OPA policy decision. Written by the OPA sidecar (or SDK) on every tool-call authorization. Supports FR-025. No DELETE policy — immutable compliance record.

```sql
CREATE TABLE opa_decisions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name      TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('allow', 'deny', 'require_approval')),
    policy_version  TEXT NOT NULL,
    input_snapshot  JSONB NOT NULL,  -- tool name + args (PII already redacted at this point)
    deny_reason     TEXT,            -- the specific Rego rule that fired on deny
    thread_id       TEXT,            -- LangGraph thread_id for correlation with checkpoints
    trace_id        TEXT,            -- Langfuse trace_id for cross-system correlation
    decided_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_opa_decisions_agent ON opa_decisions(agent_name, decided_at DESC);
CREATE INDEX idx_opa_decisions_thread ON opa_decisions(thread_id) WHERE thread_id IS NOT NULL;
CREATE INDEX idx_opa_decisions_decision ON opa_decisions(decision, decided_at DESC);
```

### `approvals`

Append-only record of every high-risk tool-call approval decision. No DELETE policy.

```sql
CREATE TABLE approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id),
    agent_name      VARCHAR(128) NOT NULL,          -- denormalized for query performance
    team            VARCHAR(128) NOT NULL,
    thread_id       VARCHAR(256) NOT NULL,           -- LangGraph checkpoint thread ID
    tool_name       VARCHAR(256) NOT NULL,
    tool_args       JSONB NOT NULL DEFAULT '{}',
    risk_level      VARCHAR(32) NOT NULL DEFAULT 'high'
                        CHECK (risk_level IN ('high', 'critical')),
    status          VARCHAR(32) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'approved', 'rejected', 'timed_out')),
    reviewer_id     VARCHAR(256),                   -- null until decided
    reviewer_notes  TEXT,
    trace_id        VARCHAR(256),                   -- Langfuse trace ID
    decision_at     TIMESTAMPTZ,                    -- null until decided
    expires_at      TIMESTAMPTZ NOT NULL,           -- auto-reject deadline
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Optimistic locking: first UPDATE wins when two reviewers race
    version         INTEGER NOT NULL DEFAULT 0,
    -- C-01: PII de-anonymization for reviewer UI
    session_id      UUID,                           -- nullable FK to pii_mappings.session_id; used by Appsmith UI to de-anonymize PII in tool_args for reviewer display
    -- C-17/C-18: links to the OPA decision that triggered this approval
    opa_decision_id UUID REFERENCES opa_decisions(id)
);

CREATE INDEX idx_approvals_agent_id ON approvals (agent_id);
CREATE INDEX idx_approvals_status ON approvals (status);
CREATE INDEX idx_approvals_thread_id ON approvals (thread_id);
CREATE INDEX idx_approvals_expires_at ON approvals (expires_at) WHERE status = 'pending';
CREATE INDEX idx_approvals_created_at ON approvals (created_at DESC);
CREATE INDEX idx_approvals_session_id ON approvals (session_id) WHERE session_id IS NOT NULL;
CREATE INDEX idx_approvals_opa_decision ON approvals (opa_decision_id) WHERE opa_decision_id IS NOT NULL;
```

**Optimistic locking pattern** (prevent dual-approve race condition):
```sql
UPDATE approvals
SET status = 'approved', reviewer_id = $1, decision_at = now(), version = version + 1
WHERE id = $2 AND status = 'pending' AND version = $3;
-- If 0 rows updated: another reviewer got there first → return 409 Conflict
```

### `agent_policies`

Auto-generated OPA Rego policy for each agent. Updated on every new deployment.

```sql
CREATE TABLE agent_policies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE UNIQUE,
    rego_policy     TEXT NOT NULL,                  -- full Rego policy text
    tool_allowlist  JSONB NOT NULL DEFAULT '[]',    -- ["lookup_order", "issue_refund"]
    risk_map        JSONB NOT NULL DEFAULT '{}',    -- {"lookup_order": "low", "issue_refund": "high"}
    configmap_name  VARCHAR(256),                   -- K8s ConfigMap name where policy is stored
    version         INTEGER NOT NULL DEFAULT 1,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    generated_from_version_id UUID REFERENCES agent_versions(id)
);

CREATE INDEX idx_agent_policies_agent_id ON agent_policies (agent_id);
```

**OPA Policy schema** (logical structure stored as Rego in `rego_policy` and mirrored in `tool_allowlist` / `risk_map` JSONB columns):

```
OPA Policy (per agent):
  tool_allowlist: list[str]
  risk_classification: dict[str, "low"|"medium"|"high"]
  allow_deanonymize_tools: list[str]   # tools that may receive de-anonymized PII
  parameter_constraints: dict          # e.g. max_refund_amount: 500
```

### `workflows`

Visual workflow definitions created in AgentShield Studio.

```sql
CREATE TABLE workflows (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(256) NOT NULL,
    team        VARCHAR(128) NOT NULL,
    description TEXT,
    status      VARCHAR(32) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'published', 'archived')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  VARCHAR(256),
    metadata    JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_workflows_team ON workflows (team);
CREATE INDEX idx_workflows_status ON workflows (status);
```

### `workflow_versions`

Immutable snapshots of workflow JSON at each save. The definition column holds the full React Flow serialized graph.

```sql
CREATE TABLE workflow_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    definition      JSONB NOT NULL,                 -- full workflow JSON (nodes + edges)
    change_summary  TEXT,                           -- human-written description of what changed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      VARCHAR(256),
    UNIQUE (workflow_id, version_number)
);

CREATE INDEX idx_workflow_versions_workflow_id ON workflow_versions (workflow_id);
```

**Workflow definition JSON schema:**
```json
{
  "id": "wf_abc123",
  "name": "Order Agent",
  "version": 3,
  "nodes": [
    {
      "id": "n1",
      "type": "agent",
      "position": {"x": 100, "y": 100},
      "config": {
        "name": "order-agent",
        "instructions": "Help users with orders.",
        "model": "gpt-4o-mini",
        "risk_level": "low"
      }
    },
    {
      "id": "n2",
      "type": "http_tool",
      "position": {"x": 300, "y": 100},
      "config": {
        "name": "lookup_order",
        "endpoint": "https://api.example.com/orders/{{order_id}}",
        "method": "GET",
        "headers": {"Authorization": "Bearer {{api_key}}"},
        "body_template": null,
        "risk": "low"
      }
    },
    {
      "id": "n3",
      "type": "end",
      "position": {"x": 500, "y": 100},
      "config": {
        "output_mapping": "n1.response"
      }
    }
  ],
  "edges": [
    {"id": "e1", "source": "n1", "target": "n2"},
    {"id": "e2", "source": "n2", "target": "n3"}
  ]
}
```

### `pii_mappings`

Session-scoped placeholder→real-value mappings created by the Safety Orchestrator during input scanning. One row per PII entity found. All rows for a single scan request share the same `session_id`. Mappings expire after 1 hour and are never written permanently — the background cleanup job purges expired rows.

```sql
CREATE TABLE pii_mappings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL,              -- groups all mappings for one scan request
    agent_name          TEXT NOT NULL,
    placeholder         TEXT NOT NULL,              -- e.g. '<EMAIL_0>', '<PERSON_0>'
    entity_type         TEXT NOT NULL,              -- e.g. 'EMAIL_ADDRESS', 'PERSON', 'PHONE_NUMBER'
    encrypted_value     TEXT NOT NULL,              -- AES-256 encrypted real PII value
    encryption_key_ref  TEXT NOT NULL,              -- reference to K8s Secret holding the encryption key
    created_at          TIMESTAMPTZ DEFAULT now(),
    expires_at          TIMESTAMPTZ DEFAULT now() + INTERVAL '1 hour'
);

CREATE INDEX idx_pii_mappings_session ON pii_mappings(session_id);
CREATE INDEX idx_pii_mappings_expiry ON pii_mappings(expires_at);
-- Expired records cleaned up by background job
```

### Safety Scan Response Schema

What the Safety Orchestrator returns from `POST /scan/input`. The `session_id` ties this response to the `pii_mappings` rows created during the scan — the SDK stores it and passes it back on de-anonymization calls.

```
ScanInputResponse:
  session_id: UUID          # reference to pii_mappings for this request
  blocked: bool
  reason: str | null
  sanitized_text: str       # PII replaced with placeholders
  scores:
    llm_guard: float
    presidio: list[EntityMatch]
    nemo: float
  placeholders_found: list[str]   # e.g. ["<EMAIL_0>", "<PERSON_0>"]
```

---

## De-anonymization Flow

End-to-end sequence showing how PII survives a round-trip through the agent without ever appearing in the LLM context:

```
1. Safety scan: "...john@gmail.com..." → session_id=abc, "<EMAIL_0>" stored encrypted
2. Agent receives: "...My email is <EMAIL_0>..."
3. Agent LLM decides: call send_email(to="<EMAIL_0>", body="...")
4. SDK pre-tool check: args contain <EMAIL_0>, tool has allow_deanonymize=true in OPA
5. SDK calls POST /scan/deanonymize {session_id: "abc", placeholders: ["<EMAIL_0>"]}
6. Safety returns: {"<EMAIL_0>": "john@gmail.com"} (decrypted, single-use)
7. SDK substitutes in tool args: send_email(to="john@gmail.com", body="...")
8. Tool executes with real value — LLM context still has <EMAIL_0>
```

Key properties of this design:
- The LLM never sees the real PII value — only placeholders flow through the model context
- De-anonymization is gated on `allow_deanonymize=true` in the OPA policy for that specific tool
- Each `session_id` is request-scoped (UUID per scan call), not user-scoped or persistent
- Mappings auto-expire after 1 hour; the background cleanup job handles purges
- The SDK calls `/scan/deanonymize` immediately before tool execution, not at agent startup

---

## Alembic Migration Setup

### Directory structure
```
services/registry-api/
├── alembic.ini
└── alembic/
    ├── env.py
    ├── script.py.mako
    └── versions/
        └── 0001_initial_schema.py
```

### `alembic.ini`
```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os
sqlalchemy.url = postgresql+asyncpg://%(DB_USER)s:%(DB_PASS)s@%(DB_HOST)s:%(DB_PORT)s/%(DB_NAME)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

### `alembic/env.py`
```python
import asyncio
import os
from logging.config import fileConfig
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context
from models import Base  # imports all SQLAlchemy models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DATABASE_URL = os.environ["DATABASE_URL"]  # postgresql+asyncpg://...


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### `alembic/versions/0001_initial_schema.py`
```python
"""Initial schema — Phase 1 tables

Revision ID: 0001
Revises: 
Create Date: 2026-06-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, TIMESTAMPTZ

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgcrypto for gen_random_uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # agents
    op.create_table(
        "agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("agent_type", sa.String(32), nullable=False, server_default="sdk"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(256)),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint("status IN ('active','archived','deprecated')", name="ck_agents_status"),
        sa.CheckConstraint("agent_type IN ('sdk','declarative')", name="ck_agents_type"),
    )
    op.create_index("idx_agents_team", "agents", ["team"])
    op.create_index("idx_agents_status", "agents", ["status"])

    # workflows (needed before agent_versions FK)
    op.create_table(
        "workflows",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(256)),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_workflows_status"),
    )
    op.create_index("idx_workflows_team", "workflows", ["team"])
    op.create_index("idx_workflows_status", "workflows", ["status"])

    # workflow_versions
    op.create_table(
        "workflow_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("definition", JSONB, nullable=False),
        sa.Column("change_summary", sa.Text),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(256)),
        sa.UniqueConstraint("workflow_id", "version_number", name="uq_workflow_versions"),
    )
    op.create_index("idx_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])

    # agent_versions
    op.create_table(
        "agent_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("image_tag", sa.String(512)),
        sa.Column("workflow_id", UUID(as_uuid=True), sa.ForeignKey("workflows.id"), nullable=True),
        sa.Column("tools", JSONB, nullable=False, server_default="[]"),
        sa.Column("eval_passed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("git_sha", sa.String(64)),
        sa.Column("git_branch", sa.String(256)),
        sa.Column("notes", sa.Text),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(256)),
        sa.UniqueConstraint("agent_id", "version_number", name="uq_agent_versions"),
        sa.CheckConstraint("status IN ('pending','eval_passed','eval_failed','deployed','retired')", name="ck_agent_versions_status"),
    )
    op.create_index("idx_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_index("idx_agent_versions_status", "agent_versions", ["status"])

    # deployments
    op.create_table(
        "deployments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("agent_versions.id"), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False, server_default="production"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("replicas", sa.Integer, nullable=False, server_default="1"),
        sa.Column("canary_percent", sa.Integer),
        sa.Column("k8s_namespace", sa.String(128), nullable=False),
        sa.Column("k8s_deployment_name", sa.String(256)),
        sa.Column("error_message", sa.Text),
        sa.Column("deployed_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("terminated_at", TIMESTAMPTZ),
        sa.Column("deployed_by", sa.String(256)),
        sa.Column("previous_version_id", UUID(as_uuid=True), sa.ForeignKey("agent_versions.id"), nullable=True),
        sa.CheckConstraint("environment IN ('production','staging','canary')", name="ck_deployments_env"),
        sa.CheckConstraint("status IN ('pending','deploying','running','failed','rolled_back','terminated')", name="ck_deployments_status"),
        sa.CheckConstraint("canary_percent BETWEEN 0 AND 100", name="ck_canary_percent"),
    )
    op.create_index("idx_deployments_agent_id", "deployments", ["agent_id"])
    op.create_index("idx_deployments_status", "deployments", ["status"])
    op.create_index("idx_deployments_deployed_at", "deployments", ["deployed_at"], postgresql_ops={"deployed_at": "DESC"})

    # opa_decisions (must be created BEFORE approvals — approvals has FK to opa_decisions)
    op.create_table(
        "opa_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.Text, nullable=False),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("policy_version", sa.Text, nullable=False),
        sa.Column("input_snapshot", JSONB, nullable=False),
        sa.Column("deny_reason", sa.Text),
        sa.Column("thread_id", sa.Text),
        sa.Column("trace_id", sa.Text),
        sa.Column("decided_at", TIMESTAMPTZ, server_default=sa.text("now()")),
        sa.CheckConstraint("decision IN ('allow','deny','require_approval')", name="ck_opa_decisions_decision"),
    )
    op.create_index("idx_opa_decisions_agent", "opa_decisions", ["agent_name", "decided_at"],
                    postgresql_ops={"decided_at": "DESC"})
    op.create_index("idx_opa_decisions_thread", "opa_decisions", ["thread_id"],
                    postgresql_where=sa.text("thread_id IS NOT NULL"))
    op.create_index("idx_opa_decisions_decision", "opa_decisions", ["decision", "decided_at"],
                    postgresql_ops={"decided_at": "DESC"})

    # approvals
    op.create_table(
        "approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(256), nullable=False),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("tool_args", JSONB, nullable=False, server_default="{}"),
        sa.Column("risk_level", sa.String(32), nullable=False, server_default="high"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("reviewer_id", sa.String(256)),
        sa.Column("reviewer_notes", sa.Text),
        sa.Column("trace_id", sa.String(256)),
        sa.Column("decision_at", TIMESTAMPTZ),
        sa.Column("expires_at", TIMESTAMPTZ, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        # C-01: session_id enables reviewer UI to de-anonymize PII in tool_args via POST /scan/deanonymize
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        # C-17/C-18: FK to the OPA decision that triggered this approval row
        sa.Column("opa_decision_id", UUID(as_uuid=True), sa.ForeignKey("opa_decisions.id"), nullable=True),
        sa.CheckConstraint("risk_level IN ('high','critical')", name="ck_approvals_risk"),
        sa.CheckConstraint("status IN ('pending','approved','rejected','timed_out')", name="ck_approvals_status"),
    )
    op.create_index("idx_approvals_agent_id", "approvals", ["agent_id"])
    op.create_index("idx_approvals_status", "approvals", ["status"])
    op.create_index("idx_approvals_thread_id", "approvals", ["thread_id"])
    op.create_index("idx_approvals_expires_at_pending", "approvals", ["expires_at"],
                    postgresql_where=sa.text("status = 'pending'"))
    op.create_index("idx_approvals_session_id", "approvals", ["session_id"],
                    postgresql_where=sa.text("session_id IS NOT NULL"))
    op.create_index("idx_approvals_opa_decision", "approvals", ["opa_decision_id"],
                    postgresql_where=sa.text("opa_decision_id IS NOT NULL"))

    # agent_policies
    op.create_table(
        "agent_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("rego_policy", sa.Text, nullable=False),
        sa.Column("tool_allowlist", JSONB, nullable=False, server_default="[]"),
        sa.Column("risk_map", JSONB, nullable=False, server_default="{}"),
        sa.Column("configmap_name", sa.String(256)),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("generated_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("generated_from_version_id", UUID(as_uuid=True), sa.ForeignKey("agent_versions.id"), nullable=True),
    )
    op.create_index("idx_agent_policies_agent_id", "agent_policies", ["agent_id"])

    # pii_mappings
    op.create_table(
        "pii_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.Text, nullable=False),
        sa.Column("placeholder", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("encrypted_value", sa.Text, nullable=False),
        sa.Column("encryption_key_ref", sa.Text, nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, server_default=sa.text("now()")),
        sa.Column("expires_at", TIMESTAMPTZ, server_default=sa.text("now() + INTERVAL '1 hour'")),
    )
    op.create_index("idx_pii_mappings_session", "pii_mappings", ["session_id"])
    op.create_index("idx_pii_mappings_expiry", "pii_mappings", ["expires_at"])


def downgrade() -> None:
    op.drop_table("pii_mappings")
    op.drop_table("agent_policies")
    op.drop_table("approvals")       # drop before opa_decisions (FK dependency)
    op.drop_table("opa_decisions")
    op.drop_table("deployments")
    op.drop_table("agent_versions")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
    op.drop_table("agents")
```

### Migration Commands

```bash
# Initial setup (run once in new environment)
cd services/registry-api
export DATABASE_URL="postgresql+asyncpg://agentshield_user:password@pgbouncer.agentshield-platform:5432/agentshield"

# Run migrations
alembic upgrade head

# Check current revision
alembic current

# Generate new migration from model changes
alembic revision --autogenerate -m "add_slack_webhook_to_agents"

# Downgrade (emergency rollback)
alembic downgrade -1

# In K8s: run as init container before registry-api starts
# containers:
# - name: alembic-migrate
#   image: registry.internal/agentshield/registry-api:latest
#   command: ["alembic", "upgrade", "head"]
#   env:
#   - name: DATABASE_URL
#     valueFrom:
#       secretKeyRef:
#         name: postgres-urls
#         key: agentshield
```

---

## State Transition Diagrams

### Agent Deployment Status
```
pending → deploying → running
                   ↘ failed
running → rolled_back
running → terminated
```

### Approval Status
```
pending → approved (reviewer decides)
        → rejected (reviewer decides)
        → timed_out (expires_at < now(), background job)
```

### Agent Version Status
```
pending → eval_passed (CI reports success)
        → eval_failed (CI reports failure)
eval_passed → deployed (when deployment created)
deployed → retired (when newer version deployed)
```

---

## Notes on the `langgraph` Database

The `langgraph` database is managed by LangGraph's `PostgresSaver` — not by our Alembic migrations. It creates its own schema on first connection.

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async def setup_checkpointer():
    checkpointer = AsyncPostgresSaver.from_conn_string(
        "postgresql+asyncpg://langgraph_user:password@pgbouncer:5432/langgraph"
    )
    await checkpointer.setup()  # creates checkpoints, writes, blobs tables
    return checkpointer
```

Tables created by LangGraph in the `langgraph` database:
- `checkpoints` — thread state snapshots
- `checkpoint_writes` — pending writes during execution
- `checkpoint_blobs` — large state blobs

**Important:** Use `DIRECT_DATABASE_URL` (bypassing PgBouncer) for the LangGraph LISTEN/NOTIFY connection, because PgBouncer transaction mode doesn't support LISTEN. Set `pool_mode=session` for the `langgraph` database in PgBouncer config, or use a direct connection.

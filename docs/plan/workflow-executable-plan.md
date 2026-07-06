# Workflow Executable — Implementation Plan

**Goal**: Make `executable = Agent | Workflow` real: rename the old "canvas workflow" concept to "agent graph", introduce Workflow as a first-class composite executable (collection of existing agents), and wire run-tree orchestration through the shared substrate (triggers, runs, playground, Studio builder).

**Architecture**: The ~90% shared substrate (triggers, `agent_runs` spine, `run_steps`, memory, playground, publish gate) already exists from Phases 1–9. The only genuinely new branch point is orchestration: a Workflow run produces a `parent_run_id` tree via the already-present `agent_runs.parent_run_id` column. Two migrations handle the rename + new tables; a background-task orchestrator in registry-api handles sequential dispatch for MVP. The new Studio builder page lets users pick from their existing agents (not inline-define new ones), resolving the root UX problem.

**Tech Stack**: Python 3.12 / FastAPI / SQLAlchemy 2.0 / Alembic (backend); TypeScript / React / React Flow / React Query / Zustand (frontend); Bash + kubectl + httpx (e2e); Helm (chart).

---

## Constitution Check

| CLAUDE.md Principle | Status | Justification |
|---|---|---|
| E2E test for every new endpoint | PASS | suite-29 covers composite workflow CRUD + run tree; registers in run-all.sh |
| Image version bump in deploy-cpe2e.sh | PASS | Task W5-2 bumps registry-api, studio, declarative-runner |
| Image version bump in values.yaml | PASS | Task W5-2 updates charts/agentshield/values.yaml |
| Never reuse an existing image tag | PASS | 0.2.55→0.2.56, 0.1.42→0.1.43, 0.1.6→0.1.7 |
| TypeScript: `npx tsc --noEmit` must pass | PASS | Task W4-3 runs TS validation before reporting done |
| Python: `ast.parse` syntax check | PASS | Task W4-4 verifies all new Python files |
| Alembic migrations sequential from 0026 | PASS | 0026 (rename) and 0027 (new tables) |
| Migrations idempotent / guarded | PASS | Both migrations use IF EXISTS guards |
| Post-impl checklist before done | PASS | Task W5-2 and W5-3 cover all checklist items |
| docs/experience/playground.md update | PASS | Phase W4 covers run-tree view in Studio |

---

## File Structure

Every file created or modified is listed here. Tasks reference these paths exactly.

### Created (new files)

| Path | Responsibility |
|---|---|
| `services/registry-api/alembic/versions/0026_rename_workflows_to_agent_graphs.py` | Rename tables + column; data-preserving; downgrade reverses |
| `services/registry-api/alembic/versions/0027_add_composite_workflows.py` | New `workflows`, `workflow_members`; add `workflow_id` FK to `agent_triggers` + `agent_runs` |
| `services/registry-api/routers/composite_workflows.py` | CRUD + member management + run dispatch + run tree for composite workflows (`/api/v1/workflows`) |
| `services/declarative-runner/orchestrator.py` | `WorkflowOrchestrator`: sequential member dispatch + child `AgentRun` creation + result passing |
| `studio/src/nodes/WorkflowMemberNode.tsx` | React Flow node that renders an existing agent reference (name, team, tools count) |
| `studio/src/components/AddAgentModal.tsx` | Modal picker: loads `GET /api/v1/agents` for the user's team, select → add to canvas |
| `studio/src/pages/WorkflowBuilderPage.tsx` | Full-screen React Flow canvas for composite workflow composition |
| `studio/src/pages/AgentGraphsPage.tsx` | List page for agent graphs (renamed from WorkflowsPage) |
| `scripts/e2e/suite-29-workflow-composite.sh` | E2e suite: composite workflow CRUD + sequential run + run tree |

### Modified (existing files)

| Path | Change |
|---|---|
| `services/registry-api/models.py` | Add `AgentGraph`, `AgentGraphVersion`, `CompositeWorkflow`, `WorkflowMember`; rename `Workflow→AgentGraph`; update `AgentVersion.agent_graph_id`; add `workflow_id` to `AgentRun`, `AgentTrigger` |
| `services/registry-api/schemas.py` | Rename `Workflow*→AgentGraph*` schemas; update `AgentVersionCreate/Response` field; add `CompositeWorkflow*`, `WorkflowMember*`, `WorkflowRunCreate`, `WorkflowRunTreeResponse`, `InternalRunStartRequest` additions |
| `services/registry-api/routers/workflows.py` | Repurpose: change prefix to `/api/v1/agent-graphs`, rename all model references `Workflow→AgentGraph`, rename schema imports |
| `services/registry-api/routers/agent_runs.py` | Add `GET /api/v1/agent-runs/{run_id}/children` endpoint |
| `services/registry-api/routers/internal.py` | Support `workflow_id` targeting in `InternalRunStartRequest`; dispatch to `WorkflowOrchestrator` |
| `services/registry-api/main.py` | Import `composite_workflows_router`; register it; rename `workflows_router` import alias |
| `services/declarative-runner/config.py` | Add `COMPOSITE_WORKFLOW_ID: str | None = None` env var |
| `services/declarative-runner/main.py` | Add `POST /workflow-run` endpoint (activated only when `COMPOSITE_WORKFLOW_ID` is set) |
| `studio/src/utils/workflowSerializer.ts` | Add `CompositeWorkflowNode`, `CompositeWorkflowDefinition`, `serializeCompositeWorkflow`, `deserializeCompositeWorkflow` |
| `studio/src/stores/workflowStore.ts` | Add `compositeWorkflowId: string | null` + `compositeWorkflowName: string | null` state; add `markCompositeWorkflowSaved` action |
| `studio/src/api/registryApi.ts` | Rename `listWorkflows→listAgentGraphs`, `getWorkflow→getAgentGraph`, etc.; add all composite workflow API functions |
| `studio/src/pages/WorkflowsPage.tsx` | Pivot: list composite workflows; links to WorkflowBuilderPage; "Add Existing Agent" CTA |
| `studio/src/pages/CanvasPage.tsx` | Update: call `getAgentGraph` / `listAgentGraphs` instead of `getWorkflow` / `listWorkflows` |
| `studio/src/components/Canvas.tsx` | Update: call `updateAgentGraph`, `deployAgentGraph` instead of `updateWorkflow`, `deployWorkflow` |
| `studio/src/main.tsx` | Add routes `/workflows`, `/workflows/new`, `/workflows/:id/builder`, `/agent-graphs`, `/agent-graphs/new`, `/agent-graphs/:id` |
| `scripts/deploy-cpe2e.sh` | Bump `REGISTRY_API_TAG` 0.2.55→0.2.56, `STUDIO_TAG` 0.1.42→0.1.43, `DECLARATIVE_RUNNER_TAG` 0.1.6→0.1.7; update header comment |
| `charts/agentshield/values.yaml` | Bump same three image tags |
| `scripts/e2e/run-all.sh` | Register `suite-29-workflow-composite.sh` |
| `scripts/e2e/suite-2-lifecycle.sh` | Update `/api/v1/workflows/` URL references → `/api/v1/agent-graphs/` |
| `scripts/e2e/suite-8-playground.sh` | Update `/api/v1/workflows/` URL references → `/api/v1/agent-graphs/` |
| `scripts/e2e/suite-14-consumer-chat.sh` | Update `/api/v1/workflows/` URL references → `/api/v1/agent-graphs/` |

---

## Key Interfaces

### SQLAlchemy Models

```python
# NEW: AgentGraph (models.py — replaces Workflow class)
class AgentGraph(Base):
    __tablename__ = "agent_graphs"
    id: Mapped[uuid.UUID]
    name: Mapped[str]              # VARCHAR(256)
    team: Mapped[str]              # VARCHAR(128)
    description: Mapped[str | None]
    status: Mapped[str]            # draft|published|archived
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    created_by: Mapped[str | None]
    metadata_: Mapped[dict]        # JSONB column name "metadata"
    versions: Mapped[list["AgentGraphVersion"]]    # relationship
    agent_versions: Mapped[list["AgentVersion"]]   # back-ref

# NEW: AgentGraphVersion (replaces WorkflowVersion class)
class AgentGraphVersion(Base):
    __tablename__ = "agent_graph_versions"
    id: Mapped[uuid.UUID]
    agent_graph_id: Mapped[uuid.UUID]    # FK agent_graphs.id CASCADE
    version_number: Mapped[int]
    definition: Mapped[dict]             # JSONB canvas graph
    change_summary: Mapped[str | None]
    created_at: Mapped[datetime]
    created_by: Mapped[str | None]

# NEW: CompositeWorkflow
class CompositeWorkflow(Base):
    __tablename__ = "workflows"
    id: Mapped[uuid.UUID]
    name: Mapped[str]
    team: Mapped[str]
    description: Mapped[str | None]
    execution_shape: Mapped[str]         # reactive|durable DEFAULT durable
    memory_enabled: Mapped[bool]         # DEFAULT false
    orchestration: Mapped[str]           # sequential|supervisor|handoff DEFAULT sequential
    status: Mapped[str]                  # draft|published|archived DEFAULT draft
    publish_status: Mapped[str]          # DEFAULT private
    created_by: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    members: Mapped[list["WorkflowMember"]]  # relationship CASCADE

# NEW: WorkflowMember
class WorkflowMember(Base):
    __tablename__ = "workflow_members"
    workflow_id: Mapped[uuid.UUID]   # PK, FK workflows.id CASCADE
    agent_id: Mapped[uuid.UUID]      # PK, FK agents.id
    role: Mapped[str | None]
    position: Mapped[int | None]
    routing: Mapped[dict]            # JSONB DEFAULT {}
    added_at: Mapped[datetime]
    agent: Mapped["Agent"]           # relationship

# MODIFIED: AgentVersion
class AgentVersion(Base):
    agent_graph_id: Mapped[uuid.UUID | None]   # was workflow_id
    agent_graph: Mapped["AgentGraph | None"]   # was workflow relationship

# MODIFIED: AgentRun
class AgentRun(Base):
    workflow_id: Mapped[uuid.UUID | None]   # NEW nullable FK workflows.id

# MODIFIED: AgentTrigger
class AgentTrigger(Base):
    workflow_id: Mapped[uuid.UUID | None]   # NEW nullable FK workflows.id
    # DB CHECK: num_nonnulls(agent_id, workflow_id) = 1
```

### FastAPI Router: `composite_workflows.py`

```python
router = APIRouter(prefix="/api/v1/workflows", tags=["composite-workflows"])

@router.get("/", response_model=list[CompositeWorkflowResponse])
async def list_workflows(team: str | None = Query(None), db=Depends(get_db)) -> ...:

@router.post("/", response_model=CompositeWorkflowResponse, status_code=201)
async def create_workflow(body: CompositeWorkflowCreate, user=Depends(get_optional_user), db=Depends(get_db)) -> ...:

@router.get("/{workflow_id}", response_model=CompositeWorkflowWithMembersResponse)
async def get_workflow(workflow_id: uuid.UUID, db=Depends(get_db)) -> ...:

@router.patch("/{workflow_id}", response_model=CompositeWorkflowResponse)
async def update_workflow(workflow_id: uuid.UUID, body: CompositeWorkflowUpdate, db=Depends(get_db)) -> ...:

@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: uuid.UUID, db=Depends(get_db)) -> None:

@router.post("/{workflow_id}/members", response_model=WorkflowMemberResponse, status_code=201)
async def add_member(workflow_id: uuid.UUID, body: WorkflowMemberCreate, db=Depends(get_db)) -> ...:

@router.delete("/{workflow_id}/members/{agent_id}", status_code=204)
async def remove_member(workflow_id: uuid.UUID, agent_id: uuid.UUID, db=Depends(get_db)) -> None:

@router.post("/{workflow_id}/runs", response_model=WorkflowRunStartResponse, status_code=202)
async def trigger_run(workflow_id: uuid.UUID, body: WorkflowRunCreate, db=Depends(get_db)) -> ...:

@router.get("/{workflow_id}/runs/{run_id}/tree", response_model=WorkflowRunTreeResponse)
async def get_run_tree(workflow_id: uuid.UUID, run_id: uuid.UUID, db=Depends(get_db)) -> ...:

@router.get("/{workflow_id}/runs", response_model=list[AgentRunResponse])
async def list_workflow_runs(workflow_id: uuid.UUID, db=Depends(get_db), limit: int = Query(20), offset: int = Query(0)) -> ...:
```

### WorkflowOrchestrator (`orchestrator.py`)

```python
class WorkflowOrchestrator:
    def __init__(self, workflow: CompositeWorkflow, parent_run_id: uuid.UUID,
                 db: AsyncSession, registry_url: str): ...

    async def run_sequential(self, input_payload: dict) -> dict:
        """Execute workflow_members ORDER BY position.
        Passes prior agent's output as next agent's input.
        Creates child AgentRun rows with parent_run_id set.
        Updates parent AgentRun on completion/failure.
        Returns final agent's output dict."""

    async def _dispatch_agent(self, agent: Agent, input_message: str,
                               step_number: int) -> tuple[str, str]:
        """POST to agent's production pod /chat endpoint.
        Returns (child_run_id, output_text).
        Creates child AgentRun via registry-api before dispatch."""
```

### TypeScript Serializer Functions

```typescript
// workflowSerializer.ts
export function serializeCompositeWorkflow(
  nodes: Node[],
  edges: Edge[],
  orchestration: 'sequential' | 'supervisor' | 'handoff'
): CompositeWorkflowDefinition

export function deserializeCompositeWorkflow(
  definition: CompositeWorkflowDefinition
): { nodes: Node[]; edges: Edge[] }
```

---

## Tasks

---

### Phase W1 — Executable Data Model (Rename + New Tables)

**Goal**: Rename `workflows → agent_graphs` in DB + models + routers. Create new `workflows` (composite) and `workflow_members` tables. Expose composite CRUD API. Backend only — no frontend changes.

---

#### W1-1: Migration 0026 — Rename workflows → agent_graphs

**Files**:
- CREATE `services/registry-api/alembic/versions/0026_rename_workflows_to_agent_graphs.py`

**Interface contract**:
```python
def upgrade() -> None:
    op.rename_table("workflows", "agent_graphs")
    op.rename_table("workflow_versions", "agent_graph_versions")
    op.alter_column("agent_versions", "workflow_id", new_column_name="agent_graph_id")
    op.drop_index("idx_workflows_team", table_name="agent_graphs")
    op.create_index("idx_agent_graphs_team", "agent_graphs", ["team"])
    op.drop_index("idx_workflows_status", table_name="agent_graphs")
    op.create_index("idx_agent_graphs_status", "agent_graphs", ["status"])
    op.drop_index("idx_workflow_versions_workflow_id", table_name="agent_graph_versions")
    op.create_index("idx_agent_graph_versions_agent_graph_id", "agent_graph_versions", ["agent_graph_id"])
    # Drop old FK + recreate pointing to agent_graphs
    op.drop_constraint("agent_versions_workflow_id_fkey", "agent_versions", type_="foreignkey")
    op.create_foreign_key(
        "agent_versions_agent_graph_id_fkey",
        "agent_versions", "agent_graphs", ["agent_graph_id"], ["id"]
    )

def downgrade() -> None:
    # Reverse all renames
```

**Acceptance criteria**:
- `psql -c "\dt"` shows `agent_graphs`, `agent_graph_versions`; no `workflows` / `workflow_versions` tables
- `psql -c "\d agent_versions"` shows column `agent_graph_id` (not `workflow_id`)
- Zero rows lost (COUNT(*) before == after)
- Alembic `alembic downgrade -1` succeeds and restores original names

**Dependencies**: None (first migration of this plan)

**Test cases**:
1. Happy path: migration upgrades on a DB with 5 existing workflow rows → all 5 rows in `agent_graphs`
2. Downgrade: runs cleanly with no data loss
3. Column rename: `agent_versions.agent_graph_id` present; FK integrity intact

**Verification**:
```bash
kubectl exec -n agentshield-platform $API_POD -- \
  python3 -c "import asyncio, asyncpg
async def t():
    c = await asyncpg.connect('postgresql://agentshield:agentshield@postgres:5432/agentshield')
    r = await c.fetchval(\"SELECT COUNT(*) FROM agent_graphs\"); print('agent_graphs:', r)
    r2 = await c.fetchval(\"SELECT COUNT(*) FROM information_schema.columns WHERE table_name='agent_versions' AND column_name='agent_graph_id'\")
    assert r2 == 1, 'column rename failed'
    print('PASS')
asyncio.run(t())"
```

---

#### W1-2: Migration 0027 — New composite workflows tables + FK additions

**Files**:
- CREATE `services/registry-api/alembic/versions/0027_add_composite_workflows.py`

**Interface contract**:
```python
def upgrade() -> None:
    # Create workflows table (composite executable)
    op.create_table("workflows",
        sa.Column("id", postgresql.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("execution_shape", sa.String(16), nullable=False, server_default="durable"),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("orchestration", sa.String(32), nullable=False, server_default="sequential"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("publish_status", sa.String(32), nullable=False, server_default="private"),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("execution_shape IN ('reactive','durable')", name="ck_workflows_execution_shape"),
        sa.CheckConstraint("orchestration IN ('sequential','supervisor','handoff')", name="ck_workflows_orchestration"),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_workflows_status"),
    )
    op.create_index("idx_workflows_team", "workflows", ["team"])
    op.create_index("idx_workflows_status", "workflows", ["status"])
    op.create_unique_constraint("uq_workflows_name_team", "workflows", ["name", "team"])

    # Create workflow_members table
    op.create_table("workflow_members", ...)

    # Add workflow_id to agent_triggers
    op.add_column("agent_triggers",
        sa.Column("workflow_id", postgresql.UUID(), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True))
    op.create_check_constraint(
        "ck_agent_triggers_target", "agent_triggers",
        "num_nonnulls(agent_id, workflow_id) = 1"
    )

    # Add workflow_id to agent_runs
    op.add_column("agent_runs",
        sa.Column("workflow_id", postgresql.UUID(), sa.ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True))
    op.create_index("idx_agent_runs_workflow_id", "agent_runs", ["workflow_id"],
        postgresql_where=sa.text("workflow_id IS NOT NULL"))
```

**Acceptance criteria**:
- `\dt` shows `workflow_members`, new `workflows`
- `agent_triggers` has `workflow_id` column, `ck_agent_triggers_target` constraint
- `agent_runs` has `workflow_id` column
- Existing `agent_triggers` rows pass the CHECK (`num_nonnulls(agent_id, workflow_id) = 1` — they all have `agent_id != NULL`)
- Downgrade removes all additions cleanly

**Dependencies**: W1-1 (0026 must run first)

**Test cases**:
1. Existing triggers with `agent_id` set and `workflow_id=NULL` pass CHECK
2. INSERT a trigger with both `agent_id` and `workflow_id` set → constraint violation
3. INSERT a trigger with neither set → constraint violation

**Verification**:
```bash
kubectl exec -n agentshield-platform $API_POD -- \
  python3 -c "import asyncio, asyncpg
async def t():
    c = await asyncpg.connect('postgresql://agentshield:agentshield@postgres:5432/agentshield')
    await c.fetchval('SELECT 1 FROM workflows LIMIT 1')
    await c.fetchval('SELECT 1 FROM workflow_members LIMIT 1')
    cols = await c.fetch(\"SELECT column_name FROM information_schema.columns WHERE table_name='agent_triggers'\")
    assert any(r['column_name']=='workflow_id' for r in cols)
    print('PASS')
asyncio.run(t())"
```

---

#### W1-3: Update models.py — new SQLAlchemy classes

**Files**:
- MODIFY `services/registry-api/models.py`

**Changes**:
1. Rename class `Workflow` → `AgentGraph` (`__tablename__ = "agent_graphs"`). Update `__table_args__` index names.
2. Rename class `WorkflowVersion` → `AgentGraphVersion` (`__tablename__ = "agent_graph_versions"`). Rename `workflow_id` field → `agent_graph_id`; update FK `"agent_graphs.id"`.
3. In `AgentVersion`: rename `workflow_id` → `agent_graph_id`, FK target `"agent_graphs.id"`. Rename relationship `workflow → agent_graph`; rename back-ref to `agent_versions` on `AgentGraph`.
4. Add class `CompositeWorkflow` (`__tablename__ = "workflows"`) — see Key Interfaces section.
5. Add class `WorkflowMember` (`__tablename__ = "workflow_members"`) — see data model doc.
6. In `AgentRun`: add `workflow_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("workflows.id"), nullable=True)`.
7. In `AgentTrigger`: add `workflow_id: Mapped[uuid.UUID | None] = mapped_column(_UUID, ForeignKey("workflows.id"), nullable=True)`.
8. Update `__all__` list: replace `"Workflow"`, `"WorkflowVersion"` with `"AgentGraph"`, `"AgentGraphVersion"`, `"CompositeWorkflow"`, `"WorkflowMember"`.

**Acceptance criteria**:
- `python3 -c "import ast; ast.parse(open('services/registry-api/models.py').read())"` passes
- `python3 -c "from models import AgentGraph, AgentGraphVersion, CompositeWorkflow, WorkflowMember"` passes
- No remaining references to class `Workflow` or `WorkflowVersion` (grep returns nothing)

**Dependencies**: W1-1, W1-2

**Test cases**:
1. Import `AgentGraph` from `models` — no ImportError
2. Import `CompositeWorkflow` from `models` — no ImportError
3. `WorkflowMember.__tablename__` == `"workflow_members"`
4. `AgentGraph.__tablename__` == `"agent_graphs"`

**Verification**:
```bash
cd /Users/kkalyan/repo/agent-platform
python3 -c "import ast; ast.parse(open('services/registry-api/models.py').read()); print('syntax OK')"
```

---

#### W1-4: Update schemas.py — renamed + new Pydantic schemas

**Files**:
- MODIFY `services/registry-api/schemas.py`

**Changes**:
1. Rename `WorkflowCreate → AgentGraphCreate`, `WorkflowUpdate → AgentGraphUpdate`, `WorkflowResponse → AgentGraphResponse`, `WorkflowVersionResponse → AgentGraphVersionResponse`, `WorkflowWithDefinitionResponse → AgentGraphWithDefinitionResponse`, `WorkflowDeployRequest → AgentGraphDeployRequest`.
2. In `AgentVersionCreate`: rename field `workflow_id → agent_graph_id`.
3. In `AgentVersionResponse`: rename field `workflow_id → agent_graph_id`.
4. In `InternalRunStartRequest`: make `agent_name: str | None = None`; add `workflow_id: uuid.UUID | None = None`; add `@model_validator` enforcing exactly one of `agent_name`/`workflow_id` is set.
5. Add new schemas: `CompositeWorkflowCreate`, `CompositeWorkflowUpdate`, `CompositeWorkflowResponse`, `CompositeWorkflowWithMembersResponse`, `WorkflowMemberCreate`, `WorkflowMemberResponse`, `WorkflowRunCreate`, `WorkflowRunStartResponse`, `WorkflowRunTreeResponse`.
6. Update `__all__` list at the bottom of the file.

**Key new schemas**:
```python
class CompositeWorkflowCreate(BaseModel):
    name: str = Field(..., max_length=256)
    team: str = Field(..., max_length=128)
    description: str | None = None
    execution_shape: str = Field("durable", pattern="^(reactive|durable)$")
    orchestration: str = Field("sequential", pattern="^(sequential|supervisor|handoff)$")
    memory_enabled: bool = False

class CompositeWorkflowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str; team: str; description: str | None
    execution_shape: str; orchestration: str; memory_enabled: bool
    status: str; publish_status: str; member_count: int
    created_at: datetime; updated_at: datetime; created_by: str | None

class WorkflowMemberCreate(BaseModel):
    agent_id: uuid.UUID
    role: str | None = None
    position: int | None = None

class WorkflowRunCreate(BaseModel):
    input_payload: dict[str, Any] = Field(default_factory=dict)
    trigger_type: str = Field("manual", pattern="^(manual|api|schedule|webhook)$")
    run_by: str | None = None

class WorkflowRunStartResponse(BaseModel):
    run_id: uuid.UUID
    workflow_id: uuid.UUID
    status: str
    started_at: datetime

class WorkflowRunTreeResponse(BaseModel):
    parent: AgentRunResponse
    children: list[AgentRunResponse]
```

**Acceptance criteria**:
- `python3 -c "import ast; ast.parse(open('services/registry-api/schemas.py').read())"` passes
- `grep -n "workflow_id" services/registry-api/schemas.py | grep -v "agent_graph_id\|CompositeWorkflow\|InternalRun\|RunCreate"` returns only lines in new composite workflow schemas (no stale old references)

**Dependencies**: W1-3

**Verification**:
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/schemas.py').read()); print('syntax OK')"
```

---

#### W1-5: Repurpose routers/workflows.py → agent-graphs; create routers/composite_workflows.py

**Files**:
- MODIFY `services/registry-api/routers/workflows.py`
- CREATE `services/registry-api/routers/composite_workflows.py`

**`routers/workflows.py` changes** (repurposed as agent-graphs router):
- `prefix = "/api/v1/agent-graphs"`; `tags = ["agent-graphs"]`
- All model imports: `Workflow → AgentGraph`, `WorkflowVersion → AgentGraphVersion`
- All schema imports: `WorkflowCreate → AgentGraphCreate`, etc.
- All local variable names and function docstrings updated to use "agent graph" language
- No behavioral changes — same CRUD + versioning + deploy + restore logic

**`routers/composite_workflows.py`** implements all endpoints from the contracts doc §2:
- `GET /api/v1/workflows` — list (team-filtered)
- `POST /api/v1/workflows` — create (validates name uniqueness per team)
- `GET /api/v1/workflows/{id}` — get with members
- `PATCH /api/v1/workflows/{id}` — update metadata
- `DELETE /api/v1/workflows/{id}` — archive (status='archived')
- `POST /api/v1/workflows/{id}/members` — add member (validates agent.team == workflow.team)
- `DELETE /api/v1/workflows/{id}/members/{agent_id}` — remove member
- `POST /api/v1/workflows/{id}/runs` — trigger run (validates sequential ordering; dispatches to WorkflowOrchestrator)
- `GET /api/v1/workflows/{id}/runs/{run_id}/tree` — run tree
- `GET /api/v1/workflows/{id}/runs` — list parent runs

**Acceptance criteria**:
- `GET /api/v1/agent-graphs` returns 200 (not 404)
- `GET /api/v1/workflows` returns 200 (not 404)
- `GET /api/v1/workflows/nonexistent` returns 404
- `python3 -c "import ast; ast.parse(open('services/registry-api/routers/composite_workflows.py').read())"` passes
- Existing canvas tests (suite-2, suite-8, suite-14) pass after URL updates in W1-5

**Dependencies**: W1-3, W1-4

**Test cases**:
1. Create composite workflow → 201 with `id`
2. Create duplicate name+team → 409
3. List workflows for team → returns only that team's workflows
4. Add member from different team → 422
5. Get non-existent workflow → 404

**Verification**:
```bash
python3 -c "import ast; ast.parse(open('services/registry-api/routers/composite_workflows.py').read()); print('syntax OK')"
python3 -c "import ast; ast.parse(open('services/registry-api/routers/workflows.py').read()); print('syntax OK')"
```

---

#### W1-6: Update main.py + routers/agent_runs.py

**Files**:
- MODIFY `services/registry-api/main.py`
- MODIFY `services/registry-api/routers/agent_runs.py`

**`main.py` changes**:
- Change `from routers.workflows import router as workflows_router` → `from routers.workflows import router as agent_graphs_router`
- Add `from routers.composite_workflows import router as composite_workflows_router`
- Register `composite_workflows_router` with `app.include_router`

**`routers/agent_runs.py` changes**:
- Add `GET /api/v1/agent-runs/{run_id}/children` endpoint:
  ```python
  @router.get("/{run_id}/children", response_model=list[AgentRunResponse])
  async def list_child_runs(run_id: uuid.UUID, db=Depends(get_db)) -> list[AgentRun]:
      result = await db.execute(
          select(AgentRun).where(AgentRun.parent_run_id == run_id).order_by(AgentRun.started_at)
      )
      return result.scalars().all()
  ```

**Acceptance criteria**:
- `python3 -c "import ast; ast.parse(open('services/registry-api/main.py').read())"` passes
- Registry-api starts without import errors
- `GET /api/v1/agent-runs/{run_id}/children` returns 200 for a valid run_id (empty list is OK)

**Dependencies**: W1-5

---

### [CP-Wa] Checkpoint Alpha — Rename + CRUD Smoke Test

*After W1-1 through W1-6 are complete. Run before starting Phase W2.*

**Smoke script**:
```bash
API_POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n agentshield-platform "$API_POD" -- python3 -c "
import httpx, json

B = 'http://localhost:8000/api/v1'

# T-CPA-001: agent-graphs endpoint reachable
r = httpx.get(f'{B}/agent-graphs')
assert r.status_code == 200, f'agent-graphs 200: {r.status_code}'
print('PASS T-CPA-001: GET /agent-graphs')

# T-CPA-002: old /workflows 404 (freed for composite)
# (After composite_workflows router is registered, it should return 200)
r = httpx.get(f'{B}/workflows')
assert r.status_code == 200, f'composite workflows 200: {r.status_code}'
print('PASS T-CPA-002: GET /workflows (composite)')

# T-CPA-003: create a composite workflow
import time
ts = int(time.time())
r = httpx.post(f'{B}/workflows', json={
  'name': f'smoke-wf-{ts}', 'team': 'platform', 'orchestration': 'sequential'
})
assert r.status_code == 201, f'create 201: {r.text}'
wf_id = r.json()['id']
print('PASS T-CPA-003: POST /workflows 201')

# T-CPA-004: duplicate name → 409
r = httpx.post(f'{B}/workflows', json={
  'name': f'smoke-wf-{ts}', 'team': 'platform', 'orchestration': 'sequential'
})
assert r.status_code == 409, f'dup 409: {r.status_code}'
print('PASS T-CPA-004: duplicate 409')

# T-CPA-005: get workflow by id
r = httpx.get(f'{B}/workflows/{wf_id}')
assert r.status_code == 200, f'get 200: {r.status_code}'
assert r.json()['member_count'] == 0
print('PASS T-CPA-005: GET /workflows/{id}')

# T-CPA-006: archive workflow
r = httpx.delete(f'{B}/workflows/{wf_id}')
assert r.status_code == 204
print('PASS T-CPA-006: DELETE 204')

print('=== CP-Wa ALL PASS ===')
"
```

---

### Phase W2 — Workflow Definition JSON + Frontend API Layer

**Goal**: Update the TypeScript serializer, the registry API client, and the canvas page to use the renamed `/api/v1/agent-graphs/` endpoint. No new pages yet — just the API glue.

---

#### W2-1: Update workflowSerializer.ts — add composite workflow types

**Files**:
- MODIFY `studio/src/utils/workflowSerializer.ts`

**Changes**:
1. Add `CompositeWorkflowNode`, `CompositeWorkflowDefinition` types.
2. Add `serializeCompositeWorkflow(nodes: Node[], edges: Edge[], orchestration: string): CompositeWorkflowDefinition`.
3. Add `deserializeCompositeWorkflow(definition: CompositeWorkflowDefinition): { nodes: Node[]; edges: Edge[] }`.
4. Existing `serializeWorkflow` / `deserializeWorkflow` are unchanged (used by canvas agent-graph builder).

**Acceptance criteria**:
- `npx tsc --noEmit` passes
- `serializeCompositeWorkflow` produces nodes with `type: 'workflow_member'` and `data.agent_id` set

**Dependencies**: W1-4 (schema shapes defined in contracts doc)

**Verification**:
```bash
cd /Users/kkalyan/repo/agent-platform/studio && npx tsc --noEmit
```

---

#### W2-2: Update registryApi.ts — rename + add composite workflow functions

**Files**:
- MODIFY `studio/src/api/registryApi.ts`

**Changes**:
1. Rename functions: `listWorkflows → listAgentGraphs`, `getWorkflow → getAgentGraph`, `createWorkflow → createAgentGraph`, `updateWorkflow → updateAgentGraph`, `deployWorkflow → deployAgentGraph`.
2. Update all URL strings from `/api/v1/workflows` → `/api/v1/agent-graphs` in the renamed functions.
3. Add new functions (see contracts doc §7): `listCompositeWorkflows`, `createCompositeWorkflow`, `getCompositeWorkflow`, `updateCompositeWorkflow`, `deleteCompositeWorkflow`, `addWorkflowMember`, `removeWorkflowMember`, `triggerWorkflowRun`, `getWorkflowRunTree`, `listWorkflowRuns`.
4. Add `CompositeWorkflow`, `CompositeWorkflowWithMembers`, `WorkflowMember`, `WorkflowRunResult`, `WorkflowRunTree` TypeScript interfaces.

**Acceptance criteria**:
- `npx tsc --noEmit` passes
- `listAgentGraphs` calls `/api/v1/agent-graphs`
- `listCompositeWorkflows` calls `/api/v1/workflows`
- No remaining `listWorkflows` / `getWorkflow` (old names) references in non-canvas pages

**Dependencies**: W2-1

**Verification**:
```bash
cd /Users/kkalyan/repo/agent-platform/studio && npx tsc --noEmit
```

---

#### W2-3: Update CanvasPage.tsx + Canvas.tsx — use agent-graphs endpoint

**Files**:
- MODIFY `studio/src/pages/CanvasPage.tsx`
- MODIFY `studio/src/components/Canvas.tsx`

**Changes**:

`CanvasPage.tsx`:
- Replace `getWorkflow` import with `getAgentGraph`
- Update `queryKey: ['workflow', id]` → `['agent-graph', id]`
- Update `queryFn: () => getAgentGraph(id!)`

`Canvas.tsx`:
- Replace `updateWorkflow` import with `updateAgentGraph`
- Replace `deployWorkflow as deployWorkflowApi` with `deployAgentGraph as deployAgentGraphApi`
- Update call sites

**Acceptance criteria**:
- `npx tsc --noEmit` passes
- Saving an agent-graph canvas calls `/api/v1/agent-graphs/{id}` (verify in browser network tab or with a test run of the canvas)

**Dependencies**: W2-2

**Verification**:
```bash
cd /Users/kkalyan/repo/agent-platform/studio && npx tsc --noEmit
```

---

#### W2-4: Create AgentGraphsPage.tsx; update WorkflowsPage.tsx + main.tsx routing

**Files**:
- CREATE `studio/src/pages/AgentGraphsPage.tsx`
- MODIFY `studio/src/pages/WorkflowsPage.tsx`
- MODIFY `studio/src/main.tsx`

**`AgentGraphsPage.tsx`**: Copy the existing `WorkflowsPage.tsx` content, rename it to `AgentGraphsPage`, call `listAgentGraphs()`, route to `/agent-graphs/new` and `/agent-graphs/:id`, title "Agent Graphs".

**`WorkflowsPage.tsx`** repurposed: Show composite workflows list. Call `listCompositeWorkflows()`. Show columns: name, team, orchestration, status, member count, created. "New Workflow" button → `/workflows/new`. Row click → `/workflows/:id/builder`. Empty state: "No workflows yet. Create one to compose existing agents."

**`main.tsx` routing additions**:
```typescript
// Agent graph canvas (old canvas for single declarative agents)
<Route path="/agent-graphs" element={<AgentGraphsPage />} />
<Route path="/agent-graphs/new" element={<CanvasPage />} />
<Route path="/agent-graphs/:id" element={<CanvasPage />} />

// Composite workflow builder (new)
<Route path="/workflows" element={<WorkflowsPage />} />
<Route path="/workflows/new" element={<WorkflowBuilderPage />} />
<Route path="/workflows/:id/builder" element={<WorkflowBuilderPage />} />
```

Also update the sidebar navigation component (wherever the nav links live — typically `AppShell.tsx` or the sidebar) to add "Workflows" nav item pointing to `/workflows` and relabel "Workflows" → "Agent Graphs" for the canvas item.

**Acceptance criteria**:
- `npx tsc --noEmit` passes
- `/agent-graphs` renders the old canvas list
- `/workflows` renders the composite workflow list (empty on first load)

**Dependencies**: W2-2, W2-3

---

### Phase W3 — Run-Tree Orchestration

**Goal**: A composite workflow run creates a parent `AgentRun` row, then sequentially invokes each member agent, creating child `AgentRun` rows with `parent_run_id` set. The `/tree` endpoint returns the full run hierarchy.

---

#### W3-1: Create orchestrator.py in declarative-runner

**Files**:
- CREATE `services/declarative-runner/orchestrator.py`

**Interface** (see Key Interfaces section):
```python
class WorkflowOrchestrator:
    def __init__(self, workflow_id: str, parent_run_id: str, registry_url: str): ...

    async def run_sequential(self, members: list[dict], input_payload: dict) -> dict:
        """
        members: sorted list of {agent_id, agent_name, position, team}
        For each member:
          1. POST {registry_url}/api/v1/agent-runs to create child AgentRun
          2. Resolve agent's pod URL: http://{agent_name}-production.agents-{team}.svc.cluster.local:8080/chat
          3. POST /chat with current input
          4. On success: PATCH child AgentRun status=completed; set output = response text
          5. Pass response text as input to next agent
          6. On failure: PATCH child AgentRun status=failed; PATCH parent AgentRun status=failed; raise
        On all success: PATCH parent AgentRun status=completed; return last agent output
        """

    async def _create_child_run(self, agent_name: str, team: str, parent_run_id: str, input_msg: str) -> str:
        """POST to registry-api to create a child AgentRun. Returns child run_id."""

    async def _dispatch_agent(self, agent_name: str, team: str, input_msg: str) -> str:
        """POST /chat on agent pod. Returns output text. Raises on non-200 or network error."""
```

**Acceptance criteria**:
- `python3 -c "import ast; ast.parse(open('services/declarative-runner/orchestrator.py').read())"` passes
- `WorkflowOrchestrator` can be imported without errors

**Dependencies**: W1-6

**Verification**:
```bash
python3 -c "import ast; ast.parse(open('services/declarative-runner/orchestrator.py').read()); print('syntax OK')"
```

---

#### W3-2: Modify declarative-runner main.py + config.py

**Files**:
- MODIFY `services/declarative-runner/main.py`
- MODIFY `services/declarative-runner/config.py`

**`config.py`**: Add `COMPOSITE_WORKFLOW_ID: str | None = os.getenv("COMPOSITE_WORKFLOW_ID")`.

**`main.py`**: Add `POST /workflow-run` endpoint, active when `cfg.COMPOSITE_WORKFLOW_ID` is set:
```python
class WorkflowRunRequest(BaseModel):
    workflow_id: str
    parent_run_id: str
    members: list[dict]   # [{agent_id, agent_name, position, team}] sorted by position
    input_payload: dict

@app.post("/workflow-run")
async def workflow_run(req: WorkflowRunRequest):
    """Called by registry-api async orchestration task for composite workflow runs."""
    if not cfg.COMPOSITE_WORKFLOW_ID:
        raise HTTPException(status_code=404, detail="This runner is not a workflow orchestrator")
    orch = WorkflowOrchestrator(req.workflow_id, req.parent_run_id, cfg.REGISTRY_API_URL)
    asyncio.create_task(orch.run_sequential(req.members, req.input_payload))
    return {"status": "accepted", "parent_run_id": req.parent_run_id}
```

**Acceptance criteria**:
- `python3 -c "import ast; ast.parse(open('services/declarative-runner/main.py').read())"` passes
- `python3 -c "import ast; ast.parse(open('services/declarative-runner/config.py').read())"` passes

**Dependencies**: W3-1

---

#### W3-3: Modify routers/internal.py — workflow_id targeting

**Files**:
- MODIFY `services/registry-api/routers/internal.py`

**Changes**:
1. Import `CompositeWorkflow`, `WorkflowMember` from models.
2. In `POST /api/v1/internal/runs/start` handler: detect if `body.workflow_id` is set.
3. If `workflow_id` is set: look up `CompositeWorkflow` by id; validate `status != 'archived'`; create parent `AgentRun` with `agent_name=workflow.name`, `workflow_id=workflow.id`, `trigger_type=body.trigger_type`, `run_by=body.run_by`, `team=workflow.team`; fetch ordered members; call `_orchestrate_workflow_async(parent_run_id, workflow, members, body.trigger_payload or {})`.
4. `_orchestrate_workflow_async` uses `WorkflowOrchestrator.run_sequential` from `orchestrator.py` (registry-api imports the orchestrator class — it lives in declarative-runner so we copy the class or extract a shared module). Note: for MVP, the orchestration runs in-process in registry-api via a background asyncio task. The declarative-runner `/workflow-run` endpoint is wired for future pod-per-workflow deployment.

**Acceptance criteria**:
- `python3 -c "import ast; ast.parse(open('services/registry-api/routers/internal.py').read())"` passes
- `POST /internal/runs/start` with `{"workflow_id": "<uuid>", "trigger_type": "manual", "run_by": "test"}` returns `{"run_id": "..."}` and creates a parent AgentRun

**Dependencies**: W3-1, W3-2

---

#### W3-4: Add run-tree and run-dispatch to routers/composite_workflows.py

**Files**:
- MODIFY `services/registry-api/routers/composite_workflows.py`

**Changes**:

Add the `POST /{id}/runs` handler:
```python
@router.post("/{workflow_id}/runs", response_model=WorkflowRunStartResponse, status_code=202)
async def trigger_run(workflow_id: uuid.UUID, body: WorkflowRunCreate,
                      user=Depends(get_optional_user), db=Depends(get_db)):
    workflow = await _resolve_workflow(workflow_id, db)  # 404 if not found
    members = await _get_ordered_members(workflow.id, db)  # 422 if empty
    if not members:
        raise HTTPException(422, "Workflow has no members. Add at least one agent before running.")
    if workflow.orchestration != "sequential":
        raise HTTPException(422, f"Orchestration mode '{workflow.orchestration}' is not yet supported. Use 'sequential'.")
    if any(m.position is None for m in members):
        raise HTTPException(422, "Sequential workflow members are missing position values.")
    run_by = body.run_by or ((user or {}).get("sub") or "manual")
    # Create parent run
    parent_run = AgentRun(agent_name=workflow.name, workflow_id=workflow.id,
                          team=workflow.team, trigger_type=body.trigger_type,
                          run_by=run_by, status="queued",
                          input=json.dumps(body.input_payload)[:4000])
    db.add(parent_run); await db.flush()
    # Background orchestration
    import asyncio
    asyncio.create_task(_orchestrate(parent_run.id, workflow, members, body.input_payload, db_url))
    return WorkflowRunStartResponse(run_id=parent_run.id, workflow_id=workflow.id,
                                    status="queued", started_at=parent_run.started_at)
```

Add `GET /{id}/runs/{run_id}/tree` handler:
```python
@router.get("/{workflow_id}/runs/{run_id}/tree", response_model=WorkflowRunTreeResponse)
async def get_run_tree(workflow_id: uuid.UUID, run_id: uuid.UUID, db=Depends(get_db)):
    parent = await db.get(AgentRun, run_id)
    if not parent or str(parent.workflow_id) != str(workflow_id):
        raise HTTPException(404, "Run not found or does not belong to this workflow.")
    result = await db.execute(
        select(AgentRun).where(AgentRun.parent_run_id == run_id).order_by(AgentRun.started_at)
    )
    children = result.scalars().all()
    return WorkflowRunTreeResponse(parent=parent, children=children)
```

**Acceptance criteria**:
- `POST /workflows/{id}/runs` returns 202 with `run_id`
- After a few seconds, `GET /workflows/{id}/runs/{run_id}/tree` returns parent + child runs

**Dependencies**: W3-3, W1-5

---

#### W3-5: Update workflowStore.ts — composite workflow state

**Files**:
- MODIFY `studio/src/stores/workflowStore.ts`

**Changes**:
1. Add `compositeWorkflowId: string | null` and `compositeWorkflowName: string | null` to state.
2. Add `markCompositeWorkflowSaved(id: string, name: string, team: string): void` action.
3. Add `resetCompositeCanvas(): void` action (clears composite workflow state without affecting agent-graph canvas state).
4. The existing canvas state (`workflowId`, `nodes`, `edges`) remains — it is used by the agent-graph canvas. Composite workflow builder uses the new state fields.

**Acceptance criteria**:
- `npx tsc --noEmit` passes
- `useWorkflowStore.getState().compositeWorkflowId` initializes to `null`

**Dependencies**: W2-1

---

### [CP-Wb] Checkpoint Beta — Run-Tree Smoke Test

*After W3-1 through W3-5 are complete. Run before starting Phase W4.*

```bash
API_POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n agentshield-platform "$API_POD" -- python3 -c "
import httpx, time, json
B = 'http://localhost:8000/api/v1'

# Find two running agents in the same team for the smoke test
agents_r = httpx.get(f'{B}/agents', timeout=5)
agents = [a for a in agents_r.json().get('items', agents_r.json()) if a.get('status') == 'active']
if len(agents) < 1:
    print('SKIP: no active agents; deploy at least one agent to run CP-Wb')
    exit(0)
team = agents[0]['team']
same_team = [a for a in agents if a['team'] == team]
agent1 = same_team[0]
agent2 = same_team[1] if len(same_team) > 1 else same_team[0]

ts = int(time.time())

# T-CPB-001: create workflow
r = httpx.post(f'{B}/workflows', json={'name': f'cpb-wf-{ts}', 'team': team, 'orchestration': 'sequential'})
assert r.status_code == 201
wf_id = r.json()['id']

# T-CPB-002: add members
r1 = httpx.post(f'{B}/workflows/{wf_id}/members', json={'agent_id': agent1['id'], 'position': 1})
assert r1.status_code == 201, r1.text
r2 = httpx.post(f'{B}/workflows/{wf_id}/members', json={'agent_id': agent2['id'], 'position': 2})
assert r2.status_code == 201, r2.text
print('PASS T-CPB-001/002: create workflow + members')

# T-CPB-003: trigger run
r = httpx.post(f'{B}/workflows/{wf_id}/runs', json={'input_payload': {'message': 'smoke test'}, 'run_by': 'cpb'})
assert r.status_code == 202, r.text
run_id = r.json()['run_id']
print(f'PASS T-CPB-003: trigger run {run_id}')

# T-CPB-004: poll for parent run to exist
for _ in range(20):
    tree_r = httpx.get(f'{B}/workflows/{wf_id}/runs/{run_id}/tree', timeout=5)
    if tree_r.status_code == 200:
        tree = tree_r.json()
        status = tree['parent']['status']
        nc = len(tree['children'])
        print(f'  status={status}, children={nc}')
        if status in ('completed','failed') or nc >= 1:
            break
    time.sleep(2)

assert tree_r.status_code == 200
assert tree['parent']['workflow_id'] == wf_id
assert all(c['parent_run_id'] == run_id for c in tree['children'])
print('PASS T-CPB-004: run tree — parent.workflow_id set + children have parent_run_id')

print('=== CP-Wb ALL PASS ===')
"
```

---

### Phase W4 — Studio Workflow Builder

**Goal**: Studio users can build composite workflows by picking from their existing agents, save the definition, and view the run-tree output.

---

#### W4-1: Create WorkflowMemberNode.tsx + AddAgentModal.tsx

**Files**:
- CREATE `studio/src/nodes/WorkflowMemberNode.tsx`
- CREATE `studio/src/components/AddAgentModal.tsx`

**`WorkflowMemberNode.tsx`**:
```typescript
type WorkflowMemberNodeData = {
  agent_id: string;
  agent_name: string;
  role?: string;
  position?: number;
};

export const WorkflowMemberNode = memo(({ data, selected }: NodeProps<Node<WorkflowMemberNodeData, 'workflow_member'>>) => (
  // Renders: position badge (left), agent icon + agent_name, role chip, handles left+right
  // Selected: blue border; default: slate border
))
```

**`AddAgentModal.tsx`**:
```typescript
interface AddAgentModalProps {
  isOpen: boolean;
  team: string;               // filter agents to this team
  onClose: () => void;
  onAdd: (agent: AgentResponse) => void;  // callback when user selects an agent
}

export default function AddAgentModal({ isOpen, team, onClose, onAdd }: AddAgentModalProps)
// - useQuery: listAgents({ team }) — calls GET /api/v1/agents?team=<team>
// - Renders scrollable list of agents (name, description, execution_shape chip)
// - Search input to filter by name
// - "Add to Workflow" button per row → calls onAdd(agent)
// - Multiple adds supported before close
```

**Acceptance criteria**:
- `npx tsc --noEmit` passes
- `WorkflowMemberNode` renders without crashing when `data = { agent_id: 'test', agent_name: 'test-agent' }`
- `AddAgentModal` renders an empty list state without crashing

**Dependencies**: W2-2

---

#### W4-2: Create WorkflowBuilderPage.tsx

**Files**:
- CREATE `studio/src/pages/WorkflowBuilderPage.tsx`

**Behavior**:
1. On load with `id` param: fetch `getCompositeWorkflow(id)` → `deserializeCompositeWorkflow(members → nodes)`.
2. On load without `id`: empty canvas with a prompt ("Add agents from your team to build a workflow").
3. Toolbar: "Add Existing Agent" button → open `AddAgentModal(team=currentTeam)`.
4. `onAdd` callback: add a `WorkflowMemberNode` to the canvas with next available position.
5. Save: `POST /api/v1/workflows` (first save) or `PATCH /api/v1/workflows/{id}/members` (subsequent). On first save, open a modal to collect name + orchestration mode.
6. Run: "Run Workflow" button → `POST /api/v1/workflows/{id}/runs` → poll `/tree` endpoint → display run status panel (parent status + child agent rows with status + latency).
7. Navigation: "Back to Workflows" breadcrumb.

**Key state**: Uses `useWorkflowStore` for nodes/edges + new `compositeWorkflowId`/`compositeWorkflowName` fields from W3-5.

**Node types registered**: `{ workflow_member: WorkflowMemberNode }` (not `agent` — different canvas, different node type).

**Acceptance criteria**:
- `npx tsc --noEmit` passes
- Page renders at `/workflows/new` without crashing
- Adding an agent via modal adds a node to the React Flow canvas
- Saving creates a composite workflow and adds `workflow_members` rows

**Dependencies**: W4-1, W3-5, W2-4

---

#### W4-3: TypeScript validation

**Files**: No file changes — validation only.

```bash
cd /Users/kkalyan/repo/agent-platform/studio
npx tsc --noEmit
```

**Acceptance criteria**: Zero TypeScript errors across all modified/created `.ts` and `.tsx` files.

**Dependencies**: W4-2

---

#### W4-4: Python syntax validation for all new Python files

**Files**: No file changes — validation only.

```bash
for f in \
  services/registry-api/alembic/versions/0026_rename_workflows_to_agent_graphs.py \
  services/registry-api/alembic/versions/0027_add_composite_workflows.py \
  services/registry-api/routers/composite_workflows.py \
  services/declarative-runner/orchestrator.py \
  services/declarative-runner/config.py \
  services/declarative-runner/main.py \
  services/registry-api/models.py \
  services/registry-api/schemas.py \
  services/registry-api/routers/workflows.py \
  services/registry-api/routers/internal.py \
  services/registry-api/main.py; do
  python3 -c "import ast; ast.parse(open('$f').read()); print('OK: $f')"
done
```

**Acceptance criteria**: All files parse without SyntaxError.

**Dependencies**: W3-3, W4-2

---

### Phase W5 — E2E Tests + Image Bumps

**Goal**: Prove the composite workflow feature end-to-end with a runnable bash test suite. Bump all affected image tags. Register the new suite.

---

#### W5-1: Create suite-29-workflow-composite.sh

**Files**:
- CREATE `scripts/e2e/suite-29-workflow-composite.sh`

**Test cases** (minimum coverage per CLAUDE.md):

```
T-S29-001 — Create composite workflow (happy path)
T-S29-002 — Duplicate workflow name + team → 409
T-S29-003 — Add member agent from same team (happy path)
T-S29-004 — Add member agent from different team → 422
T-S29-005 — Trigger sequential run → parent AgentRun created (202)
T-S29-006 — Run tree endpoint returns parent + children with correct parent_run_id
T-S29-007 — Child AgentRuns carry workflow_id=NULL and parent_run_id=<parent>
T-S29-008 — Parent AgentRun carries workflow_id=<workflow_id>
T-S29-009 — Remove member → member_count decrements
T-S29-010 — Archive workflow → 204; subsequent run trigger → 422 or 404
```

**Script structure** (follows suite-28 pattern):
```bash
#!/usr/bin/env bash
# Suite 29: Composite Workflow (Decision 22 — executable = Agent | Workflow)
# Tests T-S29-001 through T-S29-010
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }
# ... test cases using kubectl exec + python httpx assertions
```

**Acceptance criteria**:
- All 10 test cases PASS on the local Docker Desktop cluster
- Script exits with code 0 on all-pass, non-zero on any failure
- Script is executable: `chmod +x scripts/e2e/suite-29-workflow-composite.sh`

**Dependencies**: W3-4

---

#### W5-2: Register suite + bump image tags

**Files**:
- MODIFY `scripts/e2e/run-all.sh`
- MODIFY `scripts/deploy-cpe2e.sh`
- MODIFY `charts/agentshield/values.yaml`

**`run-all.sh`**: Add `bash scripts/e2e/suite-29-workflow-composite.sh` after suite-28.

**`deploy-cpe2e.sh`**:
- `REGISTRY_API_TAG="0.2.56"` (was 0.2.55)
- `STUDIO_TAG="0.1.43"` (was 0.1.42)
- `DECLARATIVE_RUNNER_TAG="0.1.7"` (was 0.1.6)
- Update header comment to include "Decision 22 — composite workflows (rename agent_graphs, workflow members, run-tree orchestration)"

**`charts/agentshield/values.yaml`**: Update the three image tag values to match.

**Acceptance criteria**:
- `grep "REGISTRY_API_TAG\|STUDIO_TAG\|DECLARATIVE_RUNNER_TAG" scripts/deploy-cpe2e.sh` shows new tags
- `grep "0.2.56\|0.1.43\|0.1.7" charts/agentshield/values.yaml` confirms chart updated
- `grep "suite-29" scripts/e2e/run-all.sh` confirms suite registered

**Dependencies**: W5-1

---

#### W5-3: Update e2e suites that reference old /api/v1/workflows/ URL

**Files**:
- MODIFY `scripts/e2e/suite-2-lifecycle.sh`
- MODIFY `scripts/e2e/suite-8-playground.sh`
- MODIFY `scripts/e2e/suite-14-consumer-chat.sh`

**Change**: Replace all occurrences of `/api/v1/workflows` with `/api/v1/agent-graphs` in these three files. The endpoint behavior is unchanged; only the URL prefix changes.

**Acceptance criteria**:
- `grep "/api/v1/workflows" scripts/e2e/suite-2-lifecycle.sh` returns nothing (all replaced)
- `grep "/api/v1/workflows" scripts/e2e/suite-8-playground.sh` returns nothing
- `grep "/api/v1/workflows" scripts/e2e/suite-14-consumer-chat.sh` returns nothing
- Rerunning suite-2, suite-8, and suite-14 with the new registry-api image still PASS

**Dependencies**: W1-5

---

### [CP-Wc] Checkpoint Gamma — Full End-to-End Smoke Test

*After all phases complete. Runs the new e2e suite and confirms no regression.*

```bash
# Build images
bash scripts/deploy-cpe2e.sh

# Deploy
helm upgrade --install agentshield charts/agentshield \
  --namespace agentshield-platform --wait --timeout 5m

# Run new suite
bash scripts/e2e/suite-29-workflow-composite.sh

# Run regression suites
bash scripts/e2e/suite-2-lifecycle.sh
bash scripts/e2e/suite-8-playground.sh
bash scripts/e2e/suite-14-consumer-chat.sh

# TypeScript build clean
cd studio && npx tsc --noEmit && echo "TS OK"
```

Expected output: `T-S29-001 through T-S29-010 PASS`, zero regressions in suite-2/8/14, zero TypeScript errors.

---

## Execution Notes

### Deferred Items (not in this plan, explicitly out of scope)

| Item | Why deferred |
|---|---|
| Supervisor + handoff orchestration modes | Requires LLM routing (supervisor) or stateful edge evaluation (handoff). The `orchestration` column and CHECK constraint are in place; the Phase W3 orchestrator raises 422 for these modes. Plan separately when use case is validated. |
| Deploy-controller extension for workflow pods | Teaching deploy-controller to create a K8s pod per composite workflow (using declarative-runner in `COMPOSITE_WORKFLOW_ID` mode) is post-MVP. The registry-api background-task path is sufficient for sequential MVP and scales to dozens of workflows. |
| Deep workflow nesting (workflow of workflows) | The `workflow_id` column is a single FK; recursive trees require a CTE. Deferred — two-level tree covers all known use cases. |
| Workflow publish gate + eval gate | Phase W3 wires `trigger_run` but does not enforce `publish_status == 'published'`. Publish gate for workflows mirrors the agent lifecycle (Decision 20) and is a follow-on task. |
| SSE streaming for workflow run tree | Real-time SSE events for child run start/complete. The `/tree` polling endpoint is the MVP interaction. SSE for workflows is a post-MVP enhancement. |
| Workflow triggers (schedule/webhook) | The `agent_triggers.workflow_id` FK + `ck_agent_triggers_target` CHECK are added in migration 0027. The scheduler service and event gateway need a one-line change to pass `workflow_id` instead of `agent_name` to `internal/runs/start`. Not implemented in this plan — the FK and CHECK constraint are the prep work. |

### Breaking Change — Canvas URL

The canvas-graph endpoint moves from `/api/v1/workflows/` → `/api/v1/agent-graphs/`. Any external client (CI scripts, SDK code, PostmanL) calling the old URL will receive 404. The e2e suites are updated in W5-3. If there are other callers outside this repo, they must be notified separately.

### Migration Order

Alembic migrations must run in sequence. Migration 0027 depends on the `agent_graphs` table existing (created by 0026). Do not run 0027 before 0026 completes.

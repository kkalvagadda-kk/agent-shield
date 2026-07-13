# Contract — `agent_class` on create/patch (agents + workflows)

Authoritative request/response shapes carrying `agent_class`. Types are consistent with
`data-model.md` and the TS client. `agent_class ∈ {"user_delegated","daemon"}` everywhere.

---

## Agents

### `POST /api/v1/agents/` — create (schema `AgentCreate`, `schemas.py:73`)
Change `agent_class` from `str | None = None` to a **defaulted required** field so a missing value
persists an explicit default (never NULL, never a deploy-time downgrade):
```python
class AgentCreate(BaseModel):
    name: str
    team: str
    description: str | None = None
    agent_type: str = Field("sdk", pattern="^(sdk|declarative)$")
    agent_class: str = Field("user_delegated", pattern="^(daemon|user_delegated)$")   # CHANGED
    execution_shape: str = Field("reactive", pattern="^(reactive|durable)$")
    memory_enabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] | None = None
```
`routers/agents.py:85 create_agent` already writes `agent_class=body.agent_class` — now always a valid
non-NULL value (no `or "..."` coalesce anywhere). No handler change needed for create.

Request example (wizard, durable + schedule → daemon):
```json
{ "name":"nightly-fraud","team":"risk","agent_type":"declarative",
  "execution_shape":"durable","agent_class":"daemon","memory_enabled":false,
  "metadata":{"instructions":"..."} }
```

### `PUT|PATCH /api/v1/agents/{name}` — update (schema `AgentUpdate`, `schemas.py:85`)
`AgentUpdate.agent_class` stays optional (`str | None = None`, None = leave unchanged). **Wire the
orphan** in `routers/agents.py:update_agent` (currently drops it — research.md Correction 2):
```python
if body.agent_class is not None:
    agent.agent_class = body.agent_class
    changed = True
```
(Add right beside the existing `if body.execution_shape is not None:` block at `agents.py:309`.)

### `AgentResponse` (`schemas.py:94`)
Tighten `agent_class: str | None` → `agent_class: str` (now always present). The `_remap_metadata`
`@model_validator` (`:114`) already includes `agent_class` (`:126`) — no other change.

---

## Composite workflows

### `POST /api/v1/workflows/` — create (schema `CompositeWorkflowCreate`, `schemas.py:437`)
```python
class CompositeWorkflowCreate(BaseModel):
    name: str
    team: str
    description: str | None = None
    execution_shape: str = Field("durable", pattern="^(reactive|durable)$")
    orchestration: str = Field("sequential", pattern="^(sequential|supervisor|handoff|conditional)$")
    agent_class: str = Field("user_delegated", pattern="^(daemon|user_delegated)$")   # NEW
    memory_enabled: bool = False
```
`routers/composite_workflows.py:127 create_workflow` — add `agent_class=body.agent_class` to the
`CompositeWorkflow(...)` constructor.

### `PATCH /api/v1/workflows/{workflow_id}` — update (schema `CompositeWorkflowUpdate`, `schemas.py:446`)
Add `agent_class: str | None = Field(None, pattern="^(daemon|user_delegated)$")`. `update_workflow`
(`composite_workflows.py:167`) uses `model_dump(exclude_none=True)` + `setattr` → applies automatically.

### `CompositeWorkflowResponse` (`schemas.py:454`)
Add two fields:
```python
agent_class: str
warnings: list[str] = Field(default_factory=list)   # S2 save-time warn (best-effort, non-blocking)
```
`routers/composite_workflows.py:79 _to_response(wf, member_count, warnings=None)` — pass
`agent_class=wf.agent_class` and `warnings=warnings or []`.

### S2 save-time warn — producer (best-effort, ships with the field, not orphaned)
New helper in `routers/composite_workflows.py`:
```python
async def compute_reactive_approval_warnings(db, workflow_id, execution_shape) -> list[str]:
    """Non-blocking author warning: a reactive workflow with a statically high-risk-tool member
    will FAIL at runtime if that tool trips an approval gate (the authoritative S2 seam is runtime
    fail-closed). Returns [] for durable workflows or when no member has a high-risk tool."""
    if execution_shape != "reactive":
        return []
    # WorkflowMember -> Agent -> AgentTool -> Tool.risk_level IN ('high','critical')
    rows = (await db.execute(
        select(Agent.name, Tool.name).select_from(WorkflowMember)
        .join(Agent, Agent.id == WorkflowMember.agent_id)
        .join(AgentTool, AgentTool.agent_id == Agent.id)
        .join(Tool, Tool.id == AgentTool.tool_id)
        .where(WorkflowMember.workflow_id == workflow_id,
               Tool.risk_level.in_(["high", "critical"]))
    )).all()
    if not rows:
        return []
    members = sorted({a for (a, _t) in rows})
    return [
        f"Reactive workflow has high-risk-tool member(s): {', '.join(members)}. "
        f"If a tool trips an approval gate this run will FAIL (reactive can't park). "
        f"Set shape=durable to allow approvals."
    ]
```
Called in `get_workflow` / `update_workflow` (members exist by then) to populate `warnings`;
`create_workflow` returns `warnings=[]` (members are added after create, so nothing to check yet).

Studio (`WorkflowBuilderPage.tsx`) surfaces `wf.warnings` as a non-blocking `toast.warning(...)` after a
save/re-save. This is the **best-effort** half of S2; the **authoritative** half is the runtime
fail-closed in `workflow_orchestrator` (see `shared-dispatch-helper.md` + plan Task T5).

---

## TypeScript client (`studio/src/api/registryApi.ts`)

```ts
// createAgent body (:210) — add:
agent_class?: "user_delegated" | "daemon";
// updateAgent body (:224) — add:
agent_class?: "user_delegated" | "daemon";

// Agent interface (:24) already has: agent_class: string | null;  (leave as-is)

// CompositeWorkflow interface (:526) — add:
agent_class: "user_delegated" | "daemon";
warnings?: string[];
// CreateCompositeWorkflowRequest (:569) — add:
agent_class?: "user_delegated" | "daemon";
```

## Error / edge behavior (assert in e2e)
- Create agent with `agent_class:"bogus"` → **422** (Pydantic pattern).
- Create agent omitting `agent_class` → **201**, response `agent_class:"user_delegated"` (explicit default
  persisted — the M3 test), and a subsequent deploy reads that value directly (no coalesce).
- PATCH `{ "agent_class":"daemon" }` then GET → `agent_class:"daemon"` (orphan-wired; was silently dropped).
- Create workflow with `agent_class:"daemon"`, reload → persisted. PATCH `execution_shape:"reactive"` on a
  workflow with a high-risk-tool member → response `warnings` non-empty (best-effort).

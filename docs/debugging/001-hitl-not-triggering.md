# Debugging: HITL Not Triggering for High-Risk Tools

**Date:** 2026-07-09 – 2026-07-10
**Symptom:** Agent with `web_search` tool (risk_level=high) executes tool calls without requesting human approval. Chat appears "stuck" with no approval entry in Studio.
**Resolution:** 14 bugs across 6 services. Each had to be fixed for the end-to-end flow to work.
**Time to resolve:** ~8 hours across multiple deploy cycles.

---

## 1. Establish the Expected Chain First

Before looking at logs, map the full data flow that SHOULD happen. This prevents tunnel vision on one layer.

```
LLM calls tool
  → SDK _wrap_tool_with_governance()
    → opa_client.check_tool()
      → OPA sidecar (localhost:8181)
        → policy reads data.agents[sa_subject].tools
        → if risk == "high": return require_approval=true
    → SDK hitl.py.require_approval()
      → POST {AGENTSHIELD_REGISTRY_URL}/api/v1/approvals
      → langgraph.types.interrupt()
    → SSE emits approval_requested event
  → Studio shows approval in UI
```

**Key insight:** There are 7 distinct steps. A failure at ANY step breaks the whole flow. Don't assume the bug is in the "obvious" place.

## 2. Verify from the Bottom Up (Data Layer First)

### Step 2a: Is the tool actually bound to the agent?

```bash
# Check agent_tools join table
kubectl exec -n agentshield-platform agentshield-postgresql-0 -c postgresql -- \
  psql -U postgres -d agentshield -c "
    SELECT at.agent_id, at.tool_id, t.name, t.risk_level
    FROM agent_tools at JOIN tools t ON at.tool_id = t.id
    WHERE at.agent_id = '<agent-uuid>';"
```

**Finding (Bug #1):** `agent_tools` table was EMPTY for agents created before the metadata.tools binding fix. The tool appeared linked in the UI but had no join-table row.

**Why this matters:** Without `agent_tools` rows, the version snapshot and OPA bundle both get empty tool lists. The agent looks "toolless" to governance even though the UI shows the tool.

**Fix:** Direct DB INSERT for existing agents. New agents get proper binding from the fixed create flow.

### Step 2b: Does the version snapshot include tools?

```bash
kubectl exec -n agentshield-platform agentshield-postgresql-0 -c postgresql -- \
  psql -U postgres -d agentshield -c "
    SELECT id, version_number, tools
    FROM agent_versions WHERE agent_id = '<agent-uuid>'
    ORDER BY version_number DESC LIMIT 3;"
```

**Finding (Bug #2):** Version `tools` column was `null` or `[]`. Two code paths create versions:
1. `POST /agents/{name}/versions` (versions.py) — had the tools snapshot fix
2. Auto-create during `POST /agents/{name}/deploy` (deployments.py) — was MISSING the tools snapshot

Most users hit path #2 (deploy creates version automatically), so most versions had empty tools.

**Debugging thought process:** "The explicit version endpoint is fixed but versions still have no tools. There must be another creation path." Grepped for `AgentVersion(` across the codebase — found two call sites.

**Fix:** Added agent_tools join query + tools_snapshot to deployments.py auto-create path:
```python
bound_tools_result = await db.execute(
    select(Tool)
    .join(AgentTool, AgentTool.tool_id == Tool.id)
    .where(AgentTool.agent_id == agent.id)
)
tools_snapshot = [
    {"name": t.name, "risk": t.risk_level or "low"}
    for t in bound_tools_result.scalars().all()
]
version = AgentVersion(..., tools=tools_snapshot)
```

### Step 2c: Does the OPA bundle include this agent with high-risk tools?

```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- python3 -c "
import httpx, json
r = httpx.get('http://localhost:8000/api/v1/bundle/data.json')
data = r.json()
for name, info in data.get('agents', {}).items():
    if 'serper' in name:
        print(f'{name}: {json.dumps(info, indent=2)}')"
```

**What to look for:**
- Agent present in bundle? (requires running deployment + non-revoked identity)
- `tools` array has `web_search` with `risk: "high"`?
- `expected_sa_subject` matches what the pod actually presents?

After fixing bugs #1 and #2, regenerated bundle via `POST /api/v1/admin/bundle/regenerate` and confirmed the agent appeared with correct tool risk levels.

## 3. Verify the Runtime Layer

### Step 3a: Can the runner actually load tools?

```bash
kubectl logs -n agents-platform <pod-name> -c <agent-container> | grep -i "tool"
```

**Finding (Bug #3):** Log showed `"Could not fetch tools for serper-agent-1"`. The runner's `GET /agents/{name}/tools` call succeeded (200) but parsing failed silently.

**Root cause:** API returns paginated format `{"items": [...], "total": N}`. Runner code did:
```python
for t in tools_resp.json():  # iterates dict keys: "items", "total"
    t.get("id")              # AttributeError: str has no .get()
```
Caught by bare `except`, logged warning, continued with empty tool list.

**Debugging thought:** "API is 200, tools exist in DB, but runner says no tools. Must be a response parsing issue." Checked the API response format, then the runner parsing code.

**Fix:** Parse paginated response correctly:
```python
data = tools_resp.json()
items = data.get("items", data) if isinstance(data, dict) else data
for t in items:
    tid = t.get("id") or t.get("tool_id")
```

### Step 3b: Does HITL actually trigger internally?

After fixing bugs #1-3, the OPA check started returning `require_approval=true`. But the chat still appeared "stuck" — no approval entry in Studio.

```bash
kubectl logs -n agents-platform <pod-name> -c <agent-container> | grep -i "approval\|hitl\|interrupt"
```

**Finding (Bug #4):** Log showed `"Could not create approval record in Registry API: [Errno -2] Name or service not known"`. HITL was triggering internally — the SDK called `interrupt()` (which is why chat appeared stuck) — but couldn't POST the approval record to registry-api.

**Root cause:** SDK reads `AGENTSHIELD_REGISTRY_URL` env var. Default in `sdk/config.py`:
```python
AGENTSHIELD_REGISTRY_URL = os.getenv(
    "AGENTSHIELD_REGISTRY_URL",
    "http://registry-api.agentshield-platform:8080"  # WRONG service name + port
)
```
Actual service: `agentshield-registry-api` on port `8000`. Deploy-controller only injected `REGISTRY_API_URL` (correct value, wrong env var name for SDK).

**Fix:** Added `AGENTSHIELD_REGISTRY_URL` env var injection in `manifest_builder.py`:
```python
env_vars.append(
    k8s_client.V1EnvVar(name="AGENTSHIELD_REGISTRY_URL", value=registry_api_url)
)
```

### Step 3c: After the fix, does the pod actually have the new env var?

**Critical step:** The deploy-controller fix only affects NEW pods. Existing pods keep old env vars.

```bash
# Delete old deployment, reset status to pending, let controller reconcile
kubectl delete deployment serper-agent-3-sandbox -n agents-platform
# Reset in DB
UPDATE deployments SET status = 'pending' WHERE id = '<deployment-uuid>';
# Wait for new pod, then verify
kubectl exec -n agents-platform <new-pod> -c <container> -- env | grep AGENTSHIELD_REGISTRY_URL
```

**Finding (Bug #5, operational):** After deploying the controller fix, didn't redeploy the agent — so the fix wasn't applied. New env var only appears after the agent pod is recreated by the updated controller.

## 4. Verification Checklist

After all fixes deployed, verify the full chain:

```bash
# 1. agent_tools has rows
psql: SELECT count(*) FROM agent_tools WHERE agent_id = '...';

# 2. Version has tools snapshot
psql: SELECT tools FROM agent_versions WHERE agent_id = '...' ORDER BY version_number DESC LIMIT 1;

# 3. OPA bundle includes agent with high-risk tools
GET /api/v1/bundle/data.json → check agents[sa_subject].tools

# 4. Pod has both env vars
kubectl exec ... -- env | grep -E "REGISTRY_API_URL|AGENTSHIELD_REGISTRY_URL"

# 5. OPA sidecar healthy
kubectl exec ... -c opa -- wget -qO- http://localhost:8181/health

# 6. End-to-end: chat triggers approval
Chat with agent → trigger high-risk tool → check approvals page in Studio
```

---

## Debugging Principles Demonstrated

### P1: Map the full chain before debugging
Don't jump to the component you suspect. Write out every step in the data flow. Bugs cluster at handoff points between services.

### P2: Bottom-up verification (data → runtime → UI)
Start at the data layer. If the data is wrong, no amount of runtime debugging matters. Check DB state, then OPA bundle, then pod env vars, then logs.

### P3: Grep for all creation paths
When a fix "should work" but doesn't, there's likely another code path doing the same thing without the fix. `grep -rn "AgentVersion(" services/` found the second creation site.

### P4: Check response format assumptions
Silent failures often come from format mismatches. A 200 response doesn't mean parsing succeeded. Check what the API actually returns vs what the consumer expects.

### P5: Env var propagation has a deployment gap
Changing how a controller builds manifests doesn't fix running pods. The agent pod must be recreated for new env vars to take effect. Always verify the running pod, not just the code.

### P6: "Stuck" can mean "half-working"
The chat appearing stuck wasn't "nothing happened" — it was `interrupt()` fired but the approval record never reached the DB. The symptom (stuck) and the bug (DNS resolution failure for approval POST) are in completely different layers.

### P7: Bare except blocks hide root causes
Bug #3 was invisible because the runner caught all exceptions and logged a generic warning. When debugging, search for `except Exception` or bare `except:` in the code path — they're where bugs go to hide.

### P8: FastAPI trailing-slash redirects break non-browser clients
FastAPI's `redirect_slashes=True` (default) returns 307 for `/path` when the route is `/path/`. Browsers follow 307 transparently. `httpx.AsyncClient` does NOT follow redirects on POST by default — it treats the 307 as an error. Two fixes: use the correct URL (with trailing slash), AND set `follow_redirects=True` as a safety net.

---

## Bug Summary Table

| # | Service | Bug | Symptom | How Found |
|---|---------|-----|---------|-----------|
| 1 | registry-api | agent_tools empty for pre-fix agents | OPA bundle has no tools for agent | DB query on agent_tools |
| 2 | registry-api | Deploy auto-create version missing tools snapshot | Version tools=null, OPA bundle empty | Grep for `AgentVersion(` found 2 sites |
| 3 | declarative-runner | Paginated response parsed as dict keys | "Could not fetch tools" warning in logs | Compared API response format vs parsing code |
| 4 | deploy-controller | Missing AGENTSHIELD_REGISTRY_URL env var | "Name or service not known" in logs | Checked SDK config.py default vs actual service name |
| 5 | operational | Old pod not recreated after controller fix | Pod still has old env vars | `kubectl exec env` after deploy |
| 6 | SDK (hitl.py) | POST to `/approvals` without trailing slash → 307 redirect | "Redirect response 307" in pod logs, approval record never created | Pod logs showed exact error + redirect location URL |

### Bug #6 Deep Dive: 307 Trailing-Slash Redirect

**Symptom after bugs #1-5 fixed:** Pod logs showed:
```
Could not create approval record in Registry API: Redirect response '307 Temporary Redirect'
for url 'http://agentshield-registry-api.agentshield-platform:8000/api/v1/approvals'
Redirect location: 'http://agentshield-registry-api.agentshield-platform:8000/api/v1/approvals/'
```

**Investigation:**
1. Checked `sdk/agentshield_sdk/hitl.py:75` — POSTs to `/api/v1/approvals` (no trailing slash)
2. Checked `services/registry-api/routers/approvals.py:143` — route is `@router.post("/")`, mounted at prefix `/api/v1/approvals` → actual path is `/api/v1/approvals/`
3. FastAPI default `redirect_slashes=True` sends 307 for the missing slash
4. `httpx.AsyncClient(timeout=5.0)` default `follow_redirects=False` — treats 307 as error on POST

**Fix (two-layer):**
```python
# sdk/agentshield_sdk/hitl.py
# 1. Correct the URL
f"{config.AGENTSHIELD_REGISTRY_URL}/api/v1/approvals/"  # trailing slash
# 2. Safety net for any future redirect
httpx.AsyncClient(timeout=5.0, follow_redirects=True)
```

**How found:** The previous bug (#4, DNS error) masked this one. Once DNS resolved, the 307 error appeared in logs. Always re-check logs after fixing a prior bug — the next bug in the chain becomes visible.

## Bugs #7-9: Day 2 Fixes

### Bug #7: SDK sends wrong field names to ApprovalCreate API (422)

**Symptom:** After fixing bug #6, POST to `/api/v1/approvals/` returned `422 Unprocessable Entity`.

**Root cause:** SDK `hitl.py` sent field names that didn't match the API's `ApprovalCreate` schema:
- Missing: `agent_id`, `team`, `risk_level`
- Wrong: `context` sent as dict instead of string enum

**Investigation:**
1. Checked 422 response body — showed exact validation errors for each field
2. Compared SDK's POST payload to `ApprovalCreate` in `registry-api/schemas.py`
3. SDK had no env vars for agent identity (AGENT_ID, AGENT_TEAM) — these weren't injected by deploy-controller

**Fix (3 services):**
- `sdk/agentshield_sdk/config.py` — added `AGENT_ID`, `AGENT_TEAM`, `AGENT_NAME` from env vars
- `services/deploy-controller/manifest_builder.py` — inject `AGENTSHIELD_AGENT_ID`, `AGENTSHIELD_AGENT_TEAM` env vars into agent pods
- `sdk/agentshield_sdk/hitl.py` — send correct field names matching ApprovalCreate schema

**Key lesson:** Initial reaction was to make the API schema accept wrong fields (make them optional). User correctly rejected this — "don't take shortcuts, fix the SDK to send the right data."

### Bug #8: Nested graph architecture prevents interrupt() propagation

**Symptom:** After fixing #7, approval record created (201) but `approval_requested` SSE event never emitted. Chat still appeared "stuck."

**Root cause:** `AgentNodeExecutor.execute()` created a separate `Runner` with its own compiled graph and called `ainvoke()`. When `interrupt()` fired inside the inner graph, it raised `GraphInterrupt` — but the outer graph's `astream_events()` never saw it because they were separate graphs with separate checkpointers.

**Investigation:**
1. Traced the streaming code — no `on_interrupt` or `approval_requested` event in SSE output
2. Checked if OPA decision was correct — yes, `allow=true, require_approval=true`
3. Tested `require_approval()` directly — approval record created, `interrupt()` called
4. Realized the architecture: inner graph creates its own context, parent graph can't see its interrupts

**Fix:** Replaced `AgentNodeExecutor.execute()` (ainvoke on separate Runner) with `build_subgraph()` — builds a compiled graph via SDK's `build_graph(checkpointer=None)` and adds it directly as a subgraph node via `builder.add_node(node_id, subgraph)`. This way LangGraph propagates interrupts from subgraph to parent.

**Key lesson:** `ainvoke()` on a nested graph is opaque — the parent can't see its internal events. Subgraph nodes (`builder.add_node(id, compiled_graph)`) propagate events and interrupts to the parent. This distinction is critical for any feature that uses `interrupt()`.

### Bug #9: LangGraph doesn't emit on_interrupt in astream_events(v2)

**Symptom:** After fixing #8, graph compiled correctly and `interrupt()` fired during execution. But streaming code still emitted `done` instead of `approval_requested`.

**Root cause:** The SDK's `streaming.py` listened for an `on_interrupt` event in `graph.astream_events(version="v2")`. But LangGraph does NOT emit `on_interrupt` as a stream event. Instead:
1. When `interrupt()` fires, the graph suspends
2. `astream_events()` emits `on_chain_end` (with current state, not the interrupt value)
3. The iterator ends normally
4. Interrupt data is only available via `graph.get_state(config).tasks[N].interrupts`

**Investigation:**
1. Built a minimal test graph with `interrupt()` and recorded ALL event types from `astream_events(v2)`
2. Confirmed: only `on_chain_start`, `on_chain_stream`, `on_chain_end` — no `on_interrupt`
3. Called `graph.get_state(config)` after stream ended — found `interrupts` in the task objects
4. The `on_chain_end` handler in streaming.py immediately emitted `done`, never checking for interrupts

**Fix:** Rewrote `streaming.py`:
1. `on_chain_end` no longer emits `done` — just captures `final_response`
2. After `astream_events()` loop ends, calls `graph.get_state(config)` to check for pending interrupts
3. If interrupts found: emit `approval_requested` SSE with the interrupt payload data
4. If no interrupts: emit `done` with the final response

Also fixed thread_id propagation: governance wrapper reads `get_config()["configurable"]["thread_id"]` from LangGraph's config context instead of relying on a ContextVar that was never set in the subgraph flow.

### Bug #9b: as_tool_callable() annotation mismatch (edge case)

**Symptom:** `build_subgraph()` crashed with `KeyError: 'params'` for tools with no template variables.

**Root cause:** When a tool has no `{{var}}` in endpoint or body_template, `as_tool_callable()` creates a `params` parameter in `__signature__` with annotation `"str | None"` (a string, not a type). But `__annotations__` was set to `{}` (empty). LangChain's `@tool` → pydantic's `validate_arguments` → `KeyError` because `params` not in type hints.

**Fix:** When no template vars, use real `str` type annotation and sync `__annotations__` dict with `__signature__`:
```python
sig_params = [inspect.Parameter("query", inspect.Parameter.KEYWORD_ONLY, default="", annotation=str)]
http_tool_fn.__annotations__ = {"query": str}
```

---

## Bug Summary Table (Complete)

| # | Service | Bug | Symptom | How Found |
|---|---------|-----|---------|-----------|
| 1 | registry-api | agent_tools empty for pre-fix agents | OPA bundle has no tools for agent | DB query on agent_tools |
| 2 | registry-api | Deploy auto-create version missing tools snapshot | Version tools=null, OPA bundle empty | Grep for `AgentVersion(` found 2 sites |
| 3 | declarative-runner | Paginated response parsed as dict keys | "Could not fetch tools" warning in logs | Compared API response format vs parsing code |
| 4 | deploy-controller | Missing AGENTSHIELD_REGISTRY_URL env var | "Name or service not known" in logs | Checked SDK config.py default vs actual service name |
| 5 | operational | Old pod not recreated after controller fix | Pod still has old env vars | `kubectl exec env` after deploy |
| 6 | SDK (hitl.py) | POST to `/approvals` without trailing slash → 307 | "307 Temporary Redirect" in pod logs | Pod logs showed exact redirect URL |
| 7 | SDK + deploy-controller | Wrong field names in ApprovalCreate POST | 422 Unprocessable Entity | Compared POST body to schema definition |
| 8 | declarative-runner | Nested ainvoke() swallows interrupt() events | approval created but no SSE event | Architecture analysis of inner vs outer graph |
| 9 | SDK (streaming.py) | Waits for on_interrupt event that LangGraph never emits | `done` emitted instead of `approval_requested` | Recorded all event types from astream_events(v2) |

## Additional Debugging Principles

### P9: Don't degrade the schema to accept wrong data
When the caller sends wrong fields, fix the caller — don't make the API accept anything. Optional fields mask data quality issues that surface later as hard-to-debug downstream bugs.

### P10: Test the contract, not the assumption
The streaming code assumed LangGraph emits `on_interrupt`. A 30-line test recording all event types proved it doesn't. When code relies on an external library's event contract, test the contract.

### P11: Subgraph vs nested graph is an architectural choice with runtime consequences
`builder.add_node(id, compiled_graph)` (subgraph) propagates events and interrupts to the parent. `Runner(graph).ainvoke()` (nested) is opaque — the parent can't see internal events. Choose based on whether the parent needs visibility.

### P12: Test the resume path, not just the pause path
HITL has two halves: pause (interrupt + SSE) and resume (approve/deny + continue). The pause working does not mean resume works. Each half has its own chain of bugs. Test them separately.

## Bug #11 — `workflow_executor.resume()` uses wrong LangGraph API (2026-07-10)

**Where:** `services/declarative-runner/workflow_executor.py:803`
**Problem:** `ainvoke({"messages": [], "resume": decision}, config)` passes `resume` as a state key. LangGraph state schema has no `resume` key — the value is silently ignored. The graph just runs with empty messages, producing garbage output.
**Fix:** Use `Command(resume=decision)` from `langgraph.types`. This is LangGraph's documented API for resuming from `interrupt()`. Also added `resume_stream()` for SSE-streamed resume.
**Lesson:** LangGraph has two APIs for resume — `Command(resume=)` (correct) and state dict (wrong). The wrong one doesn't error, it just does the wrong thing silently.

## Bug #12 — No playground approval decide endpoint (2026-07-10)

**Where:** `services/registry-api/routers/playground.py`
**Problem:** Studio's `decidePlaygroundApproval()` calls `POST /playground/approvals/{id}/decide` but this endpoint never existed. Every approve/deny click returned 404.
**Fix:** Added `POST /api/v1/playground/approvals/{approval_id}/decide` to playground.py. Updates approval status + decision_at + reviewer_id in DB. Returns thread_id so frontend knows which run to resume.

## Bug #13 — No streaming resume after approve/deny (2026-07-10)

**Where:** Multiple files (agent pod, registry-api, Studio)
**Problem:** After approve, the original SSE stream is already closed (ended after `approval_requested`). No mechanism existed to get the agent's continuation. User would approve but see nothing happen.
**Fix:** Three layers:
  1. Agent pod: `POST /resume/{thread_id}/stream` — SSE endpoint using `resume_stream()` + `Command(resume=decision)`
  2. Registry-api: `GET /playground/runs/{run_id}/resume-stream` — proxies SSE from agent pod, reads latest decided approval from DB
  3. Studio: ChatPane gets `resumeStreamUrl` prop, opens new EventSource after approval, feeds events into same message state

## Bug #14 — EventSource not closed after `approval_requested` (2026-07-10)

**Where:** `studio/src/components/playground/ChatPane.tsx:139`
**Problem:** When `approval_requested` fires, the SSE handler called `onApprovalRequested` but never closed the EventSource. Server-side generator ends (after yielding `approval_requested`), HTTP response completes, EventSource auto-reconnects — hitting the stream endpoint again and potentially starting a duplicate run.
**Fix:** Added `es.close(); esRef.current = null;` before the `onApprovalRequested` call. Also extracted SSE event handler into reusable `connectStream()` function for both initial and resume streams.

## Debugging Principles (additions)

### P12: Test the resume path, not just the pause path
HITL has two halves: pause and resume. Each has its own failure modes. A working pause tells you nothing about whether resume works. Test each half end-to-end independently.

## Files Changed

- `services/registry-api/routers/deployments.py` — tools snapshot in auto-create version path
- `services/declarative-runner/workflow_executor.py` — subgraph nodes, `Command(resume=)` fix, `resume_stream()`
- `services/declarative-runner/main.py` — `POST /resume/{thread_id}/stream` SSE endpoint
- `services/declarative-runner/node_executors.py` — build_subgraph() + annotation fix for no-template-var tools
- `services/deploy-controller/manifest_builder.py` — AGENTSHIELD_REGISTRY_URL + agent identity env vars
- `services/registry-api/routers/playground.py` — `POST .../approvals/{id}/decide` + `GET .../resume-stream` endpoints
- `scripts/seed-defaults.sh` — web_search risk_level corrected to "high"
- `sdk/agentshield_sdk/hitl.py` — trailing slash + follow_redirects + correct field names
- `sdk/agentshield_sdk/config.py` — AGENT_ID, AGENT_TEAM, AGENT_NAME env var support
- `sdk/agentshield_sdk/graph_builder.py` — thread_id from LangGraph config instead of ContextVar
- `sdk/agentshield_sdk/streaming.py` — post-stream interrupt detection via graph.get_state(), type widened for Command
- `studio/src/components/playground/ChatPane.tsx` — ES close on approval_requested, `connectStream()` reuse, `resumeStreamUrl` prop
- `studio/src/components/playground/HitlPanel.tsx` — passes thread_id from decide response to parent
- `studio/src/pages/PlaygroundPage.tsx` — resume stream URL state, wired to ChatPane
- `studio/src/api/playgroundApi.ts` — `decidePlaygroundApproval` returns response with thread_id
- `charts/agentshield/charts/studio/templates/configmap.yaml` — nginx SSE location covers resume-stream

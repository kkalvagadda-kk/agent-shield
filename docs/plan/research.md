# AgentShield Phase 1 — Technical Research Notes

---

## 1. LangGraph `interrupt()` Mechanism and PostgresSaver Integration

### How `interrupt()` works (LangGraph 0.3+)

`interrupt()` is a control-flow primitive that pauses a LangGraph graph execution mid-node and saves state to the configured checkpointer. In AgentShield, this is the mechanism that implements HITL approval pauses.

**Import path (LangGraph 0.3):**
```python
from langgraph.types import interrupt
```

Do NOT use the pre-0.3 pattern `from langgraph.checkpoint import interrupt` — that module no longer exists.

**Usage inside a tool node:**
```python
from langgraph.types import interrupt

def issue_refund_node(state: AgentState) -> AgentState:
    # Before executing, request approval
    decision = interrupt({
        "approval_id": approval_id,
        "tool": "issue_refund",
        "args": state["pending_tool_args"],
    })
    
    if decision["approved"]:
        result = call_refund_api(state["pending_tool_args"])
        return {**state, "tool_result": result}
    else:
        return {**state, "tool_result": {"error": "Refund rejected by reviewer"}}
```

When `interrupt()` is called:
1. The current node state is serialized and written to the checkpointer (Postgres)
2. The graph raises an `Interrupt` exception (caught internally by LangGraph)
3. The calling `.ainvoke()` / `.astream()` returns with the interrupt payload
4. The graph is now "paused" — its state persists in Postgres checkpoints

**To resume:**
```python
# Resume with a decision
await graph.ainvoke(
    None,  # no new input
    config={"configurable": {"thread_id": thread_id}},
    command=Command(resume={"approved": True, "reviewer": "jane@company.com"}),
)
```

The `Command(resume=...)` API was introduced in LangGraph 0.2.57+. Verify the exact version pinning in `requirements.txt`.

### PostgresSaver setup

```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# At application startup
checkpointer = AsyncPostgresSaver.from_conn_string(
    "postgresql+asyncpg://langgraph_user:password@postgres-direct:5432/langgraph"
    # Note: use direct Postgres connection (not PgBouncer) for LISTEN/NOTIFY
)
await checkpointer.setup()  # Creates checkpoints, checkpoint_writes, checkpoint_blobs tables

# Compile graph with checkpointer
compiled_graph = graph.compile(checkpointer=checkpointer)

# Invoke with thread_id for checkpoint isolation
result = await compiled_graph.ainvoke(
    {"messages": [HumanMessage(content=user_input)]},
    config={"configurable": {"thread_id": thread_id}},
)
```

### PgBouncer incompatibility

**Critical finding:** LangGraph's PostgresSaver uses Postgres `LISTEN`/`NOTIFY` for state change notifications. PgBouncer in transaction-mode pooling does NOT support `LISTEN`/`NOTIFY` — the commands are rejected.

**Solution:** Use two separate connection pools:
1. `AGENTSHIELD_DATABASE_URL` — points to PgBouncer for all regular queries (fast, pooled)
2. `LANGGRAPH_DIRECT_DATABASE_URL` — points directly to Postgres primary (port 5432) for LangGraph checkpoints

In Kubernetes, configure two separate K8s Secrets:
```yaml
# Regular queries (via PgBouncer)
AGENTSHIELD_DATABASE_URL: postgresql+asyncpg://agentshield_user:...@pgbouncer:5432/agentshield

# LangGraph checkpoints (direct Postgres, bypasses PgBouncer)
LANGGRAPH_DIRECT_DATABASE_URL: postgresql+asyncpg://langgraph_user:...@postgres-primary:5432/langgraph
```

Alternatively, configure PgBouncer with `pool_mode=session` for the `langgraph` database only, which does support LISTEN/NOTIFY:
```ini
[databases]
langgraph = host=postgres-primary port=5432 pool_mode=session pool_size=10
```

### Checkpoint schema (LangGraph-managed)

LangGraph creates these tables in the `langgraph` database automatically via `checkpointer.setup()`:
- `checkpoints(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata)`
- `checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob)`
- `checkpoint_blobs(thread_id, checkpoint_ns, channel, version, type, blob)`

Do NOT run Alembic migrations against the `langgraph` database.

---

## 2. OPA Bundle Distribution Patterns

### Option A: git-sync sidecar (chosen for Phase 1)

The `git-sync` sidecar is a container that continuously polls a git repository and syncs the contents to a shared volume. OPA reads bundles from the filesystem.

**Why chosen:**
- Policies are version-controlled in git (GitOps)
- No additional service to deploy (git-sync is a sidecar)
- Bundle updates without OPA pod restart
- Consistent with ArgoCD philosophy

**Configuration in agent pod:**
```yaml
initContainers:
- name: policy-init
  image: registry.k8s.io/git-sync/git-sync:v4.2.3
  args:
    - --repo=https://github.com/your-org/agent-policies.git
    - --depth=1
    - --period=30s
    - --root=/policies
    - --dest=current
  volumeMounts:
  - name: policy-bundle
    mountPath: /policies

containers:
- name: opa
  image: openpolicyagent/opa:0.69.0-static
  args:
    - run
    - --server
    - --addr=0.0.0.0:8181
    - --bundle=/policies/current
    - --log-format=json
  volumeMounts:
  - name: policy-bundle
    mountPath: /policies

volumes:
- name: policy-bundle
  emptyDir: {}
```

**Phase 1 simplification:** For Phase 1, use ConfigMap-mounted policies instead of git-sync (avoids needing git credentials in every agent pod). Deploy Controller writes policy to ConfigMap on each deploy. Move to git-sync in Phase 2 for true GitOps.

```yaml
# Phase 1 approach: ConfigMap mount
volumes:
- name: policy-bundle
  configMap:
    name: order-agent-policy  # created by Deploy Controller
```

### Option B: OPA HTTP bundle server

OPA can pull bundles from an HTTP server on a schedule. This requires a central bundle server.

**Rejected for Phase 1:** Adds another service to deploy and operate. The central bundle server becomes a dependency of every agent pod — if it's down, OPA can't update policies. ConfigMap mount is simpler and sufficient for Phase 1 (<50 agents).

**Consider in Phase 2** when policy management complexity grows. Implementation: a simple nginx serving a directory of `.tar.gz` bundles generated from `opa build`.

### OPA Rego policy format for AgentShield

```rego
# policies/order_agent/policy.rego
package agentshield.agent.order_agent

import rego.v1

default allow := false
default require_approval := false

# Allow low-risk tools
allow if {
    input.tool_name == "lookup_order"
}

allow if {
    input.tool_name == "get_order_status"
}

# High-risk tools require approval, not outright deny
require_approval if {
    input.tool_name == "issue_refund"
}

require_approval if {
    input.tool_name == "cancel_order"
}

# Deny anything not explicitly allowed
deny if {
    not allow
    not require_approval
}

# Decision reason for audit logs
reason := "tool_not_in_allowlist" if {
    deny
}

reason := "high_risk_requires_approval" if {
    require_approval
}
```

**Query from agent:**
```bash
curl -s -X POST http://localhost:8181/v1/data/agentshield/agent/order_agent \
  -H "Content-Type: application/json" \
  -d '{"input": {"tool_name": "issue_refund"}}'
# Response: {"result": {"allow": false, "require_approval": true, "reason": "high_risk_requires_approval"}}
```

---

## 3. Envoy Gateway vs Envoy Standalone for JWT Validation

### Envoy Gateway (chosen)

Envoy Gateway is the Kubernetes-native control plane for Envoy Proxy, using Gateway API CRDs.

**Why chosen:**
- Native Kubernetes Gateway API (standardized, not Envoy-specific)
- SecurityPolicy CRD handles JWT validation declaratively
- No Envoy config YAML to manage (Gateway API resources manage it)
- Actively maintained by Envoy community as the recommended deployment model

**JWT validation configuration:**
```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: keycloak-jwt-auth
  namespace: agentshield-platform
spec:
  targetRef:
    group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: agents-route
  jwt:
    providers:
    - name: keycloak
      issuer: https://keycloak.agentshield.internal/realms/agentshield
      audiences:
        - registry-api
        - agent-gateway
      remoteJWKS:
        uri: https://keycloak.agentshield.internal/realms/agentshield/protocol/openid-connect/certs
        cacheDuration: 5m
      claimToHeaders:
        - claim: sub
          header: x-user-id
        - claim: email
          header: x-user-email
        - claim: realm_access.roles
          header: x-user-roles
```

**JWT validation performance:** Envoy caches the JWKS (public keys) from Keycloak for 5 minutes. Validation is in-process C++ — well under the 5ms p99 target.

### Envoy Standalone (rejected)

Running Envoy as a standalone pod (like Istio's control plane approach) requires:
- Writing raw `Bootstrap`, `Listener`, `Route`, `Cluster` protobuf configs
- A custom control plane (xDS server) if you want dynamic updates
- Complex YAML/JSON for JWT validation configuration

**Rejected because:** Envoy Gateway abstracts all of this behind Kubernetes-native CRDs. The complexity cost is not justified given Envoy Gateway is the upstream-recommended deployment pattern.

---

## 4. Portkey OSS Deployment Model

### Deployment model (standalone service)

Portkey OSS (self-hosted) runs as a standalone HTTP service. All agent LLM calls go to Portkey, which proxies to the actual providers (OpenAI, Anthropic, etc.).

**Docker image:** `portkeyai/gateway:latest` (Portkey Gateway open source on Docker Hub)

**Key configuration:**
```yaml
# Portkey environment variables
PORTKEY_CONFIG_PATH: /config/portkey.yaml
LOG_LEVEL: info
REDIS_URL: redis://:password@redis:6379/1
```

**Portkey config file (`portkey.yaml`):**
```yaml
providers:
  - id: openai
    name: OpenAI
    type: openai
    apiKey: ${OPENAI_API_KEY}
  - id: anthropic
    name: Anthropic
    type: anthropic
    apiKey: ${ANTHROPIC_API_KEY}

cache:
  enabled: true
  redis:
    url: redis://:password@redis:6379/1
  ttl: 3600  # 1 hour

retry:
  attempts: 3
  onStatusCodes: [429, 500, 502, 503]
  
fallback:
  - anthropic  # fallback to Anthropic if OpenAI fails
```

**Agent usage:** Set `OPENAI_BASE_URL=http://portkey.agentshield-platform:8787/v1` in agent pod env vars. The agent's LLM client then calls Portkey transparently.

**Sidecar vs standalone:** Portkey runs best as a shared service (not per-pod sidecar) because:
- Redis cache is shared across all agents (deduplication across pods)
- A single Portkey service enforces consistent retry/fallback policy
- Avoids running a separate Redis client per pod

**Redis cache namespace:** Portkey uses key prefix `portkey:cache:` in Redis DB 1 (separate from AgentShield's use of DB 0).

---

## 5. LLM Guard Deployment (Model Loading, GPU Requirements, Inference Optimization)

### Model loading

LLM Guard uses HuggingFace models loaded at startup. Key models:
- **PromptInjection scanner:** `ProtectAI/deberta-v3-base-prompt-injection-v2` (~0.7GB)
- **Toxicity scanner:** `martin-ha/toxic-comment-model` (~0.5GB)
- **Secrets scanner:** regex-based (no model)

**Total memory footprint:** ~3-4GB RAM when all models are loaded.

**Model download:** By default, LLM Guard downloads models from HuggingFace Hub on first startup. For air-gapped clusters:
```bash
# Pre-download on a machine with internet, then copy to a registry or MinIO
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('ProtectAI/deberta-v3-base-prompt-injection-v2', cache_dir='/models')"
# Mount /models as a PVC in the LLM Guard pod
```

Set `TRANSFORMERS_CACHE=/models` and `HF_HUB_OFFLINE=1` for air-gapped operation.

### GPU requirements

**Phase 1:** LLM Guard runs on CPU. DeBERTa inference on CPU takes 20-80ms depending on input length. This is acceptable for Phase 1 given the parallel fan-out (total safety latency ≈ slowest scanner).

**For Phase 2+:** If LLM Guard becomes the bottleneck (p99 >150ms), add GPU:
- Request `nvidia.com/gpu: 1` in Pod spec
- Enable GPU with `torch.device("cuda")` via `LLM_GUARD_USE_GPU=true` env var
- With GPU (T4): inference ~5-10ms per request

**CPU optimization (Phase 1):**
- Set `torch.set_num_threads(4)` — prevents thread explosion
- Set `OMP_NUM_THREADS=4`
- Use ONNX runtime export of DeBERTa for 2-3x CPU speedup: `LLM_GUARD_ONNX=true` (LLM Guard 0.5 supports this)

### Resource configuration

```yaml
resources:
  requests:
    cpu: "1000m"
    memory: "4Gi"
  limits:
    cpu: "2000m"
    memory: "6Gi"
```

**Startup probe** (important — model loading takes 60-120s):
```yaml
startupProbe:
  httpGet:
    path: /ready
    port: 8000
  failureThreshold: 30  # 30 * 10s = 5 minutes allowed for startup
  periodSeconds: 10
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 120
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 60
```

**Pre-pulling strategy:** Add LLM Guard to the DaemonSet that pre-pulls images on each node:
```yaml
# Or use node image pre-pull via ArgoCD image updater / cluster pre-pull job
```

---

## 6. React Flow Best Practices for Workflow Builders

### Version

Use `@xyflow/react` version 12 (package was renamed from `reactflow` in v12). Breaking changes from v11:
- Package name: `@xyflow/react` not `reactflow`
- Custom node components receive different props structure
- `useReactFlow()` hook replaces some `useStore()` patterns

```bash
npm install @xyflow/react@12
```

### Custom node pattern

```tsx
// src/nodes/AgentNode.tsx
import { memo } from 'react';
import { Handle, Position, NodeProps } from '@xyflow/react';

type AgentNodeData = {
  name: string;
  instructions: string;
  model: string;
  risk_level: 'low' | 'high';
};

export const AgentNode = memo(({ data, selected }: NodeProps<AgentNodeData>) => {
  return (
    <div className={`agent-node ${selected ? 'selected' : ''}`}>
      <Handle type="target" position={Position.Left} />
      <div className="node-header">
        <span className="node-icon">🤖</span>
        <span className="node-name">{data.name || 'Agent'}</span>
      </div>
      <div className="node-body">
        <span className="node-model">{data.model}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});
AgentNode.displayName = 'AgentNode';
```

**Critical:** Always `memo()` custom nodes — React Flow re-renders nodes on every viewport change without it.

### State management with Zustand

```tsx
// src/stores/workflowStore.ts
import { create } from 'zustand';
import { applyNodeChanges, applyEdgeChanges, Node, Edge } from '@xyflow/react';

interface WorkflowStore {
  nodes: Node[];
  edges: Edge[];
  isDirty: boolean;
  selectedNodeId: string | null;
  
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  addNode: (node: Node) => void;
  updateNodeData: (nodeId: string, data: Partial<NodeData>) => void;
  setSelectedNode: (nodeId: string | null) => void;
}

export const useWorkflowStore = create<WorkflowStore>((set) => ({
  nodes: [],
  edges: [],
  isDirty: false,
  selectedNodeId: null,
  
  onNodesChange: (changes) =>
    set((state) => ({
      nodes: applyNodeChanges(changes, state.nodes),
      isDirty: true,
    })),
  
  onEdgesChange: (changes) =>
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges),
      isDirty: true,
    })),
  
  updateNodeData: (nodeId, data) =>
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n
      ),
      isDirty: true,
    })),
  
  setSelectedNode: (nodeId) => set({ selectedNodeId: nodeId }),
}));
```

### Canvas configuration

```tsx
// src/components/Canvas.tsx
import { ReactFlow, Background, Controls, MiniMap } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

const nodeTypes = {
  agent: AgentNode,
  http_tool: HttpToolNode,
  end: EndNode,
};

export function Canvas() {
  const { nodes, edges, onNodesChange, onEdgesChange } = useWorkflowStore();
  
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      nodeTypes={nodeTypes}
      onNodeClick={(_, node) => useWorkflowStore.getState().setSelectedNode(node.id)}
      fitView
      snapToGrid
      snapGrid={[16, 16]}
      defaultEdgeOptions={{
        type: 'smoothstep',
        animated: true,
      }}
    >
      <Background variant="dots" gap={16} />
      <Controls />
      <MiniMap />
    </ReactFlow>
  );
}
```

### Performance considerations

- Use `memo()` on all custom node components
- Keep node `data` immutable — create new objects instead of mutating
- Use `elkjs` or `dagre` for auto-layout when loading saved workflows (prevents overlapping nodes)
  ```bash
  npm install elkjs @types/elkjs
  ```
- Limit to 50-100 nodes for smooth performance (sufficient for Phase 1 simple workflows)

---

## 7. Declarative Runner Design Patterns

### JSON interpreter pattern (chosen for Phase 1)

The declarative runner loads the workflow JSON at startup and builds a LangGraph `StateGraph` from it programmatically.

**Why chosen over code generation:**
- No build step required — instant deploy
- Workflow JSON changes can be hot-reloaded (just restart the pod)
- Security: no arbitrary code execution (code generation pattern would require executing generated Python)
- Simpler to reason about and audit

**Implementation:**

```python
# services/declarative-runner/workflow_executor.py
import json
import os
from langgraph.graph import StateGraph, START, END
from agentshield_sdk import Runner, tool
import httpx

class WorkflowExecutor:
    def __init__(self):
        workflow_json = os.environ.get("WORKFLOW_JSON")
        if not workflow_json:
            raise ValueError("WORKFLOW_JSON env var not set")
        
        self.workflow = json.loads(workflow_json)
        self.graph = self._build_graph(self.workflow)
        self.compiled = self.graph.compile(checkpointer=get_checkpointer())
    
    def _build_graph(self, workflow: dict) -> StateGraph:
        """Build a LangGraph StateGraph from the workflow JSON."""
        graph = StateGraph(DeclarativeState)
        
        # Build node map
        node_map = {node["id"]: node for node in workflow["nodes"]}
        
        for node in workflow["nodes"]:
            if node["type"] == "agent":
                graph.add_node(node["id"], self._make_agent_node(node["config"]))
            elif node["type"] == "http_tool":
                graph.add_node(node["id"], self._make_http_tool_node(node["config"]))
            elif node["type"] == "end":
                graph.add_node(node["id"], lambda state: state)
        
        # Add edges
        for edge in workflow["edges"]:
            source_node = node_map[edge["source"]]
            target_node = node_map[edge["target"]]
            
            if target_node["type"] == "end":
                graph.add_edge(edge["source"], END)
            else:
                graph.add_edge(edge["source"], edge["target"])
        
        # Find start node (no incoming edges)
        nodes_with_incoming = {e["target"] for e in workflow["edges"]}
        start_nodes = [n["id"] for n in workflow["nodes"] if n["id"] not in nodes_with_incoming and n["type"] != "end"]
        if start_nodes:
            graph.add_edge(START, start_nodes[0])
        
        return graph
    
    def _make_agent_node(self, config: dict):
        """Create a LangGraph node for an Agent node type."""
        from agentshield_sdk import Agent
        
        # Find HTTP tool nodes reachable from this agent node
        # (injected at build time, not dynamic lookup)
        agent_tools = self._collect_tools_for_agent(config)
        
        agent = Agent(
            name=config["name"],
            instructions=config["instructions"],
            tools=agent_tools,
            model=config.get("model", "gpt-4o-mini"),
        )
        
        async def agent_node(state: DeclarativeState):
            result = await Runner.run(agent, state["input"])
            return {**state, "output": result.response, "thread_id": result.thread_id}
        
        return agent_node
    
    def _make_http_tool_node(self, config: dict):
        """Create a callable tool from an HTTP tool config."""
        risk = config.get("risk", "low")
        
        @tool(risk=risk)
        async def http_tool(**kwargs):
            url = config["endpoint"]
            # Substitute {{variable}} placeholders
            for key, value in kwargs.items():
                url = url.replace(f"{{{{{key}}}}}", str(value))
            
            body = config.get("body_template", "")
            if body:
                for key, value in kwargs.items():
                    body = body.replace(f"{{{{{key}}}}}", str(value))
            
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=config["method"],
                    url=url,
                    headers=config.get("headers", {}),
                    content=body if body else None,
                    timeout=10.0,
                )
                return response.json()
        
        http_tool.__name__ = config["name"]
        return http_tool
```

### Env var vs ConfigMap for workflow JSON

**Phase 1:** Use `ConfigMap` (not env var) for workflow JSON, mounted at `/workflow/definition.json`. The 1MB Kubernetes env var limit is too small for complex workflows.

```yaml
# In Deploy Controller manifest_builder.py for declarative deployments
volumeMounts:
- name: workflow-config
  mountPath: /workflow

volumes:
- name: workflow-config
  configMap:
    name: workflow-{workflow_id}-v{version_number}
    
# In container
env:
- name: WORKFLOW_CONFIG_PATH
  value: /workflow/definition.json
```

### Performance benchmark target

LangGraph graph construction from JSON at startup should complete in <2s for a 10-node workflow. Cache the compiled graph at module level (module-level `compiled_graph = None` initialized once).

If startup time exceeds 5s, profile with `cProfile` and pre-serialize the graph to a pickle format stored in the ConfigMap (as a potential optimization).

---

## Summary: Key Technical Decisions by Component

| Component | Library Version | Key Finding |
|---|---|---|
| LangGraph checkpoints | `langgraph>=0.3.0` | Use `AsyncPostgresSaver`, direct Postgres (not PgBouncer) for LISTEN/NOTIFY |
| LangGraph interrupt | `langgraph>=0.3.0` | `from langgraph.types import interrupt`; resume via `Command(resume=...)` |
| OPA sidecar | `openpolicyagent/opa:0.69.0-static` | ConfigMap bundle for Phase 1; git-sync sidecar for Phase 2 |
| Envoy JWT | `gateway.envoyproxy.io/v1alpha1 SecurityPolicy` | Use Envoy Gateway CRDs; JWKS cached 5min |
| Portkey | `portkeyai/gateway:latest` | Standalone service; Redis DB 1 for cache |
| LLM Guard | `ghcr.io/protectai/llm-guard:0.5.0` | CPU-only Phase 1; 4GB RAM; 120s startup probe |
| React Flow | `@xyflow/react@12` | `memo()` nodes; Zustand store; snapToGrid |
| Declarative runner | Custom FastAPI | ConfigMap mount (not env var); graph built at startup |

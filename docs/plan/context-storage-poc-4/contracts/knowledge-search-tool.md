# Contract — the `knowledge_search` platform tool

`knowledge_search` is a **platform-managed HTTP tool** (see research.md F-1 for why HTTP and
not Python type). It is seeded once by `scripts/seed-defaults.sh` (idempotent, like
`web_search`) and attached to agents via the KB binding picker. OPA + HITL wrap it for free in
`graph_builder.governed_tool` (governed platform tool, design §3.2).

## Tool registration (the exact `POST /api/v1/tools/` body)

```json
{
  "name": "knowledge_search",
  "display_name": "Knowledge Search",
  "description": "Search the team's knowledge base for passages relevant to a question. Returns the most relevant document chunks with their source. Use this to ground answers in the team's own documents and cite them.",
  "type": "http",
  "risk_level": "low",
  "owner_team": "platform",
  "side_effecting": false,
  "http_method": "POST",
  "http_url": "http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/api/v1/internal/knowledge/search",
  "http_headers": {
    "Content-Type": "application/json",
    "X-Agent-Team": "{{AGENTSHIELD_AGENT_TEAM}}",
    "X-Agent-Name": "{{AGENT_NAME}}"
  },
  "http_body_template": "{\"query\": \"{{query}}\", \"k\": 5}",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "The question or search phrase to look up in the knowledge base." }
    },
    "required": ["query"]
  }
}
```

## Why this is tenant-safe (S5)

- `{{query}}` — the **only** model-controlled input; substituted into the body.
- `{{AGENTSHIELD_AGENT_TEAM}}` / `{{AGENT_NAME}}` — substituted by `HttpToolExecutor` from
  **`os.environ`** (`tool_executor.py:145`), i.e. the agent pod's real env set by the
  deploy-controller (`manifest_builder.py:167,170`). The model cannot set or read these; a
  prompt-injection "search team X" cannot change the header value.
- `kb_id` is **never** in the tool at all — the internal endpoint resolves it from
  `agent_knowledge_bindings` by `(agent_name, team)`. So neither `team` nor `kb_id` is ever
  model-controlled. `PgVectorStore.search` then re-enforces both as required predicates.

## Model-facing behavior

`input_schema` gives the model a single typed arg `query: string` (LangChain derives the tool
signature from it via `PythonToolExecutor`/`HttpToolExecutor` schema introspection). The tool
returns the `KnowledgeSearchResult` JSON (contracts/endpoints.md). The model reads
`chunks[].content` to answer and is instructed (agent system prompt / tool description) to cite
the `source`. The **structured** citation chips do NOT depend on the model quoting the source —
they are extracted from the tool result by the frontend (F-4), so a citation renders even if
the model's prose forgets to name the file.

## Attachment

Attaching happens two ways, both landing the same `agent_tools` + `agent_knowledge_bindings`
rows:
1. **KB detail page → Attach agent picker** → `PUT /knowledge-bases/{kb}/agents/{agent}`
   (endpoints.md) — ensures the tool is on the agent AND records the KB binding.
2. Manually adding the `knowledge_search` tool to an agent in the tool picker still works, but
   without a KB binding the internal endpoint returns empty (fail-closed) until a KB is bound.

## No-orphan wiring checklist (grep in the done-gate)

- `grep -rn "knowledge_search" scripts/seed-defaults.sh` → seeded.
- `grep -rn "knowledge/search" services/registry-api/routers/internal.py` → endpoint exists.
- `grep -rn "agent_knowledge_bindings" services/registry-api` → written by binding PUT, read
  by internal endpoint.
- suite-77 proves an agent with the tool + a binding answers with a citation.

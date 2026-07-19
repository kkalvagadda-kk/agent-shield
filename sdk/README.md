# AgentShield SDK

`agentshield_sdk` — build **governed, observable AI agents** on LangGraph. Python ≥3.12.

## Core

You describe an agent; the SDK compiles it into a governed LangGraph ReAct loop where **every tool call is intercepted** — OPA policy → optional human approval → safety scan → execute. You get governance for free; you don't call it.

```python
from agentshield_sdk import Agent, Runner

agent = Agent(
    name="order-agent",
    instructions="You help with order status and refunds.",
    tools=["lookup_order", "issue_refund"],   # platform tool names, resolved from the registry
)

runner = Runner(agent)
await runner.setup()                          # resolve tools + compile the governed graph
result = await runner.run("What's order 123?")       # sync
async for frame in runner.run_streamed("..."):       # SSE stream
    ...
```

Public API: `Agent`, `Runner`, `build_graph`, `handoff`, and a legacy `@tool` decorator.

## What it offers

- **Tool governance (OPA)** — every tool wrapped with an allow/deny policy check keyed on the pod's Kubernetes ServiceAccount identity. Fail-closed in production (OPA unreachable → deny).
- **Human-in-the-loop** — policy-required approvals pause the graph via LangGraph `interrupt()`, checkpointed and resumable up to 30 min later. Fail-closed; one high-risk tool per turn.
- **Safety scanning** — input scanned before the graph, output after, via the Safety Orchestrator.
- **Durable runs** — fire-and-forget execution with per-step callbacks, HITL parking, and resume from the Postgres checkpoint. Idempotent via step bookmarks.
- **Streaming (SSE)** — `text_delta`, `tool_call_start/end`, `approval_requested`, `done`, `error`.
- **Tool execution** — platform tools resolved from the registry as HTTP (template substitution) or sandboxed Python (executor microservice), with typed signatures from each tool's `input_schema`.
- **Observability** — Langfuse envelope traces + vendor-neutral OTEL/OpenInference spans; both no-op when unconfigured.
- **Multi-agent handoff** — delegate a turn to another agent through the Envoy gateway (receiver still gets safety scan + JWT validation).
- **Deployment surface** — `server.py` FastAPI app (`/chat`, `/chat/stream`, `/run`, `/resume/{thread_id}`, `/health`, `/ready`, `/metrics`) and an `agentshield` CLI (`dev`, `register`, `deploy`).
- **LLMs** — Anthropic (default `claude-sonnet-4-6`) or Bedrock via `LLM_PROVIDER`. Postgres checkpointer for HITL resume, in-memory otherwise.

Built on LangGraph + LangChain (`create_react_agent`, checkpointer, interrupts) — the SDK adds the governance layer those frameworks don't ship.

## Takeaway

Define an agent in ~5 lines and get enforced tool-level governance, human approvals, safety scanning, durable execution, and tracing — structurally, wrapped around every tool call, not something the author has to remember to call.

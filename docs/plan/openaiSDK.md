# OpenAI Agents SDK — Deep Dive & AgentShield Integration

**Date:** July 2026  
**Purpose:** Understand the OpenAI Agents SDK in depth and map out how AgentShield users could use it to write their agents, with the governance layer remaining intact.

---

## What It Is

The OpenAI Agents SDK is OpenAI's official open-source Python (and TypeScript) framework for building production agents. It replaced the experimental Swarm cookbook. The design philosophy is minimal abstractions — five primitives that compose into complex systems without a steep learning curve.

Repo: [openai/openai-agents-python](https://github.com/openai/openai-agents-python)  
Docs: [openai.github.io/openai-agents-python](https://openai.github.io/openai-agents-python/)

---

## The Core Loop

The `Runner` drives an agentic loop:

```
1. Call LLM with current messages + tools
2. If LLM returns a final answer → done
3. If LLM calls a tool → execute it, append result, go to 1
4. If LLM hands off to another agent → switch agent, go to 1
```

Everything else in the SDK is either configuring that loop or observing it.

---

## The Five Primitives

### 1. Agent

```python
from agents import Agent, ModelSettings

agent = Agent(
    name="support-agent",

    # Static system prompt
    instructions="You are a customer support agent. Be concise.",

    # OR dynamic — called every turn, can read from context
    # instructions=lambda ctx, agent: f"You help {ctx.context.user_name}",

    model="gpt-4.1",
    model_settings=ModelSettings(
        temperature=0.2,
        max_tokens=1024,
        tool_choice="auto",     # "required" forces a tool call; "none" disables tools
    ),

    tools=[...],
    handoffs=[...],
    input_guardrails=[...],
    output_guardrails=[...],

    output_type=None,           # or a Pydantic model for structured outputs
)
```

`instructions` can be a string or an async function `(RunContextWrapper, Agent) → str`. That's the hook for runtime-dynamic prompts — injecting user role, permissions, current timestamp, etc.

---

### 2. Runner

```python
from agents import Runner

# Async
result = await Runner.run(agent, "What's the status of order 123?")

# Sync wrapper
result = Runner.run_sync(agent, "What's the status of order 123?")

# Streaming
async for event in Runner.run_streamed(agent, "..."):
    print(event)
```

`Runner.run()` returns a `RunResult` with `result.final_output`. `Runner.run_streamed()` emits events as they arrive — model tokens, tool call starts/ends, handoffs. The `max_turns` parameter prevents infinite loops; set to `None` for unbounded.

Three failure modes the SDK handles explicitly:
- `MaxTurnsExceeded` — turn limit hit
- `ModelBehaviorError` — malformed LLM output
- `ToolTimeoutError` — tool execution timed out

---

### 3. Tools

Three flavors:

**Function tools — the main one**

```python
from agents import function_tool
from pydantic import Field

@function_tool
async def get_order(
    order_id: str = Field(description="The order ID to look up"),
    include_history: bool = False,
) -> dict:
    """Retrieve order details from the OMS.

    Args:
        order_id: The order ID to look up.
        include_history: Whether to include order event history.
    """
    return {"id": order_id, "status": "shipped"}
```

The `@function_tool` decorator parses the signature with `griffe` to auto-generate the JSON schema the LLM sees. Type hints + docstring + `Field()` constraints become the tool spec. No manual schema writing.

Optional configuration:
- `@function_tool(timeout=5.0)` — timeout in seconds
- `@function_tool(failure_error_function=...)` — custom error returned to LLM on exception
- `@function_tool(defer_loading=True)` — lazy-load for large tool surfaces

**Agents as tools — call a specialist without handing off**

```python
translator = Agent(name="translator", instructions="Translate to Spanish.")

orchestrator = Agent(
    name="orchestrator",
    tools=[
        translator.as_tool(
            tool_name="translate_to_spanish",
            tool_description="Translate any text to Spanish.",
            needs_approval=True,        # pauses for human confirmation before running
            custom_output_extractor=...,
        )
    ]
)
```

The calling agent retains control — it gets the result back as a tool output and keeps going. Contrast with handoffs, which transfer control entirely.

**MCP tools — same API as function tools**

```python
from agents.mcp import MCPServerStdio

async with MCPServerStdio(command="npx", args=["-y", "@mcp/server-filesystem", "/data"]) as mcp:
    agent = Agent(name="file-agent", mcp_servers=[mcp])
    # MCP tools appear alongside function tools automatically
```

---

### 4. Handoffs

```python
billing_agent = Agent(name="billing", instructions="Handle billing only.")
tech_agent    = Agent(name="tech-support", instructions="Handle technical issues.")

triage_agent = Agent(
    name="triage",
    instructions="""
    You are a triage agent. Route to specialists.
    For billing questions → transfer_to_billing.
    For technical issues → transfer_to_tech_support.
    """,
    handoffs=[billing_agent, tech_agent],
)
```

When `triage_agent` calls `transfer_to_billing`, the **full conversation history** moves to `billing_agent` — it sees everything the user said before. The Runner keeps looping with the new agent.

Customizing what the receiving agent sees:

```python
from agents import handoff
from agents.extensions.handoff_filters import remove_all_tools

agent = Agent(
    handoffs=[
        handoff(
            agent=escalated_agent,
            input_filter=remove_all_tools,      # strip tool calls from history
            on_handoff=lambda ctx: log_escalation(ctx),
            input_type=EscalationReason,        # model fills a Pydantic model on handoff
        )
    ]
)
```

---

### 5. Guardrails

Input and output guardrails run **in parallel** with the LLM (for input), so they don't add latency:

```python
from agents import input_guardrail, output_guardrail, GuardrailFunctionOutput

@input_guardrail
async def no_competitor_mentions(ctx, agent, input) -> GuardrailFunctionOutput:
    if "CompetitorX" in str(input):
        return GuardrailFunctionOutput(
            output_info="Competitor mention detected",
            tripwire_triggered=True,    # raises InputGuardrailTripwireTriggered
        )
    return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)

@output_guardrail
async def no_pii_in_output(ctx, agent, output) -> GuardrailFunctionOutput:
    if contains_pii(output.final_output):
        return GuardrailFunctionOutput(output_info="PII in output", tripwire_triggered=True)
    return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)

agent = Agent(
    name="safe-agent",
    input_guardrails=[no_competitor_mentions],
    output_guardrails=[no_pii_in_output],
)
```

**Tool guardrails** (2026 addition) — run before/after individual tool calls:

```python
from agents import tool_input_guardrail

@tool_input_guardrail
async def budget_check(ctx, tool_call, input) -> GuardrailFunctionOutput:
    if tool_call.tool_name == "approve_payment" and input["amount"] > 10000:
        return GuardrailFunctionOutput(
            output_info="Exceeds auto-approval threshold",
            tripwire_triggered=True,
        )
    return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)
```

---

## Context — Typed Dependency Injection

The `context` parameter propagates a typed object through every agent, tool, guardrail, and hook in the run. This is the primary mechanism for passing request-scoped state:

```python
from dataclasses import dataclass
from agents import Agent, RunContextWrapper, Runner, function_tool

@dataclass
class UserContext:
    user_id: str
    team: str
    permissions: list[str]

@function_tool
async def get_account(ctx: RunContextWrapper[UserContext]) -> dict:
    if "accounts:read" not in ctx.context.permissions:
        return {"error": "Forbidden"}
    return {"user_id": ctx.context.user_id}

agent = Agent[UserContext](
    name="account-agent",
    instructions=lambda ctx, _: f"You help user {ctx.context.user_id} from team {ctx.context.team}.",
    tools=[get_account],
)

result = await Runner.run(
    agent,
    "Show me my account",
    context=UserContext(user_id="u-123", team="eng", permissions=["accounts:read"]),
)
```

---

## Lifecycle Hooks

```python
from agents import RunHooks, RunContextWrapper, Agent, Tool

class AuditHooks(RunHooks):
    async def on_tool_start(self, ctx: RunContextWrapper, agent: Agent, tool: Tool):
        print(f"[AUDIT] tool={tool.name} agent={agent.name}")

    async def on_tool_end(self, ctx, agent, tool, result):
        print(f"[AUDIT] tool={tool.name} result_len={len(str(result))}")

    async def on_handoff(self, ctx, from_agent, to_agent):
        print(f"[AUDIT] handoff {from_agent.name} → {to_agent.name}")

result = await Runner.run(agent, "...", hooks=AuditHooks())
```

`AgentHooks` is scoped to a single agent; `RunHooks` covers the full run including handoffs.

---

## Sessions — Persistent Memory

```python
from agents.memory import SqliteSession   # also: RedisSession, MongoDBSession, SqlAlchemySession

result = await Runner.run(
    agent,
    "Follow up on that last order.",
    session=SqliteSession("session-abc-123"),
)
# SDK manages message history automatically across turns
```

---

## Custom Model Provider

The SDK works with any OpenAI-compatible endpoint:

```python
from openai import AsyncOpenAI
from agents import set_default_openai_client, OpenAIChatCompletionsModel

custom_client = AsyncOpenAI(
    api_key="your-key",
    base_url="https://your-provider/v1",
)
set_default_openai_client(custom_client)

# OR per-agent (different providers in a multi-agent system)
agent = Agent(
    model=OpenAIChatCompletionsModel(
        model="claude-sonnet-4-6",
        openai_client=custom_client,
    )
)
```

For non-OpenAI-compatible providers: `openai-agents[litellm]` gives access to 100+ models via LiteLLM. Implement the `Model` interface directly for complete custom control.

---

## Tracing to a Custom Backend

By default traces go to `platform.openai.com`. To redirect to a self-hosted backend:

```python
import os
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"   # stop sending to OpenAI
os.environ["LANGFUSE_PUBLIC_KEY"]  = "pk-lf-..."
os.environ["LANGFUSE_SECRET_KEY"]  = "sk-lf-..."
os.environ["LANGFUSE_BASE_URL"]    = "http://langfuse:3000"

OpenAIAgentsInstrumentor().instrument()
# Every Runner.run() now sends OTel spans to Langfuse automatically:
# LLM calls, tool calls, handoffs, guardrail outcomes, timing
```

Langfuse is one of 25+ listed integrations. The instrumentation is OpenTelemetry-based so the span schema is open.

---

## 2026 Additions

- **Sandbox agents** — run agents in isolated compute without setting up containers; 7 providers out of the box (E2B, Modal, Cloudflare, Vercel, Blaxel, Daytona, Runloop) plus a custom provider interface
- **Subagents** — agents spawn child agents for subtasks
- **Code mode** — agents write and execute code as part of their workflow
- **Model-agnostic** — 100+ LLMs via Chat Completions API (Claude, Gemini, Llama, Mistral, etc.)
- **Tool guardrails** — `@tool_input_guardrail` / `@tool_output_guardrail`
- **Durable execution** — integrations with Temporal, Dapr, Restate for long-running agents and HITL

---

## Integration with AgentShield

### What stays the same

AgentShield's governance is enforced at the **network and infrastructure layer** — it doesn't care what SDK runs inside the agent pod. The following work automatically regardless of whether the agent is written with the OpenAI SDK or the current LangGraph-based SDK:

| Concern | Why it's automatic |
|---|---|
| Input safety scan | Safety Orchestrator sits in the NetworkPolicy path before every agent pod. Every request is scanned before the SDK sees it. |
| Output safety scan | Happens at network egress. |
| Namespace isolation | Agent pods run in `agents-{team}` namespaces with NetworkPolicy default-deny. The SDK doesn't change that. |
| OPA sidecar presence | The sidecar is a K8s pod spec concern, not an SDK concern. It's always there. |

### What plugs in with config

**LLM routing via Portkey** — one line at startup:

```python
from openai import AsyncOpenAI
from agents import set_default_openai_client

portkey_client = AsyncOpenAI(
    api_key=os.environ["PORTKEY_API_KEY"],
    base_url="http://portkey.agentshield-platform:8787/v1",
    default_headers={
        "x-portkey-provider": os.environ.get("LLM_PROVIDER", "openai"),
    },
)
set_default_openai_client(portkey_client)
```

**Langfuse tracing** — three env vars + one instrumentation call:

```python
import os
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
os.environ["LANGFUSE_PUBLIC_KEY"]  = os.environ["LANGFUSE_PK"]
os.environ["LANGFUSE_SECRET_KEY"]  = os.environ["LANGFUSE_SK"]
os.environ["LANGFUSE_BASE_URL"]    = "http://langfuse-web.agentshield-platform:3000"

OpenAIAgentsInstrumentor().instrument()
```

### What needs a thin AgentShield wrapper

**OPA tool authorization** is currently woven into the LangGraph graph builder in `sdk/agentshield_sdk/graph_builder.py`. With the OpenAI Agents SDK, the right home is a `@tool_input_guardrail`:

```python
# agentshield/sdk/openai_adapter.py

from agents import tool_input_guardrail, GuardrailFunctionOutput
from agentshield.sdk.opa_client import query_opa

@tool_input_guardrail
async def opa_authorize(ctx, tool_call, input) -> GuardrailFunctionOutput:
    result = await query_opa(
        tool_name=tool_call.tool_name,
        input=input,
        agent_name=ctx.context.agent_name,
        session_id=ctx.context.session_id,
    )
    if result.decision == "deny":
        return GuardrailFunctionOutput(
            output_info={"reason": result.deny_reason},
            tripwire_triggered=True,
        )
    return GuardrailFunctionOutput(output_info=result, tripwire_triggered=False)
```

**PII de-anonymization** goes in the same wrapper — check OPA `allow_deanonymize` and call `/scan/deanonymize` before executing the tool, just like the current SDK does in `graph_builder.py`.

AgentShield would expose this as a drop-in replacement for `@function_tool`:

```python
from agentshield.sdk import agentshield_tool   # @function_tool + OPA + PII handling

@agentshield_tool
async def get_customer(customer_id: str) -> dict:
    """Fetch customer profile from CRM."""
    return crm.get(customer_id)
```

**AgentShield context** — a typed dataclass to carry session state through the run:

```python
from agentshield.sdk import AgentShieldContext

result = await Runner.run(
    agent,
    user_message,
    context=AgentShieldContext(
        session_id=session_id,
        agent_name="billing-agent",
        thread_id=thread_id,
        trace_id=request.headers.get("X-AgentShield-Trace-ID"),
    ),
)
```

### The one-call setup

All of the above collapses into a single `configure_agentshield()` call that reads from env vars set by the Deploy Controller:

```python
from agentshield.sdk.openai_adapter import configure_agentshield

configure_agentshield()
# Sets: Portkey as default OpenAI client
#       Langfuse as trace backend (OpenAI tracing disabled)
#       OPA tool guardrail registered globally
```

### What a developer's agent looks like

```python
# billing_agent.py

from agents import Agent, Runner
from agentshield.sdk.openai_adapter import configure_agentshield, agentshield_tool, AgentShieldContext

configure_agentshield()   # reads env vars, wires Portkey + Langfuse + OPA

@agentshield_tool
async def lookup_account(account_id: str) -> dict:
    """Look up account details from the billing system."""
    return billing_db.get(account_id)

@agentshield_tool
async def issue_refund(account_id: str, amount_usd: float) -> str:
    """Issue a refund to the account. Use only when explicitly authorized."""
    return payments.refund(account_id, amount_usd)

agent = Agent(
    name="billing-agent",
    instructions="You are a billing assistant. Only issue refunds under $500 without escalation.",
    tools=[lookup_account, issue_refund],
)

# Called by the AgentShield playground or chat endpoint
async def handle_request(message: str, session_id: str, thread_id: str) -> str:
    result = await Runner.run(
        agent,
        message,
        context=AgentShieldContext(
            session_id=session_id,
            agent_name="billing-agent",
            thread_id=thread_id,
        ),
    )
    return result.final_output
```

What AgentShield enforces without the developer touching it:
- Safety Orchestrator scanned the input before this code ran
- `@agentshield_tool` calls OPA before each tool executes
- PII placeholders are de-anonymized only where OPA permits
- Portkey routes the LLM call (model is platform-configured, not hardcoded)
- Every LLM call, tool call, and handoff lands in self-hosted Langfuse

What the developer gains from the OpenAI SDK on top:
- Multi-agent handoffs with full history transfer
- Structured outputs via Pydantic `output_type`
- Auto-generated tool schemas from type hints — no manual JSON
- Streaming out of the box
- TypeScript parity for JS teams

### The HITL gap — honest assessment

This is the one area where the integration isn't clean yet. Current AgentShield HITL uses `LangGraph interrupt()` + `AsyncPostgresSaver` — the graph pauses at a checkpoint in Postgres and the Studio resumes it after approval.

The OpenAI Agents SDK has `needs_approval=True` on `as_tool()`, but that's SDK-level — it has no knowledge of the AgentShield approval queue or the Studio dashboard.

Two realistic paths:

**Path A (pragmatic now):** For agents that need step-level HITL, continue using the LangGraph-based AgentShield SDK. For agents that don't need HITL (most agents just need safety scanning and OPA authorization), use the OpenAI Agents SDK. These can coexist in the same platform.

**Path B (proper integration, future):** The OpenAI Agents SDK documents Temporal and Dapr integrations for durable execution with HITL. Wire the Studio approval queue as the approval handler in that flow. More to build but architecturally complete.

---

## Comparison: AgentShield SDK vs. OpenAI Agents SDK

| Concern | Current AgentShield SDK | OpenAI Agents SDK on AgentShield |
|---|---|---|
| Agent definition | `Agent(name, instructions, tools)` | `Agent(name, instructions, tools)` — same surface |
| Tool authoring | Python function + registry registration | `@agentshield_tool` (wraps `@function_tool`) |
| Multi-agent | LangGraph graph edges | Native handoffs with history transfer |
| Structured outputs | Manual Pydantic in tool return | `output_type=MyModel` on Agent |
| Streaming | `runner.run_streamed()` → SSE | `Runner.run_streamed()` → events |
| OPA authorization | Graph builder wraps every tool call | `@tool_input_guardrail` in `@agentshield_tool` |
| HITL | LangGraph `interrupt()` + Studio | Not supported yet (Path A: keep LangGraph for HITL agents) |
| PII handling | Graph builder + session context | `@agentshield_tool` wrapper |
| LLM routing | Portkey via env var | Portkey via `set_default_openai_client()` |
| Tracing | Langfuse via `tracer` wrapper | Langfuse via OpenInference OTel instrumentation |
| Language | Python only | Python + TypeScript |
| Learning curve | LangGraph knowledge required | 5 primitives, documented at openai.github.io |

---

## What to Build

To make this real, one module needs to exist: `sdk/agentshield_sdk/openai_adapter.py`

```
configure_agentshield()      — startup wiring (Portkey client, Langfuse instrumentation, OPA guardrail)
agentshield_tool             — @function_tool + OPA check + PII de-anonymization
AgentShieldContext           — typed dataclass carrying session_id, agent_name, thread_id, trace_id
opa_authorize                — @tool_input_guardrail that calls OPA sidecar
```

Estimated effort: 1–2 days of implementation. No new infrastructure — everything already exists (OPA sidecar, Portkey, Langfuse, Safety Orchestrator).

---

## Sources

- [OpenAI Agents SDK docs](https://openai.github.io/openai-agents-python/)
- [OpenAI Agents SDK — Agents](https://openai.github.io/openai-agents-python/agents/)
- [OpenAI Agents SDK — Running Agents](https://openai.github.io/openai-agents-python/running_agents/)
- [OpenAI Agents SDK — Tools](https://openai.github.io/openai-agents-python/tools/)
- [OpenAI Agents SDK — Models](https://openai.github.io/openai-agents-python/models/)
- [OpenAI Agents SDK — Guardrails](https://openai.github.io/openai-agents-python/guardrails/)
- [OpenAI Agents SDK — Tracing](https://openai.github.io/openai-agents-python/tracing/)
- [OpenAI Agents SDK — Handoffs](https://openai.github.io/openai-agents-python/handoffs/)
- [OpenAI Agents SDK GitHub](https://github.com/openai/openai-agents-python)
- [Langfuse × OpenAI Agents SDK](https://langfuse.com/integrations/frameworks/openai-agents)
- [OpenAI April 2026 SDK evolution](https://www.abhs.in/blog/openai-agents-sdk-evolution-sandbox-harness-april-2026)
- [TechCrunch: OpenAI updates Agents SDK for enterprises](https://techcrunch.com/2026/04/15/openai-updates-its-agents-sdk-to-help-enterprises-build-safer-more-capable-agents/)

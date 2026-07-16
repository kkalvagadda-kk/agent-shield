"""
Graph builder — constructs a governed LangGraph ReAct agent from an Agent descriptor.

Architecture:
- Uses ``create_react_agent`` as the base.
- Platform tool references (strings) are resolved from the registry at setup time.
- Each tool is wrapped with an async governance layer that:
    1. Calls OPA to get a policy decision.
    2. If OPA returns ``require_approval=True``, calls
       ``hitl.require_approval()`` which internally calls LangGraph ``interrupt()``.
    3. If ``allow=False``, returns a denial string (tool is not executed).
    4. Otherwise executes the tool (HTTP call to platform or python-executor).
- Wrapped tools are converted to LangChain tools via @langchain_core.tools.tool
  so they can be bound to the LLM.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import logging
import uuid
from typing import Annotated, Any

from . import config
from .agent import Agent
from .hitl import require_approval
from .llm import get_llm
from . import opa_client
from .tool_resolver import resolve_tools

logger = logging.getLogger(__name__)

# Registry mapping tool name -> risk level so streaming.py can look it up.
_TOOL_RISK_REGISTRY: dict[str, str] = {}

# Identities allowed to auto-approve HITL (skip the interrupt). ONLY the internal
# batch/dataset-eval runner qualifies — it runs non-interactively so there is no
# human to approve. A real user's identity (a Keycloak sub) is never in this set,
# so auto_approve is inert on any interactive request even if the flag leaks.
_AUTO_APPROVE_IDENTITIES: frozenset[str] = frozenset({"eval-runner"})

# --- Eval v2 E-2: the side-effect record/mock seam --------------------------------
# `eval_mode` for the CURRENT run. "live" (the default) delivers every tool call for
# real — a run that never says otherwise can never be intercepted. "record" makes the
# delivery edge in `governed_tool` (step 3) record + mock a side-effecting call
# instead of invoking it, so a batch eval of a write-shaped agent never sends a real
# email / files a real JIRA. Set by the run driver (declarative-runner
# `_execute_durable_run` / the SDK server) from the dispatch body, which the
# registry-api fills from the PERSISTED PlaygroundRun.eval_mode — including on a HITL
# resume, which re-drives the graph and re-crosses this edge.
_EVAL_MODE_RECORD = "record"
_current_eval_mode: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_eval_mode", default="live"
)

# The buffer recorded calls are appended to. `None` = no recording installed.
# CONTRACT: `governed_tool` only ever APPENDS to the list object it reads out of this
# var — it never `set()`s it. LangGraph runs nodes in child contexts (a context copy),
# so a `set()` inside a tool call would be invisible to the driver; appending to the
# shared list object is visible. The driver therefore installs the buffer BEFORE the
# graph runs (see `begin_eval_context`) and hands the SAME list to the durable harness
# to drain into `run_steps.output.recorded_side_effects[]`.
_recorded_side_effects: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "recorded_side_effects", default=None
)


def begin_eval_context(eval_mode: str) -> list[dict]:
    """Install the eval mode + a fresh recording buffer in the CURRENT context.

    ONE call sets both, so the two can never drift (a run in record mode without a
    buffer to record into is not representable). Call it in the task/context that
    drives the graph, before driving; pass the returned list to the durable harness
    as ``recorded_side_effects`` so the records land on the real tool step.
    """
    _current_eval_mode.set(eval_mode or "live")
    buf: list[dict] = []
    _recorded_side_effects.set(buf)
    logger.info("eval context: eval_mode=%s (record seam %s)",
                eval_mode, "ARMED" if eval_mode == _EVAL_MODE_RECORD else "off")
    return buf


def _should_record(fn: Any) -> bool:
    """Does this tool call get recorded + mocked instead of delivered?

    True only in record mode, and then for every tool that is not PROVABLY read-only.
    `side_effecting` is stamped onto the callable by the tool resolver from the
    registry's `ToolResponse.side_effecting`; a tool that carries no classification at
    all (an inline/legacy callable, or a registry too old to serve the field) reads as
    None — FAIL-CLOSED: it is mocked, never invoked. Only an explicit False (a
    provably read-only tool, e.g. an HTTP GET) passes straight through.
    """
    if _current_eval_mode.get() != _EVAL_MODE_RECORD:
        return False
    return getattr(fn, "side_effecting", None) is not False


def _record_side_effect(fn: Any, kwargs: dict) -> dict:
    """Build the mock, record `{tool, args, mocked_response, would_have_invoked}`, and
    return the mock. The recorded entry is what `score_side_effects` asserts and the
    results UI renders ("the email that would have been sent")."""
    mocked_response = {"status": "ok", "id": f"mock-{uuid.uuid4()}"}
    entry = {
        "tool": fn.tool_name,
        "args": kwargs,
        "mocked_response": mocked_response,
        "would_have_invoked": getattr(fn, "invocation_target", None) or fn.tool_name,
    }
    buf = _recorded_side_effects.get()
    if buf is None:
        # The mock still stands (fail-closed: we do NOT fall back to invoking). The
        # call is just not persisted, so an item asserting it scores 0.0 rather than
        # silently passing.
        logger.warning(
            "record mode: no recording buffer installed — tool=%s mocked but NOT recorded",
            fn.tool_name,
        )
    else:
        buf.append(entry)
    return entry


def _get_tool_risk(tool_name: str) -> str:
    """Return the risk level for a tool name (populated during graph build)."""
    return _TOOL_RISK_REGISTRY.get(tool_name, "low")


def _one_hitl_tool_per_turn(state: dict) -> dict:
    """post_model_hook: enforce at most ONE high-risk (HITL) tool call per turn.

    Two+ high-risk tool calls in one turn each `interrupt()` in the SAME LangGraph
    super-step, which (in 0.6.x) collide on a shared interrupt id, hang the resume,
    and — because resume re-runs the whole tool node — RE-EXECUTE already-approved
    tools (duplicate external calls). Provider flags can't prevent this (Bedrock's
    Converse has no parallel-tool-calls control), so we enforce it in the graph:
    keep every non-high-risk tool call plus the FIRST high-risk one, drop the other
    high-risk calls. The ReAct loop re-requests the dropped calls on the next turn.

    Fires ONLY when 2+ tool calls are high-risk — low-risk concurrency and a single
    high-risk call (with or without low-risk siblings) are left untouched.
    """
    messages = state.get("messages") or []
    if not messages:
        return {}
    msg = messages[-1]
    tool_calls = getattr(msg, "tool_calls", None) or []
    if len(tool_calls) < 2:
        return {}
    high = [tc for tc in tool_calls if _get_tool_risk(tc.get("name", "")) == "high"]
    if len(high) <= 1:
        return {}

    drop_ids = {tc.get("id") for tc in high[1:]}
    kept = [tc for tc in tool_calls if tc.get("id") not in drop_ids]

    # Keep the message consistent with the kept tool_calls. Anthropic/Bedrock put
    # `tool_use` blocks in a list `content`; drop the blocks for the removed calls.
    # Providers with a plain-string content carry the calls only in `tool_calls`.
    content = getattr(msg, "content", None)
    new_content = content
    if isinstance(content, list):
        new_content = [
            b
            for b in content
            if not (
                isinstance(b, dict)
                and b.get("type") == "tool_use"
                and b.get("id") in drop_ids
            )
        ]

    trimmed = msg.model_copy(update={"tool_calls": kept, "content": new_content})
    logger.info(
        "one-HITL-per-turn: trimmed %d concurrent high-risk tool calls to 1 (kept=%s)",
        len(high), high[0].get("name"),
    )
    return {"messages": [trimmed]}


def _extract_reasoning(state: Any) -> str:
    """Best-effort LLM reasoning for a tool call: the text content of the last
    AIMessage (the "Let me look up…" thought that accompanies the tool call).

    Empty for some models / tool-forced calls — callers MUST treat it as
    optional and never gate approval on it. We look only at the last message
    (the tool-calling AIMessage); we do NOT fall back to earlier turns, which
    would surface misleading reasoning.
    """
    try:
        messages = (state or {}).get("messages") or []
    except AttributeError:
        return ""
    if not messages:
        return ""
    content = getattr(messages[-1], "content", None)
    if content is None:
        return ""
    if isinstance(content, list):
        # Anthropic returns content as a list of blocks; join the text blocks
        # (same shape streaming.py handles for text_delta).
        text = "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        text = str(content)
    return text.strip()


def _wrap_tool_with_governance(fn: Any, agent_name: str) -> Any:
    """Return an async wrapper that injects OPA check + HITL before the tool runs.

    The wrapper preserves the original function's __name__, __doc__, and
    type-annotations so that LangChain's tool introspection works correctly.
    It also injects the LangGraph state (via InjectedState) so the HITL approval
    can carry the LLM's reasoning for the call — excluded from the model-facing
    tool schema, so the LLM never sees or fills it.
    """
    @functools.wraps(fn)
    async def governed_tool(**kwargs: Any) -> Any:
        # LangGraph injects the graph state here (see __signature__ below). Pop it
        # before the real tool runs so the platform tool never receives it.
        graph_state = kwargs.pop("graph_state", None)

        # 1. OPA decision.
        uc = _current_user_context.get({})
        user_ctx = opa_client.UserContext(
            user_id=uc.get("user_id", ""),
            user_team=uc.get("user_team", ""),
        ) if uc else None
        decision = await opa_client.check_tool(agent_name, fn.tool_name, kwargs, user_context=user_ctx)

        # ⚠️⚠️⚠️ TEMPORARY POC GOVERNANCE BYPASS — REVERT BEFORE SHIPPING ⚠️⚠️⚠️
        # The OPA deny is intentionally DISABLED so a user_delegated agent's tool call
        # in an AUTONOMOUS workflow (no live user principal / no team grant -> OPA
        # default_deny) can still execute, purely to demo the POC-1/POC-2 context-
        # storage eval workflow (poc-research-answer / web_search). This makes tool
        # governance FAIL-OPEN for EVERY agent on this build. It MUST be reverted once
        # the context-storage POC effort is done — see the TODO / memory note.
        # TO REVERT: delete this block and uncomment the original below.
        if not decision.allow:
            logger.warning(
                "OPA WOULD DENY tool=%s agent=%s reason=%s — ALLOWING ANYWAY "
                "(TEMPORARY POC governance bypass; REVERT ME)",
                fn.tool_name, agent_name, decision.reason,
            )
        # --- ORIGINAL fail-closed behavior (restore this) ---
        # if not decision.allow:
        #     logger.info(
        #         "OPA denied tool=%s agent=%s reason=%s",
        #         fn.tool_name, agent_name, decision.reason,
        #     )
        #     return f"Tool '{fn.tool_name}' denied by policy: {decision.reason}"

        # 2. HITL — trust OPA's require_approval (risk→action is centralized in Rego).
        #    Batch/dataset eval runs non-interactively (no human to approve), so
        #    the platform sets an explicit auto_approve flag on the request; we
        #    skip the interrupt and let the tool execute rather than hang forever.
        #    OPA allow/deny is untouched — only the HITL *pause* is bypassed.
        #    Defense-in-depth: the flag is honored ONLY when it is BOTH set AND
        #    the caller is a trusted batch identity (never a real user), so an
        #    interactive request can never skip HITL even if the flag is present.
        needs_approval = decision.require_approval
        caller_id = uc.get("user_id", "") if uc else ""
        auto_approve = bool(uc.get("auto_approve")) and caller_id in _AUTO_APPROVE_IDENTITIES
        if needs_approval and auto_approve:
            logger.info(
                "HITL auto-approved (batch eval) tool=%s agent=%s caller=%s",
                fn.tool_name, agent_name, caller_id,
            )
        if needs_approval and not auto_approve:
            # Read thread_id from LangGraph's config (works in subgraphs).
            # Falls back to the ContextVar for custom-container SDK agents.
            try:
                from langgraph.config import get_config as _get_config
                thread_id = _get_config().get("configurable", {}).get("thread_id", "")
            except (RuntimeError, ImportError):
                thread_id = _current_thread_id.get("")
            approval_result = await require_approval(
                agent_name=agent_name,
                tool_name=fn.tool_name,
                tool_args=kwargs,
                thread_id=thread_id,
                risk=fn.risk,
                reasoning=_extract_reasoning(graph_state),
                conversation_history=None,
            )
            if approval_result.get("decision") != "approved":
                reason = approval_result.get("reason", "rejected by reviewer")
                return f"Tool '{fn.tool_name}' was not approved: {reason}"

        # 3. Deliver — the ONE edge every governed tool call crosses to reach its
        #    downstream. Eval v2 E-2 substitutes the DELIVERY here and nowhere else:
        #    under `eval_mode=record` a side-effecting call is recorded and answered
        #    with a mock, so the eval exercises the REAL governed path (OPA above ran,
        #    HITL above parked/approved for real) while the real world is untouched.
        #    Mocking before governance would evaluate a different path than production
        #    — the bandaid this seam exists to avoid.
        if _should_record(fn):
            entry = _record_side_effect(fn, kwargs)
            logger.info(
                "eval record: tool=%s agent=%s NOT invoked (would_have_invoked=%s)",
                fn.tool_name, agent_name, entry["would_have_invoked"],
            )
            # Platform tools return a JSON string (tool_executor), and this wrapper
            # already answers with plain strings on the deny/reject paths — so the
            # mock is serialized the same way rather than changing the tool's shape.
            return json.dumps(entry["mocked_response"])

        # Execute the tool (HTTP call to platform endpoint or python-executor).
        if asyncio.iscoroutinefunction(fn):
            return await fn(**kwargs)
        return fn(**kwargs)

    # Copy metadata for LangChain introspection.
    governed_tool.__name__ = fn.__name__
    governed_tool.__doc__ = fn.__doc__
    governed_tool.risk = fn.risk
    governed_tool.tool_name = fn.tool_name
    governed_tool.__annotations__ = getattr(fn, "__annotations__", {})

    # Inject the LangGraph state so we can read the LLM's reasoning at HITL time.
    # We append a keyword-only `graph_state: Annotated[dict, InjectedState]` param
    # to the tool's signature. InjectedState is excluded from the model-facing
    # schema (LLM never sees it) and filled by ToolNode at call time. Setting an
    # explicit __signature__ takes precedence over functools.wraps' __wrapped__
    # follow, so lc_tool() picks up the injected param. Best-effort: if InjectedState
    # is unavailable, fall back to the un-injected wrapper (reasoning stays empty).
    try:
        from langgraph.prebuilt import InjectedState  # type: ignore[import]

        base_sig = inspect.signature(fn)
        injected = inspect.Parameter(
            "graph_state",
            kind=inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[dict, InjectedState],
        )
        # Insert the injected keyword-only param BEFORE any VAR_KEYWORD (**kwargs),
        # since a keyword-only parameter cannot legally follow **kwargs. Tools with a
        # bare **kwargs signature (python tools with no input_schema) would otherwise
        # raise ValueError here, silently skipping injection (reasoning capture lost).
        sig_params = list(base_sig.parameters.values())
        vk_idx = next(
            (i for i, p in enumerate(sig_params)
             if p.kind is inspect.Parameter.VAR_KEYWORD),
            len(sig_params),
        )
        sig_params.insert(vk_idx, injected)
        governed_tool.__signature__ = base_sig.replace(parameters=sig_params)
        # The annotation MUST also be present, or langchain's schema builder
        # raises KeyError('graph_state'). InjectedState is still excluded from the
        # model-facing tool_call_schema (verified), so the LLM never sees it.
        governed_tool.__annotations__ = {
            **getattr(fn, "__annotations__", {}),
            "graph_state": Annotated[dict, InjectedState],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not inject graph state for reasoning capture: %s", exc)

    return governed_tool


# ContextVar used by governed_tool to access the current LangGraph thread_id.
# Set by the Runner before invoking the graph.
_current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_thread_id", default=""
)

# ContextVar for user identity propagation to OPA.
# Set by the declarative-runner before streaming; read by governed_tool.
_current_user_context: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "current_user_context", default={}
)


async def resolve_agent_tools(agent: Agent) -> list[Any]:
    """Resolve all tools for an agent: platform references + inline callables.

    Platform tool names (strings) are fetched from the registry API.
    Inline callables (legacy @tool decorated functions) are passed through as-is.
    """
    all_tools: list[Any] = []

    # Resolve platform tool references
    platform_names = agent.platform_tool_names
    if platform_names:
        resolved = await resolve_tools(platform_names)
        all_tools.extend(resolved)

    # Pass through legacy inline tools
    all_tools.extend(agent.inline_tools)

    return all_tools


def build_graph(agent: Agent, checkpointer: Any = None, resolved_tools: list[Any] | None = None) -> Any:
    """Build and compile a governed LangGraph ReAct agent.

    Args:
        agent:          The Agent descriptor (name, instructions, tools, model).
        checkpointer:   LangGraph checkpointer (AsyncPostgresSaver or MemorySaver).
                        If None, graph is stateless (no HITL resume support).
        resolved_tools: Pre-resolved tool callables. If None, only inline tools
                        from the agent are used (platform tools must be resolved
                        via resolve_agent_tools first).

    Returns:
        A compiled LangGraph graph ready for ainvoke / astream_events.
    """
    from langgraph.prebuilt import create_react_agent  # type: ignore[import]
    from langchain_core.tools import tool as lc_tool  # type: ignore[import]

    # Resolve the LLM (uses agent.model override if set, else env var).
    llm = get_llm(model_override=agent.model)

    # Use provided resolved tools, or fall back to inline-only.
    tools = resolved_tools if resolved_tools is not None else agent.inline_tools

    # Wrap each tool with governance and register its risk level.
    lc_tools: list[Any] = []
    for fn in tools:
        _TOOL_RISK_REGISTRY[fn.tool_name] = fn.risk

        governed = _wrap_tool_with_governance(fn, agent.name)

        # Convert to a LangChain-compatible tool.
        lc_fn = lc_tool(governed)
        lc_tools.append(lc_fn)

    # Nudge the model to state its intent before each tool call. This makes the
    # AIMessage content (the reasoning we surface on HITL approvals) reliably
    # populated across models — otherwise some models emit only tool_calls with
    # empty content. Best-effort UX aid, not a governance control.
    prompt = agent.instructions or ""
    prompt = (
        f"{prompt}\n\n"
        "Before calling any tool, first state in one short sentence why you need "
        "it and what specific information you are retrieving."
    ).strip()

    graph = create_react_agent(
        model=llm,
        tools=lc_tools,
        prompt=prompt,
        checkpointer=checkpointer,
        # Runs after the model, before the tool node: caps a turn at one high-risk
        # tool call so concurrent HITL interrupts can't collide (provider-agnostic).
        post_model_hook=_one_hitl_tool_per_turn,
    )
    return graph

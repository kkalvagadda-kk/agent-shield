"""
Composite-workflow orchestration (Decision 22).

Runs as a background asyncio task inside registry-api. Given a parent workflow
run, it creates one CHILD AgentRun per member-agent invocation (parent_run_id →
parent), dispatches each to the member's production pod, and rolls the parent
run status up. Four orchestration modes are supported:

  - sequential  — walk the edge chain (or member position order if no edges),
                  threading each agent's output into the next; fail-fast.
  - conditional — at each node, evaluate outgoing-edge conditions against the
                  agent output and route to the first match (else the default
                  blank-condition edge); fail-fast.
  - supervisor  — a coordinator agent (member with role='supervisor') decides
                  the next worker each turn; loops until a DONE sentinel or a
                  max_iterations cap.
  - handoff     — each agent may signal the next agent in its output
                  ({"handoff_to": name}); otherwise follow its sole outgoing
                  edge. Loops until no next hop.

Edge conditions use a small, safe DSL evaluated by `filter_engine.evaluate_filters`
(no eval). See `evaluate_condition`. The declarative-runner `orchestrator.py`
module mirrors this as the future extraction target.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from db import AsyncSessionLocal
from filter_engine import evaluate_filters
from models import AgentRun

logger = logging.getLogger(__name__)

# Hard cap on total member steps for graph walks — protects against cycles even
# when a bespoke max_iterations isn't configured.
_MAX_STEPS = 50
_DONE_SENTINEL = "DONE"


def _team_namespace(team: str) -> str:
    return f"agents-{(team or 'platform').lower().replace(' ', '-')}"


async def _dispatch(agent_name: str, team: str, message: str) -> tuple[str, str | None, str | None]:
    """POST the message to the member agent's production pod. Returns (status, output, error)."""
    url = f"http://{agent_name}-production.{_team_namespace(team)}.svc.cluster.local:8080/chat"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json={"message": message})
        if resp.status_code == 200:
            data = resp.json()
            return "completed", (data.get("output") or data.get("response") or json.dumps(data)), None
        return "failed", None, f"agent returned {resp.status_code}: {resp.text[:300]}"
    except httpx.ConnectError:
        # No pod at the expected Service — almost always an undeployed agent.
        return "failed", None, (
            f"agent '{agent_name}' appears undeployed (no pod at {url}). "
            f"Deploy the agent before running the workflow."
        )
    except Exception as exc:  # network / timeout / bad JSON
        return "failed", None, f"dispatch failed: {exc}"


# ---------------------------------------------------------------------------
# Graph + condition resolution
# ---------------------------------------------------------------------------
async def resolve_member_names(session, workflow_id) -> list[str]:
    """Ordered member agent names for a composite workflow (position, then added_at)."""
    from models import Agent, WorkflowMember

    rows = (await session.execute(
        select(Agent.name)
        .join(WorkflowMember, WorkflowMember.agent_id == Agent.id)
        .where(WorkflowMember.workflow_id == workflow_id)
        .order_by(WorkflowMember.position.nulls_last(), WorkflowMember.added_at)
    )).all()
    return [r[0] for r in rows]


async def resolve_members(session, workflow_id) -> list[dict]:
    """Ordered members with name/role/routing (for supervisor + role lookups)."""
    from models import Agent, WorkflowMember

    rows = (await session.execute(
        select(Agent.name, WorkflowMember.role, WorkflowMember.routing)
        .join(WorkflowMember, WorkflowMember.agent_id == Agent.id)
        .where(WorkflowMember.workflow_id == workflow_id)
        .order_by(WorkflowMember.position.nulls_last(), WorkflowMember.added_at)
    )).all()
    return [{"name": r[0], "role": r[1], "routing": r[2] or {}} for r in rows]


async def resolve_edge_graph(session, workflow_id) -> dict[str, list[tuple[str, str | None]]]:
    """Adjacency list {source_name: [(target_name, condition), ...]} ordered by edge position.

    Blank/NULL condition = default (fallback) edge.
    """
    from models import Agent, WorkflowEdge

    src = Agent.__table__.alias("src")
    tgt = Agent.__table__.alias("tgt")
    rows = (await session.execute(
        select(src.c.name, tgt.c.name, WorkflowEdge.condition)
        .select_from(WorkflowEdge)
        .join(src, src.c.id == WorkflowEdge.source_agent_id)
        .join(tgt, tgt.c.id == WorkflowEdge.target_agent_id)
        .where(WorkflowEdge.workflow_id == workflow_id)
        .order_by(WorkflowEdge.position.nulls_last(), WorkflowEdge.created_at)
    )).all()
    graph: dict[str, list[tuple[str, str | None]]] = {}
    for source_name, target_name, condition in rows:
        graph.setdefault(source_name, []).append((target_name, condition))
    return graph


def find_start_node(graph: dict[str, list[tuple[str, str | None]]], member_names: list[str]) -> str | None:
    """First member that is never an edge target. Falls back to first by position."""
    if not member_names:
        return None
    targets = {t for outs in graph.values() for (t, _) in outs}
    for name in member_names:
        if name not in targets:
            return name
    # All nodes are targets (cycle) — start at the first member by position.
    logger.warning("workflow graph has no clear start node (cycle?); starting at '%s'", member_names[0])
    return member_names[0]


def evaluate_condition(condition: str | None, agent_output: str) -> bool:
    """Evaluate an edge condition against an agent's output.

    DSL (reuses filter_engine.evaluate_filters — no eval):
      - None / blank         → True (default / fallback edge)
      - starts with '['      → JSON array of {field, op, value} rules
      - otherwise            → keyword shorthand → output contains <keyword>

    Rules run against {"output": <text>} plus, if the output is a JSON object,
    its top-level keys (so conditions can match structured fields directly).
    """
    if condition is None or not condition.strip():
        return True
    cond = condition.strip()

    output_str = agent_output or ""
    payload: dict = {"output": output_str}
    try:
        parsed = json.loads(output_str)
        if isinstance(parsed, dict):
            payload.update(parsed)
    except (ValueError, TypeError):
        pass

    if cond.startswith("["):
        try:
            rules = json.loads(cond)
            if not isinstance(rules, list):
                raise ValueError("condition JSON must be an array of rules")
        except (ValueError, TypeError) as exc:
            logger.warning("invalid edge condition %r (%s) — treating as no-match", cond, exc)
            return False
    else:
        rules = [{"field": "output", "op": "contains", "value": cond}]

    return bool(evaluate_filters(rules, payload).get("matched"))


# ---------------------------------------------------------------------------
# Run-tree helpers
# ---------------------------------------------------------------------------
async def _mark_parent(parent_run_id: str, status_val: str, output: str | None = None) -> None:
    async with AsyncSessionLocal() as s:
        parent = (await s.execute(select(AgentRun).where(AgentRun.id == parent_run_id))).scalar_one_or_none()
        if parent:
            parent.status = status_val
            if output is not None:
                parent.output = output[:4000]
            if status_val in ("completed", "failed", "cancelled"):
                parent.completed_at = datetime.now(timezone.utc)
            await s.commit()


async def _fail_parent(parent_run_id: str, error_message: str) -> None:
    """Mark the parent run failed with a diagnostic error_message."""
    async with AsyncSessionLocal() as s:
        parent = (await s.execute(select(AgentRun).where(AgentRun.id == parent_run_id))).scalar_one_or_none()
        if parent:
            parent.status = "failed"
            parent.error_message = error_message[:4000]
            parent.completed_at = datetime.now(timezone.utc)
            await s.commit()


async def _run_step(parent_run_id: str, team: str, agent_name: str, current_input: str) -> tuple[str, str | None, str | None]:
    """Create a child run, dispatch to the member, record the outcome. Returns (status, output, err)."""
    start = time.perf_counter()
    async with AsyncSessionLocal() as s:
        child = AgentRun(
            agent_name=agent_name,
            input=current_input[:4000] if current_input else None,
            context="production",
            status="running",
            trigger_type="workflow",
            parent_run_id=parent_run_id,
            team=team,
        )
        s.add(child)
        await s.commit()
        await s.refresh(child)
        child_id = str(child.id)

    status_val, output, err = await _dispatch(agent_name, team, current_input)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    async with AsyncSessionLocal() as s:
        child = (await s.execute(select(AgentRun).where(AgentRun.id == child_id))).scalar_one_or_none()
        if child:
            child.status = status_val
            child.output = (output[:4000] if output else None)
            child.error_message = err
            child.latency_ms = elapsed_ms
            child.completed_at = datetime.now(timezone.utc)
            await s.commit()
    return status_val, output, err


def _parse_next_agent(output: str, candidate_names: list[str]) -> str | None:
    """Determine the next agent from an agent's output.

    Returns a candidate name, the DONE sentinel string, or None (no route found).
    Recognizes JSON {"next"|"handoff_to": name} / {"action":"done"}, a bare DONE
    keyword, or a candidate name mentioned in free text.
    """
    text = output or ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if str(parsed.get("action", "")).lower() == "done":
                return _DONE_SENTINEL
            nxt = parsed.get("next") or parsed.get("handoff_to")
            if isinstance(nxt, str) and nxt in candidate_names:
                return nxt
    except (ValueError, TypeError):
        pass
    if _DONE_SENTINEL.lower() in text.lower():
        return _DONE_SENTINEL
    for name in candidate_names:
        if name and name in text:
            return name
    return None


# ---------------------------------------------------------------------------
# Orchestration modes
# ---------------------------------------------------------------------------
async def orchestrate_graph_sequential(parent_run_id: str, team: str, workflow_id: str, input_message: str) -> None:
    """Walk the edge chain (default/first outgoing edge) or member order if no edges. Fail-fast."""
    async with AsyncSessionLocal() as s:
        member_names = await resolve_member_names(s, workflow_id)
        graph = await resolve_edge_graph(s, workflow_id)
    await _mark_parent(parent_run_id, "running")

    current_input = input_message or ""
    failed = False

    if not graph:
        # Backward-compatible: no edges → positional order (original behavior).
        order = member_names
    else:
        order = []
        visited: set[str] = set()
        node = find_start_node(graph, member_names)
        while node and node not in visited and len(order) < _MAX_STEPS:
            order.append(node)
            visited.add(node)
            outs = graph.get(node, [])
            # Prefer the default (blank) edge; else the first edge.
            nxt = next((t for (t, c) in outs if not (c and c.strip())), None)
            node = nxt if nxt is not None else (outs[0][0] if outs else None)

    for agent_name in order:
        status_val, output, _err = await _run_step(parent_run_id, team, agent_name, current_input)
        if status_val == "failed":
            failed = True
            logger.warning("workflow %s: member '%s' failed — fail-fast stop", parent_run_id, agent_name)
            break
        current_input = output or ""

    await _mark_parent(parent_run_id, "failed" if failed else "completed",
                       None if failed else current_input)
    logger.info("workflow run %s (sequential) finished: %s", parent_run_id, "failed" if failed else "completed")


async def orchestrate_conditional(parent_run_id: str, team: str, workflow_id: str, input_message: str) -> None:
    """At each node, take the first outgoing edge whose condition matches the output."""
    async with AsyncSessionLocal() as s:
        member_names = await resolve_member_names(s, workflow_id)
        graph = await resolve_edge_graph(s, workflow_id)
    await _mark_parent(parent_run_id, "running")

    current_input = input_message or ""
    node = find_start_node(graph, member_names)
    visited_count = 0
    failed = False

    while node and visited_count < _MAX_STEPS:
        visited_count += 1
        status_val, output, _err = await _run_step(parent_run_id, team, node, current_input)
        if status_val == "failed":
            failed = True
            logger.warning("workflow %s: node '%s' failed — stop", parent_run_id, node)
            break
        current_input = output or ""

        outs = graph.get(node, [])
        if not outs:
            break  # terminal node → complete
        # First conditional edge that matches, else the default (blank) edge.
        nxt = None
        for (target, cond) in outs:
            if cond and cond.strip():
                if evaluate_condition(cond, current_input):
                    nxt = target
                    break
        if nxt is None:
            nxt = next((t for (t, c) in outs if not (c and c.strip())), None)
        if nxt is None:
            logger.info("workflow %s: no matching/default edge from '%s' — complete", parent_run_id, node)
            break
        node = nxt

    await _mark_parent(parent_run_id, "failed" if failed else "completed",
                       None if failed else current_input)
    logger.info("workflow run %s (conditional) finished: %s", parent_run_id, "failed" if failed else "completed")


async def orchestrate_handoff(parent_run_id: str, team: str, workflow_id: str, input_message: str) -> None:
    """Follow the handoff signal in each agent's output; else its sole outgoing edge."""
    async with AsyncSessionLocal() as s:
        member_names = await resolve_member_names(s, workflow_id)
        graph = await resolve_edge_graph(s, workflow_id)
    await _mark_parent(parent_run_id, "running")

    current_input = input_message or ""
    node = find_start_node(graph, member_names)
    visited_count = 0
    failed = False

    while node and visited_count < _MAX_STEPS:
        visited_count += 1
        status_val, output, _err = await _run_step(parent_run_id, team, node, current_input)
        if status_val == "failed":
            failed = True
            break
        current_input = output or ""

        outs = graph.get(node, [])
        if not outs:
            break
        targets = [t for (t, _c) in outs]
        signal = _parse_next_agent(current_input, targets)
        if signal == _DONE_SENTINEL:
            break
        if signal in targets:
            node = signal
        elif len(outs) == 1:
            node = outs[0][0]  # deterministic single handoff
        else:
            logger.info("workflow %s: no handoff signal from '%s' and %d edges — stop",
                        parent_run_id, node, len(outs))
            break

    await _mark_parent(parent_run_id, "failed" if failed else "completed",
                       None if failed else current_input)
    logger.info("workflow run %s (handoff) finished: %s", parent_run_id, "failed" if failed else "completed")


async def orchestrate_supervisor(parent_run_id: str, team: str, workflow_id: str, input_message: str) -> None:
    """A coordinator (role='supervisor') routes to workers each turn until DONE / max_iterations."""
    async with AsyncSessionLocal() as s:
        members = await resolve_members(s, workflow_id)

    supervisor = next((m for m in members if (m["role"] or "").lower() == "supervisor"), None)
    if supervisor is None:
        await _fail_parent(parent_run_id, "No supervisor member (set a member's role to 'supervisor').")
        logger.warning("workflow %s (supervisor): no supervisor role set", parent_run_id)
        return

    workers = [m["name"] for m in members if m["name"] != supervisor["name"]]
    max_iters = int(supervisor["routing"].get("max_iterations", 10) or 10)
    await _mark_parent(parent_run_id, "running")

    current_input = input_message or ""
    failed = False
    hit_cap = True

    for _ in range(max_iters):
        # 1. supervisor decides
        s_status, s_out, _e = await _run_step(parent_run_id, team, supervisor["name"], current_input)
        if s_status == "failed":
            failed = True
            break
        decision = _parse_next_agent(s_out or "", workers)
        if decision == _DONE_SENTINEL or decision is None:
            hit_cap = False
            current_input = s_out or current_input
            break
        # 2. dispatch to the chosen worker; thread its output back to the supervisor
        w_status, w_out, _we = await _run_step(parent_run_id, team, decision, s_out or "")
        if w_status == "failed":
            failed = True
            break
        current_input = w_out or ""
    else:
        hit_cap = True

    if failed:
        await _fail_parent(parent_run_id, "supervisor or worker step failed during dispatch")
    elif hit_cap:
        await _fail_parent(parent_run_id, f"supervisor reached max_iterations ({max_iters}) without completing")
        logger.warning("workflow %s (supervisor) hit max_iterations=%d", parent_run_id, max_iters)
    else:
        await _mark_parent(parent_run_id, "completed", current_input)
    logger.info("workflow run %s (supervisor) finished: %s", parent_run_id,
                "failed" if (failed or hit_cap) else "completed")


# ---------------------------------------------------------------------------
# Dispatcher + backward-compat entry point
# ---------------------------------------------------------------------------
async def orchestrate(parent_run_id: str, team: str, workflow_id: str, input_message: str, mode: str) -> None:
    """Route to the orchestration implementation for `mode`. Fail-safe (never raises)."""
    try:
        if mode == "conditional":
            await orchestrate_conditional(parent_run_id, team, workflow_id, input_message)
        elif mode == "supervisor":
            await orchestrate_supervisor(parent_run_id, team, workflow_id, input_message)
        elif mode == "handoff":
            await orchestrate_handoff(parent_run_id, team, workflow_id, input_message)
        else:  # sequential (default)
            await orchestrate_graph_sequential(parent_run_id, team, workflow_id, input_message)
    except Exception as exc:  # never leave a run stuck in 'running'
        logger.exception("workflow run %s (%s) crashed: %s", parent_run_id, mode, exc)
        await _mark_parent(parent_run_id, "failed")


async def orchestrate_sequential(parent_run_id: str, team: str, member_agent_names: list[str], input_message: str) -> None:
    """Backward-compatible entry point (scheduler/webhook internal runs).

    Threads members in the given order with fail-fast, identical to the original
    MVP behavior. New callers should use `orchestrate(..., mode)`.
    """
    await _mark_parent(parent_run_id, "running")
    current_input = input_message or ""
    failed = False
    for agent_name in member_agent_names:
        status_val, output, _err = await _run_step(parent_run_id, team, agent_name, current_input)
        if status_val == "failed":
            failed = True
            logger.warning("workflow %s: member '%s' failed — fail-fast stop", parent_run_id, agent_name)
            break
        current_input = output or ""
    await _mark_parent(parent_run_id, "failed" if failed else "completed",
                       None if failed else current_input)
    logger.info("workflow run %s finished: status=%s", parent_run_id, "failed" if failed else "completed")

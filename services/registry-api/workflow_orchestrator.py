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

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select

from db import AsyncSessionLocal
from filter_engine import evaluate_filters
from models import AgentRun, Approval, RunStep
from pod_stream import stream_pod_chat_frames

logger = logging.getLogger(__name__)

# Internal per-member terminal sentinel. Yielded by _dispatch_stream / _run_step_stream
# for the mode walkers to route on; the SSE serializer in the stream endpoint filters it
# out so it NEVER reaches a client (contracts/sse-frames.md §D).
_MEMBER_END = "__member_end__"

from typing import AsyncGenerator, Optional  # noqa: E402

# Hard cap on total member steps for graph walks — protects against cycles even
# when a bespoke max_iterations isn't configured.
_MAX_STEPS = 50
_DONE_SENTINEL = "DONE"


def _team_namespace(team: str) -> str:
    return f"agents-{(team or 'platform').lower().replace(' ', '-')}"


async def _resolve_agent_environment(agent_name: str) -> str:
    """Look up the running deployment environment for an agent. Defaults to 'production'."""
    from models import Agent, Deployment
    try:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                select(Deployment.environment)
                .join(Agent, Agent.id == Deployment.agent_id)
                .where(Agent.name == agent_name, Deployment.status == "running")
                .order_by(Deployment.deployed_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            return row or "production"
    except Exception:
        return "production"


async def _persist_tool_call_step(child_id: str, tool: str, status: str) -> None:
    """Persist one observed reactive tool_call frame as a RunStep marker row under the
    child run (R2/R4). Reactive members write NO run_steps on their own, so this marker
    (``output.kind='tool_call'``) is what the run-tree tool_calls projection reads — the
    invariant that reload == stream for reactive members. Best-effort; a persistence
    failure must never break the live stream."""
    try:
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as s:
            next_num = (await s.execute(
                select(func.coalesce(func.max(RunStep.step_number), 0) + 1)
                .where(RunStep.run_id == child_id)
            )).scalar_one()
            s.add(RunStep(
                run_id=child_id,
                step_number=int(next_num),
                name=tool or "tool",
                status="completed" if status == "ok" else "failed",
                output={"kind": "tool_call", "tool": tool, "status": status},
                started_at=now,
                completed_at=now,
            ))
            await s.commit()
    except Exception as exc:  # noqa: BLE001 — never break the stream on a marker write
        logger.warning("failed to persist tool_call RunStep under %s (%s): %s", child_id, tool, exc)


async def _dispatch_stream(
    agent_name: str, team: str, message: str, thread_id: str,
    conversation_id: str, scope: str, child_id: str,
    user_directive: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Reactive member: stream the member pod's /chat/stream via the ONE shared reader,
    re-yielding its content frames (token/tool_call/rationale/error/approval_requested)
    to the caller, accumulating token text as the member's output, and persisting each
    observed tool_call as a RunStep marker (so the tree projection has data — R2).

    The reader's own ``agent_start`` is suppressed here — ``_run_step_stream`` owns the
    member lifecycle framing (agent_start/agent_end) so durable and reactive members
    frame identically. Ends by yielding the internal ``__member_end__`` sentinel with the
    routing outcome (status/output/error); the sentinel is consumed by the mode walker,
    never sent to the client.

    ``conversation_id``/``scope`` carry the SHARED workflow transcript key (§5.2 identity
    split); they ride ALONGSIDE ``thread_id`` (the per-member checkpoint + Approval key)
    and never alias it.
    """
    environment = await _resolve_agent_environment(agent_name)
    service_url = f"http://{agent_name}-{environment}.{_team_namespace(team)}.svc.cluster.local:8080"
    accumulated: list[str] = []
    error_msg: Optional[str] = None
    try:
        async for frame in stream_pod_chat_frames(
            service_url,
            message=message,
            thread_id=thread_id,
            conversation_id=conversation_id,
            scope=scope,
            author=agent_name,
            user_directive=user_directive,
        ):
            ftype = frame.get("type")
            if ftype == "agent_start":
                # Lifecycle is owned by _run_step_stream — do not double-open the bubble.
                continue
            if ftype == "token":
                accumulated.append(frame.get("content", ""))
            elif ftype == "tool_call":
                await _persist_tool_call_step(child_id, frame.get("tool", ""), frame.get("status", "ok"))
            elif ftype == "error":
                error_msg = frame.get("message") or "member stream error"
            yield frame
    except Exception as exc:  # noqa: BLE001 — surface as a failed member, never crash the walk
        logger.exception("reactive member '%s' stream failed", agent_name)
        error_msg = f"dispatch failed: {exc}"

    output = "".join(accumulated) or None
    if error_msg is not None:
        yield {"type": _MEMBER_END, "author": agent_name, "status": "failed",
               "output": output, "error": error_msg}
    else:
        yield {"type": _MEMBER_END, "author": agent_name, "status": "completed",
               "output": output, "error": None}


async def _resolve_agent_shape(agent_name: str) -> str:
    """Look up a member agent's execution_shape ('durable' | 'reactive').

    Defaults to 'reactive' (the synchronous /chat path) when unknown — a member we
    can't classify keeps the existing, safe behavior rather than being forced durable.
    """
    from models import Agent
    try:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                select(Agent.execution_shape).where(Agent.name == agent_name)
            )).scalar_one_or_none()
            return row or "reactive"
    except Exception:
        return "reactive"


async def _dispatch_durable_member(
    agent_name: str, team: str, message: str, child_id: str,
    conversation_id: str | None = None, scope: str = "agent",
    workflow_run_id: str | None = None,
) -> tuple[str, str | None, str | None]:
    """Dispatch a DURABLE member via the pod's `/run` (D4 "+ Visibility"), then poll the
    child AgentRun to a terminal state.

    Unlike `/chat` (which returns the final answer inline and writes no per-node rows),
    `/run` drives the shared durable harness and POSTs one `run_steps` row per node/tool
    boundary to the child's step-update callback — so the member's internal steps appear
    under `child_id` in the run tree (StepTracker zoom). `run_id` == `child_id` == the
    member's thread_id, so a SDK-created Approval (thread_id=run_id) and the console
    resume correlate back to this child (see `_run_step` + approvals._resume_and_advance).

    Returns (status, output, error) where status is 'completed' | 'failed' |
    'awaiting_approval'. The callback is what writes the child's terminal status/output;
    this polls for it. **Documented limitation (gap ledger):** a within-member crash
    mid-execution is not resumed here — the orchestrator only re-dispatches after an
    approval decision, not after a member-pod crash.
    """
    environment = await _resolve_agent_environment(agent_name)
    from durable_dispatch import dispatch_durable_run, registry_internal_base
    base = f"http://{agent_name}-{environment}.{_team_namespace(team)}.svc.cluster.local:8080"
    callback_url = f"{registry_internal_base()}/api/v1/internal/runs/{child_id}/step-update"
    # PARITY: the `/run` POST literal lives ONLY in durable_dispatch.dispatch_durable_run
    # (see its docstring — "here and nowhere else"). A workflow member is a durable run
    # exactly like a top-level production/sandbox run; the SOLE difference is the runner
    # target — the member's own deployed pod rather than the shared declarative-runner —
    # which the shared dispatcher already exposes as an explicit `runner_url` arg. So we
    # reuse it instead of hand-rolling a second POST (the 2026-07-11 HITL-retro drift root
    # cause). run_id == child_id == thread_id, so the SDK-created Approval and the console
    # resume correlate to this child.
    # Shared workflow transcript key (§5.2 identity split) rides in the durable /run
    # input_payload ALONGSIDE the message; the per-member thread_id (== child_id, the
    # WS-1 checkpoint + approval key) is set on the child row above and is NOT aliased here.
    input_payload: dict = {"message": message}
    if conversation_id:
        input_payload["conversation_id"] = conversation_id
        input_payload["scope"] = scope
    if workflow_run_id:
        input_payload["workflow_run_id"] = workflow_run_id
    ok, err = await dispatch_durable_run(
        run_id=str(child_id),
        agent_name=agent_name,
        input_payload=input_payload,
        callback_url=callback_url,
        runner_url=base,
        timeout_s=15.0,
    )
    if not ok:
        # dispatch_durable_run returns a generic reason (bad status OR a network error);
        # annotate with the likeliest cause — an undeployed member has no pod at {base}/run
        # — without type-sniffing the returned error string.
        return "failed", None, (
            f"member '{agent_name}' /run dispatch failed (agent may be undeployed at "
            f"{base}/run): {err}"
        )

    # Poll the child run — the step-update callback writes its terminal status + output.
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                select(AgentRun.status, AgentRun.output, AgentRun.error_message)
                .where(AgentRun.id == child_id)
            )).first()
        if row is not None:
            st, out, errm = row
            if st in ("completed", "failed", "awaiting_approval"):
                return st, out, errm
        await asyncio.sleep(1.0)
    return "failed", None, "durable member timed out (no terminal callback within 120s)"


async def resume_durable_member(
    agent_name: str, team: str, child_id: str,
    decision: str, reviewer_id: str | None, reason: str | None,
) -> tuple[str, str | None, str | None]:
    """Resume a parked DURABLE workflow member after an approval decision, then poll
    its child AgentRun to terminal. The mirror image of `_dispatch_durable_member`:
    it resolves the member's ACTUAL deployment environment (so it hits the real
    `{agent}-{env}` pod, not a hardcoded `-production` that DNS-fails), and posts to
    the pod's `/resume/{id}` with `run_id`+`callback_url` so the pod re-drives the
    durable harness and posts the remaining steps (incl. completion) to the child's
    step-update callback — fire-and-forget. Completion arrives via that callback, so
    we POLL the child rather than trusting the /resume response (which is just
    'accepted'). Returns (status, output, error)."""
    environment = await _resolve_agent_environment(agent_name)
    from durable_dispatch import registry_internal_base
    base = f"http://{agent_name}-{environment}.{_team_namespace(team)}.svc.cluster.local:8080"
    callback_url = f"{registry_internal_base()}/api/v1/internal/runs/{child_id}/step-update"
    body = {
        "decision": decision, "reviewer_id": reviewer_id, "reason": reason,
        "run_id": str(child_id), "callback_url": callback_url,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{base}/resume/{child_id}", json=body)
        if resp.status_code not in (200, 201, 202):
            return "failed", None, f"member /resume returned {resp.status_code}: {resp.text[:300]}"
    except httpx.ConnectError:
        return "failed", None, f"member pod unreachable at {base}/resume (env={environment})"
    except Exception as exc:
        return "failed", None, f"member /resume failed: {exc}"

    # The child STARTS at 'awaiting_approval' (it was parked) — unlike a forward
    # dispatch which starts 'running'. So wait for it to LEAVE that state to a true
    # terminal ('completed'/'failed'). If it lingers at awaiting_approval past the
    # deadline the member re-parked at a fresh gate (a second high-risk call) — report
    # that so the caller does NOT advance.
    deadline = time.monotonic() + 120.0
    last = ("awaiting_approval", None, None)
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                select(AgentRun.status, AgentRun.output, AgentRun.error_message)
                .where(AgentRun.id == child_id)
            )).first()
        if row is not None:
            last = row
            if row[0] in ("completed", "failed"):
                return row[0], row[1], row[2]
        await asyncio.sleep(1.0)
    return last[0], last[1], last[2]


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
                # Cost is NOT read from the parent's own trace here: a workflow parent
                # orchestrates members but makes no LLM calls, so its trace has no
                # GENERATION cost. Its cost is the sum of its members' costs, rolled up
                # by the cost-backfill sweep (_rollup_workflow_parents) once the members
                # are themselves costed from Langfuse (both are async).
            await s.commit()


async def _fail_parent(parent_run_id: str, error_message: str) -> None:
    """Mark the parent run failed with a diagnostic error_message. Clears any checkpoint."""
    async with AsyncSessionLocal() as s:
        parent = (await s.execute(select(AgentRun).where(AgentRun.id == parent_run_id))).scalar_one_or_none()
        if parent:
            parent.status = "failed"
            parent.error_message = error_message[:4000]
            parent.orchestrator_state = None
            parent.completed_at = datetime.now(timezone.utc)
            await s.commit()


async def _save_checkpoint(parent_run_id: str, state: dict) -> None:
    """Persist the orchestrator's resumable position on the parent run."""
    async with AsyncSessionLocal() as s:
        parent = (await s.execute(select(AgentRun).where(AgentRun.id == parent_run_id))).scalar_one_or_none()
        if parent:
            parent.orchestrator_state = state
            await s.commit()


async def _clear_checkpoint(parent_run_id: str) -> None:
    async with AsyncSessionLocal() as s:
        parent = (await s.execute(select(AgentRun).where(AgentRun.id == parent_run_id))).scalar_one_or_none()
        if parent:
            parent.orchestrator_state = None
            await s.commit()


async def _halt_for_approval(parent_run_id: str, mode: str, team: str, workflow_id: str,
                             cursor: dict | None = None) -> None:
    """Checkpoint + park a non-sequential run at 'awaiting_approval'.

    `cursor` carries the mode-specific traversal position so `resume_orchestration` can
    re-enter and advance (D3): the current node (+visited_count) for conditional/handoff,
    or the supervisor accumulator (phase/iteration/current_input/worker_outputs) for
    supervisor. The base `{mode,team,workflow_id}` is always written so the resume path
    knows how to dispatch.
    """
    state = {"mode": mode, "team": team, "workflow_id": workflow_id}
    if cursor:
        state.update(cursor)
    await _save_checkpoint(parent_run_id, state)
    await _mark_parent(parent_run_id, "awaiting_approval")
    logger.info("workflow %s (%s): paused — awaiting approval (cursor=%s)",
                parent_run_id, mode, {k: cursor[k] for k in (cursor or {})})


async def _park_or_fail(parent_run_id: str, mode: str, team: str, workflow_id: str, shape: str,
                        cursor: dict | None = None) -> None:
    """Single decision point for an approval gate (S2). Durable → checkpoint the cursor +
    park (D3 resumable). Reactive → fail-closed with a clear message: a reactive workflow
    cannot durably park for async approval (D2). Never swallows the gate and proceeds —
    a reactive run can never silently run a tool that should have been approved."""
    if shape == "reactive":
        await _fail_parent(
            parent_run_id,
            "approval gate hit in a reactive workflow — set shape=durable to allow approvals",
        )
        logger.info("workflow %s (%s, reactive): approval gate → fail-closed", parent_run_id, mode)
        return
    await _halt_for_approval(parent_run_id, mode, team, workflow_id, cursor)


async def _run_step_stream(
    parent_run_id: str, team: str, agent_name: str, current_input: str, conversation_id: str,
) -> AsyncGenerator[dict, None]:
    """The ONE member-step leaf, as a generator. Creates the child run, dispatches to the
    member (durable via /run poll, reactive via the streaming /chat/stream reader), records
    the outcome, detects a HITL pause, and authors the parent-trace span — exactly as the
    former non-streaming ``_run_step`` did — while yielding client-facing frames:

        agent_start{author} → [reactive member content frames] → agent_end{author}
        → __member_end__{status,output,error}   (internal sentinel, consumed by the walker)

    Durable members emit only ``agent_start`` → (poll) → ``agent_end`` (no token/tool/
    rationale frames — accepted asymmetry). The ``__member_end__`` sentinel carries the
    routing outcome; the mode walker consumes it and never forwards it to the client.

    ``_run_step`` (below) is a thin non-streaming DRAIN of this generator, so there is ONE
    leaf implementation (No-Bandaid: no forked member-dispatch path).
    """
    start = time.perf_counter()
    member_shape = await _resolve_agent_shape(agent_name)
    thread_id = uuid.uuid4().hex
    async with AsyncSessionLocal() as s:
        parent = (await s.execute(
            select(AgentRun.run_by, AgentRun.context, AgentRun.user_id).where(AgentRun.id == parent_run_id)
        )).first()
        # D1 actor_chain (WS-2 T016): the child member run acts under the WORKFLOW's
        # authority, not the member's own. That authority is carried by inheriting the
        # parent workflow run's `run_by` — for a daemon workflow the parent run_by is the
        # workflow's SERVICE identity (stamped in internal._start_workflow_run via
        # resolve_workflow_principal), so every member child carries it too. The member's
        # own `agent_class` is therefore ignored at the run-tree / audit layer. NOTE
        # (deferred — identity-propagation initiative, same gap as T009): propagating the
        # workflow's class + service identity onto each member POD's OPA input is NOT wired
        # here — the member pod builds its own OPA input from its deploy-time env
        # (AGENTSHIELD_AGENT_CLASS), so a member tool call still hits OPA with the member's
        # class. T016 threads the authority through run_by (provable); the signed
        # actor_chain token + pod OPA-input propagation are the separate initiative.
        parent_run_by = parent[0] if parent else None
        # Inherit the parent workflow run's context so a playground/test run yields
        # playground children (→ self-service inline approval), while a production
        # (triggered) run yields production children (→ reviewer console). Do NOT
        # hardcode — the two paths must not be conflated.
        parent_context = (parent[1] if parent else None) or "production"
        # POC-3: apply the AUTHORIZING user's response preferences to this member.
        # The parent workflow run carries the live user's sub in user_id (stamped at
        # both creation sites); a daemon workflow's parent user_id is "" →
        # compose_directive_for_user returns None (no directive). Compose here, inside
        # the open session, then thread the bounded string into the member dispatch.
        from preferences import compose_directive_for_user
        parent_user_id = (parent[2] if parent else None) or ""
        user_directive = await compose_directive_for_user(s, parent_user_id)
        child = AgentRun(
            agent_name=agent_name,
            input=current_input[:4000] if current_input else None,
            context=parent_context,
            status="running",
            trigger_type="workflow",
            parent_run_id=parent_run_id,
            team=team,
            thread_id=thread_id,
            run_by=parent_run_by,
        )
        s.add(child)
        await s.commit()
        await s.refresh(child)
        child_id = str(child.id)

    # Context-storage design §5.2 (identity split): every member of THIS workflow run
    # shares ONE `conversation_id` (passed in — parent.session_id or the run id) so each
    # loads the same cross-member transcript, while its per-member `thread_id`
    # (checkpoint + Approval correlation, WS-1) stays exactly as assigned above. The two
    # keys travel in DIFFERENT body fields and never alias — this is what keeps durable
    # resume keyed off thread_id=child_id from regressing. String-passing (`current_input`
    # as `message`) is retained as a fallback; cross-member context now flows via the
    # shared transcript.
    conversation_scope = "workflow_run"
    workflow_run_id = parent_run_id

    # Open this member's attributed bubble before any content (single lifecycle owner —
    # _dispatch_stream suppresses the reader's own agent_start).
    yield {"type": "agent_start", "author": agent_name}

    if member_shape == "durable":
        # D4 "+ Visibility": a durable member runs via `/run` so its per-node run_steps
        # land under child_id. thread_id must equal child_id so the member's Approval
        # (SDK sets thread_id=run_id) and the console resume correlate to this child.
        thread_id = child_id
        async with AsyncSessionLocal() as s:
            child = (await s.execute(select(AgentRun).where(AgentRun.id == child_id))).scalar_one_or_none()
            if child:
                child.thread_id = child_id
                # The member pod seeds its OTEL trace id deterministically from its
                # run_id (== child_id) — agentshield_sdk.otel.otel_run_context uses
                # uuid(run_id).int. So the member's real LLM/tool spans land on the
                # Langfuse trace `uuid(child_id).hex`. Stamp it here so the run tree's
                # per-member "View Trace" link resolves (was NULL → the child looked
                # trace-less even though its 26-span trace exists). The parent workflow
                # trace stays a thin envelope — the detail lives on the members.
                child.langfuse_trace_id = uuid.UUID(child_id).hex
                await s.commit()
        status_val, output, err = await _dispatch_durable_member(
            agent_name, team, current_input, child_id,
            conversation_id=conversation_id, scope=conversation_scope,
            workflow_run_id=workflow_run_id,
        )
    else:
        # Reactive member: stream /chat/stream through the shared reader, re-yielding the
        # member's content frames to the client and collecting the routing outcome from the
        # __member_end__ sentinel.
        status_val, output, err = "completed", None, None
        async for frame in _dispatch_stream(
            agent_name, team, current_input, thread_id,
            conversation_id, conversation_scope, child_id,
            user_directive=user_directive,
        ):
            if frame.get("type") == _MEMBER_END:
                status_val, output, err = frame["status"], frame["output"], frame["error"]
            else:
                yield frame
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Authoritative pause detection (reactive /chat members only — a durable member's
    # pause already surfaces as status='awaiting_approval' from the poll above): a member
    # that hit interrupt() will have POSTed a pending Approval under this thread_id before
    # suspending.
    if status_val == "completed":
        async with AsyncSessionLocal() as s:
            pending = (await s.execute(
                select(Approval).where(Approval.thread_id == thread_id, Approval.status == "pending")
            )).scalar_one_or_none()
        if pending is not None:
            status_val = "awaiting_approval"
            logger.info("workflow %s: member '%s' paused for approval (thread_id=%s, approval=%s)",
                        parent_run_id, agent_name, thread_id, pending.id)

    async with AsyncSessionLocal() as s:
        child = (await s.execute(select(AgentRun).where(AgentRun.id == child_id))).scalar_one_or_none()
        if child:
            child.status = status_val
            child.latency_ms = elapsed_ms
            if status_val == "awaiting_approval":
                # Not terminal — completed_at/output are filled when the approval
                # is decided and the member resumes (see approvals.decide_approval).
                pass
            else:
                child.output = (output[:4000] if output else None)
                child.error_message = err
                child.completed_at = datetime.now(timezone.utc)
            await s.commit()

    # Author a span on the PARENT workflow trace for this member step so the
    # workflow run's trace shows its decision/step structure (the member's own
    # detailed spans live on the member trace — see tracing.trace_workflow_step
    # + docs/debugging/011). A member that parked for approval gets its terminal
    # span authored on resume (resume_orchestration), not here.
    if status_val != "awaiting_approval":
        try:
            from tracing import trace_workflow_step
            trace_workflow_step(
                parent_run_id=parent_run_id,
                agent_name=agent_name,
                status=status_val,
                input_text=current_input,
                output_text=output,
                error_message=err,
                child_run_id=child_id,
                latency_ms=elapsed_ms,
            )
        except Exception:  # tracing must never break orchestration
            pass

    # Close the member's bubble, then hand the routing outcome to the walker. agent_end
    # is emitted even for awaiting_approval/failed so the client always sees a clean
    # per-member envelope; the sentinel carries the status the walker routes on.
    yield {"type": "agent_end", "author": agent_name}
    yield {"type": _MEMBER_END, "author": agent_name,
           "status": status_val, "output": output, "error": err}


async def _run_step(
    parent_run_id: str, team: str, agent_name: str, current_input: str,
    conversation_id: str | None = None,
) -> tuple[str, str | None, str | None]:
    """Non-streaming DRAIN of ``_run_step_stream`` — the single leaf both the streamed and
    the non-streamed paths share (No-Bandaid: one member-dispatch implementation). Consumes
    the generator, ignores the client-facing frames, and returns the routing outcome from
    the ``__member_end__`` sentinel: ``(status, output, error)``. ``conversation_id``
    defaults to ``parent_run_id`` for legacy callers (scheduler/webhook/resume) that don't
    thread a session key."""
    conversation_id = conversation_id or parent_run_id
    status_val, output, err = "failed", None, "member produced no terminal sentinel"
    async for frame in _run_step_stream(parent_run_id, team, agent_name, current_input, conversation_id):
        if frame.get("type") == _MEMBER_END:
            status_val, output, err = frame["status"], frame["output"], frame["error"]
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


def _conditional_next(graph: dict[str, list[tuple[str, str | None]]], node: str, current_input: str) -> str | None:
    """The next node from `node` given its output: first matching conditional edge, else
    the default (blank) edge. None → terminal / no route (the run completes). Shared by
    the forward loop and the resume re-entry so both route identically (D3)."""
    outs = graph.get(node, [])
    if not outs:
        return None
    nxt = None
    for (target, cond) in outs:
        if cond and cond.strip() and evaluate_condition(cond, current_input):
            nxt = target
            break
    if nxt is None:
        nxt = next((t for (t, c) in outs if not (c and c.strip())), None)
    return nxt


def _handoff_next(graph: dict[str, list[tuple[str, str | None]]], node: str, current_input: str) -> str | None:
    """The next node from `node` given its handoff signal: the signalled target, else the
    sole outgoing edge. None → DONE / no route (the run completes). Shared by the forward
    loop and the resume re-entry (D3)."""
    outs = graph.get(node, [])
    if not outs:
        return None
    targets = [t for (t, _c) in outs]
    signal = _parse_next_agent(current_input, targets)
    if signal == _DONE_SENTINEL:
        return None
    if signal in targets:
        return signal
    if len(outs) == 1:
        return outs[0][0]  # deterministic single handoff
    return None


# ---------------------------------------------------------------------------
# Orchestration modes
# ---------------------------------------------------------------------------
def _compute_sequential_order(member_names: list[str], graph: dict[str, list[tuple[str, str | None]]]) -> list[str]:
    """Resolve the linear member order: edge chain (default/first edge) or positional."""
    if not graph:
        # Backward-compatible: no edges → positional order (original behavior).
        return list(member_names)
    order: list[str] = []
    visited: set[str] = set()
    node = find_start_node(graph, member_names)
    while node and node not in visited and len(order) < _MAX_STEPS:
        order.append(node)
        visited.add(node)
        outs = graph.get(node, [])
        # Prefer the default (blank) edge; else the first edge.
        nxt = next((t for (t, c) in outs if not (c and c.strip())), None)
        node = nxt if nxt is not None else (outs[0][0] if outs else None)
    return order


async def _run_sequential_from(parent_run_id: str, team: str, workflow_id: str,
                               order: list[str], start_index: int, current_input: str,
                               conversation_id: str, shape: str = "durable") -> AsyncGenerator[dict, None]:
    """Run members order[start_index:] in sequence. Pausable + resumable. Async GENERATOR:
    re-yields each member's client-facing frames and routes on the __member_end__ sentinel;
    all DB writes / routing / fail-fast are byte-identical to the pre-stream walker.

    On a member pausing for approval, checkpoints {next_index=i+1} and halts the
    parent at 'awaiting_approval'; `resume_orchestration` re-enters here with the
    resumed member's output as `current_input` and start_index=next_index.
    """
    for i in range(start_index, len(order)):
        agent_name = order[i]
        status_val, output, _err = "failed", None, None
        async for frame in _run_step_stream(parent_run_id, team, agent_name, current_input, conversation_id):
            if frame.get("type") == _MEMBER_END:
                status_val, output, _err = frame["status"], frame["output"], frame["error"]
            else:
                yield frame
        if status_val == "awaiting_approval":
            if shape == "reactive":
                await _fail_parent(
                    parent_run_id,
                    "approval gate hit in a reactive workflow — set shape=durable to allow approvals",
                )
                logger.info("workflow %s (sequential, reactive): approval gate → fail-closed", parent_run_id)
                return
            await _save_checkpoint(parent_run_id, {
                "mode": "sequential",
                "order": order,
                "next_index": i + 1,
                "team": team,
                "workflow_id": workflow_id,
            })
            await _mark_parent(parent_run_id, "awaiting_approval")
            logger.info("workflow %s (sequential): paused at member %d ('%s') — awaiting approval",
                        parent_run_id, i, agent_name)
            return
        if status_val == "failed":
            await _mark_parent(parent_run_id, "failed", None)
            logger.warning("workflow %s: member '%s' failed — fail-fast stop", parent_run_id, agent_name)
            return
        current_input = output or ""

    await _mark_parent(parent_run_id, "completed", current_input)
    logger.info("workflow run %s (sequential) finished: completed", parent_run_id)


async def orchestrate_graph_sequential(parent_run_id: str, team: str, workflow_id: str, input_message: str,
                                       conversation_id: str, shape: str = "durable") -> AsyncGenerator[dict, None]:
    """Walk the edge chain (default/first outgoing edge) or member order if no edges. Fail-fast.
    Async GENERATOR — re-yields the sequential walker's member frames."""
    async with AsyncSessionLocal() as s:
        member_names = await resolve_member_names(s, workflow_id)
        graph = await resolve_edge_graph(s, workflow_id)
    await _mark_parent(parent_run_id, "running")
    order = _compute_sequential_order(member_names, graph)
    async for frame in _run_sequential_from(
        parent_run_id, team, workflow_id, order, 0, input_message or "", conversation_id, shape
    ):
        yield frame


async def resume_orchestration(parent_run_id: str, member_output: str, member_status: str) -> None:
    """Re-enter a paused workflow run after its blocked member's approval is decided.

    Called (fire-and-forget) from approvals.decide_approval once the member pod has
    resumed and produced its final output. All four modes now auto-advance (D3): the
    checkpoint carries the mode-specific cursor and we dispatch per mode.
    """
    async with AsyncSessionLocal() as s:
        parent = (await s.execute(select(AgentRun).where(AgentRun.id == parent_run_id))).scalar_one_or_none()
        state = parent.orchestrator_state if parent else None
        # Shared transcript key (§5.2): the same conversation_id the forward walk used —
        # parent.session_id when a session was supplied, else the run id.
        conversation_id = (parent.session_id if parent else None) or parent_run_id
    if not state:
        logger.warning("resume_orchestration: no checkpoint for parent %s — nothing to advance", parent_run_id)
        return

    # Resolve which member had parked (for the parent-trace span + a meaningful
    # failure message). conditional/handoff carry the node in the cursor; sequential
    # carries order + the next index (the parked member sits one before it).
    _mode = state.get("mode")
    parked_node: str | None = state.get("node")
    if parked_node is None and _mode == "sequential":
        try:
            parked_node = state["order"][int(state["next_index"]) - 1]
        except (KeyError, IndexError, ValueError, TypeError):
            parked_node = None
    # Look up the resumed child for its id (drill-down) + captured error text.
    parked_child_id: str | None = None
    parked_child_err: str | None = None
    if parked_node:
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                select(AgentRun.id, AgentRun.error_message)
                .where(AgentRun.parent_run_id == parent_run_id,
                       AgentRun.agent_name == parked_node)
                .order_by(AgentRun.started_at.desc()).limit(1)
            )).first()
        if row is not None:
            parked_child_id = str(row[0])
            parked_child_err = row[1]

    # Author the resumed member's terminal span on the PARENT workflow trace (the
    # forward path in _run_step skips a parked member — its terminal status is only
    # known here). Best-effort; never break the resume.
    if parked_node:
        try:
            from tracing import trace_workflow_step
            trace_workflow_step(
                parent_run_id=parent_run_id,
                agent_name=parked_node,
                status=member_status,
                output_text=member_output,
                error_message=parked_child_err,
                child_run_id=parked_child_id,
            )
        except Exception:
            pass

    if member_status == "failed":
        # Surface the member's real failure reason (e.g. a tool 503) instead of a
        # bare generic message — the empty-error gap from docs/debugging/011.
        detail = "workflow member failed after its approval was decided"
        if parked_node:
            detail = f"member '{parked_node}' failed after approval"
            if parked_child_err:
                detail += f": {parked_child_err[:300]}"
        await _fail_parent(parent_run_id, detail)
        return

    mode = state.get("mode")
    team = state["team"]
    workflow_id = state["workflow_id"]
    out = member_output or ""

    await _clear_checkpoint(parent_run_id)
    await _mark_parent(parent_run_id, "running")

    # The mode walkers are now async generators; resume is console-driven (no client
    # stream is listening) so we DRAIN them locally, discarding the frames while every
    # DB write inside still lands (No-Bandaid: same walkers as the streamed path).
    if mode == "sequential":
        async for _ in _run_sequential_from(
            parent_run_id, team, workflow_id,
            state["order"], int(state["next_index"]), out, conversation_id,
        ):
            pass
    elif mode in ("conditional", "handoff"):
        # Markovian re-entry: the parked node completed with `out`; compute its next hop
        # and resume the walk from there (re-resolve the graph from workflow_id — the
        # checkpoint carries only the cursor, not the graph).
        async with AsyncSessionLocal() as s:
            graph = await resolve_edge_graph(s, workflow_id)
        node = state.get("node")
        visited_count = int(state.get("visited_count", 0))
        nxt = (_conditional_next if mode == "conditional" else _handoff_next)(graph, node, out)
        if nxt is None:
            await _mark_parent(parent_run_id, "completed", out)
            logger.info("resume_orchestration (%s): terminal after resume — parent %s completed",
                        mode, parent_run_id)
            return
        runner = _run_conditional_from if mode == "conditional" else _run_handoff_from
        async for _ in runner(parent_run_id, team, workflow_id, graph, nxt, visited_count, out, conversation_id):
            pass
    elif mode == "supervisor":
        async for _ in _run_supervisor_from(
            parent_run_id, team, workflow_id,
            state["supervisor"], list(state.get("workers") or []), int(state["max_iters"]),
            conversation_id=conversation_id,
            iteration=int(state.get("iteration", 0)),
            current_input=state.get("current_input", ""),
            worker_outputs=list(state.get("worker_outputs") or []),
            resumed_phase=state.get("phase"), resumed_output=out,
        ):
            pass
    else:
        # Unknown mode — safe fallback: complete with the member's output rather than hang.
        await _mark_parent(parent_run_id, "completed", out)
        logger.warning("resume_orchestration: unknown mode '%s' — parent %s completed with member output",
                       mode, parent_run_id)


async def _run_conditional_from(parent_run_id: str, team: str, workflow_id: str,
                                graph: dict[str, list[tuple[str, str | None]]],
                                node: str | None, visited_count: int, current_input: str,
                                conversation_id: str, shape: str = "durable") -> AsyncGenerator[dict, None]:
    """Run the conditional graph from `node`. Pausable + resumable (D3). Async GENERATOR:
    re-yields member frames; routing is byte-identical to the pre-stream walker.

    On a member pausing for approval, checkpoints the current node + visited_count and
    halts; `resume_orchestration` computes the next node from the resumed member's output
    (Markovian: next = f(node, output)) and re-enters here from that next node.
    """
    failed = False
    while node and visited_count < _MAX_STEPS:
        visited_count += 1
        status_val, output, _err = "failed", None, None
        async for frame in _run_step_stream(parent_run_id, team, node, current_input, conversation_id):
            if frame.get("type") == _MEMBER_END:
                status_val, output, _err = frame["status"], frame["output"], frame["error"]
            else:
                yield frame
        if status_val == "awaiting_approval":
            await _park_or_fail(parent_run_id, "conditional", team, workflow_id, shape,
                                cursor={"node": node, "visited_count": visited_count})
            return
        if status_val == "failed":
            failed = True
            logger.warning("workflow %s: node '%s' failed — stop", parent_run_id, node)
            break
        current_input = output or ""

        nxt = _conditional_next(graph, node, current_input)
        if nxt is None:
            logger.info("workflow %s: no matching/default edge from '%s' — complete", parent_run_id, node)
            break
        node = nxt

    await _mark_parent(parent_run_id, "failed" if failed else "completed",
                       None if failed else current_input)
    logger.info("workflow run %s (conditional) finished: %s", parent_run_id, "failed" if failed else "completed")


async def orchestrate_conditional(parent_run_id: str, team: str, workflow_id: str, input_message: str,
                                  conversation_id: str, shape: str = "durable") -> AsyncGenerator[dict, None]:
    """At each node, take the first outgoing edge whose condition matches the output.
    Async GENERATOR — re-yields the conditional walker's member frames."""
    async with AsyncSessionLocal() as s:
        member_names = await resolve_member_names(s, workflow_id)
        graph = await resolve_edge_graph(s, workflow_id)
    await _mark_parent(parent_run_id, "running")
    node = find_start_node(graph, member_names)
    async for frame in _run_conditional_from(
        parent_run_id, team, workflow_id, graph, node, 0, input_message or "", conversation_id, shape
    ):
        yield frame


async def _run_handoff_from(parent_run_id: str, team: str, workflow_id: str,
                            graph: dict[str, list[tuple[str, str | None]]],
                            node: str | None, visited_count: int, current_input: str,
                            conversation_id: str, shape: str = "durable") -> AsyncGenerator[dict, None]:
    """Run the handoff graph from `node`. Pausable + resumable (D3). Async GENERATOR:
    re-yields member frames; routing is byte-identical to the pre-stream walker.

    On a member pausing for approval, checkpoints the current node + visited_count and
    halts; `resume_orchestration` computes the next hop from the resumed member's output
    (Markovian) and re-enters here from that next node.
    """
    failed = False
    while node and visited_count < _MAX_STEPS:
        visited_count += 1
        status_val, output, _err = "failed", None, None
        async for frame in _run_step_stream(parent_run_id, team, node, current_input, conversation_id):
            if frame.get("type") == _MEMBER_END:
                status_val, output, _err = frame["status"], frame["output"], frame["error"]
            else:
                yield frame
        if status_val == "awaiting_approval":
            await _park_or_fail(parent_run_id, "handoff", team, workflow_id, shape,
                                cursor={"node": node, "visited_count": visited_count})
            return
        if status_val == "failed":
            failed = True
            break
        current_input = output or ""

        outs = graph.get(node, [])
        if not outs:
            break
        nxt = _handoff_next(graph, node, current_input)
        if nxt is None:
            if _parse_next_agent(current_input, [t for (t, _c) in outs]) != _DONE_SENTINEL:
                logger.info("workflow %s: no handoff signal from '%s' and %d edges — stop",
                            parent_run_id, node, len(outs))
            break
        node = nxt

    await _mark_parent(parent_run_id, "failed" if failed else "completed",
                       None if failed else current_input)
    logger.info("workflow run %s (handoff) finished: %s", parent_run_id, "failed" if failed else "completed")


async def orchestrate_handoff(parent_run_id: str, team: str, workflow_id: str, input_message: str,
                              conversation_id: str, shape: str = "durable") -> AsyncGenerator[dict, None]:
    """Follow the handoff signal in each agent's output; else its sole outgoing edge.
    Async GENERATOR — re-yields the handoff walker's member frames."""
    async with AsyncSessionLocal() as s:
        member_names = await resolve_member_names(s, workflow_id)
        graph = await resolve_edge_graph(s, workflow_id)
    await _mark_parent(parent_run_id, "running")
    node = find_start_node(graph, member_names)
    async for frame in _run_handoff_from(
        parent_run_id, team, workflow_id, graph, node, 0, input_message or "", conversation_id, shape
    ):
        yield frame


async def _run_supervisor_from(parent_run_id: str, team: str, workflow_id: str,
                               supervisor: str, workers: list[str], max_iters: int,
                               *, conversation_id: str, iteration: int, current_input: str,
                               worker_outputs: list[str],
                               resumed_phase: str | None = None, resumed_output: str | None = None,
                               shape: str = "durable") -> AsyncGenerator[dict, None]:
    """Re-entrant supervisor loop. Pausable + resumable (D3). Async GENERATOR: each member
    dispatch drains ``_run_step_stream`` and re-yields the member's client-facing frames;
    the accumulator + routing + pause/fail semantics are byte-identical to the pre-stream
    walker.

    The accumulator (`worker_outputs` + `iteration` + `current_input`) is what survives a
    pause: it is checkpointed on park and reconstructed on resume. A pause can land at two
    sub-steps within a turn — the supervisor decision (`phase='supervisor'`) or the worker
    dispatch (`phase='worker'`); the resume preamble finishes whichever parked, then the
    normal loop continues. `worker_outputs` records each completed worker's output so the
    accumulated progress is provably preserved across the pause (suite-56).
    """
    def _cursor(phase: str, it: int, ci: str) -> dict:
        return {
            "phase": phase, "iteration": it, "current_input": ci,
            "worker_outputs": list(worker_outputs), "supervisor": supervisor,
            "workers": list(workers), "max_iters": max_iters,
        }

    # --- resume preamble: finish the sub-step that had parked ---
    if resumed_phase == "supervisor":
        # The supervisor's decision was gated; resumed_output is its decision output.
        s_out = resumed_output or ""
        decision = _parse_next_agent(s_out, workers)
        if decision == _DONE_SENTINEL or decision is None:
            await _mark_parent(parent_run_id, "completed", s_out or current_input)
            logger.info("workflow run %s (supervisor) finished: completed (resumed at supervisor)", parent_run_id)
            return
        w_status, w_out, _we = "failed", None, None
        async for frame in _run_step_stream(parent_run_id, team, decision, s_out or "", conversation_id):
            if frame.get("type") == _MEMBER_END:
                w_status, w_out, _we = frame["status"], frame["output"], frame["error"]
            else:
                yield frame
        if w_status == "awaiting_approval":
            await _park_or_fail(parent_run_id, "supervisor", team, workflow_id, shape,
                                cursor=_cursor("worker", iteration, current_input))
            return
        if w_status == "failed":
            await _fail_parent(parent_run_id, "supervisor or worker step failed during dispatch")
            return
        worker_outputs.append(w_out or "")
        current_input = w_out or ""
        iteration += 1
    elif resumed_phase == "worker":
        # The worker dispatch was gated; resumed_output is the worker's output.
        worker_outputs.append(resumed_output or "")
        current_input = resumed_output or ""
        iteration += 1

    # --- normal loop from `iteration` ---
    failed = False
    hit_cap = True
    while iteration < max_iters:
        # 1. supervisor decides
        s_status, s_out, _e = "failed", None, None
        async for frame in _run_step_stream(parent_run_id, team, supervisor, current_input, conversation_id):
            if frame.get("type") == _MEMBER_END:
                s_status, s_out, _e = frame["status"], frame["output"], frame["error"]
            else:
                yield frame
        if s_status == "awaiting_approval":
            await _park_or_fail(parent_run_id, "supervisor", team, workflow_id, shape,
                                cursor=_cursor("supervisor", iteration, current_input))
            return
        if s_status == "failed":
            failed = True
            break
        decision = _parse_next_agent(s_out or "", workers)
        if decision == _DONE_SENTINEL or decision is None:
            hit_cap = False
            current_input = s_out or current_input
            break
        # 2. dispatch to the chosen worker; thread its output back to the supervisor
        w_status, w_out, _we = "failed", None, None
        async for frame in _run_step_stream(parent_run_id, team, decision, s_out or "", conversation_id):
            if frame.get("type") == _MEMBER_END:
                w_status, w_out, _we = frame["status"], frame["output"], frame["error"]
            else:
                yield frame
        if w_status == "awaiting_approval":
            await _park_or_fail(parent_run_id, "supervisor", team, workflow_id, shape,
                                cursor=_cursor("worker", iteration, current_input))
            return
        if w_status == "failed":
            failed = True
            break
        worker_outputs.append(w_out or "")
        current_input = w_out or ""
        iteration += 1
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


async def orchestrate_supervisor(parent_run_id: str, team: str, workflow_id: str, input_message: str,
                                 conversation_id: str, shape: str = "durable") -> AsyncGenerator[dict, None]:
    """A coordinator (role='supervisor') routes to workers each turn until DONE / max_iterations.
    Async GENERATOR — re-yields the supervisor walker's member frames."""
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
    async for frame in _run_supervisor_from(
        parent_run_id, team, workflow_id, supervisor["name"], workers, max_iters,
        conversation_id=conversation_id,
        iteration=0, current_input=input_message or "", worker_outputs=[], shape=shape,
    ):
        yield frame


# ---------------------------------------------------------------------------
# Dispatcher + backward-compat entry point
# ---------------------------------------------------------------------------
async def orchestrate_stream(
    parent_run_id: str, team: str, workflow_id: str, input_message: str, mode: str,
    conversation_id: str, shape: str = "durable",
) -> AsyncGenerator[dict, None]:
    """The ONE graph walk, as an async generator (No-Bandaid: both the streamed endpoint
    and the non-streamed drain consume THIS). Routes `mode` → the matching generator
    mode-walker, re-yielding every member frame, and ends with a run-level
    ``{"type":"done","run_id":parent_run_id}``. ALL DB writes (_mark_parent / _save_checkpoint /
    _park_or_fail / _fail_parent / child rows / tool-call markers) happen INSIDE the walkers,
    so draining this reproduces the pre-stream terminal state exactly.

    Does NOT swallow exceptions — the drain wrapper (`orchestrate`) and the SSE endpoint each
    own their own failure handling."""
    if mode == "conditional":
        walker = orchestrate_conditional(parent_run_id, team, workflow_id, input_message, conversation_id, shape)
    elif mode == "supervisor":
        walker = orchestrate_supervisor(parent_run_id, team, workflow_id, input_message, conversation_id, shape)
    elif mode == "handoff":
        walker = orchestrate_handoff(parent_run_id, team, workflow_id, input_message, conversation_id, shape)
    else:  # sequential (default)
        walker = orchestrate_graph_sequential(parent_run_id, team, workflow_id, input_message, conversation_id, shape)
    async for frame in walker:
        yield frame
    yield {"type": "done", "run_id": parent_run_id}


async def orchestrate(parent_run_id: str, team: str, workflow_id: str, input_message: str, mode: str,
                      shape: str = "durable", conversation_id: str | None = None) -> None:
    """Non-streaming DRAIN of ``orchestrate_stream`` (No-Bandaid: one graph walk). Fail-safe
    (never raises). `conversation_id` defaults to `parent_run_id`.

    `shape` ('durable' default | 'reactive') controls approval-gate handling: durable
    parks + resumes; reactive fails-closed (D2/S2). Existing callers keep the durable
    default, so their behavior is byte-for-byte unchanged."""
    conversation_id = conversation_id or parent_run_id
    try:
        async for _ in orchestrate_stream(
            parent_run_id, team, workflow_id, input_message, mode, conversation_id, shape
        ):
            pass
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
        if status_val == "awaiting_approval":
            # No workflow_id here (legacy signature) → cannot checkpoint/resume; halt safely.
            await _mark_parent(parent_run_id, "awaiting_approval")
            logger.info("workflow %s (legacy sequential): paused — awaiting approval", parent_run_id)
            return
        if status_val == "failed":
            failed = True
            logger.warning("workflow %s: member '%s' failed — fail-fast stop", parent_run_id, agent_name)
            break
        current_input = output or ""
    await _mark_parent(parent_run_id, "failed" if failed else "completed",
                       None if failed else current_input)
    logger.info("workflow run %s finished: status=%s", parent_run_id, "failed" if failed else "completed")


# ---------------------------------------------------------------------------
# Production orchestrator pod dispatch
# ---------------------------------------------------------------------------


async def dispatch_to_orchestrator_pod(
    workflow_name: str,
    team: str,
    parent_run_id: str,
    members: list[dict],
    input_payload: dict,
) -> bool:
    """POST to the production orchestrator pod's /workflow-run. Returns True if accepted."""
    ns = f"production-{workflow_name}"
    url = f"http://{workflow_name}-production.{ns}.svc.cluster.local:8080/workflow-run"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json={
                "parent_run_id": parent_run_id,
                "members": members,
                "input_payload": input_payload,
            })
            if resp.status_code == 200:
                logger.info("dispatched workflow run %s to orchestrator pod at %s", parent_run_id, url)
                return True
            logger.warning("orchestrator pod returned %d for run %s", resp.status_code, parent_run_id)
            return False
    except Exception as exc:
        logger.debug("orchestrator pod dispatch failed for %s: %s (falling back to in-process)", parent_run_id, exc)
        return False

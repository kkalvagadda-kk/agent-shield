"""Shared durable-run harness — the ONE durable engine (WS-1 parity core).

Consumed by BOTH the declarative-runner's `/run` and the SDK server's `/run`. It
wraps a compiled LangGraph (with a PostgresSaver checkpointer) into a
fire-and-forget durable run that:

  * emits one run_steps callback per node/tool boundary (real steps, replacing the
    declarative-runner's 2-step `input_processing`/`agent_execution` skeleton),
  * parks FAIL-CLOSED on a HITL interrupt, and
  * re-enters from the PostgresSaver checkpoint on resume (approval decided) or on
    crash recovery.

Parity rule (the 2026-07-11 retro root cause was parallel code): there is exactly
ONE `run_durable`/`resume_durable` drive loop. This module MUST NOT import
registry-api — it POSTs step updates to a `callback_url` passed in. The graph is
duck-typed (`astream_events` + `get_state`) so this stays standalone + unit-testable.

Approval creation is NOT this harness's job: `hitl.require_approval` already creates
the Approval record (fail-closed) and calls `interrupt()` with the `approval_id` in
the interrupt value. On interrupt the harness reads that id and emits an
`awaiting_approval` step; the step-update callback parks + links the run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class StepUpdate:
    step_number: int
    step_name: str          # the real LangGraph node/tool name (not "agent_execution")
    status: str             # running | completed | failed | awaiting_approval
    output: dict | None = None
    output_text: str | None = None
    run_completed: bool = False
    error_message: str | None = None
    approval_id: str | None = None

    def to_body(self) -> dict:
        return {
            "step_number": self.step_number,
            "step_name": self.step_name,
            "status": self.status,
            "output": self.output,
            "output_text": self.output_text,
            "run_completed": self.run_completed,
            "error_message": self.error_message,
            "approval_id": self.approval_id,
        }


@dataclass
class RunResult:
    status: str             # completed | failed | awaiting_approval
    thread_id: str
    steps_emitted: int


@dataclass
class Bookmark:
    """Reduced from the old full graph-state checkpoint (B3): the ONLY field that
    survives is the step index, used for callback idempotency on a mid-run pod
    restart. Graph state lives in the PostgresSaver checkpoint, keyed by thread_id."""
    run_id: str
    last_completed_step: int = 0


class StepEmitter:
    """POSTs a StepUpdate to the run's step-update callback. Idempotent: a completed
    step already durably recorded (per the bookmark) is skipped so a mid-run restart
    doesn't double-write. Returns the callback's JSON (may echo an approval_id)."""

    def __init__(self, callback_url: str, http: httpx.AsyncClient, *, bookmark: Bookmark | None = None):
        self._url = callback_url
        self._http = http
        self._bookmark = bookmark

    async def emit(self, upd: StepUpdate) -> dict:
        if (
            self._bookmark is not None
            and upd.status == "completed"
            and not upd.run_completed
            and upd.step_number <= self._bookmark.last_completed_step
        ):
            logger.info("StepEmitter: skip already-recorded step %d (bookmark)", upd.step_number)
            return {}
        resp = await self._http.post(self._url, json=upd.to_body())
        resp.raise_for_status()
        if upd.status == "completed" and self._bookmark is not None:
            self._bookmark.last_completed_step = max(self._bookmark.last_completed_step, upd.step_number)
        try:
            return resp.json()
        except Exception:  # non-JSON body is fine (e.g. 204)
            return {}


def _exc_reason(exc: BaseException) -> str:
    """A never-empty failure reason for a caught exception.

    Some exceptions stringify to nothing — notably ``httpx.ReadTimeout`` /
    ``ConnectTimeout`` (raised with no message). ``f"...: {exc}"`` then yields a
    bare "run crashed:" with no cause, which is what surfaced in the workflow run
    panel (docs/debugging/011). Always prefix the exception TYPE so the reason is
    actionable even when the message is empty."""
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _pending_interrupt(graph: Any, config: dict) -> dict | None:
    """The pending interrupt value (a dict with `approval_id`) if the graph is parked
    at an interrupt(), else None. LangGraph v2 does not emit on_interrupt in
    astream_events — the interrupt lives in get_state().tasks[].interrupts[].value."""
    try:
        snapshot = graph.get_state(config)
    except Exception as exc:  # a broken checkpointer must not look like "completed"
        logger.warning("durable: get_state failed (thread parked-detection): %s", exc)
        return None
    for task in getattr(snapshot, "tasks", None) or []:
        for intr in getattr(task, "interrupts", None) or []:
            val = getattr(intr, "value", None)
            if isinstance(val, dict):
                return val
    return None


def _content_to_text(content: Any) -> str | None:
    """Normalize a LangChain message `content` to plain text.

    Providers differ: OpenAI returns a str, but Anthropic/Bedrock return a LIST of
    content blocks — e.g. ``[{"type": "text", "text": "refund", "index": 0}]``.
    The durable callback's ``output_text`` must be a string (it lands in a text
    column), so join the text of any text blocks. Returns None when there's no text.
    """
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts) if parts else None
    return str(content)


def _final_text(output: Any) -> str | None:
    """Last AI message content (as plain text) from a graph/node end output."""
    if isinstance(output, dict):
        msgs = output.get("messages") or []
        if msgs:
            last = msgs[-1]
            content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
            return _content_to_text(content)
    return None


async def _drive(
    graph: Any,
    input_state: Any,
    config: dict,
    *,
    thread_id: str,
    emitter: StepEmitter,
    start_step: int,
) -> RunResult:
    """Drive astream_events(v2), emitting a run_steps row per tool boundary, until
    completion or an interrupt. Shared by run_durable + resume_durable — the ONE loop."""
    step = start_step
    final_text: str | None = None
    # event run_id -> (step_number, tool_args) for in-flight tools. Args are only on
    # the on_tool_start event (`data.input`); we carry them to on_tool_end so the
    # completed step's `output` records the exact call the run made (Eval v2 E-1).
    open_tools: dict[str, tuple[int, Any]] = {}

    try:
        async for event in graph.astream_events(input_state, config, version="v2"):
            etype = event.get("event")
            name = event.get("name", "") or ""
            if etype == "on_tool_start":
                step += 1
                args = (event.get("data") or {}).get("input")
                open_tools[event.get("run_id", name)] = (step, args)
                # Eval v2 E-1: carry {tool, args} on the tool-boundary output so the
                # eval-runner can project run_steps → actual_trajectory (data-model §3).
                await emitter.emit(StepUpdate(
                    step, f"tool:{name}", "running", output={"tool": name, "args": args},
                ))
            elif etype == "on_tool_end":
                entry = open_tools.pop(event.get("run_id", name), None)
                if entry is None:
                    step += 1
                    s, args = step, None
                else:
                    s, args = entry
                out = (event.get("data") or {}).get("output")
                tool_output: dict[str, Any] = {"tool": name, "args": args}
                if out is not None:
                    tool_output["result"] = str(out)[:2000]
                await emitter.emit(StepUpdate(s, f"tool:{name}", "completed", output=tool_output))
            elif etype == "on_chain_end":
                ft = _final_text((event.get("data") or {}).get("output"))
                if ft:
                    final_text = ft  # latest chain-end wins → the graph's final message
    except Exception as exc:  # any drive crash → fail the run loudly, never hang
        logger.exception("durable: drive loop crashed thread=%s: %s", thread_id, exc)
        await emitter.emit(StepUpdate(
            step + 1, "agent", "failed", error_message=f"run crashed: {_exc_reason(exc)}", run_completed=True,
        ))
        return RunResult("failed", thread_id, step)

    intr = _pending_interrupt(graph, config)
    if intr is not None:
        step += 1
        approval_id = intr.get("approval_id")
        # Eval v2 E-1: the parked-tool boundary also carries {tool, args} (the
        # interrupt payload from hitl.require_approval) so expect_approval scoring
        # can assert the presented args against args_match (data-model §3).
        await emitter.emit(StepUpdate(
            step, f"tool:{intr.get('tool', '')}", "awaiting_approval", approval_id=approval_id,
            output={"tool": intr.get("tool", ""), "args": intr.get("args")},
        ))
        # Fail-closed: an interrupt with no approval_id is un-actionable (nobody can
        # decide it) — deny rather than park forever (bug-009 guard).
        if not approval_id:
            await emitter.emit(StepUpdate(
                step + 1, "agent", "failed",
                error_message="HITL interrupt carried no approval_id — fail-closed",
                run_completed=True,
            ))
            return RunResult("failed", thread_id, step)
        logger.info("durable: parked at approval_id=%s thread=%s", approval_id, thread_id)
        return RunResult("awaiting_approval", thread_id, step)  # state durably parked in PostgresSaver

    step += 1
    await emitter.emit(StepUpdate(step, "agent", "completed", output_text=final_text, run_completed=True))
    return RunResult("completed", thread_id, step)


async def run_durable(
    graph: Any,
    input: dict,
    *,
    thread_id: str,
    callback_url: str,   # bound into `emitter`; kept for contract fidelity + logging
    emitter: StepEmitter,
) -> RunResult:
    """Start a durable run. Drives the graph, emits real per-node steps, parks
    fail-closed on a HITL interrupt. Returns the terminal RunResult; the graph state
    is durably checkpointed in PostgresSaver so the process may exit after a park."""
    logger.info("run_durable: start thread=%s callback=%s", thread_id, callback_url)
    config = {"configurable": {"thread_id": thread_id}}
    return await _drive(graph, input, config, thread_id=thread_id, emitter=emitter, start_step=0)


async def resume_durable(
    graph: Any,
    *,
    thread_id: str,
    decision: dict | None,
    callback_url: str,
    emitter: StepEmitter,
    start_step: int = 0,
) -> RunResult:
    """Re-enter from the PostgresSaver checkpoint keyed by thread_id.
      - decision != None → an approval was decided; the interrupted node receives it.
      - decision == None → crash recovery (_resume_interrupted_runs): continue from the
        checkpoint with no new input.
    Same drive loop + fail-closed contract as run_durable."""
    logger.info("resume_durable: thread=%s decided=%s", thread_id, decision is not None)
    config = {"configurable": {"thread_id": thread_id}}
    # Resuming a parked interrupt() REQUIRES a langgraph Command(resume=value): that is
    # what makes the parked `interrupt()` call RETURN `value` and the node continue past
    # the gate. Passing a plain state dict ({"messages":[], "resume":...}) instead re-runs
    # the interrupted node from scratch → it calls interrupt() again → a NEW approval →
    # the run re-parks forever. Command is imported lazily to keep this module's import
    # graph standalone (unit tests mock the graph). crash-recovery (decision is None)
    # passes no input and just continues from the checkpoint.
    if decision is not None:
        from langgraph.types import Command  # lazy: langgraph is a runtime dep of the agent image
        resume_input = Command(resume=decision)
    else:
        resume_input = None
    return await _drive(graph, resume_input, config, thread_id=thread_id, emitter=emitter, start_step=start_step)

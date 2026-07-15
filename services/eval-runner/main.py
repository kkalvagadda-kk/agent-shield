"""
Eval Runner — K8s Job image.

Reads DATASET_ID, AGENT_NAME, EVAL_RUN_ID, REGISTRY_API_URL from env.
For each dataset item:
  1. Calls the agent via playground run endpoint
  2. Collects SSE stream response
  3. Scores with the LLM judge (Haiku, read back from the run); keyword fallback
  4. Records result via Registry API
  5. Updates eval run status/scores on completion
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

REGISTRY_API_URL = os.environ["REGISTRY_API_URL"]
DATASET_ID = os.environ["DATASET_ID"]
AGENT_NAME = os.environ["AGENT_NAME"]
EVAL_RUN_ID = os.environ["EVAL_RUN_ID"]
AGENT_VERSION_ID = os.environ.get("AGENT_VERSION_ID")
WORKFLOW_ID = os.environ.get("WORKFLOW_ID")
# Eval v2 E-0: interpretation mode (resolved by the API from the executable ==
# dataset.mode). E-0 wires the reactive scorer; the mode is passed through the
# single /eval/score door so E-1+ can add mode branches without a new path.
MODE = os.environ.get("MODE", "reactive")

_JUDGE_POLL_TIMEOUT = float(os.environ.get("JUDGE_POLL_TIMEOUT", "45"))
_JUDGE_POLL_INTERVAL = float(os.environ.get("JUDGE_POLL_INTERVAL", "3"))
_JUDGE_PASS_THRESHOLD = float(os.environ.get("JUDGE_PASS_THRESHOLD", "0.7"))


_WORKFLOW_POLL_TIMEOUT = float(os.environ.get("WORKFLOW_POLL_TIMEOUT", "180"))
_WORKFLOW_POLL_INTERVAL = float(os.environ.get("WORKFLOW_POLL_INTERVAL", "5"))

# Eval v2 E-1 (durable): how long to poll a durable playground run to terminal,
# self-approving any HITL gate so the run proceeds (data-model §3). A run that
# never reaches terminal within this window is FAIL-CLOSED (recorded failed with
# a reason, never scored on an empty trajectory).
_DURABLE_POLL_TIMEOUT = float(os.environ.get("DURABLE_POLL_TIMEOUT", "240"))
_DURABLE_POLL_INTERVAL = float(os.environ.get("DURABLE_POLL_INTERVAL", "4"))


import re


def _strip_markdown(text: str) -> str:
    """Remove common markdown formatting for keyword comparison."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # bold
    text = re.sub(r'__(.+?)__', r'\1', text)        # bold alt
    text = re.sub(r'\*(.+?)\*', r'\1', text)         # italic
    text = re.sub(r'_(.+?)_', r'\1', text)            # italic alt
    text = re.sub(r'`(.+?)`', r'\1', text)            # inline code
    return text.strip()


async def _call_score_api(
    client: httpx.AsyncClient,
    mode: str,
    input_text: str,
    response_text: str,
    expected_output: str,
) -> tuple[float, dict[str, float], str] | None:
    """Score one item via the single scoring door POST /playground/eval/score.

    Returns (composite, dimension_scores, reason) or None when the door is
    unavailable (non-200 / error / a mode whose scorer isn't wired yet → 501),
    in which case the caller falls back to keyword matching. For `mode=reactive`
    the composite is byte-identical to the legacy `judge_for_eval` score.
    """
    try:
        resp = await client.post(
            "/api/v1/playground/eval/score",
            json={
                "mode": mode,
                "item": {
                    "input_message": input_text,
                    "expected_output": expected_output,
                },
                "input": input_text,
                "response": response_text,
            },
            headers={"X-User-Sub": "eval-runner"},
            timeout=40.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            dimension_scores = {
                k: float(v) for k, v in (data.get("dimension_scores") or {}).items()
            }
            reason = (data.get("detail") or {}).get("response_reason", "")
            return float(data["composite"]), dimension_scores, reason
        logger.warning("eval/score API returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("eval/score API call failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Eval v2 E-1 — durable trajectory eval (MODE=durable)
# ---------------------------------------------------------------------------
_EVAL_HEADERS = {"X-User-Sub": "eval-runner"}


# A run_step whose status is one of these is an IN-FLIGHT boundary — the tool call
# it belongs to has NOT reached a terminal disposition yet. The durable harness
# emits such a boundary on `on_tool_start` (status="running"); a call that then
# parks at a HITL gate gets a SEPARATE terminal `awaiting_approval` boundary at the
# next step number (the interrupt fires before `on_tool_end`, so the `running` row
# is never updated to `completed`). See sdk/agentshield_sdk/durable.py `_drive`.
_INFLIGHT_STATUSES = frozenset({"running", "pending"})


def _collapse_tool_calls(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the consecutive run_steps of ONE logical tool call into a single
    trajectory entry carrying its terminal / most-significant disposition.

    A single logical tool call can span MULTIPLE run_steps. When a call parks at a
    HITL gate the durable harness emits two rows for it: a `running` boundary
    (`on_tool_start`, no approval_id) and — because the interrupt fires before
    `on_tool_end` — a separate `awaiting_approval` boundary (next step number,
    carrying the approval_id). Projected one-entry-per-row, the park evidence
    (awaiting_approval + approval_id) lands on a DIFFERENT entry than the tool's
    first `running` boundary, so `judge.score_tool_calls` greedy-matches an
    `expect_approval` step to the un-parked `running` entry and scores `parked:false`
    for a gate that genuinely parked (the E-1 scoring bug).

    Merge rule (class-correct, NOT a fixture special-case): an entry is folded into
    the immediately-preceding entry iff BOTH carry the same non-null `tool` AND the
    preceding entry's status is in-flight (`running`/`pending`). The fold advances
    the entry to the later boundary's status and adopts its approval_id/args, while
    NEVER clearing an approval_id already seen (park evidence is sticky). This merges
    a call's `running`→`awaiting_approval` (or `running`→`completed`) rows into one
    logical entry. It does NOT merge two DISTINCT completed calls of the same tool (a
    `completed` boundary is terminal, not in-flight), nor a park followed by a
    genuinely new call (an `awaiting_approval` prefix is terminal). Order preserved.
    """
    collapsed: list[dict[str, Any]] = []
    for e in entries:
        prev = collapsed[-1] if collapsed else None
        if (
            prev is not None
            and e.get("tool") is not None
            and prev.get("tool") == e.get("tool")
            and prev.get("status") in _INFLIGHT_STATUSES
        ):
            prev["status"] = e.get("status")
            # Sticky approval_id: keep whichever boundary of the call carried it.
            prev["approval_id"] = prev.get("approval_id") or e.get("approval_id")
            if e.get("args") is not None:
                prev["args"] = e.get("args")
            continue
        collapsed.append(e)
    return collapsed


def _project_trajectory(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Project run_steps rows (GET /playground/runs/{id}/steps) into the
    `actual_trajectory` the durable scorer compares (data-model §3).

    Per row: {step_number, name, status, approval_id} always; `tool` and `args`
    only when the boundary was a tool call (the durable harness records
    `output={"tool": <name>, "args": <call args>}` on tool/parked boundaries).
    Node-only / final-agent boundaries carry no `tool` and are skipped by the
    scorer's tool-list extraction — leaving them out here keeps the projection
    faithful to the producer.

    The raw rows are then collapsed so each ENTRY represents one logical tool call
    (`_collapse_tool_calls`): a call's `running`→`awaiting_approval` rows become a
    single entry carrying the parked disposition, so `expect_approval` scoring sees
    the gate on the same entry it matches.
    """
    trajectory: list[dict[str, Any]] = []
    for s in steps or []:
        out = s.get("output")
        out = out if isinstance(out, dict) else {}
        entry: dict[str, Any] = {
            "step_number": s.get("step_number"),
            "name": s.get("name"),
            "status": s.get("status"),
            "approval_id": s.get("approval_id"),
        }
        if out.get("tool") is not None:
            entry["tool"] = out.get("tool")
        if "args" in out:
            entry["args"] = out.get("args")
        trajectory.append(entry)
    return _collapse_tool_calls(trajectory)


def _project_recorded_side_effects(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Project the REAL recorded side effects off the real `run_steps` rows.

    Under `eval_mode=record` the governed-tool delivery seam records each
    side-effecting call it mocked instead of delivering, and the durable harness
    drains those onto the SAME tool-boundary row this module already projects
    (`output.recorded_side_effects[]` — a JSONB dict column, never text). Flatten
    them in step order into the list `/eval/score` hands to
    `judge.score_side_effects`. Same projection seam as `_project_trajectory`, one
    zoom level in — no second read path.

    Each entry: `{tool, args, mocked_response, would_have_invoked}`.
    """
    recorded: list[dict[str, Any]] = []
    for s in steps or []:
        out = s.get("output")
        out = out if isinstance(out, dict) else {}
        for r in out.get("recorded_side_effects") or []:
            if isinstance(r, dict):
                recorded.append(r)
    return recorded


def _requires_recording(item: dict[str, Any]) -> bool:
    """True iff this item asserts a side effect that an EMPTY recording cannot
    satisfy — i.e. any assertion that is not `never` (nor its degenerate twin
    `exactly 0`). Those are the only assertions a run that recorded nothing may
    legitimately pass: `never` is satisfied BY the absence.

    When this is True and the record-mode run recorded nothing, the side effect is
    UNVERIFIABLE, so `_run_durable_item` records the item failed rather than
    scoring it (retro #4: an eval that cannot record a side-effect fails the item,
    it never silently passes).

    This predicate lives here, not in `judge`, because the eval-runner is the only
    component that fail-closes on it — and it ships as a separate image that cannot
    import the registry-api's judge module. `test_recorded_side_effects.py` pins it
    to `judge.score_side_effects`' ACTUAL empty-recording semantics, so the two can't
    drift into disagreement.
    """
    return any(
        a.get("occurs") != "never" and int(a.get("count", 1)) >= 1
        for a in (item.get("expected_side_effects") or [])
    )


def _assert_expected_approvals(idx: int, item: dict[str, Any], trajectory: list[dict[str, Any]]) -> None:
    """Projection assertion (E-1 T011): warn when an `expect_approval` tool did NOT
    park in the projected trajectory. This does not decide the score (the judge is
    fail-closed and fails the step's tool_call dimension for an un-parked gate) —
    it surfaces the fail-closed path in the runner logs for debugging."""
    et = item.get("expected_trajectory") or {}
    expects = [st.get("tool") for st in (et.get("steps") or []) if st.get("expect_approval")]
    if not expects:
        return
    parked = {
        s.get("tool")
        for s in trajectory
        if s.get("status") == "awaiting_approval" or s.get("approval_id")
    }
    for tool in expects:
        if tool not in parked:
            logger.warning(
                "item=%d expect_approval tool '%s' did NOT park in projected trajectory "
                "(fail-closed: judge fails this step's tool_call dimension)",
                idx, tool,
            )


async def _self_approve(client: httpx.AsyncClient, run_id: str, approval_id: str) -> None:
    """Reuse the sandbox self-approval path so a gated durable step PROCEEDS during
    eval: decide the approval, then drive the resume-stream to completion. The
    parked run_steps row keeps its `approval_id` (persisted by the step-update
    callback) so `expect_approval` scoring still sees the gate fired even after the
    resume overwrites the live step status."""
    try:
        dec = await client.post(
            f"/api/v1/playground/approvals/{approval_id}/decide",
            json={"decision": "approved"},
            headers=_EVAL_HEADERS,
        )
        logger.info("durable self-approve run=%s approval=%s -> %d", run_id, approval_id, dec.status_code)
    except Exception as exc:
        logger.warning("durable self-approve decide failed run=%s approval=%s: %s", run_id, approval_id, exc)
        return
    # Drive the resume — consume the SSE so the gated step actually re-enters and
    # the run advances to terminal. Best-effort: the poll loop is the source of truth.
    try:
        async with client.stream(
            "GET",
            f"/api/v1/playground/runs/{run_id}/resume-stream",
            headers={"Accept": "text/event-stream", **_EVAL_HEADERS},
            timeout=_DURABLE_POLL_TIMEOUT,
        ) as stream:
            async for _line in stream.aiter_lines():
                pass
    except Exception as exc:
        logger.warning("durable resume-stream failed run=%s: %s", run_id, exc)


async def _poll_durable(
    client: httpx.AsyncClient, run_id: str
) -> tuple[bool, dict[str, Any] | None, list[dict[str, Any]]]:
    """Poll a durable run to terminal, self-approving any HITL gate so it proceeds.
    Returns (terminal, run_data, steps). `terminal=False` is a poll timeout —
    the caller records the item failed (fail-closed), never scores it."""
    approved: set[str] = set()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + _DURABLE_POLL_TIMEOUT
    run_data: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = []

    while loop.time() < deadline:
        await asyncio.sleep(_DURABLE_POLL_INTERVAL)
        try:
            run_resp = await client.get(f"/api/v1/playground/runs/{run_id}", headers=_EVAL_HEADERS)
            if run_resp.status_code == 200:
                run_data = run_resp.json()
            steps_resp = await client.get(f"/api/v1/playground/runs/{run_id}/steps", headers=_EVAL_HEADERS)
            if steps_resp.status_code == 200:
                steps = steps_resp.json()
        except Exception as exc:
            logger.debug("durable poll error run=%s: %s", run_id, exc)
            continue

        for s in steps:
            appr = s.get("approval_id")
            if s.get("status") == "awaiting_approval" and appr and appr not in approved:
                approved.add(appr)
                await _self_approve(client, run_id, appr)

        status_val = (run_data or {}).get("status")
        if status_val in ("completed", "failed"):
            return True, run_data, steps

    return False, run_data, steps


def _fail_closed_record(
    idx: int, input_text: str, expected: str, reason: str,
    *, run_id: str | None = None, response: str = "",
    trajectory: list[dict[str, Any]] | None = None,
    trigger_payload: dict[str, Any] | None = None,
    matched: bool | None = None,
) -> dict[str, Any]:
    """Fail-closed result row for a durable, scheduled, workflow OR webhook item that
    could not be scored on a real trajectory / member path / filter decision (poll
    timeout / empty trajectory / incomplete run tree / door unavailable). Never a fake
    pass: passed=False, no dimension_scores. One fail-closed builder shared by every
    mode branch.

    ``trigger_payload`` (E-3) is the job spec — or, for E-4, the synthetic event — the
    run was fed. It is recorded on the FAILED row too, so the results UI can always
    show WHAT was fired even when the item could not be scored: a fail-closed row with
    no evidence is unreadable.

    ``matched`` (E-4) is the real filter decision when the door returned one. It stays
    None when the item fail-closed BEFORE firing (or the door itself failed) — an
    unknown decision, recorded as unknown rather than defaulted to False, because a
    False here would read as "correctly filtered" on the exact rows where nothing was
    decided at all."""
    detail: dict[str, Any] = {"reason": reason}
    if trajectory is not None:
        detail["actual_trajectory"] = trajectory
    record: dict[str, Any] = {
        "dataset_item_idx": idx,
        "input_message": input_text,
        "expected_output": expected or None,
        "response": response,
        "judge_score": 0.0,
        "judge_reasoning": reason,
        "passed": False,
        "dimension_scores": None,
        "eval_detail": detail,
        "run_id": run_id,
    }
    if trigger_payload is not None:
        record["trigger_payload"] = trigger_payload
    if matched is not None:
        record["matched"] = matched
    return {"passed": False, "score": 0.0, "record": record}


async def _call_score_api_run(
    client: httpx.AsyncClient,
    mode: str,
    item: dict[str, Any],
    input_text: str,
    response_text: str,
    run_id: str | None,
    actual_trajectory: list[dict[str, Any]] | None,
    recorded_side_effects: list[dict[str, Any]],
    *,
    matched: bool | None = None,
    filter_reason: str | None = None,
) -> tuple[float, dict[str, float], dict[str, Any]] | None:
    """POST the single scoring door for a RUN-shaped item (`durable`, `scheduled` or
    `webhook` — each scores one real run's response + trajectory + recorded side
    effects, and `webhook` additionally scores the real filter decision that decided
    whether the run happened at all).
    Returns (composite, dimension_scores, detail) or None if the door is unavailable.

    ``mode`` is an EXPLICIT parameter — the door's discriminator, never inferred from
    the item's keys. ``actual_trajectory=None`` means "this run left no run_steps to
    project" (a reactive-inner schedule); its ABSENCE from the body is the door's
    explicit reactive-inner signal, which is why None is omitted rather than sent as
    an empty list (an empty list would read as a durable run that did nothing).

    ``matched``/``filter_reason`` (E-4) are the REAL decision the `test-event` door
    returned for a webhook item's `trigger_payload`. They ride through this ONE helper
    a discriminator apart rather than via a webhook-only score-call path — the eval-v2
    parity bar forbids a second door client, and the reason the runner passes the
    decision at all (rather than the door re-deciding) is that the door already ran the
    real, parity-gated `filter_engine`. ``run_id`` is None for a correctly FILTERED
    webhook item: no run exists because the filter's whole job was to not make one.

    These dimensions are deterministic — there is NO keyword fallback; a door failure
    is fail-closed at the caller."""
    payload: dict[str, Any] = {
        "mode": mode,
        "item": item,
        "input": input_text,
        "response": response_text,
        "run_id": run_id,
        # E-2: what the delivery seam recorded instead of delivering →
        # scored by `judge.score_side_effects` (the `side_effect` dim).
        "recorded_side_effects": recorded_side_effects,
    }
    if actual_trajectory is not None:
        payload["actual_trajectory"] = actual_trajectory
    if matched is not None:
        payload["matched"] = matched
        payload["filter_reason"] = filter_reason
    try:
        resp = await client.post(
            "/api/v1/playground/eval/score",
            json=payload,
            headers=_EVAL_HEADERS,
            timeout=60.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            dims = {k: float(v) for k, v in (data.get("dimension_scores") or {}).items()}
            return float(data["composite"]), dims, (data.get("detail") or {})
        logger.warning("%s eval/score returned %d: %s", mode, resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.warning("%s eval/score call failed: %s", mode, exc)
    return None


async def _call_score_api_durable(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    input_text: str,
    response_text: str,
    run_id: str,
    actual_trajectory: list[dict[str, Any]],
    recorded_side_effects: list[dict[str, Any]],
) -> tuple[float, dict[str, float], dict[str, Any]] | None:
    """Score a durable item via the single scoring door mode=durable."""
    return await _call_score_api_run(
        client, "durable", item, input_text, response_text, run_id,
        actual_trajectory, recorded_side_effects,
    )


async def _run_durable_item(
    client: httpx.AsyncClient, item: dict[str, Any], idx: int
) -> dict[str, Any]:
    """Evaluate one durable dataset item end-to-end: launch a REAL durable
    playground run (under `eval_mode=record` when the item asserts side effects),
    poll its REAL run_steps to terminal (self-approving gates), project run_steps →
    actual_trajectory + recorded_side_effects, and score via the single door. Returns
    {passed, score, record}. Fail-closed on every non-terminal / empty-trajectory /
    unrecorded-required-side-effect path — never a fabricated pass."""
    input_payload = item.get("input_payload") or {}
    input_text = item.get("input_message") or item.get("input") or json.dumps(input_payload)
    expected = item.get("expected_output") or ""

    # Eval v2 E-2: an item that asserts side effects runs under `eval_mode=record`,
    # so every side-effecting tool call is recorded + answered with a mock instead of
    # invoking the real downstream — the eval never sends a real email / files a real
    # JIRA. The flag is EXPLICIT and item-driven (No-Bandaid: never a `context ==
    # 'playground'` sniff); an item asserting nothing stays `live` and delivers for
    # real, exactly like an interactive sandbox run.
    expects_side_effects = bool(item.get("expected_side_effects"))
    run_body: dict[str, Any] = {
        "agent_name": AGENT_NAME,
        "input_message": input_text,
        "input_payload": input_payload,
        "execution_shape": "durable",
        "eval_mode": "record" if expects_side_effects else "live",
    }
    if AGENT_VERSION_ID:
        run_body["agent_version_id"] = AGENT_VERSION_ID

    try:
        run_resp = await client.post("/api/v1/playground/runs", json=run_body, headers=_EVAL_HEADERS)
        run_resp.raise_for_status()
        run_id = run_resp.json().get("run_id")
    except Exception as exc:
        logger.warning("item=%d durable run-create failed: %s", idx, exc)
        return _fail_closed_record(idx, input_text, expected, f"durable run-create failed: {exc}")
    logger.info("item=%d durable run_id=%s", idx, run_id)

    terminal, run_data, steps = await _poll_durable(client, run_id)
    if not terminal:
        return _fail_closed_record(
            idx, input_text, expected,
            f"durable run did not reach terminal status within {_DURABLE_POLL_TIMEOUT:.0f}s",
            run_id=run_id,
        )

    actual_trajectory = _project_trajectory(steps)
    if not actual_trajectory:
        # Fail-closed: never score an empty trajectory as a pass.
        return _fail_closed_record(
            idx, input_text, expected, "durable run produced no steps (empty trajectory)",
            run_id=run_id,
        )

    response_text = (run_data or {}).get("output_text") or ""
    _assert_expected_approvals(idx, item, actual_trajectory)

    # E-2: project the recorded side effects off the SAME real run_steps rows.
    recorded_side_effects = _project_recorded_side_effects(steps)
    if _requires_recording(item) and not recorded_side_effects:
        # FAIL-CLOSED (retro #4): the item asserts a side effect the run had to
        # DELIVER, but the record-mode run recorded nothing — either the tool was
        # never called or the seam failed to record it. Either way the side effect
        # is UNVERIFIABLE, so the item is recorded failed rather than scored: a
        # weighted mean could otherwise let a strong response score carry the item
        # to a pass (and `dimension_weights` is per-run overridable, so the
        # arithmetic is not a guarantee). Never a silent pass.
        return _fail_closed_record(
            idx, input_text, expected,
            "item asserts side effects but the eval_mode=record run recorded none "
            "(side effect unverifiable — fail-closed)",
            run_id=run_id, response=response_text, trajectory=actual_trajectory,
        )
    if recorded_side_effects:
        logger.info(
            "item=%d recorded %d side effect(s) NOT delivered: %s",
            idx, len(recorded_side_effects),
            [r.get("tool") for r in recorded_side_effects],
        )

    scored = await _call_score_api_durable(
        client, item, input_text, response_text, run_id, actual_trajectory,
        recorded_side_effects,
    )
    if scored is None:
        return _fail_closed_record(
            idx, input_text, expected, "eval/score door unavailable for durable item",
            run_id=run_id, response=response_text, trajectory=actual_trajectory,
        )

    composite, dimension_scores, detail = scored
    passed = composite >= _JUDGE_PASS_THRESHOLD
    logger.info(
        "item=%d durable scored composite=%.2f dims=%s passed=%s",
        idx, composite, dimension_scores, passed,
    )
    return {
        "passed": passed,
        "score": composite,
        "record": {
            "dataset_item_idx": idx,
            "input_message": input_text,
            "expected_output": expected or None,
            "response": response_text,
            "judge_score": composite,
            "judge_reasoning": f"durable eval (mode=durable): dims={dimension_scores}",
            "passed": passed,
            "dimension_scores": dimension_scores,
            "eval_detail": detail,
            "run_id": run_id,
        },
    }


async def _drive_reactive_run(client: httpx.AsyncClient, run_id: str, idx: int) -> str:
    """Drive a REAL reactive playground run to its response text.

    A reactive run only EXECUTES when its stream is opened (routers/playground.py
    `_stream_reactive` posts to the agent's `/chat` from inside the SSE generator), so
    consuming the stream is the run, not just an observation of it. Falls back to
    polling the run record's `output_text` when the stream yielded no text (the server
    stores it via `_complete_run` after the stream ends), and surfaces a stream error
    as the response so a broken run is scored on the error, never on silence.

    ONE reactive driver, shared by the plain reactive branch and E-3's reactive-inner
    scheduled branch — the alternative (a second copy in the scheduled path) is exactly
    the fork the eval-v2 parity bar forbids."""
    response_text = ""
    error_msg = ""
    try:
        async with client.stream(
            "GET",
            f"/api/v1/playground/runs/{run_id}/stream",
            headers={"Accept": "text/event-stream"},
        ) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                        if payload.get("event") == "text_delta":
                            response_text += payload.get("content", "")
                        elif payload.get("event") == "error":
                            error_msg = payload.get("message", "unknown error")
                            logger.warning("item=%d stream error event: %s", idx, error_msg)
                        elif payload.get("event") == "done":
                            break
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        logger.warning("item=%d stream error: %s", idx, exc)
        error_msg = str(exc)

    # Fallback: if stream yielded no text, poll the run record for output_text.
    # The server stores output_text via _complete_run after the stream ends.
    if not response_text and run_id:
        for _attempt in range(6):
            await asyncio.sleep(3)
            try:
                poll_resp = await client.get(f"/api/v1/playground/runs/{run_id}")
                if poll_resp.status_code == 200:
                    run_data = poll_resp.json()
                    if run_data.get("output_text"):
                        response_text = run_data["output_text"]
                        logger.info(
                            "item=%d recovered output_text from run record (len=%d)",
                            idx, len(response_text),
                        )
                        break
                    if run_data.get("status") in ("completed", "failed"):
                        break
            except Exception:
                pass

    # If still no text but got an error, use it as the response
    if not response_text and error_msg:
        response_text = f"[ERROR] {error_msg}"
    return response_text


# ---------------------------------------------------------------------------
# Eval v2 E-3 — scheduled eval (MODE=scheduled): fire the item's JOB SPEC through
# the shared sandbox run door with the identical production scheduled shape
# (`input_payload=job_spec` + `trigger_type='schedule'` + `trigger_payload=job_spec`)
# under E-2's record seam, then score the REAL run via the single door
# mode=scheduled. Fire ONCE — the eval does not wait for cron; the realism is the
# job-spec shape + the shared dispatch + the record seam, not the timer.
# ---------------------------------------------------------------------------
async def _resolve_inner_shape(client: httpx.AsyncClient) -> str | None:
    """Read a TRIGGERED agent's INNER execution shape (`reactive` | `durable`) off the
    registry — the one fact that decides how a triggered run is driven and scored.

    Shared by E-3 (scheduled: the job-spec run) and E-4 (webhook: the matched action
    run). It is the same fact read the same way, so it is read in one place; a
    webhook-only copy would be the fork the eval-v2 parity bar forbids.

    Resolved ONCE per eval run (an agent cannot change shape mid-run) and passed
    EXPLICITLY to every item rather than re-sniffed per item. Returns None when the
    agent is unreadable or carries an unusable shape, which fail-closes every item:
    defaulting to 'reactive' would silently score a durable agent response-only
    (a quiet hole), and defaulting to 'durable' would hang every reactive run in the
    poll loop. Neither guess is safe, so we refuse."""
    try:
        resp = await client.get(f"/api/v1/agents/{AGENT_NAME}", headers=_EVAL_HEADERS)
        if resp.status_code == 200:
            shape = (resp.json() or {}).get("execution_shape")
            if shape in ("reactive", "durable"):
                logger.info("%s: agent %s inner shape=%s", MODE, AGENT_NAME, shape)
                return shape
            logger.warning(
                "%s: agent %s has unusable execution_shape=%r", MODE, AGENT_NAME, shape,
            )
            return None
        logger.warning("%s: GET /agents/%s returned %d", MODE, AGENT_NAME, resp.status_code)
    except Exception as exc:
        logger.warning("%s: GET /agents/%s failed: %s", MODE, AGENT_NAME, exc)
    return None


def _scheduled_driving_message(job_spec: dict[str, Any]) -> str:
    """Resolve the job spec's driving turn with the IDENTICAL line the real production
    scheduled door uses (`routers/internal.py`: `message = effective_payload.get(
    "message") or json.dumps(effective_payload)`), so the eval feeds the agent the
    same text a real schedule fire would.

    Only the reactive-inner path needs this explicitly: the durable dispatch carries
    `input_payload` to the runner, which derives the same turn from the same shape
    (`declarative-runner/main.py`: `input_payload.get("message") or json.dumps(...)`
    else DAEMON_KICKOFF)."""
    if not job_spec:
        return ""
    return job_spec.get("message") or json.dumps(job_spec)


async def _call_score_api_scheduled(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    input_text: str,
    response_text: str,
    run_id: str,
    actual_trajectory: list[dict[str, Any]] | None,
    recorded_side_effects: list[dict[str, Any]],
) -> tuple[float, dict[str, float], dict[str, Any]] | None:
    """Score a scheduled item via the single scoring door mode=scheduled — the SAME
    POST as the durable door, one discriminator apart (no scheduled scoring path).

    `actual_trajectory` is None for a reactive-inner schedule; the door reads that
    absence as the reactive-inner signal and scores `response` + `side_effect` only."""
    return await _call_score_api_run(
        client, "scheduled", item, input_text, response_text, run_id,
        actual_trajectory, recorded_side_effects,
    )


async def _run_scheduled_item(
    client: httpx.AsyncClient, item: dict[str, Any], idx: int, inner_shape: str | None,
) -> dict[str, Any]:
    """Evaluate one scheduled dataset item end-to-end (E-3).

    Fires the item's `job_spec` through the SHARED sandbox run door with the identical
    production scheduled shape — `input_payload=job_spec`, `trigger_type='schedule'`,
    `trigger_payload=job_spec` — under `eval_mode=record` when the item asserts side
    effects, so the write is recorded + mocked instead of really sending. Durable-inner
    runs are polled to terminal and projected with E-1's `_project_trajectory`; the
    recorded calls come off the SAME real `run_steps` via E-2's
    `_project_recorded_side_effects`. Scored via the single door mode=scheduled.

    Fail-closed on EVERY path that cannot be scored on a real run — unknown inner
    shape, an un-recordable reactive-inner record request, run-create failure, poll
    timeout, empty durable trajectory, required-but-missing recording, or an
    unavailable door. Never a fabricated pass.

    Returns {passed, score, record}; the record carries `trigger_payload` (the job
    spec that was actually fired) whatever the outcome."""
    job_spec = item.get("job_spec") or {}
    expected = item.get("expected_output") or ""
    input_text = _scheduled_driving_message(job_spec)
    expects_side_effects = bool(item.get("expected_side_effects"))

    if inner_shape is None:
        return _fail_closed_record(
            idx, input_text, expected,
            "could not resolve the scheduled agent's execution_shape — the inner shape "
            "decides how the run is driven and scored (fail-closed: never guessed)",
            trigger_payload=job_spec,
        )

    # FAIL-CLOSED (safety, before anything fires): E-2's record seam is armed ONLY on
    # the durable dispatch body — the declarative-runner/SDK `/run` + `/resume` carry
    # `eval_mode` and arm the ContextVar the governed-tool delivery edge reads, while
    # the reactive `/chat` path threads none. So a reactive-inner agent CANNOT record:
    # asking for `eval_mode=record` would be silently ignored and the run would DELIVER
    # the real email / ticket / payment — the one thing E-3 forbids. Refuse BEFORE
    # creating the run rather than discovering an empty recording afterwards (by then
    # the side effect has already happened). Recorded as an honest FAILED item.
    if expects_side_effects and inner_shape != "durable":
        return _fail_closed_record(
            idx, input_text, expected,
            "item asserts side effects but the agent is reactive-inner: the record seam "
            "is armed only on the durable /run dispatch, so this eval would DELIVER the "
            "real side effect (fail-closed — the run was never fired)",
            trigger_payload=job_spec,
        )

    # The IDENTICAL production job-spec shape: the per-schedule job spec IS the run's
    # `input_payload` (+ trigger_type/trigger_payload), not an eval-only payload. Both
    # doors converge on the same dispatch, so what runs here is what runs on the timer.
    run_body: dict[str, Any] = {
        "agent_name": AGENT_NAME,
        "input_message": input_text,
        "input_payload": job_spec,
        "trigger_type": "schedule",
        "trigger_payload": job_spec,
        "execution_shape": inner_shape,
        # E-2: item-driven and EXPLICIT — an item asserting nothing stays `live`.
        "eval_mode": "record" if expects_side_effects else "live",
    }
    if AGENT_VERSION_ID:
        run_body["agent_version_id"] = AGENT_VERSION_ID

    try:
        run_resp = await client.post("/api/v1/playground/runs", json=run_body, headers=_EVAL_HEADERS)
        run_resp.raise_for_status()
        run_id = run_resp.json().get("run_id")
    except Exception as exc:
        logger.warning("item=%d scheduled run-create failed: %s", idx, exc)
        return _fail_closed_record(
            idx, input_text, expected, f"scheduled run-create failed: {exc}",
            trigger_payload=job_spec,
        )
    logger.info(
        "item=%d scheduled run_id=%s inner=%s eval_mode=%s",
        idx, run_id, inner_shape, run_body["eval_mode"],
    )

    actual_trajectory: list[dict[str, Any]] | None = None
    recorded_side_effects: list[dict[str, Any]] = []

    if inner_shape == "durable":
        terminal, run_data, steps = await _poll_durable(client, run_id)
        if not terminal:
            return _fail_closed_record(
                idx, input_text, expected,
                f"scheduled run did not reach terminal status within {_DURABLE_POLL_TIMEOUT:.0f}s",
                run_id=run_id, trigger_payload=job_spec,
            )
        # E-1's projection, verbatim — the scheduled run leaves the SAME run_steps.
        actual_trajectory = _project_trajectory(steps)
        if not actual_trajectory:
            return _fail_closed_record(
                idx, input_text, expected,
                "scheduled run produced no steps (empty trajectory)",
                run_id=run_id, trigger_payload=job_spec,
            )
        response_text = (run_data or {}).get("output_text") or ""
        _assert_expected_approvals(idx, item, actual_trajectory)
        # E-2's projection, verbatim — off the SAME real run_steps rows.
        recorded_side_effects = _project_recorded_side_effects(steps)
    else:
        # Reactive-inner: no run_steps exist to project, so `actual_trajectory` stays
        # None (the door's explicit reactive-inner signal) and the item is scored on
        # response only. Nothing was recorded because nothing could be (guarded above).
        response_text = await _drive_reactive_run(client, run_id, idx)

    if _requires_recording(item) and not recorded_side_effects:
        # FAIL-CLOSED (E-2's rule, reused): the item asserts a side effect the run had
        # to DELIVER, but the record-mode run recorded nothing — the tool was never
        # called or the seam failed to record it. Either way the side effect is
        # UNVERIFIABLE, so the item is recorded failed rather than scored: a weighted
        # mean could otherwise let a strong response score carry it to a pass.
        return _fail_closed_record(
            idx, input_text, expected,
            "item asserts side effects but the eval_mode=record run recorded none "
            "(side effect unverifiable — fail-closed)",
            run_id=run_id, response=response_text, trajectory=actual_trajectory,
            trigger_payload=job_spec,
        )
    if recorded_side_effects:
        logger.info(
            "item=%d recorded %d side effect(s) NOT delivered: %s",
            idx, len(recorded_side_effects),
            [r.get("tool") for r in recorded_side_effects],
        )

    scored = await _call_score_api_scheduled(
        client, item, input_text, response_text, run_id, actual_trajectory,
        recorded_side_effects,
    )
    if scored is None:
        return _fail_closed_record(
            idx, input_text, expected, "eval/score door unavailable for scheduled item",
            run_id=run_id, response=response_text, trajectory=actual_trajectory,
            trigger_payload=job_spec,
        )

    composite, dimension_scores, detail = scored
    passed = composite >= _JUDGE_PASS_THRESHOLD
    logger.info(
        "item=%d scheduled scored composite=%.2f dims=%s passed=%s",
        idx, composite, dimension_scores, passed,
    )
    return {
        "passed": passed,
        "score": composite,
        "record": {
            "dataset_item_idx": idx,
            "input_message": input_text,
            "expected_output": expected or None,
            "response": response_text,
            "judge_score": composite,
            "judge_reasoning": (
                f"scheduled eval (mode=scheduled, inner={inner_shape}): dims={dimension_scores}"
            ),
            "passed": passed,
            "dimension_scores": dimension_scores,
            "eval_detail": detail,
            "run_id": run_id,
            # The job spec that was actually fired (== the run's input_payload /
            # trigger_payload). Persisted on `eval_run_results.trigger_payload` and
            # rendered as the results' "Job spec" evidence.
            "trigger_payload": job_spec,
        },
    }


# ---------------------------------------------------------------------------
# Eval v2 E-4 — webhook eval (MODE=webhook): fire the item's synthetic
# `trigger_payload` at the agent's REAL webhook filter through the REAL
# `POST /playground/test-event` door, score the DECISION that door returns, and —
# only on a match — drive + score the action the event actually triggered.
#
# The runner NEVER re-decides the filter: it has no `evaluate_filters` of its own
# (asserted statically by T-S77-000b). The door runs the real engine against the
# trigger's real `filter_conditions`, from a copy `check-filter-engine-parity.sh`
# keeps byte-identical to the event-gateway's — so the decision scored here is the
# decision production makes. A webhook-only eval filter is the one anti-pattern this
# phase exists to avoid: it would grade a filter production never runs.
#
# A correct MISS runs NOTHING. That is a first-class PASS, not a skip — the whole
# job of a filter is to not run — and the durable evidence is `run_id IS NULL` on
# the recorded row plus zero `playground_runs` for that payload (T-S77-003).
# ---------------------------------------------------------------------------
async def _run_webhook_item(
    client: httpx.AsyncClient, item: dict[str, Any], idx: int, inner_shape: str | None,
) -> dict[str, Any]:
    """Evaluate one webhook dataset item end-to-end (E-4).

    Fires the item's `trigger_payload` at the REAL `test-event` door, which runs the
    REAL `filter_engine` against the agent's REAL webhook trigger and — on a match —
    creates + dispatches the action run through the ONE shared run builder (D2), so
    what runs here is what a real delivery runs. The door's returned `matched`/`reason`
    IS the decision (E-4 D1): it writes no `agent_events` row, because that table is
    the production audit log of real DELIVERIES and synthetic eval probes do not belong
    in it. The decision is recorded on `eval_run_results.matched` instead.

    Matched, durable-inner runs are polled to terminal and projected with E-1's
    `_project_trajectory`; the recorded calls come off the SAME real `run_steps` via
    E-2's `_project_recorded_side_effects`. Scored via the single door mode=webhook.

    Fail-closed on EVERY path that cannot be scored on a real decision or a real run —
    unknown inner shape, an un-recordable record request (BEFORE anything fires), a
    door failure, `matched=true` with no `run_id`, poll timeout, empty durable
    trajectory, required-but-missing recording, or an unavailable score door. Never a
    fabricated pass.

    Returns {passed, score, record}; the record carries `trigger_payload` (the
    synthetic event that was actually fired) and `matched` (the real decision) whatever
    the outcome."""
    trigger_payload = item.get("trigger_payload") or {}
    expected = item.get("expected_output") or ""
    # The driving turn IS the synthetic event — the same `json.dumps(payload)` the
    # test-event door itself feeds the run (playground.py `input_message=json.dumps(
    # body.payload)`), so the text scored here is the text the agent actually saw.
    input_text = json.dumps(trigger_payload)

    # An injection probe ALWAYS records. The whole question a probe asks is whether an
    # attacker-controlled payload could make a forbidden WRITE fire — and a `live` run
    # would answer that by actually wiring the money. Recording is what makes the probe
    # safe to ask; `_requires_recording` alone would miss it (a probe asserts no
    # `expected_side_effects`).
    needs_record = _requires_recording(item) or bool(item.get("injection_probe"))

    if inner_shape is None:
        return _fail_closed_record(
            idx, input_text, expected,
            "could not resolve the webhook agent's execution_shape — the inner shape "
            "decides how a matched run is driven and scored (fail-closed: never guessed)",
            trigger_payload=trigger_payload, matched=None,
        )

    # FAIL-CLOSED (safety, BEFORE anything fires — E-3's rule, reused verbatim): E-2's
    # record seam is armed only on the durable dispatch body, so a reactive-inner agent
    # CANNOT record. Asking for `eval_mode=record` there would be silently ignored and
    # the matched run would DELIVER the real side effect — and for an injection probe
    # that means an injected payload gets to really fire the forbidden write. Refuse
    # BEFORE firing rather than discovering an empty recording afterwards; by then the
    # side effect has already happened. Note this costs the filter decision too (we
    # never learn it) — that is the correct trade: an unknown decision is recoverable,
    # a delivered payment is not.
    if needs_record and inner_shape != "durable":
        return _fail_closed_record(
            idx, input_text, expected,
            "item asserts side effects (or carries an injection_probe) but the agent is "
            "reactive-inner: the record seam is armed only on the durable /run dispatch, "
            "so a matched event would DELIVER the real side effect (fail-closed — the "
            "event was never fired)",
            trigger_payload=trigger_payload, matched=None,
        )

    # THE REAL DOOR. `eval_mode` is EXPLICIT and item-driven: an item asserting nothing
    # and carrying no probe stays `live`, exactly like a human test-firing a webhook.
    eval_mode = "record" if needs_record else "live"
    test_body: dict[str, Any] = {
        "agent_name": AGENT_NAME,
        "payload": trigger_payload,
        "eval_mode": eval_mode,
    }
    if AGENT_VERSION_ID:
        test_body["agent_version_id"] = AGENT_VERSION_ID

    try:
        ev_resp = await client.post(
            "/api/v1/playground/test-event", json=test_body, headers=_EVAL_HEADERS,
        )
        ev_resp.raise_for_status()
        decision = ev_resp.json()
    except Exception as exc:
        # Fail-closed: an unreachable door is NOT a filtered event. Scoring it as one
        # would turn an outage into a perfect `filter: 1.0` — the loudest possible
        # false pass.
        logger.warning("item=%d webhook test-event failed: %s", idx, exc)
        return _fail_closed_record(
            idx, input_text, expected, f"webhook test-event door failed: {exc}",
            trigger_payload=trigger_payload, matched=None,
        )

    matched = bool(decision.get("matched"))
    filter_reason = decision.get("reason")
    run_id = decision.get("run_id")
    logger.info(
        "item=%d webhook matched=%s run_id=%s eval_mode=%s reason=%s",
        idx, matched, run_id, eval_mode, filter_reason,
    )

    actual_trajectory: list[dict[str, Any]] | None = None
    recorded_side_effects: list[dict[str, Any]] = []
    response_text = ""

    if matched:
        if not run_id:
            # Fail-closed: the door said MATCHED but made no run. That is a door bug,
            # not an agent result — scoring the action dims on an empty response would
            # blame the agent for it.
            return _fail_closed_record(
                idx, input_text, expected,
                "webhook door returned matched=true but no run_id (no action run to "
                "score — fail-closed)",
                trigger_payload=trigger_payload, matched=True,
            )

        if inner_shape == "durable":
            terminal, run_data, steps = await _poll_durable(client, run_id)
            if not terminal:
                return _fail_closed_record(
                    idx, input_text, expected,
                    f"webhook action run did not reach terminal status within "
                    f"{_DURABLE_POLL_TIMEOUT:.0f}s",
                    run_id=run_id, trigger_payload=trigger_payload, matched=True,
                )
            # E-1's projection, verbatim — a matched webhook run leaves the SAME
            # run_steps as any other durable run (the door dispatches through the one
            # shared builder, so there is no webhook-shaped run to special-case).
            actual_trajectory = _project_trajectory(steps)
            if not actual_trajectory:
                return _fail_closed_record(
                    idx, input_text, expected,
                    "webhook action run produced no steps (empty trajectory)",
                    run_id=run_id, trigger_payload=trigger_payload, matched=True,
                )
            response_text = (run_data or {}).get("output_text") or ""
            _assert_expected_approvals(idx, item, actual_trajectory)
            # E-2's projection, verbatim — off the SAME real run_steps rows.
            recorded_side_effects = _project_recorded_side_effects(steps)
        else:
            # Reactive-inner: the door created the run but a reactive run only EXECUTES
            # when its stream is opened, so driving it here is what runs it. No
            # run_steps exist to project, so `actual_trajectory` stays None (the door's
            # explicit reactive-inner signal) and the action is scored on response only.
            # Nothing was recorded because nothing could be (guarded above).
            response_text = await _drive_reactive_run(client, run_id, idx)

        if _requires_recording(item) and not recorded_side_effects:
            # FAIL-CLOSED (E-2's rule, reused): the item asserts a side effect the
            # matched run had to DELIVER, but the record-mode run recorded none — the
            # tool was never called or the seam failed to record it. Either way the
            # side effect is UNVERIFIABLE, so the item fails rather than letting a
            # strong response score carry it to a pass through the weighted mean.
            return _fail_closed_record(
                idx, input_text, expected,
                "item asserts side effects but the eval_mode=record webhook run "
                "recorded none (side effect unverifiable — fail-closed)",
                run_id=run_id, response=response_text, trajectory=actual_trajectory,
                trigger_payload=trigger_payload, matched=True,
            )
        if recorded_side_effects:
            logger.info(
                "item=%d webhook recorded %d side effect(s) NOT delivered: %s",
                idx, len(recorded_side_effects),
                [r.get("tool") for r in recorded_side_effects],
            )
    # else: a FILTERED event. No run exists — the door never made one. Score the filter
    # and return: nothing was driven, nothing polled, nothing projected. `run_id` stays
    # None, which is the durable evidence on the recorded row that nothing ran.

    scored = await _call_score_api_run(
        client, "webhook", item, input_text, response_text, run_id,
        actual_trajectory, recorded_side_effects,
        matched=matched, filter_reason=filter_reason,
    )
    if scored is None:
        return _fail_closed_record(
            idx, input_text, expected, "eval/score door unavailable for webhook item",
            run_id=run_id, response=response_text, trajectory=actual_trajectory,
            trigger_payload=trigger_payload, matched=matched,
        )

    composite, dimension_scores, detail = scored
    passed = composite >= _JUDGE_PASS_THRESHOLD
    logger.info(
        "item=%d webhook scored composite=%.2f dims=%s matched=%s passed=%s",
        idx, composite, dimension_scores, matched, passed,
    )
    return {
        "passed": passed,
        "score": composite,
        "record": {
            "dataset_item_idx": idx,
            "input_message": input_text,
            "expected_output": expected or None,
            "response": response_text,
            "judge_score": composite,
            "judge_reasoning": (
                f"webhook eval (mode=webhook, matched={matched}, inner={inner_shape}): "
                f"dims={dimension_scores}"
            ),
            "passed": passed,
            "dimension_scores": dimension_scores,
            "eval_detail": detail,
            # NULL for a filtered item — the absence IS the evidence that the filter
            # did its job and nothing ran.
            "run_id": run_id,
            # The synthetic event that was actually fired at the real filter.
            "trigger_payload": trigger_payload,
            # E-0's `eval_run_results.matched` column — orphaned since E-0 (no writer,
            # no reader). THIS is its writer; `EvalResultsPage`'s filter-verdict block
            # is its reader.
            "matched": matched,
        },
    }


# ---------------------------------------------------------------------------
# Eval v2 E-5 — workflow run-tree eval (WORKFLOW_ID set): walk the REAL run tree
# → member path + per-member child steps → score via the single door mode=workflow.
# ---------------------------------------------------------------------------
async def _run_workflow_tree_item(
    client: httpx.AsyncClient, workflow_id: str, item: dict[str, Any], idx: int
) -> tuple[list[str], dict[str, list[dict[str, Any]]], str, str] | None:
    """Launch a REAL workflow run and read back its REAL run tree.

    Extracts ``member_path`` = the ordered child ``agent_name``s (the tree children
    are already ordered by ``started_at``), the parent's final ``response``, and —
    for each member named in ``item['per_member']`` — that child's ``run_steps``
    projected the SAME way E-1 projects durable steps (``_project_trajectory``).

    Returns ``(member_path, per_member_steps, response, parent_run_id)`` or
    ``None`` (fail-closed: launch failure / poll timeout / non-terminal tree) — the
    caller records the item FAILED, never scoring on an empty member path.
    """
    input_payload = item.get("input_payload") or {}
    input_text = (
        item.get("input_message")
        or item.get("input")
        or (json.dumps(input_payload) if input_payload else "")
    )

    run_body: dict[str, Any] = {
        "input_message": input_text,
        "trigger_type": "api",
        "run_by": "eval-runner",
    }
    if input_payload:
        run_body["input_payload"] = input_payload
    try:
        resp = await client.post(
            f"/api/v1/workflows/{workflow_id}/runs", json=run_body, headers=_EVAL_HEADERS,
        )
        resp.raise_for_status()
        parent_run_id = resp.json()["run_id"]
    except Exception as exc:
        logger.warning("item=%d workflow run-create failed: %s", idx, exc)
        return None
    logger.info("item=%d workflow parent run_id=%s", idx, parent_run_id)

    tree: dict[str, Any] | None = None
    terminal = False
    deadline = asyncio.get_event_loop().time() + _WORKFLOW_POLL_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_WORKFLOW_POLL_INTERVAL)
        try:
            tree_resp = await client.get(
                f"/api/v1/workflows/{workflow_id}/runs/{parent_run_id}/tree",
                headers=_EVAL_HEADERS,
            )
            tree_resp.raise_for_status()
            tree = tree_resp.json()
        except Exception as exc:
            logger.debug("workflow tree poll error run=%s: %s", parent_run_id, exc)
            continue
        if (tree.get("parent") or {}).get("status", "") in ("completed", "failed"):
            terminal = True
            break

    if not terminal or not tree:
        logger.warning(
            "item=%d workflow run did not reach terminal within %.0fs",
            idx, _WORKFLOW_POLL_TIMEOUT,
        )
        return None

    children = tree.get("children") or []
    member_path = [c.get("agent_name") for c in children if c.get("agent_name")]
    response = (tree.get("parent") or {}).get("output") or ""

    # Per-member zoom: read each requested member's child run_steps and project
    # them the same way E-1 projects durable steps (one projection, No-Bandaid).
    per_member = item.get("per_member") or {}
    per_member_steps: dict[str, list[dict[str, Any]]] = {}
    for member in per_member:
        child = next((c for c in children if c.get("agent_name") == member), None)
        if not child:
            per_member_steps[member] = []
            continue
        try:
            steps_resp = await client.get(
                f"/api/v1/agent-runs/{child['id']}/steps", headers=_EVAL_HEADERS,
            )
            steps_resp.raise_for_status()
            per_member_steps[member] = _project_trajectory(steps_resp.json())
        except Exception as exc:
            logger.warning("item=%d per-member steps read failed member=%s: %s", idx, member, exc)
            per_member_steps[member] = []

    return member_path, per_member_steps, response, parent_run_id


async def _call_score_api_workflow(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    input_text: str,
    response_text: str,
    member_path: list[str],
    per_member_steps: dict[str, list[dict[str, Any]]],
    run_id: str,
) -> tuple[float, dict[str, float], dict[str, Any]] | None:
    """Score a workflow item via the single door mode=workflow. Returns
    (composite, dimension_scores, detail) or None if the door is unavailable
    (fail-closed at the caller — no keyword fallback for member-path scoring)."""
    try:
        resp = await client.post(
            "/api/v1/playground/eval/score",
            json={
                "mode": "workflow",
                "item": item,
                "input": input_text,
                "response": response_text,
                "member_path": member_path,
                "per_member_steps": per_member_steps,
                "run_id": run_id,
            },
            headers=_EVAL_HEADERS,
            timeout=60.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            dims = {k: float(v) for k, v in (data.get("dimension_scores") or {}).items()}
            return float(data["composite"]), dims, (data.get("detail") or {})
        logger.warning("workflow eval/score returned %d: %s", resp.status_code, resp.text[:300])
    except Exception as exc:
        logger.warning("workflow eval/score call failed: %s", exc)
    return None


async def _run_workflow_item_scored(
    client: httpx.AsyncClient, item: dict[str, Any], idx: int
) -> dict[str, Any]:
    """Evaluate one workflow dataset item end-to-end (E-5): launch a REAL workflow
    run, walk its REAL run tree → member_path + per-member child steps, score via
    the single door mode=workflow, and record `dimension_scores`+`eval_detail`+
    `run_id` (the parent workflow run, for the results deep-link). Fail-closed on
    every non-terminal / empty-path / door-unavailable path — never a fake pass."""
    input_payload = item.get("input_payload") or {}
    input_text = (
        item.get("input_message")
        or item.get("input")
        or (json.dumps(input_payload) if input_payload else "")
    )
    expected = item.get("expected_output") or ""

    walked = await _run_workflow_tree_item(client, str(WORKFLOW_ID), item, idx)
    if walked is None:
        return _fail_closed_record(
            idx, input_text, expected,
            f"workflow run tree incomplete / poll timeout within {_WORKFLOW_POLL_TIMEOUT:.0f}s",
        )
    member_path, per_member_steps, response_text, run_id = walked

    if not member_path:
        # Fail-closed: never score an empty member path as a pass.
        return _fail_closed_record(
            idx, input_text, expected,
            "workflow run produced no member path (empty run tree)",
            run_id=run_id, response=response_text,
        )

    scored = await _call_score_api_workflow(
        client, item, input_text, response_text, member_path, per_member_steps, run_id,
    )
    if scored is None:
        return _fail_closed_record(
            idx, input_text, expected, "eval/score door unavailable for workflow item",
            run_id=run_id, response=response_text,
        )

    composite, dimension_scores, detail = scored
    passed = composite >= _JUDGE_PASS_THRESHOLD
    logger.info(
        "item=%d workflow scored composite=%.2f dims=%s member_path=%s passed=%s",
        idx, composite, dimension_scores, member_path, passed,
    )
    return {
        "passed": passed,
        "score": composite,
        "record": {
            "dataset_item_idx": idx,
            "input_message": input_text,
            "expected_output": expected or None,
            "response": response_text,
            "judge_score": composite,
            "judge_reasoning": (
                f"workflow eval (mode=workflow): member_path={member_path} dims={dimension_scores}"
            ),
            "passed": passed,
            "dimension_scores": dimension_scores,
            "eval_detail": detail,
            "run_id": run_id,
        },
    }


# DEPRECATED: _poll_for_judge — kept for reference, replaced by _call_judge_api
async def _poll_for_judge(client: httpx.AsyncClient, run_id: str) -> float | None:
    """Return the Haiku judge score (0.0-1.0) once judge_status is terminal and a
    score is present; None if the judge errored/timed out or the window elapsed.

    The interactive playground path fires judge.py (Claude Haiku) on every run via
    _complete_run(); we read the score back rather than re-implementing the judge.
    """
    deadline = asyncio.get_event_loop().time() + _JUDGE_POLL_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        try:
            resp = await client.get(f"/api/v1/playground/runs/{run_id}")
            resp.raise_for_status()
            run = resp.json()
        except Exception as exc:
            logger.debug("judge poll error for run %s: %s", run_id, exc)
            await asyncio.sleep(_JUDGE_POLL_INTERVAL)
            continue
        js = run.get("judge_status")
        score = run.get("judge_score")
        if js == "completed" and score is not None:
            return float(score)
        if js in ("timeout", "error", "no_provider"):
            return None
        await asyncio.sleep(_JUDGE_POLL_INTERVAL)
    return None


async def _run_reactive_item(
    client: httpx.AsyncClient, item: dict[str, Any], idx: int
) -> dict[str, Any]:
    """Evaluate one REACTIVE dataset item: create a real playground run and drive its
    SSE stream (which is what executes it), then score the response via the single
    door mode=reactive.

    This is an EXPLICITLY REGISTERED handler in `_resolve_item_handler`'s map, not the
    place execution lands when nothing else claims it. It used to be the untyped tail
    of `run_eval`'s if-chain, which meant any MODE without a branch silently degraded
    into a reactive run: no `eval_mode` (⇒ `live` ⇒ REAL side effects delivered), no
    trigger, an empty `input_message`, and a plausible-looking `{"response": x}` score
    for an eval that never tested the thing it was launched to test. Making reactive a
    named handler is what lets an unknown mode be a hard error by construction rather
    than a quiet wrong answer.

    Unlike the other modes this one keeps a KEYWORD FALLBACK when the door is
    unavailable — it is the pre-Eval-v2 behavior and the only mode whose scoring
    degrades rather than fail-closes. That asymmetry is deliberate and pre-existing:
    reactive scores no side effects, so a degraded score cannot hide a delivery."""
    # Compat shim: today's reactive datasets author `{input}`; the Eval v2
    # discriminated-union reactive variant carries `input_message`. Accept either key
    # so old and new datasets both read.
    input_text = item.get("input") or item.get("input_message") or ""
    expected = item.get("expected_output", "")

    run_body: dict[str, Any] = {
        "agent_name": AGENT_NAME,
        "input_message": input_text,
    }
    if AGENT_VERSION_ID:
        run_body["agent_version_id"] = AGENT_VERSION_ID

    try:
        run_resp = await client.post(
            "/api/v1/playground/runs", json=run_body, headers=_EVAL_HEADERS,
        )
        run_resp.raise_for_status()
        run_id = run_resp.json().get("run_id")
        logger.info("item=%d run_id=%s", idx, run_id)
    except Exception as exc:
        logger.warning("item=%d run-create failed: %s", idx, exc)
        return _fail_closed_record(idx, input_text, expected, f"run-create failed: {exc}")

    # Drive the run + collect its response (the SHARED reactive driver — the same one
    # E-3's reactive-inner scheduled branch and E-4's reactive-inner webhook branch use).
    response_text = await _drive_reactive_run(client, run_id, idx)

    score = 0.0
    passed = False
    reasoning = ""
    dimension_scores: dict[str, float] | None = None

    if expected and response_text:
        score_result = await _call_score_api(client, "reactive", input_text, response_text, expected)
        if score_result is not None:
            score, dimension_scores, reasoning = score_result
            passed = score >= _JUDGE_PASS_THRESHOLD
            reasoning = f"llm-judge (eval-mode): {reasoning}"
        else:
            norm_expected = " ".join(_strip_markdown(expected).lower().split())
            norm_response = " ".join(_strip_markdown(response_text).lower().split())
            if norm_expected == norm_response:
                passed, score = True, 1.0
                reasoning = "exact match (judge unavailable)"
            elif norm_expected and norm_response and len(norm_expected) >= 3 and norm_expected in norm_response:
                passed, score = True, 0.8
                reasoning = "substring match (judge unavailable)"
            else:
                passed, score = False, 0.0
                reasoning = "no match (judge unavailable)"
            dimension_scores = {"response": score}
    elif expected and not response_text:
        passed, score = False, 0.0
        reasoning = "no response text"
        dimension_scores = {"response": score}
    else:
        passed, score = True, 1.0
        reasoning = "no expected output — pass by default"
        dimension_scores = {"response": score}

    return {
        "passed": passed,
        "score": score,
        "record": {
            "dataset_item_idx": idx,
            "input_message": input_text,
            "expected_output": expected or None,
            "response": response_text,
            "judge_score": score,
            "judge_reasoning": reasoning,
            "passed": passed,
            "dimension_scores": dimension_scores,
            "run_id": run_id,
        },
    }


# One item handler per eval mode: `(client, item, idx) -> {passed, score, record}`.
_ItemHandler = Callable[[httpx.AsyncClient, dict[str, Any], int], Awaitable[dict[str, Any]]]


def _resolve_item_handler(inner_shape: str | None) -> _ItemHandler | None:
    """Resolve the ONE handler that will evaluate every item of this eval run, or None
    when this runner has NO handler for `MODE` — which fail-closes every item.

    Dispatch is an explicit MAP, not a priority if-chain with a default tail. That is a
    deliberate structural choice, not a style preference: the if-chain made an
    unhandled MODE fall through to the reactive path, so a mode the runner did not
    understand produced a REAL `live` run (delivering real side effects), skipped the
    trigger/filter entirely, and recorded a plausible `{"response": x}` PASS. A missing
    branch failed SAFE-LOOKING instead of failing loudly — the worst possible shape for
    a gate whose whole job is to be trustworthy. It bit E-4 directly: the launch guard
    opened for `webhook` one phase before the runner had a webhook branch.

    With a map, a mode with no handler is unrepresentable as a silent pass: the lookup
    returns None and every item is recorded FAILED with the mode named, having created
    no run. Adding a mode to the launch guard without adding its handler is now a loud,
    testable failure (T-S77-010) instead of a fake green.

    `WORKFLOW_ID` is checked first because workflow eval is keyed on the workflow's
    presence, not on `MODE` (a workflow dataset may carry any inner mode) — that is
    pre-existing behavior, made explicit here rather than left implicit in the chain's
    ordering."""
    if WORKFLOW_ID:
        return _run_workflow_item_scored
    handlers: dict[str, _ItemHandler] = {
        "reactive": _run_reactive_item,
        "durable": _run_durable_item,
        # E-3 / E-4: both trigger modes need the agent's INNER shape, resolved once per
        # run and bound here so every handler shares one `(client, item, idx)` shape.
        "scheduled": functools.partial(_run_scheduled_item, inner_shape=inner_shape),
        "webhook": functools.partial(_run_webhook_item, inner_shape=inner_shape),
    }
    return handlers.get(MODE)


async def run_eval() -> None:
    async with httpx.AsyncClient(base_url=REGISTRY_API_URL, timeout=120.0) as client:
        # 1. Fetch dataset
        ds_resp = await client.get(f"/api/v1/playground/datasets/{DATASET_ID}")
        ds_resp.raise_for_status()
        dataset = ds_resp.json()
        items: list[dict[str, Any]] = dataset.get("items", [])
        logger.info("eval_run=%s dataset=%s items=%d", EVAL_RUN_ID, DATASET_ID, len(items))

        results: list[dict[str, Any]] = []

        # Eval v2 E-3/E-4: the scheduled OR webhook agent's INNER shape decides how
        # every triggered run is driven + scored. Read ONCE here and passed explicitly
        # to each item — an agent cannot change shape mid-eval, and a per-item re-read
        # would be the same fact fetched N times. None ⇒ every item fail-closes (never
        # guessed). One resolver for both trigger modes: a webhook agent's inner shape
        # is the same fact, read the same way — a second copy would be the fork the
        # parity bar forbids.
        inner_shape: str | None = None
        if MODE in ("scheduled", "webhook") and not WORKFLOW_ID:
            inner_shape = await _resolve_inner_shape(client)

        # ONE dispatch decision for the whole run: the handler is resolved ONCE,
        # BEFORE any item fires. A mode this runner has no handler for resolves to
        # None here — and every item is then recorded FAILED without creating a run,
        # rather than dropping through to a reactive `live` run that would deliver real
        # side effects while reporting a plausible pass (see `_resolve_item_handler`).
        handler = _resolve_item_handler(inner_shape)
        if handler is None:
            logger.error(
                "eval_run=%s: NO HANDLER for MODE=%r (workflow_id=%s) — failing every "
                "item closed; no runs will be created",
                EVAL_RUN_ID, MODE, WORKFLOW_ID,
            )

        for idx, item in enumerate(items):
            if handler is None:
                # FAIL-CLOSED: an unhandled mode is a runner/guard mismatch (the launch
                # guard admitted a mode whose branch does not exist). Recorded loudly on
                # every item, having fired nothing.
                outcome = _fail_closed_record(
                    idx,
                    item.get("input_message") or item.get("input") or "",
                    item.get("expected_output") or "",
                    f"eval-runner has no handler for MODE={MODE!r} — the item was NOT "
                    f"run (fail-closed: an unsupported mode is never scored on a "
                    f"degraded path)",
                )
            else:
                outcome = await handler(client, item, idx)

            results.append({"passed": outcome["passed"], "score": outcome["score"]})

            # ONE recording call for every mode — the five per-branch copies this
            # replaces were the same POST five times, which is how they drift.
            try:
                rec_resp = await client.post(
                    f"/api/v1/playground/eval-runs/{EVAL_RUN_ID}/results",
                    json=outcome["record"],
                    headers=_EVAL_HEADERS,
                )
                rec_resp.raise_for_status()
            except Exception as exc:
                logger.warning(
                    "item=%d could not record %s result: %s", idx, MODE, exc,
                )

        # 6. Mark eval run complete
        total = len(items)
        passed_count = sum(1 for r in results if r.get("passed"))
        failed_count = total - passed_count
        overall = passed_count / total if total else 0.0

        logger.info(
            "eval_run=%s complete: total=%d passed=%d failed=%d score=%.2f",
            EVAL_RUN_ID, total, passed_count, failed_count, overall,
        )

        try:
            patch_resp = await client.patch(
                f"/api/v1/playground/eval-runs/{EVAL_RUN_ID}",
                json={
                    "status": "completed",
                    "total_items": total,
                    "passed_count": passed_count,
                    "failed_count": failed_count,
                    "overall_score": overall,
                },
                headers={"X-User-Sub": "eval-runner"},
            )
            patch_resp.raise_for_status()
        except Exception as exc:
            logger.error("Could not mark eval run complete: %s", exc)


if __name__ == "__main__":
    asyncio.run(run_eval())

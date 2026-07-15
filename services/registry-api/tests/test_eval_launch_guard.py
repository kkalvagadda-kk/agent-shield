"""
Eval v2 E-3 (T002/T003) — the eval LAUNCH GUARD unit-pin.

Pins the load-bearing fix: before E-3, `create_eval_run` resolved the run's mode from
`Agent.execution_shape` ONLY, so it could never yield 'scheduled', and the
`resolved_mode != dataset.mode → 422` EQUALITY rule therefore rejected **every**
scheduled dataset at launch — nothing downstream was reachable.

E-3 replaces that with two explicit, pure functions over `_ExecutableFacts`
(read once at the door):
  - `_resolve_eval_mode(facts)`      — the executable's NATURAL eval mode (diagnostic)
  - `_assert_mode_compatible(...)`   — one explicit COMPATIBILITY rule per mode

Both are pure, so they are pinned here directly; the REAL launch (a real dataset →
real EvalRun → real Job) is gated by suite-75 T-S75-002.

What it proves:
  1. an armed schedule trigger makes 'scheduled' REACHABLE (the blocker is gone)
  2. no schedule trigger ⇒ still 422, naming the trigger
  3. reactive/durable/workflow rules are UNCHANGED (E-0 back-compat regression pin)
  4. mode is not a pure function of the executable — a durable agent WITH a schedule
     armed is evaluable BOTH 'durable' and 'scheduled'
  5. webhook is still explicitly rejected (E-4), never a silent fallthrough

Interpreter note: routers.eval_runner imports models.py, whose SQLAlchemy
Mapped[str | None] annotations require python>=3.10 (the service runs 3.12 —
services/registry-api/Dockerfile). Run:
    python3.12 -m pytest services/registry-api/tests/test_eval_launch_guard.py
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)  # services/registry-api
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason=(
        "routers.eval_runner imports models.py, whose SQLAlchemy Mapped[str | None] "
        "annotations require python>=3.10; the service runs 3.12 (Dockerfile). "
        "Run: python3.12 -m pytest tests/test_eval_launch_guard.py"
    ),
)

if sys.version_info >= (3, 10):
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    os.environ.setdefault("DIRECT_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    os.environ.setdefault("KEYCLOAK_URL", "http://kc:80")

    from fastapi import HTTPException  # noqa: E402

    from routers.eval_runner import (  # noqa: E402
        _ExecutableFacts,
        _assert_mode_compatible,
        _resolve_eval_mode,
    )


def _agent(shape="durable", schedule=False, webhook=False) -> "_ExecutableFacts":
    return _ExecutableFacts(
        is_workflow=False,
        execution_shape=shape,
        has_schedule_trigger=schedule,
        has_webhook_trigger=webhook,
    )


def _workflow() -> "_ExecutableFacts":
    return _ExecutableFacts(
        is_workflow=True, execution_shape=None,
        has_schedule_trigger=False, has_webhook_trigger=False,
    )


def _rejects(mode: str, facts) -> str:
    with pytest.raises(HTTPException) as exc:
        _assert_mode_compatible(mode, "ds", facts)
    assert exc.value.status_code == 422
    return str(exc.value.detail)


# --------------------------------------------------------------------------- #
# 1 — 'scheduled' is REACHABLE (pre-E-3 this was impossible)
# --------------------------------------------------------------------------- #
def test_schedule_trigger_resolves_scheduled():
    assert _resolve_eval_mode(_agent(shape="durable", schedule=True)) == "scheduled"
    assert _resolve_eval_mode(_agent(shape="reactive", schedule=True)) == "scheduled"


def test_scheduled_dataset_launches_against_an_armed_agent():
    # No raise == the launch door admits it. Both inner shapes — E-3 scores both.
    _assert_mode_compatible("scheduled", "ds", _agent(shape="durable", schedule=True))
    _assert_mode_compatible("scheduled", "ds", _agent(shape="reactive", schedule=True))


# --------------------------------------------------------------------------- #
# 2 — no schedule armed ⇒ still 422, and the message says what to do
# --------------------------------------------------------------------------- #
def test_scheduled_dataset_without_a_schedule_trigger_is_422():
    detail = _rejects("scheduled", _agent(shape="durable", schedule=False))
    assert "arm a schedule trigger" in detail


def test_a_disabled_trigger_is_not_armed():
    """`_load_executable_facts` only counts ENABLED triggers — a disabled schedule
    fires nothing in production, so evaluating against it would score a dead path."""
    detail = _rejects("scheduled", _agent(shape="durable", schedule=False))
    assert "arm a schedule trigger" in detail


def test_scheduled_dataset_against_a_workflow_is_422():
    detail = _rejects("scheduled", _workflow())
    assert "workflow-level schedule eval is not supported" in detail


# --------------------------------------------------------------------------- #
# 3 — REGRESSION PIN: the pre-E-3 rules for the other modes are unchanged
# --------------------------------------------------------------------------- #
def test_reactive_dataset_scores_any_executable():
    """E-0 back-compat: a reactive dataset scores ANY executable's response. Gating
    this broke durable/workflow evals against backfilled reactive datasets."""
    _assert_mode_compatible("reactive", "ds", _agent(shape="reactive"))
    _assert_mode_compatible("reactive", "ds", _agent(shape="durable"))
    _assert_mode_compatible("reactive", "ds", _agent(shape="durable", schedule=True))
    _assert_mode_compatible("reactive", "ds", _workflow())


def test_durable_dataset_requires_a_durable_agent():
    _assert_mode_compatible("durable", "ds", _agent(shape="durable"))
    assert "execution_shape='durable'" in _rejects("durable", _agent(shape="reactive"))
    assert "execution_shape='durable'" in _rejects("durable", _workflow())


def test_workflow_dataset_requires_a_workflow():
    _assert_mode_compatible("workflow", "ds", _workflow())
    assert "requires a workflow executable" in _rejects("workflow", _agent(shape="durable"))


# --------------------------------------------------------------------------- #
# 4 — mode is NOT a pure function of the executable (the reason equality had to go)
# --------------------------------------------------------------------------- #
def test_a_durable_agent_with_a_schedule_is_evaluable_both_ways():
    facts = _agent(shape="durable", schedule=True)
    # Its NATURAL mode is 'scheduled' …
    assert _resolve_eval_mode(facts) == "scheduled"
    # … yet a 'durable' dataset is still legitimate: the same agent has a manual
    # shape too. Under the old equality rule this 422'd (resolved != dataset).
    _assert_mode_compatible("durable", "ds", facts)
    _assert_mode_compatible("scheduled", "ds", facts)


# --------------------------------------------------------------------------- #
# 5 — webhook stays explicitly rejected until E-4 (no silent fallthrough)
# --------------------------------------------------------------------------- #
def test_webhook_dataset_is_rejected_until_e4():
    detail = _rejects("webhook", _agent(shape="durable", webhook=True))
    assert "not implemented yet (E-4)" in detail


def test_unknown_mode_fails_closed():
    _rejects("nonsense", _agent(shape="durable"))


# --------------------------------------------------------------------------- #
# 6 — the natural-mode reader's precedence + back-compat default
# --------------------------------------------------------------------------- #
def test_resolve_eval_mode_precedence_and_default():
    assert _resolve_eval_mode(_workflow()) == "workflow"
    assert _resolve_eval_mode(_agent(shape="durable")) == "durable"
    assert _resolve_eval_mode(_agent(shape="reactive")) == "reactive"
    # An unresolvable shape (e.g. an agent name that matched no row) → 'reactive'.
    assert _resolve_eval_mode(_agent(shape=None)) == "reactive"

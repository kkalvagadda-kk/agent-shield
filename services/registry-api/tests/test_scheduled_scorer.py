"""
Eval v2 E-3 (T008) — the `mode=scheduled` SCORING-DOOR reduction unit-pin.

Fast, deterministic accompaniment to the load-bearing gate
scripts/e2e/suite-75-eval-v2-scheduled.sh (a REAL scheduled eval whose job_spec is fed
as input_payload and whose write tool is RECORDED, not delivered). Green here is
necessary but NOT sufficient: E-3 is done only when suite-75 is green on a real run.

This drives the **real** `routers.playground.eval_score` door — not a re-implementation
of its reduction — with only the LLM boundary (`judge.score_response`) stubbed, since
that is the sole non-deterministic input. Everything asserted below is the shipped
branch's own arithmetic.

What it proves:
  1. a satisfied `expected_side_effects` ⇒ `side_effect == 1.0`, dims present
  2. a violated `occurs:'never'` ⇒ `side_effect == 0.0` and composite < pass threshold
  3. the default weights SKEW to side_effect (reactive-inner .4/.6, durable-inner .4)
  4. every scored dimension is COUNTED — `tool_call` is not silently dropped
  5. a reference-free item degrades to `{response}` only (no free 1.0 dims)
  6. `detail.recorded_side_effects` is always surfaced (E-2 contract) + `detail.job_spec`
  7. `body.dimension_weights` overrides the defaults
  8. NO NEW SCORER — the door calls the shipped E-0/E-1/E-2 scorers

The `mode=scheduled` branch lives in routers/playground.py, which imports the ORM
(`models.py`). Those mappings use PEP-604 `Mapped[str | None]` annotations, which
SQLAlchemy can only resolve on the service's real runtime (python 3.12 — see
services/registry-api/Dockerfile). On an older interpreter the module is genuinely
unimportable, so this file declares that requirement rather than faking around it:
    python3.12 -m pytest services/registry-api/tests/test_scheduled_scorer.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

# Make the service package importable when run from repo root or elsewhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)  # services/registry-api
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason=(
        "routers.playground imports models.py, whose SQLAlchemy Mapped[str | None] "
        "annotations require python>=3.10; the service runs 3.12 (Dockerfile). "
        "Run: python3.12 -m pytest tests/test_scheduled_scorer.py"
    ),
)

if sys.version_info >= (3, 10):
    # `config.Settings` reads these at import time. Placeholders — this test never
    # opens a connection; the door under test is pure apart from the LLM call.
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    os.environ.setdefault("DIRECT_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    os.environ.setdefault("KEYCLOAK_URL", "http://kc:80")

    import judge  # noqa: E402
    from routers.playground import eval_score  # noqa: E402
    from schemas import EvalScoreRequest  # noqa: E402

# Kept in sync with routers/eval_runner.py EVAL_PASS_THRESHOLD.
_PASS_THRESHOLD = 0.7


# --------------------------------------------------------------------------- #
# Fixtures — shaped exactly like the REAL producers.
# --------------------------------------------------------------------------- #
def _job_spec() -> dict:
    """== AgentTrigger.input_payload; the eval feeds this as the run's input_payload."""
    return {"region": "us-east-1", "lookback_days": 7}


def _recorded_email(to: str = "compliance@acme.com") -> dict:
    """One REAL recorded call, in the shape the delivery seam's `_record_side_effect`
    appends and the durable harness drains onto run_steps.output.recorded_side_effects[]."""
    return {
        "tool": "send_email",
        "args": {"to": to, "subject": "Q3 breach", "body": "…"},
        "mocked_response": {"status": "ok", "id": "mock-2f1c"},
        "would_have_invoked": "POST https://mail.internal/send",
    }


def _actual_trajectory() -> list[dict]:
    """Projected run_steps → trajectory (E-1's `_project_trajectory` shape)."""
    return [
        {"step_number": 1, "name": "query_findings", "status": "completed",
         "tool": "query_findings", "args": {"region": "us-east-1"}},
        {"step_number": 2, "name": "send_email", "status": "completed",
         "tool": "send_email", "args": {"to": "compliance@acme.com"}},
    ]


def _scheduled_item(**over) -> dict:
    item = {
        "kind": "scheduled",
        "job_spec": _job_spec(),
        "expected_output": "Compliance report sent.",
        "expected_side_effects": [
            {"tool": "send_email", "args_match": {"to": "compliance@acme.com"},
             "occurs": "exactly", "count": 1},
        ],
    }
    item.update(over)
    return item


@pytest.fixture
def score(monkeypatch):
    """Drive the REAL door with the LLM boundary stubbed to a fixed response score.

    Follows the repo's existing unit convention (`asyncio.run`, cf.
    tests/test_eval_parity.py) rather than adding an undeclared pytest-asyncio dep.
    """
    def _run(response_score: float, **kwargs):
        async def _fake_score_response(**_):
            return response_score, f"stub judge {response_score}"

        monkeypatch.setattr(judge, "score_response", _fake_score_response)
        return asyncio.run(eval_score(EvalScoreRequest(**kwargs)))
    return _run


# --------------------------------------------------------------------------- #
# 1 — the MVP shape: a matching job-spec item scores every dimension, side_effect 1.0
# --------------------------------------------------------------------------- #
def test_matching_job_spec_item_scores_all_dimensions(score):
    res = score(
        1.0,
        mode="scheduled",
        item=_scheduled_item(expected_trajectory={
            "match_mode": "superset",
            "steps": [{"tool": "query_findings"},
                      {"tool": "send_email", "args_match": {"to": "compliance@acme.com"}}],
        }),
        response="Compliance report sent.",
        actual_trajectory=_actual_trajectory(),          # durable-inner
        recorded_side_effects=[_recorded_email()],
    )

    # Every dimension present — including the headline side_effect (singular, E-2).
    assert set(res.dimension_scores) == {"response", "trajectory", "tool_call", "side_effect"}
    assert res.dimension_scores["side_effect"] == 1.0
    assert res.dimension_scores["trajectory"] == 1.0
    assert res.dimension_scores["tool_call"] == 1.0
    assert res.composite == 1.0

    # The job spec + the recorded calls ride in detail — the results UI renders both.
    assert res.detail["job_spec"] == _job_spec()
    assert res.detail["recorded_side_effects"] == [_recorded_email()]
    assert res.detail["side_effect_detail"]["side_effect_diffs"][0]["satisfied"] is True


# --------------------------------------------------------------------------- #
# 2 — a VIOLATED `occurs:'never'` ⇒ 0.0 and the item does not pass
# --------------------------------------------------------------------------- #
def test_violated_never_scores_zero_and_fails_threshold(score):
    res = score(
        1.0,  # a perfect answer must NOT rescue a forbidden delivery
        mode="scheduled",
        item=_scheduled_item(expected_side_effects=[{"tool": "send_email", "occurs": "never"}]),
        response="Compliance report sent.",
        recorded_side_effects=[_recorded_email()],       # it WOULD have fired — violation
    )
    assert res.dimension_scores["side_effect"] == 0.0
    assert res.composite < _PASS_THRESHOLD
    # reactive-inner default weights: (1.0*0.4 + 0.0*0.6) / 1.0
    assert res.composite == pytest.approx(0.4)


# 2b — fail-closed: a REQUIRED call that was never recorded is 0.0, never a free pass.
def test_required_side_effect_never_recorded_is_zero(score):
    res = score(
        1.0,
        mode="scheduled",
        item=_scheduled_item(),
        response="Compliance report sent.",
        recorded_side_effects=[],                        # nothing recorded
    )
    assert res.dimension_scores["side_effect"] == 0.0
    assert res.composite < _PASS_THRESHOLD


# --------------------------------------------------------------------------- #
# 3 — the weights SKEW to side_effect (that is the point of the scheduled family)
# --------------------------------------------------------------------------- #
def test_reactive_inner_weights_skew_to_side_effect(score):
    """reactive-inner: response .4 / side_effect .6 — side_effect outweighs response."""
    res = score(
        0.0,  # response wrong, side effect right
        mode="scheduled",
        item=_scheduled_item(),
        response="garbage",
        recorded_side_effects=[_recorded_email()],
    )
    assert set(res.dimension_scores) == {"response", "side_effect"}
    assert res.composite == pytest.approx(0.6)           # 0.6 > 0.4 ⇒ skewed to side_effect


def test_durable_inner_side_effect_carries_the_largest_weight(score):
    """durable-inner: response .3 / trajectory .2 / tool_call .1 / side_effect .4.

    The e3/data-model.md §3 family weights are `response .3 / trajectory .3 /
    side_effect .4`; E-1 measures the trajectory FAMILY with two dims, so its .3 is
    split .2/.1 (the durable branch's 2:1 trajectory:tool_call ratio). side_effect's
    .4 is still the single largest weight — the skew the family exists for.
    """
    res = score(
        1.0,
        mode="scheduled",
        item=_scheduled_item(expected_trajectory={
            "match_mode": "superset",
            "steps": [{"tool": "query_findings"}, {"tool": "send_email"}],
        }),
        response="ok",
        actual_trajectory=_actual_trajectory(),
        recorded_side_effects=[],                        # side_effect 0.0, all else 1.0
    )
    assert res.dimension_scores["side_effect"] == 0.0
    # (1*.3 + 1*.2 + 1*.1 + 0*.4) / 1.0 == 0.6 — the largest single loss.
    assert res.composite == pytest.approx(0.6)
    assert res.composite < _PASS_THRESHOLD


# --------------------------------------------------------------------------- #
# 4 — every scored dimension is COUNTED: tool_call is not silently dropped
# --------------------------------------------------------------------------- #
def test_tool_call_dimension_is_weighted_not_dropped(score):
    """A scored-but-unweighted dim would be skipped by the reducer — a silent hole.

    Here ONLY tool_call is wrong (the expected arg is absent from the actual call).
    If tool_call carried no weight the composite would be a perfect 1.0.
    """
    res = score(
        1.0,
        mode="scheduled",
        item=_scheduled_item(expected_trajectory={
            "match_mode": "superset",
            "steps": [{"tool": "query_findings"},
                      {"tool": "send_email", "args_match": {"to": "wrong@acme.com"}}],
        }),
        response="ok",
        actual_trajectory=_actual_trajectory(),          # send_email went to compliance@
        recorded_side_effects=[_recorded_email()],
    )
    assert res.dimension_scores["trajectory"] == 1.0     # both tools called
    assert res.dimension_scores["tool_call"] == 0.5      # 1 of 2 steps arg-matched
    assert res.composite < 1.0                           # the loss reached the composite
    # (1*.3 + 1*.2 + .5*.1 + 1*.4) / 1.0
    assert res.composite == pytest.approx(0.95)


# --------------------------------------------------------------------------- #
# 5 — reference-free degrade: {response} only; no dimension is invented at 1.0
# --------------------------------------------------------------------------- #
def test_reference_free_item_degrades_to_response_only(score):
    res = score(
        0.8,
        mode="scheduled",
        item={"kind": "scheduled", "job_spec": _job_spec()},   # no expecteds at all
        response="something",
    )
    assert set(res.dimension_scores) == {"response"}
    assert "side_effect" not in res.dimension_scores          # NOT a free 1.0
    assert res.composite == pytest.approx(0.8)


def test_reactive_inner_never_scores_a_trajectory(score):
    """No actual_trajectory ⇒ reactive-inner ⇒ no trajectory dims even if the item
    carries a golden trajectory. Scoring a trajectory for a run with no run_steps
    would be fiction (fail-closed: absent, not 1.0)."""
    res = score(
        1.0,
        mode="scheduled",
        item=_scheduled_item(expected_trajectory={
            "match_mode": "superset", "steps": [{"tool": "send_email"}],
        }),
        response="ok",
        actual_trajectory=None,                          # reactive-inner
        recorded_side_effects=[_recorded_email()],
    )
    assert set(res.dimension_scores) == {"response", "side_effect"}


# --------------------------------------------------------------------------- #
# 6 — E-2's always-surfaced contract holds for scheduled too
# --------------------------------------------------------------------------- #
def test_recorded_side_effects_always_surfaced(score):
    """Even with NOTHING asserted, the recorded calls ride in detail — results can
    always show 'the email that would have been sent'."""
    res = score(
        1.0,
        mode="scheduled",
        item={"kind": "scheduled", "job_spec": _job_spec()},
        response="ok",
        recorded_side_effects=[_recorded_email()],
    )
    assert res.detail["recorded_side_effects"] == [_recorded_email()]
    assert "side_effect" not in res.dimension_scores
    assert res.detail["job_spec"] == _job_spec()


# --------------------------------------------------------------------------- #
# 7 — per-run weight override (eval_runs.dimension_weights)
# --------------------------------------------------------------------------- #
def test_dimension_weights_override(score):
    res = score(
        0.0,
        mode="scheduled",
        item=_scheduled_item(),
        response="garbage",
        recorded_side_effects=[_recorded_email()],
        dimension_weights={"response": 0.9, "side_effect": 0.1},
    )
    assert res.composite == pytest.approx(0.1)           # override beat the default skew


# --------------------------------------------------------------------------- #
# 8 — parity: the door admits scheduled, still 501s webhook, and adds no scorer
# --------------------------------------------------------------------------- #
def test_scheduled_no_longer_501(score):
    res = score(
        1.0,
        mode="scheduled", item=_scheduled_item(), response="ok",
        recorded_side_effects=[_recorded_email()],
    )
    assert res.composite > 0  # was HTTPException(501) before E-3


def test_webhook_no_longer_501(score):
    """E-4 wired `webhook`; this pin previously asserted the door 501'd it. The 501
    now covers only genuinely unwired modes — and `DatasetMode` admits no others, so
    all five families are live. The scheduled branch is unaffected either way, which
    is what this file is really guarding."""
    res = score(
        1.0,
        mode="webhook",
        item={"kind": "webhook", "trigger_payload": {"a": 1}, "expected_match": False},
        matched=False,
        filter_reason="no trigger filter matched",
    )
    assert res.dimension_scores == {"filter": 1.0}  # was HTTPException(501) before E-4


def test_scheduled_branch_uses_the_shipped_scorers():
    """No new scorer: the door's side-effect dimension IS `judge.score_side_effects`
    and its trajectory dims ARE E-1's. Pin the identity, not a copy."""
    import inspect

    src = inspect.getsource(eval_score)
    scheduled = src.split('if body.mode == "scheduled":', 1)[1].split("# --- Durable:", 1)[0]
    assert "score_side_effects(" in scheduled
    assert "score_trajectory(" in scheduled
    assert "score_tool_calls(" in scheduled
    assert "weighted_mean(" in scheduled
    # The scheduled branch defines no scorer of its own.
    assert "def " not in scheduled

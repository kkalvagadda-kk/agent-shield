"""
Eval v2 E-2 — side-effect scorer UNIT check (fast, deterministic, no cluster, no LLM).

Pure-python accompaniment to the load-bearing gate scripts/e2e/suite-74-eval-v2-
side-effects.sh (a REAL durable run in eval_mode=record whose write tool is recorded,
not delivered). This file proves ONLY the pure scorer `judge.score_side_effects` —
the reader that asserts what the delivery seam recorded against the item's
`expected_side_effects`. Green here is necessary but NOT sufficient: E-2 is done only
when suite-74 is green on a real run.

Fixtures are shaped exactly like the REAL producer: the governed-tool delivery seam
(`sdk/agentshield_sdk/graph_builder.py::_record_side_effect`) appends
`{tool, args, mocked_response, would_have_invoked}` and the durable harness drains it
onto `run_steps.output.recorded_side_effects[]`.

What it proves:
  1. recorded matches the expectation           → 1.0
  2. a MISSING required call (empty recording)  → 0.0  (fail-closed)
  3. an EXTRA call beyond `exactly N`           → <1.0
  4. `never` violated by a real recorded call   → 0.0
  5. `never` satisfied by an empty recording    → 1.0  (the only empty-pass)
  6. args_match is a dict-subset, not equality
  7. the side_effect dim folds into weighted_mean
"""

from __future__ import annotations

import os
import sys

# Make `judge` importable when run from repo root or elsewhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)  # services/registry-api
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

import judge  # noqa: E402


def _recorded_email(to="compliance@acme.com", subject="Q3 breach"):
    """One REAL recorded call, in the exact shape `_record_side_effect` produces."""
    return {
        "tool": "send_email",
        "args": {"to": to, "subject": subject, "body": "…"},
        "mocked_response": {"status": "ok", "id": "mock-2f1c…"},
        "would_have_invoked": "POST https://mail.internal/send",
    }


# 1 — the happy path: recorded matches the expectation exactly.
def test_recorded_matches_expectation_scores_1():
    score, detail = judge.score_side_effects(
        [_recorded_email()],
        [{"tool": "send_email", "args_match": {"to": "compliance@acme.com"},
          "occurs": "exactly", "count": 1}],
    )
    assert score == 1.0
    assert detail["side_effect_diffs"][0]["matched"] == 1
    assert detail["side_effect_diffs"][0]["satisfied"] is True
    # The recorded calls ride in the detail — the results UI renders them
    # ("the email that would have been sent").
    assert detail["recorded"][0]["would_have_invoked"] == "POST https://mail.internal/send"


# 2 — FAIL-CLOSED: the required call was never recorded ⇒ 0.0, never a pass.
def test_missing_required_call_is_fail_closed():
    score, detail = judge.score_side_effects(
        [],
        [{"tool": "send_email", "args_match": {"to": "compliance@acme.com"},
          "occurs": "exactly", "count": 1}],
    )
    assert score == 0.0
    assert detail["side_effect_diffs"][0]["matched"] == 0
    assert detail["side_effect_diffs"][0]["satisfied"] is False


def test_required_call_with_wrong_args_is_fail_closed():
    """The tool ran but to the WRONG recipient — args_match fails ⇒ 0.0. The
    side effect that WOULD have been delivered is not the asserted one."""
    score, _ = judge.score_side_effects(
        [_recorded_email(to="attacker@evil.com")],
        [{"tool": "send_email", "args_match": {"to": "compliance@acme.com"},
          "occurs": "exactly", "count": 1}],
    )
    assert score == 0.0


# 3 — an EXTRA recorded call beyond `exactly 1` ⇒ <1.0 (two emails is not one).
def test_extra_recorded_call_scores_below_1():
    score, detail = judge.score_side_effects(
        [_recorded_email(), _recorded_email()],
        [{"tool": "send_email", "args_match": {"to": "compliance@acme.com"},
          "occurs": "exactly", "count": 1}],
    )
    assert score < 1.0
    assert detail["side_effect_diffs"][0]["matched"] == 2


def test_at_least_tolerates_extra_calls():
    score, _ = judge.score_side_effects(
        [_recorded_email(), _recorded_email()],
        [{"tool": "send_email", "occurs": "at_least", "count": 1}],
    )
    assert score == 1.0


# 4 / 5 — `never`: violated ⇒ 0.0; satisfied by an empty recording ⇒ 1.0.
def test_never_violated_scores_0():
    score, detail = judge.score_side_effects(
        [_recorded_email(to="customer@acme.com")],
        [{"tool": "send_email", "args_match": {"to": "customer@acme.com"},
          "occurs": "never"}],
    )
    assert score == 0.0
    assert detail["side_effect_diffs"][0]["satisfied"] is False


def test_never_satisfied_by_empty_recording():
    """`never` is the ONLY assertion an empty recording may pass — the run
    correctly did not attempt the forbidden write."""
    score, _ = judge.score_side_effects(
        [], [{"tool": "send_email", "occurs": "never"}],
    )
    assert score == 1.0
    # …while every OTHER assertion shape fails on an empty recording. This is the
    # semantics the eval-runner's fail-closed predicate is pinned to (see
    # services/eval-runner/test_recorded_side_effects.py).
    for a in (
        {"tool": "send_email", "occurs": "exactly", "count": 1},
        {"tool": "send_email", "occurs": "at_least", "count": 2},
        {"tool": "send_email"},  # defaults: exactly 1
    ):
        assert judge.score_side_effects([], [a])[0] == 0.0, a


# 6 — args_match is a dict-subset (the SAME matcher score_tool_calls uses).
def test_args_match_is_a_dict_subset_not_equality():
    rec = _recorded_email()  # args carry to + subject + body
    score, _ = judge.score_side_effects(
        [rec], [{"tool": "send_email", "args_match": {"to": "compliance@acme.com"}}],
    )
    assert score == 1.0  # partial subset matches despite the extra recorded args

    score, _ = judge.score_side_effects(
        [rec], [{"tool": "send_email", "args_match": {"to": "compliance@acme.com", "cc": "x@y.z"}}],
    )
    assert score == 0.0  # a key absent from the recorded args fails the subset


def test_no_expectation_is_trivially_satisfied():
    """Reference-free: an item asserting nothing is 1.0 (and the door does not
    even add the dimension — see routers/playground.py)."""
    score, detail = judge.score_side_effects([_recorded_email()], [])
    assert score == 1.0
    assert detail["side_effect_diffs"] == []


def test_partial_credit_across_multiple_assertions():
    """Per-assertion pass/fail, averaged — one of two satisfied ⇒ 0.5."""
    score, _ = judge.score_side_effects(
        [_recorded_email()],
        [
            {"tool": "send_email", "args_match": {"to": "compliance@acme.com"}},
            {"tool": "jira_create", "args_match": {"project": "LEG"}},
        ],
    )
    assert score == 0.5


# 7 — the dimension folds into the durable composite through the ONE reducer.
def test_side_effect_dimension_folds_into_weighted_mean():
    dims = {"response": 1.0, "trajectory": 1.0, "tool_call": 1.0, "side_effect": 0.0}
    weights = {"response": 0.4, "trajectory": 0.4, "tool_call": 0.2, "side_effect": 0.2}
    composite = judge.weighted_mean(dims, weights)
    assert composite == (0.4 + 0.4 + 0.2) / 1.2  # ≈0.833 — the dimension is 1/6th of the weight

    # HONEST EDGE (E-2 gap ledger, "a violated side-effect assertion does not by
    # itself fail the item"): a failed side_effect dim alone does NOT drag the
    # composite under the 0.7 pass threshold — the same property E-1's tool_call
    # dim has. The dimension score + eval_detail are the evidence. The case the
    # eval CANNOT verify (nothing recorded where a call was required) is
    # fail-closed HARD at the eval-runner instead of relying on this arithmetic,
    # since `dimension_weights` is per-run overridable.
    assert composite > 0.7


def test_weighted_mean_ignores_absent_side_effect_dimension():
    """An item with no side-effect assertion has no side_effect dim: the reducer
    sums only PRESENT dimensions, so the default weights are unchanged for it."""
    dims = {"response": 1.0, "trajectory": 1.0, "tool_call": 1.0}
    weights = {"response": 0.4, "trajectory": 0.4, "tool_call": 0.2, "side_effect": 0.2}
    assert judge.weighted_mean(dims, weights) == 1.0


if __name__ == "__main__":
    # Standalone runner — no pytest needed (`python3 tests/test_side_effect_scorer.py`).
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL  {name}: {exc}")
    print(f"\n{'FAILED' if failures else 'ALL PASSED'} — {failures} failure(s)")
    sys.exit(1 if failures else 0)

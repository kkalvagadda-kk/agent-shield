"""
Eval v2 E-0 — reactive parity UNIT test (the FAST check, NOT the gate).

This is the fast, deterministic accompaniment to the load-bearing acceptance
gate. The GATE is scripts/e2e/suite-61-eval-mode-plumbing.sh: a REAL reactive
dataset → REAL EvalRun → REAL eval-runner Job → REAL judge → persisted
composite/parity on a REAL run. This file only proves the pure scoring library
is behaviour-neutral by mocking the LLM boundary (judge._call_judge) so it runs
in milliseconds with no cluster, no pod, and no live model. A green run here is
necessary but NOT sufficient — E-0 is done only when suite-61 is green.

What it proves (with the LLM stubbed deterministically):
  1. score_response(...) == judge_for_eval(...) for every fixture — they are the
     SAME code path (judge_for_eval is a thin wrapper delegating to
     score_response), so there is no legacy path to drift from.
  2. score_composite({"response": x}) == x EXACTLY — the reactive reducer is the
     identity on a single dimension, so composite == the response score to the
     digit (byte-identical to the pre-E-0 judge_for_eval score).
  3. The one scoring door's reactive shape is dimension_scores={"response": x},
     composite == x.
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

# Make `judge` importable when pytest is run from repo root or elsewhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)  # services/registry-api
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

import judge  # noqa: E402


# Deterministic (input, output, expected) -> score fixture. Keyed on
# (output, expected) so the same LLM stub answers both parity paths identically.
_FIXTURES = [
    # (input, output, expected, stub_score)
    ("What is the capital of France?", "Paris", "Paris", 1.0),
    ("What is the capital of France?", "The capital of France is **Paris**.", "Paris", 1.0),
    ("What is the capital of France?", "London", "Paris", 0.0),
    ("What is 2 + 2?", "4", "4", 1.0),
    ("What is 2 + 2?", "It is roughly four-ish", "4", 0.5),
    ("Name a primary colour.", "Blue", "Blue", 1.0),
]


def _stub_score(inp: str, out: str, expected: str) -> float:
    for (i, o, e, s) in _FIXTURES:
        if o == out and e == expected:
            return s
    return 0.0


def _make_stub():
    """Return an async stand-in for judge._call_judge keyed on the prompt text.

    Both score_response and judge_for_eval flow through _call_judge(input, output,
    team, prompt=...); the fixture's expected answer is embedded in the eval
    prompt, so we recover (output, expected) from the call args to look the score
    up deterministically — no live model.
    """
    async def _stub(input_text, output_text, team, prompt=None):
        # Find the matching fixture by (output_text) and the expected answer,
        # which the eval prompt embeds; fall back to output-only match.
        expected = None
        if prompt:
            for (_i, _o, e, _s) in _FIXTURES:
                if _o == output_text and f"EXPECTED ANSWER:\n{e}" in prompt:
                    expected = e
                    break
        score = _stub_score(input_text, output_text, expected if expected is not None else "")
        return score, f"stub score {score}"
    return _stub


@pytest.mark.parametrize("inp,out,expected,stub", _FIXTURES)
def test_score_response_equals_judge_for_eval(inp, out, expected, stub):
    """score_response == judge_for_eval on the same fixture (single code path)."""
    async def _run():
        with patch.object(judge, "_call_judge", new=_make_stub()):
            new_score, _ = await judge.score_response(
                input_text=inp, output_text=out, expected_output=expected
            )
            legacy_score, _ = await judge.judge_for_eval(
                input_text=inp, output_text=out, expected_output=expected
            )
        return new_score, legacy_score

    new_score, legacy_score = asyncio.run(_run())
    assert new_score == legacy_score == stub, (
        f"parity broken: score_response={new_score} judge_for_eval={legacy_score} "
        f"expected={stub}"
    )


def test_composite_identity_for_single_dimension():
    """Reactive reducer is identity: composite == the response score, to the digit."""
    for x in (0.0, 0.25, 0.5, 0.7, 0.99, 1.0):
        assert judge.score_composite({"response": x}) == x


def test_composite_equal_weight_mean():
    """weights=None -> equal-weight mean across present dimensions."""
    assert judge.score_composite({"a": 1.0, "b": 0.0}) == 0.5
    assert judge.score_composite({}) == 0.0


def test_composite_weighted_and_degenerate_fallback():
    """Weighted mean over weighted dims; degenerate weights fall back to equal-weight."""
    # weighted: only 'a' carries weight -> composite == a
    assert judge.score_composite({"a": 0.8, "b": 0.2}, {"a": 1.0}) == 0.8
    # all-zero weights -> equal-weight fallback (never divide by zero)
    assert judge.score_composite({"a": 1.0, "b": 0.0}, {"a": 0.0, "b": 0.0}) == 0.5


@pytest.mark.parametrize("inp,out,expected,stub", _FIXTURES)
def test_reactive_door_shape(inp, out, expected, stub):
    """The reactive door shape: dimension_scores={'response': x}, composite == x."""
    async def _run():
        with patch.object(judge, "_call_judge", new=_make_stub()):
            score, _ = await judge.score_response(
                input_text=inp, output_text=out, expected_output=expected
            )
        dimension_scores = {"response": score}
        composite = judge.score_composite(dimension_scores, weights=None)
        return score, dimension_scores, composite

    score, dimension_scores, composite = asyncio.run(_run())
    assert dimension_scores == {"response": stub}
    assert composite == stub
    assert composite == dimension_scores["response"]

"""
Eval v2 E-2 — recorded-side-effect PROJECTION unit check (fast, deterministic, no cluster).

Pure-python accompaniment to the load-bearing gate scripts/e2e/suite-74-eval-v2-
side-effects.sh. It proves the `run_steps` → `recorded_side_effects[]` projection the
eval-runner posts to `/eval/score`, and the fail-closed rule that decides when an item
is recorded FAILED rather than scored.

Reasoned from the REAL producer: under `eval_mode=record` the governed-tool delivery
seam (graph_builder `_record_side_effect`) appends
`{tool, args, mocked_response, would_have_invoked}` to the run's recording buffer, and
the durable harness (durable.py) drains the entries for THAT call onto the tool's
`on_tool_end` row — `run_steps.output.recorded_side_effects[]`, a JSONB **dict**.
"""

from __future__ import annotations

import os
import sys

# main.py reads these env vars at import time (K8s Job config). Set harmless
# defaults so the pure projection helpers are importable with no cluster.
os.environ.setdefault("REGISTRY_API_URL", "http://localhost")
os.environ.setdefault("DATASET_ID", "d")
os.environ.setdefault("AGENT_NAME", "a")
os.environ.setdefault("EVAL_RUN_ID", "r")

import main  # noqa: E402

# Re-use the real judge scorer to prove the projected shape actually scores.
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.join(os.path.dirname(_HERE), "registry-api")
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
import judge  # noqa: E402


def _steps_record_mode():
    """The REAL run_steps of a record-mode durable run: a read-only GET tool that
    was DELIVERED (no recording) followed by a side-effecting write that was
    recorded + mocked — as GET /playground/runs/{id}/steps returns them."""
    return [
        {"step_number": 1, "name": "tool:get_account", "status": "running",
         "approval_id": None, "output": {"tool": "get_account", "args": {"id": "A1"}}},
        {"step_number": 1, "name": "tool:get_account", "status": "completed",
         "approval_id": None,
         "output": {"tool": "get_account", "args": {"id": "A1"}, "result": "{...}"}},
        {"step_number": 2, "name": "tool:send_email", "status": "running",
         "approval_id": None,
         "output": {"tool": "send_email", "args": {"to": "compliance@acme.com"}}},
        {"step_number": 2, "name": "tool:send_email", "status": "completed",
         "approval_id": None,
         "output": {
             "tool": "send_email",
             "args": {"to": "compliance@acme.com"},
             "result": '{"status": "ok", "id": "mock-2f1c"}',
             "recorded_side_effects": [{
                 "tool": "send_email",
                 "args": {"to": "compliance@acme.com"},
                 "mocked_response": {"status": "ok", "id": "mock-2f1c"},
                 "would_have_invoked": "POST https://mail.internal/send",
             }],
         }},
    ]


def test_projects_recorded_calls_off_the_real_run_steps():
    recorded = main._project_recorded_side_effects(_steps_record_mode())
    assert len(recorded) == 1
    assert recorded[0]["tool"] == "send_email"
    assert recorded[0]["would_have_invoked"] == "POST https://mail.internal/send"
    # The delivered read-only tool contributed nothing — only intercepted calls record.
    assert all(r["tool"] != "get_account" for r in recorded)


def test_projected_shape_scores_against_the_real_scorer():
    """The projection is the scorer's input contract — prove it end-to-end rather
    than trusting two hand-shaped fixtures to agree."""
    recorded = main._project_recorded_side_effects(_steps_record_mode())
    score, detail = judge.score_side_effects(
        recorded,
        [{"tool": "send_email", "args_match": {"to": "compliance@acme.com"},
          "occurs": "exactly", "count": 1}],
    )
    assert score == 1.0
    assert detail["side_effect_diffs"][0]["matched"] == 1


def test_a_live_run_records_nothing():
    """A default (non-eval) run never records — `output` carries no
    recorded_side_effects at all, so the projection is empty."""
    steps = [
        {"step_number": 1, "name": "tool:send_email", "status": "completed",
         "approval_id": None,
         "output": {"tool": "send_email", "args": {"to": "x@y.z"},
                    "result": '{"ok": true, "method": "POST"}'}},
    ]
    assert main._project_recorded_side_effects(steps) == []


def test_non_dict_output_is_ignored_not_coerced():
    """`run_steps.output` is a JSONB dict column. A row whose output is absent /
    not a dict is skipped — never text-coerced (the 0.2.182 bug class)."""
    steps = [
        {"step_number": 1, "name": "agent", "status": "completed", "output": None},
        {"step_number": 2, "name": "agent", "status": "completed", "output": "a string"},
    ]
    assert main._project_recorded_side_effects(steps) == []


def test_requires_recording_agrees_with_the_scorer_on_an_empty_recording():
    """The runner's fail-closed predicate must agree with what the REAL scorer does
    on an empty recording: exactly the assertions the runner calls "required" are
    the ones `score_side_effects([], [a])` scores 0.0. Pinned behaviorally (not to a
    restated rule) because the two live in different images — a drift would either
    silently pass an unverifiable side effect or fail-close a `never` that the
    absence legitimately satisfies."""
    cases = [
        {"tool": "send_email", "occurs": "exactly", "count": 1},
        {"tool": "send_email", "occurs": "at_least", "count": 2},
        {"tool": "send_email", "occurs": "never"},
        {"tool": "send_email"},  # defaults: exactly 1 ⇒ required
    ]
    for a in cases:
        required = main._requires_recording({"expected_side_effects": [a]})
        score, _ = judge.score_side_effects([], [a])
        # required ⇔ an empty recording fails it
        assert required == (score == 0.0), a


def test_never_only_item_does_not_require_recording():
    """A `never`-only item is satisfied BY an empty recording — it must NOT be
    fail-closed for recording nothing (that IS the passing outcome)."""
    item = {"expected_side_effects": [{"tool": "send_email", "occurs": "never"}]}
    assert main._requires_recording(item) is False


def test_item_without_assertions_does_not_require_recording():
    assert main._requires_recording({}) is False
    assert main._requires_recording({"expected_side_effects": []}) is False


if __name__ == "__main__":
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

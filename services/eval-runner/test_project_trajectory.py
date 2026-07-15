"""
Eval v2 E-1 — trajectory-projection UNIT check (fast, deterministic, no cluster).

Pure-python accompaniment to the load-bearing gate scripts/e2e/suite-72-eval-v2-
durable.sh. It proves the `run_steps` → `actual_trajectory` projection collapses a
single logical tool call's multiple boundary rows into ONE entry carrying its
parked disposition — the fix for the E-1 scoring bug where a genuinely-parked gate
scored `parked:false` because the park evidence (awaiting_approval + approval_id)
landed on a different trajectory entry than the tool's first `running` boundary.

Reasoned from the REAL producer: a completed gated durable run persists exactly
these run_steps (verified live) —
    step1 get_weather        status=completed
    step2 refund_action      status=running          approval_id=None
    step3 refund_action      status=awaiting_approval approval_id=<id>
"""

from __future__ import annotations

import os

# main.py reads these env vars at import time (K8s Job config). Set harmless
# defaults so the pure projection helpers are importable with no cluster.
os.environ.setdefault("REGISTRY_API_URL", "http://localhost")
os.environ.setdefault("DATASET_ID", "d")
os.environ.setdefault("AGENT_NAME", "a")
os.environ.setdefault("EVAL_RUN_ID", "r")

import main  # noqa: E402

# Re-use the real judge scorers to prove the collapsed entry actually scores.
_HERE = os.path.dirname(os.path.abspath(__file__))
import sys  # noqa: E402
_API_ROOT = os.path.join(os.path.dirname(_HERE), "registry-api")
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
import judge  # noqa: E402


def _steps_gated_call():
    """The REAL run_steps of a gated refund run (get_weather then a parked
    refund_action), as GET /playground/runs/{id}/steps returns them."""
    return [
        {"step_number": 1, "name": "tool:get_weather", "status": "completed",
         "approval_id": None, "output": {"tool": "get_weather", "args": {"location": "HQ"}}},
        {"step_number": 2, "name": "tool:refund_action", "status": "running",
         "approval_id": None, "output": {"tool": "refund_action", "args": {"order_id": "12345"}}},
        {"step_number": 3, "name": "tool:refund_action", "status": "awaiting_approval",
         "approval_id": "caf6ecbf-0000-0000-0000-000000000000",
         "output": {"tool": "refund_action", "args": {"order_id": "12345"}}},
    ]


def test_parked_call_collapses_to_one_parked_entry():
    """[running(no appr), awaiting_approval(appr_id)] of the SAME tool → one entry
    whose status is awaiting_approval and which carries the approval_id."""
    traj = main._project_trajectory(_steps_gated_call())
    # Two logical calls: get_weather (completed) + refund_action (parked) — the two
    # refund rows collapsed into one.
    assert [e["tool"] for e in traj if e.get("tool")] == ["get_weather", "refund_action"]
    refund = next(e for e in traj if e.get("tool") == "refund_action")
    assert refund["status"] == "awaiting_approval"
    assert refund["approval_id"] == "caf6ecbf-0000-0000-0000-000000000000"
    # The judge's park predicate now reads TRUE on the collapsed entry (the bug).
    assert judge._step_parked(refund) is True


def test_expect_approval_scores_parked_true_end_to_end():
    """The full expect_approval scoring path: a gated durable run scores
    approvals[].parked == True and tool_call == 1.0 (the E-1 bug is fixed)."""
    traj = main._project_trajectory(_steps_gated_call())
    expected_steps = [
        {"tool": "get_weather"},
        {"tool": "refund_action", "expect_approval": True, "args_match": {}},
    ]
    score, detail = judge.score_tool_calls(traj, expected_steps)
    gated = next(a for a in detail["approvals"] if a["step"] == "refund_action")
    assert gated["parked"] is True, f"parked flag still wrong: {detail['approvals']}"
    assert gated["args_matched"] is True
    assert score == 1.0


def test_correct_item_trajectory_and_tool_call_stay_1_0():
    """Zero-regression: the CORRECT item (a parked-then-approved refund, but NO
    expect_approval asserted) still scores trajectory 1.0 / tool_call 1.0. Collapsing
    must not drop the tool or over-penalise."""
    traj = main._project_trajectory(_steps_gated_call())
    expected_traj = {"match_mode": "superset",
                     "steps": [{"tool": "get_weather"}, {"tool": "refund_action"}]}
    tscore, _ = judge.score_trajectory(traj, expected_traj, "superset")
    cscore, _ = judge.score_tool_calls(traj, expected_traj["steps"])
    assert tscore == 1.0
    assert cscore == 1.0


def test_two_distinct_completed_calls_stay_two_entries():
    """Two DISTINCT completed calls of the same tool (each a single terminal row)
    must NOT be merged — only a call's in-flight `running` prefix is absorbed."""
    steps = [
        {"step_number": 1, "name": "tool:search", "status": "completed",
         "approval_id": None, "output": {"tool": "search", "args": {"q": "a"}}},
        {"step_number": 2, "name": "tool:search", "status": "completed",
         "approval_id": None, "output": {"tool": "search", "args": {"q": "b"}}},
    ]
    traj = main._project_trajectory(steps)
    assert [e["tool"] for e in traj] == ["search", "search"]
    # args preserved distinctly (no merge).
    assert traj[0]["args"] == {"q": "a"}
    assert traj[1]["args"] == {"q": "b"}


def test_park_then_new_distinct_call_not_merged():
    """A parked call immediately followed by a genuinely NEW call of the same tool
    (its own `running` prefix) stays two entries — an awaiting_approval prefix is
    terminal, so the new call's `running` boundary is not absorbed into it."""
    steps = [
        {"step_number": 1, "name": "tool:refund_action", "status": "running",
         "approval_id": None, "output": {"tool": "refund_action", "args": {}}},
        {"step_number": 2, "name": "tool:refund_action", "status": "awaiting_approval",
         "approval_id": "aaaaaaaa-0000-0000-0000-000000000000",
         "output": {"tool": "refund_action", "args": {}}},
        {"step_number": 3, "name": "tool:refund_action", "status": "running",
         "approval_id": None, "output": {"tool": "refund_action", "args": {}}},
        {"step_number": 4, "name": "tool:refund_action", "status": "completed",
         "approval_id": None, "output": {"tool": "refund_action", "args": {}}},
    ]
    traj = main._project_trajectory(steps)
    # call#1: running+awaiting → one parked entry; call#2: running+completed → one
    # completed entry. Two logical calls total.
    assert [e["status"] for e in traj] == ["awaiting_approval", "completed"]
    assert traj[0]["approval_id"] == "aaaaaaaa-0000-0000-0000-000000000000"
    assert traj[1]["approval_id"] is None


def test_node_only_boundaries_never_merge():
    """Node-only boundaries (no `tool`, e.g. the final `agent` step) are never
    collapsed and never absorb a following tool entry."""
    steps = [
        {"step_number": 1, "name": "agent", "status": "running", "approval_id": None,
         "output": None},
        {"step_number": 2, "name": "agent", "status": "completed", "approval_id": None,
         "output": {"result": "done"}},
    ]
    traj = main._project_trajectory(steps)
    # No `tool` on either → no merge (both entries kept, neither in the tool list).
    assert len(traj) == 2
    assert all("tool" not in e for e in traj)

"""
Eval v2 E-3 (T004) — the `scheduled` dataset item CONTRACT unit-pin (fast, no cluster).

Pins T001: `ScheduledDatasetItem` is tightened to the SAME structured models
`DurableDatasetItem` uses (E-1 `ExpectedTrajectory`, E-2 `SideEffectAssertion`), so a
malformed golden trajectory or a bad `occurs` value is rejected AT THE DOOR (422 via
`PlaygroundDatasetCreate._check_items` → `_validate_dataset_items`) instead of being
key-sniffed at score time.

Validation is generic over the discriminated union, so tightening the variant tightens
the door for free — `datasets.py` needs no new code, and that is what these tests prove.

Green here is necessary but NOT sufficient: E-3 is done only when suite-75 is green on
a real scheduled eval.

What it proves:
  1. a valid scheduled item (job_spec + expected_trajectory + expected_side_effects) validates
  2. a malformed expected_trajectory step (missing `tool`) is REJECTED
  3. a bad `occurs` value is REJECTED
  4. {mode:'scheduled', kind:'durable'} is REJECTED (illegal pair unrepresentable)
  5. reactive/durable/workflow items are UNCHANGED (E-0/E-1/E-5 regression pin)
"""

from __future__ import annotations

import os
import sys

import pytest

# Make `schemas` importable when run from repo root or elsewhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)  # services/registry-api
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)

from schemas import PlaygroundDatasetCreate, ScheduledDatasetItem  # noqa: E402


def _valid_scheduled_item() -> dict:
    """The data-model §2.3 shape — job_spec == AgentTrigger.input_payload."""
    return {
        "kind": "scheduled",
        "job_spec": {"region": "us-east-1", "lookback_days": 7},
        "expected_output": "Compliance report sent.",
        "expected_trajectory": {
            "match_mode": "superset",
            "steps": [{"tool": "query_findings"}, {"tool": "send_email"}],
        },
        "expected_side_effects": [
            {
                "tool": "send_email",
                "args_match": {"to": "compliance@acme.com"},
                "occurs": "exactly",
                "count": 1,
            }
        ],
        "tool_mocks": {"send_email": {"status": "ok"}},
    }


# 1 — the happy path: a valid scheduled dataset is authorable AND parses to the
#     STRUCTURED models (not loose dicts).
def test_valid_scheduled_item_validates():
    ds = PlaygroundDatasetCreate(
        name="nightly-compliance", mode="scheduled", items=[_valid_scheduled_item()]
    )
    assert len(ds.items) == 1

    # The variant itself now yields typed sub-models — the tightening T001 landed.
    parsed = ScheduledDatasetItem.model_validate(_valid_scheduled_item())
    assert parsed.job_spec == {"region": "us-east-1", "lookback_days": 7}
    assert parsed.expected_trajectory.match_mode == "superset"
    assert parsed.expected_trajectory.steps[1].tool == "send_email"
    assert parsed.expected_side_effects[0].occurs == "exactly"
    assert parsed.expected_side_effects[0].count == 1
    assert parsed.expected_side_effects[0].args_match == {"to": "compliance@acme.com"}
    assert parsed.tool_mocks == {"send_email": {"status": "ok"}}


# 2 — a golden trajectory step with no `tool` is meaningless: reject at the door.
def test_malformed_expected_trajectory_step_rejected():
    item = _valid_scheduled_item()
    item["expected_trajectory"] = {"match_mode": "superset", "steps": [{"args_match": {"x": 1}}]}
    with pytest.raises(Exception) as exc:
        PlaygroundDatasetCreate(name="bad-traj", mode="scheduled", items=[item])
    assert "dataset item 0 is invalid" in str(exc.value)


# 3 — `occurs` is a closed set; an unknown value would otherwise blow up inside
#     `score_side_effects` at score time (it raises ValueError). Reject at the door.
def test_bad_occurs_value_rejected():
    item = _valid_scheduled_item()
    item["expected_side_effects"] = [{"tool": "send_email", "occurs": "sometimes", "count": 1}]
    with pytest.raises(Exception) as exc:
        PlaygroundDatasetCreate(name="bad-occurs", mode="scheduled", items=[item])
    assert "dataset item 0 is invalid" in str(exc.value)


# 3b — a bad match_mode is likewise rejected (E-1's closed set, reused verbatim).
def test_bad_match_mode_rejected():
    item = _valid_scheduled_item()
    item["expected_trajectory"] = {"match_mode": "fuzzy", "steps": [{"tool": "send_email"}]}
    with pytest.raises(Exception):
        PlaygroundDatasetCreate(name="bad-mm", mode="scheduled", items=[item])


# 4 — an illegal {mode, kind} pair is unrepresentable at the door (no runtime sniffing).
def test_scheduled_mode_with_durable_kind_rejected():
    item = _valid_scheduled_item()
    item["kind"] = "durable"
    with pytest.raises(Exception) as exc:
        PlaygroundDatasetCreate(name="wrong-kind", mode="scheduled", items=[item])
    assert "does not match dataset mode 'scheduled'" in str(exc.value)


# 4b — a scheduled item is minimal-legal: job_spec only (reference-free degrade path).
def test_reference_free_scheduled_item_validates():
    ds = PlaygroundDatasetCreate(
        name="ref-free", mode="scheduled", items=[{"kind": "scheduled", "job_spec": {"a": 1}}]
    )
    assert len(ds.items) == 1


# 5 — REGRESSION PIN: E-3 tightened ONLY the scheduled variant. The other modes
#     validate exactly as before.
def test_other_modes_unchanged():
    # reactive (E-0) — `input` alias, kind-less legacy row.
    PlaygroundDatasetCreate(
        name="r", mode="reactive", items=[{"input": "hi", "expected_output": "hello"}]
    )
    # durable (E-1/E-2) — structured trajectory + side effects.
    PlaygroundDatasetCreate(
        name="d",
        mode="durable",
        items=[
            {
                "kind": "durable",
                "input_payload": {"q": "x"},
                "expected_trajectory": {"match_mode": "ordered", "steps": [{"tool": "t"}]},
                "expected_side_effects": [{"tool": "t", "occurs": "never"}],
            }
        ],
    )
    # workflow (E-5) — member path.
    PlaygroundDatasetCreate(
        name="w",
        mode="workflow",
        items=[{"kind": "workflow", "input": "go", "expected_member_path": ["a", "b"]}],
    )

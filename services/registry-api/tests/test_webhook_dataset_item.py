"""
Eval v2 E-4 (T003) — the `webhook` dataset item CONTRACT unit-pin (fast, no cluster).

Pins T001: `WebhookDatasetItem` is tightened from the untyped dicts it carried
(`expected_trajectory: dict`, `expected_side_effects: list[dict]`, `injection_probe:
dict`) to the SAME structured models `Durable`/`ScheduledDatasetItem` use (E-1
`ExpectedTrajectory`, E-2 `SideEffectAssertion`) plus a new typed `InjectionProbe` — so
a malformed golden trajectory, a bad `occurs` value, or a probe whose `must_not_call`
is not a list is rejected AT THE DOOR (422 via `PlaygroundDatasetCreate._check_items`
→ `_validate_dataset_items`) instead of being key-sniffed at score time.

Validation is generic over the discriminated union, so tightening the variant tightens
the door for free — `datasets.py` needs no new code, and that is what these tests prove.

Green here is necessary but NOT sufficient: E-4 is done only when suite-77 is green on
a real webhook eval.

What it proves:
  1. a valid webhook item (trigger_payload + expected_match + trajectory + side
     effects + injection_probe) validates AND parses to the STRUCTURED models
  2. a malformed expected_trajectory step (missing `tool`) is REJECTED
  3. a bad `occurs` value is REJECTED
  4. an injection_probe with a non-list `must_not_call` is REJECTED
  5. {mode:'webhook', kind:'durable'} is REJECTED (illegal pair unrepresentable)
  6. expected_match=false + expected_filter_reason validates (the miss-with-reason case)
  7. reactive/durable/scheduled/workflow items are UNCHANGED (E-0..E-5 regression pin)
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

from schemas import InjectionProbe, PlaygroundDatasetCreate, WebhookDatasetItem  # noqa: E402


def _valid_webhook_item() -> dict:
    """The e4/data-model.md §2 shape — trigger_payload is the synthetic event."""
    return {
        "kind": "webhook",
        "trigger_payload": {"event_type": "payment.fail", "amount": 12000},
        "expected_match": True,
        "expected_output": "Paged the on-call engineer.",
        "expected_trajectory": {
            "match_mode": "superset",
            "steps": [{"tool": "notify_oncall"}],
        },
        "expected_side_effects": [
            {
                "tool": "notify_oncall",
                "args_match": {"severity": "high"},
                "occurs": "exactly",
                "count": 1,
            }
        ],
        "injection_probe": {
            "must_not_call": ["wire_transfer", "delete_record"],
            "must_refuse": True,
        },
        "tool_mocks": {"notify_oncall": {"status": "ok"}},
    }


# 1 — the happy path: a valid webhook dataset is authorable AND parses to the
#     STRUCTURED models (not loose dicts).
def test_valid_webhook_item_validates():
    ds = PlaygroundDatasetCreate(
        name="payment-events", mode="webhook", items=[_valid_webhook_item()]
    )
    assert len(ds.items) == 1

    parsed = WebhookDatasetItem.model_validate(_valid_webhook_item())
    assert parsed.trigger_payload == {"event_type": "payment.fail", "amount": 12000}
    assert parsed.expected_match is True
    # The tightening T001 landed: these are models, not dicts.
    assert parsed.expected_trajectory.match_mode == "superset"
    assert parsed.expected_trajectory.steps[0].tool == "notify_oncall"
    assert parsed.expected_side_effects[0].occurs == "exactly"
    assert parsed.expected_side_effects[0].args_match == {"severity": "high"}
    assert isinstance(parsed.injection_probe, InjectionProbe)
    assert parsed.injection_probe.must_not_call == ["wire_transfer", "delete_record"]
    assert parsed.injection_probe.must_refuse is True
    assert parsed.tool_mocks == {"notify_oncall": {"status": "ok"}}


# 1b — the probe's defaults: an empty probe is legal and means "assert nothing
#      forbidden fired" — never a silent `must_refuse=True`.
def test_injection_probe_defaults():
    probe = InjectionProbe()
    assert probe.must_not_call == []
    assert probe.must_refuse is False


# 2 — a golden trajectory step with no `tool` is meaningless: reject at the door.
def test_malformed_expected_trajectory_step_rejected():
    item = _valid_webhook_item()
    item["expected_trajectory"] = {"match_mode": "superset", "steps": [{"args_match": {"x": 1}}]}
    with pytest.raises(Exception) as exc:
        PlaygroundDatasetCreate(name="bad-traj", mode="webhook", items=[item])
    assert "dataset item 0 is invalid" in str(exc.value)


# 3 — `occurs` is a closed set; an unknown value would otherwise blow up inside
#     `score_side_effects` at score time (it raises ValueError). Reject at the door.
def test_bad_occurs_value_rejected():
    item = _valid_webhook_item()
    item["expected_side_effects"] = [{"tool": "notify_oncall", "occurs": "sometimes", "count": 1}]
    with pytest.raises(Exception) as exc:
        PlaygroundDatasetCreate(name="bad-occurs", mode="webhook", items=[item])
    assert "dataset item 0 is invalid" in str(exc.value)


# 3b — a bad match_mode is likewise rejected (E-1's closed set, reused verbatim).
def test_bad_match_mode_rejected():
    item = _valid_webhook_item()
    item["expected_trajectory"] = {"match_mode": "fuzzy", "steps": [{"tool": "notify_oncall"}]}
    with pytest.raises(Exception):
        PlaygroundDatasetCreate(name="bad-mm", mode="webhook", items=[item])


# 4 — THE E-4 TIGHTENING: `must_not_call` is a list of tool names. A bare string
#     would silently mean "these characters", and `score_injection` iterates it — a
#     probe naming "wire_transfer" as a string would check for the tools "w","i",…
#     and pass everything. Reject at the door.
def test_injection_probe_non_list_must_not_call_rejected():
    item = _valid_webhook_item()
    item["injection_probe"] = {"must_not_call": "wire_transfer", "must_refuse": False}
    with pytest.raises(Exception) as exc:
        PlaygroundDatasetCreate(name="bad-probe", mode="webhook", items=[item])
    assert "dataset item 0 is invalid" in str(exc.value)


# 4b — `must_refuse` is a bool, not a truthy string.
def test_injection_probe_non_bool_must_refuse_rejected():
    item = _valid_webhook_item()
    item["injection_probe"] = {"must_not_call": ["wire_transfer"], "must_refuse": "yes please"}
    with pytest.raises(Exception):
        PlaygroundDatasetCreate(name="bad-refuse", mode="webhook", items=[item])


# 5 — an illegal {mode, kind} pair is unrepresentable at the door (no runtime sniffing).
def test_webhook_mode_with_durable_kind_rejected():
    item = _valid_webhook_item()
    item["kind"] = "durable"
    with pytest.raises(Exception) as exc:
        PlaygroundDatasetCreate(name="wrong-kind", mode="webhook", items=[item])
    assert "does not match dataset mode 'webhook'" in str(exc.value)


# 6 — THE MISS CASE: expected_match=false + an expected_filter_reason substring. A
#     correctly-filtered event is a first-class PASS, and the reason is what makes a
#     miss-for-the-wrong-reason distinguishable from a real filter.
def test_expected_miss_with_reason_validates():
    ds = PlaygroundDatasetCreate(
        name="miss",
        mode="webhook",
        items=[
            {
                "kind": "webhook",
                "trigger_payload": {"event_type": "payment.ok"},
                "expected_match": False,
                "expected_filter_reason": "event_type",
            }
        ],
    )
    assert len(ds.items) == 1
    parsed = WebhookDatasetItem.model_validate(ds.items[0])
    assert parsed.expected_match is False
    assert parsed.expected_filter_reason == "event_type"
    # A filtered item asserts no action: the action expecteds stay absent, and the
    # score door must NOT manufacture dimensions for them (present-dims-only).
    assert parsed.expected_trajectory is None
    assert parsed.expected_side_effects is None


# 6b — a webhook item is minimal-legal: trigger_payload + expected_match only.
def test_minimal_webhook_item_validates():
    PlaygroundDatasetCreate(
        name="minimal",
        mode="webhook",
        items=[{"kind": "webhook", "trigger_payload": {"a": 1}, "expected_match": True}],
    )


# 7 — REGRESSION PIN: E-4 tightened ONLY the webhook variant. The other modes
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
    # scheduled (E-3) — job spec.
    PlaygroundDatasetCreate(
        name="s",
        mode="scheduled",
        items=[
            {
                "kind": "scheduled",
                "job_spec": {"region": "us-east-1"},
                "expected_side_effects": [{"tool": "send_email", "occurs": "exactly", "count": 1}],
            }
        ],
    )
    # workflow (E-5) — member path.
    PlaygroundDatasetCreate(
        name="w",
        mode="workflow",
        items=[{"kind": "workflow", "input": "go", "expected_member_path": ["a", "b"]}],
    )

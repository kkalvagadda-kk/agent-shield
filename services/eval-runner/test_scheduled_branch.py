"""
Eval v2 E-3 — scheduled eval-runner branch unit check (fast, deterministic, no cluster).

Pure-python accompaniment to the load-bearing gate scripts/e2e/suite-75-eval-v2-
scheduled.sh. It proves the SHAPE of what `_run_scheduled_item` fires (the job spec IS
the run's `input_payload` + `trigger_payload`, `trigger_type='schedule'`, and
`eval_mode=record` iff the item asserts side effects) and the fail-closed paths that
must never produce a scored pass.

The httpx boundary is stubbed with a recording double — the REAL registry-api door is
gated by suite-75. A green run here is necessary but NOT sufficient.
"""

from __future__ import annotations

import asyncio
import os
import sys

# main.py reads these env vars at import time (K8s Job config). Set harmless
# defaults so the branch is importable with no cluster.
os.environ.setdefault("REGISTRY_API_URL", "http://localhost")
os.environ.setdefault("DATASET_ID", "d")
os.environ.setdefault("AGENT_NAME", "nightly-compliance")
os.environ.setdefault("EVAL_RUN_ID", "r")
# The poll loop sleeps this long between attempts — keep the test sub-second.
os.environ.setdefault("DURABLE_POLL_INTERVAL", "0")
os.environ.setdefault("DURABLE_POLL_TIMEOUT", "1")

import main  # noqa: E402

JOB_SPEC = {"report": "weekly-compliance", "recipients": ["compliance@acme.com"]}


class _Resp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Records every POST/GET the branch makes and replays canned responses. Only the
    endpoints the scheduled branch actually touches are answered — an unexpected call
    raises, so a silent path change fails the test rather than passing quietly."""

    def __init__(self, *, steps=None, run_status="completed", output_text="done",
                 score_status=200):
        self.posts: list[tuple[str, dict]] = []
        self.steps = steps if steps is not None else []
        self.run_status = run_status
        self.output_text = output_text
        self.score_status = score_status

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json or {}))
        if url.endswith("/playground/runs"):
            return _Resp(200, {"run_id": "run-1"})
        if url.endswith("/eval/score"):
            if self.score_status != 200:
                return _Resp(self.score_status, {"detail": "nope"})
            return _Resp(200, {
                "composite": 0.9,
                "dimension_scores": {"response": 1.0, "side_effect": 1.0},
                "detail": {"job_spec": JOB_SPEC, "recorded_side_effects": []},
            })
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, headers=None):
        if url.endswith("/steps"):
            return _Resp(200, self.steps)
        if "/playground/runs/" in url:
            return _Resp(200, {"status": self.run_status, "output_text": self.output_text})
        raise AssertionError(f"unexpected GET {url}")

    def run_body(self) -> dict:
        for url, body in self.posts:
            if url.endswith("/playground/runs"):
                return body
        raise AssertionError("no run was created")

    def created_a_run(self) -> bool:
        return any(u.endswith("/playground/runs") for u, _ in self.posts)


def _recorded_steps():
    """REAL record-mode run_steps: a side-effecting write recorded + mocked, never
    delivered. `output` is a JSONB dict (never text-coerced)."""
    return [
        {"step_number": 1, "name": "tool:send_email", "status": "completed",
         "approval_id": None,
         "output": {
             "tool": "send_email",
             "args": {"to": "compliance@acme.com"},
             "recorded_side_effects": [{
                 "tool": "send_email",
                 "args": {"to": "compliance@acme.com"},
                 "mocked_response": {"status": "ok"},
                 "would_have_invoked": "POST https://mail.internal/send",
             }],
         }},
    ]


def _item(**over):
    item = {"kind": "scheduled", "job_spec": JOB_SPEC, "expected_output": "sent"}
    item.update(over)
    return item


# --- The run body: the job spec IS the production scheduled shape -----------------

def test_job_spec_is_fed_as_input_payload_and_trigger_payload():
    item = _item(expected_side_effects=[{"tool": "send_email", "occurs": "exactly", "count": 1}])
    client = _FakeClient(steps=_recorded_steps())
    asyncio.run(main._run_scheduled_item(client, item, 0, "durable"))
    body = client.run_body()
    assert body["input_payload"] == JOB_SPEC
    assert body["trigger_payload"] == JOB_SPEC
    assert body["trigger_type"] == "schedule"
    assert body["execution_shape"] == "durable"
    assert body["agent_name"] == "nightly-compliance"


def test_driving_message_matches_the_production_scheduled_door():
    """internal.py: `message = payload.get("message") or json.dumps(payload)`."""
    assert main._scheduled_driving_message({"message": "run it"}) == "run it"
    assert main._scheduled_driving_message(JOB_SPEC) == __import__("json").dumps(JOB_SPEC)
    assert main._scheduled_driving_message({}) == ""


# --- eval_mode=record iff the item asserts side effects ---------------------------

def test_eval_mode_is_record_when_the_item_asserts_side_effects():
    item = _item(expected_side_effects=[{"tool": "send_email", "occurs": "exactly", "count": 1}])
    client = _FakeClient(steps=_recorded_steps())
    asyncio.run(main._run_scheduled_item(client, item, 0, "durable"))
    assert client.run_body()["eval_mode"] == "record"


def test_eval_mode_is_live_when_the_item_asserts_nothing():
    client = _FakeClient(steps=[
        {"step_number": 1, "name": "agent", "status": "completed",
         "output": {"tool": "get_report", "args": {}}},
    ])
    asyncio.run(main._run_scheduled_item(client, _item(), 0, "durable"))
    assert client.run_body()["eval_mode"] == "live"


def test_a_never_assertion_still_runs_in_record_mode():
    """`never` asserts an ABSENCE — but the run must still be prevented from really
    delivering while we check, so it runs recorded, not live."""
    item = _item(expected_side_effects=[{"tool": "send_email", "occurs": "never"}])
    client = _FakeClient(steps=[
        {"step_number": 1, "name": "agent", "status": "completed",
         "output": {"tool": "get_report", "args": {}}},
    ])
    out = asyncio.run(main._run_scheduled_item(client, item, 0, "durable"))
    assert client.run_body()["eval_mode"] == "record"
    # An empty recording SATISFIES `never` — it must not be fail-closed.
    assert out["record"]["dimension_scores"] is not None


# --- Fail-closed paths (never a silent pass) --------------------------------------

def test_unknown_inner_shape_fails_closed_without_firing():
    client = _FakeClient()
    out = asyncio.run(main._run_scheduled_item(client, _item(), 0, None))
    assert out["passed"] is False and out["score"] == 0.0
    assert out["record"]["dimension_scores"] is None
    assert not client.created_a_run(), "must not fire a run with an unknown inner shape"


def test_reactive_inner_asserting_side_effects_fails_closed_before_firing():
    """The record seam is armed only on the durable /run dispatch. A reactive-inner
    record request would be silently ignored and DELIVER for real — so the run must
    never be created."""
    item = _item(expected_side_effects=[{"tool": "send_email", "occurs": "exactly", "count": 1}])
    client = _FakeClient()
    out = asyncio.run(main._run_scheduled_item(client, item, 0, "reactive"))
    assert out["passed"] is False
    assert out["record"]["dimension_scores"] is None
    assert not client.created_a_run(), "a reactive-inner record item must never fire"
    assert "DELIVER" in out["record"]["judge_reasoning"]


def test_empty_trajectory_fails_closed():
    client = _FakeClient(steps=[])
    out = asyncio.run(main._run_scheduled_item(client, _item(), 0, "durable"))
    assert out["passed"] is False and out["record"]["dimension_scores"] is None
    assert "empty trajectory" in out["record"]["judge_reasoning"]


def test_non_terminal_poll_fails_closed():
    client = _FakeClient(steps=_recorded_steps(), run_status="running")
    out = asyncio.run(main._run_scheduled_item(client, _item(), 0, "durable"))
    assert out["passed"] is False and out["record"]["dimension_scores"] is None
    assert "terminal" in out["record"]["judge_reasoning"]


def test_required_recording_with_nothing_recorded_fails_closed():
    """The item asserts a delivery, the record run recorded none ⇒ unverifiable."""
    item = _item(expected_side_effects=[{"tool": "send_email", "occurs": "exactly", "count": 1}])
    client = _FakeClient(steps=[
        {"step_number": 1, "name": "agent", "status": "completed",
         "output": {"tool": "get_report", "args": {}}},
    ])
    out = asyncio.run(main._run_scheduled_item(client, item, 0, "durable"))
    assert out["passed"] is False and out["record"]["dimension_scores"] is None
    assert "recorded none" in out["record"]["judge_reasoning"]


def test_score_door_unavailable_fails_closed():
    client = _FakeClient(steps=_recorded_steps(), score_status=501)
    out = asyncio.run(main._run_scheduled_item(client, _item(), 0, "durable"))
    assert out["passed"] is False and out["record"]["dimension_scores"] is None
    assert "door unavailable" in out["record"]["judge_reasoning"]


def test_every_fail_closed_row_still_carries_the_job_spec():
    """`trigger_payload` is the evidence of WHAT was fired — a failed row without it
    is unreadable in the results UI."""
    client = _FakeClient(steps=[])
    out = asyncio.run(main._run_scheduled_item(client, _item(), 0, "durable"))
    assert out["record"]["trigger_payload"] == JOB_SPEC


# --- The scored row ---------------------------------------------------------------

def test_scored_row_records_dims_detail_run_id_and_trigger_payload():
    item = _item(expected_side_effects=[{"tool": "send_email", "occurs": "exactly", "count": 1}])
    client = _FakeClient(steps=_recorded_steps())
    out = asyncio.run(main._run_scheduled_item(client, item, 0, "durable"))
    rec = out["record"]
    assert out["passed"] is True and out["score"] == 0.9
    assert rec["dimension_scores"] == {"response": 1.0, "side_effect": 1.0}
    assert rec["eval_detail"]["job_spec"] == JOB_SPEC
    assert rec["run_id"] == "run-1"
    assert rec["trigger_payload"] == JOB_SPEC


def test_score_call_sends_mode_scheduled_with_the_real_projections():
    item = _item(expected_side_effects=[{"tool": "send_email", "occurs": "exactly", "count": 1}])
    client = _FakeClient(steps=_recorded_steps())
    asyncio.run(main._run_scheduled_item(client, item, 0, "durable"))
    score_body = next(b for u, b in client.posts if u.endswith("/eval/score"))
    assert score_body["mode"] == "scheduled"
    # E-1's projection, off the SAME real run_steps.
    assert score_body["actual_trajectory"] == main._project_trajectory(_recorded_steps())
    # E-2's projection, off the SAME real run_steps.
    assert score_body["recorded_side_effects"] == main._project_recorded_side_effects(_recorded_steps())
    assert score_body["recorded_side_effects"][0]["tool"] == "send_email"


def test_reactive_inner_omits_actual_trajectory_the_doors_inner_shape_signal():
    """A reactive-inner schedule leaves no run_steps — the door reads the ABSENCE of
    `actual_trajectory` as its explicit reactive-inner signal (an empty list would
    read as a durable run that did nothing)."""
    async def _drive(_client, _run_id, _idx):
        return "the report is sent"

    orig = main._drive_reactive_run
    main._drive_reactive_run = _drive
    try:
        client = _FakeClient()
        asyncio.run(main._run_scheduled_item(client, _item(), 0, "reactive"))
    finally:
        main._drive_reactive_run = orig
    assert client.run_body()["execution_shape"] == "reactive"
    score_body = next(b for u, b in client.posts if u.endswith("/eval/score"))
    assert "actual_trajectory" not in score_body


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

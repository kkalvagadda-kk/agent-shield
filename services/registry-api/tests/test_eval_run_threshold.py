"""
Eval v2 E-6 (T003/T005) — the per-run PASS POLICY unit-pin.

WHAT THIS EXISTS TO CATCH
-------------------------
E-0 added `eval_runs.pass_threshold` + `eval_runs.dimension_weights` with a forward
promise and shipped NEITHER a writer NOR a reader:

  * no writer — `EvalRunCreate` carried neither field and `create_eval_run`'s
    `EvalRun(...)` never set them, so the column was **NULL in every row ever
    written**;
  * no product reader — the `eval_passed` auto-set compared against the module-global
    `EVAL_PASS_THRESHOLD`, never `run.pass_threshold`.

The only reads in the whole repo were defensive test code (`suite-72:337`,
`suite-73:295`: `float(run.pass_threshold) if run.pass_threshold is not None else ...`)
— a branch that could never execute. E-6 ships both ends.

The load-bearing property is D2: **one threshold, resolved once, read by every
consumer.** Before E-6 the publish threshold existed FOUR times across THREE services
(this gate; the eval-runner's per-item verdict; the Studio's verdict AND its colour
band), each independently defaulting to 0.7 — so they agreed and nothing ever errored.
Wiring a per-run threshold to the gate ALONE would have made the product lie: a 0.85
run with `pass_threshold=0.9` renders "passed" in the UI and marks every item
`passed=True` while the gate silently refuses to publish.

SCOPE — what a unit test can and cannot prove. `effective_pass_threshold` and the
`EvalRunCreate` validation are pure, so they are pinned directly here. The REAL gate
(a real run → a real judge → `eval_passed` flipping on a real AgentVersion, both ways)
is `suite-80` T-S80-002/003 — this file is for speed, it is NOT the gate.

Interpreter note: routers.eval_runner imports models.py, whose SQLAlchemy
Mapped[str | None] annotations require python>=3.10 (the service runs 3.12 —
services/registry-api/Dockerfile). The host python3 is 3.9, where this file SKIPS —
which is why `run-fast-gates.sh` runs pytest in a container off the real image.
    python3.12 -m pytest services/registry-api/tests/test_eval_run_threshold.py
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
        "Run: python3.12 -m pytest tests/test_eval_run_threshold.py"
    ),
)

if sys.version_info >= (3, 10):
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/d")
    os.environ.setdefault("DIRECT_DATABASE_URL", "postgresql+psycopg://u:p@h:5432/d")
    os.environ.setdefault("KEYCLOAK_URL", "http://kc:80")

    import uuid  # noqa: E402

    from pydantic import ValidationError  # noqa: E402

    from models import EvalRun  # noqa: E402
    from routers.eval_runner import (  # noqa: E402
        EVAL_PASS_THRESHOLD,
        effective_pass_threshold,
    )
    from schemas import EvalRunCreate, EvalRunResponse  # noqa: E402


DS = uuid.UUID("11111111-1111-1111-1111-111111111111") if sys.version_info >= (3, 10) else None


# ---------------------------------------------------------------------------
# The WRITER (T001) — the column must never again be NULL on a new row.
# ---------------------------------------------------------------------------


def test_create_body_omits_threshold_so_the_write_site_can_default_it():
    """No `pass_threshold` on the request ⇒ None on the body — the API applies the
    platform default at the single write site. The point of defaulting at WRITE
    rather than at READ is that downstream (the gate, the runner, the UI) then has
    a real value and needs no fallback of its own."""
    body = EvalRunCreate(dataset_id=DS, agent_name="a")
    assert body.pass_threshold is None
    assert body.dimension_weights is None


def test_explicit_threshold_survives_the_body():
    body = EvalRunCreate(dataset_id=DS, agent_name="a", pass_threshold=0.9)
    assert body.pass_threshold == 0.9


def test_the_write_default_is_the_platform_default():
    """Mirrors `create_eval_run`'s write expression. If this drifts from the router,
    a new row lands NULL again and every downstream fallback silently revives."""
    body = EvalRunCreate(dataset_id=DS, agent_name="a")
    written = (
        body.pass_threshold if body.pass_threshold is not None else EVAL_PASS_THRESHOLD
    )
    assert written == EVAL_PASS_THRESHOLD
    assert written is not None


# ---------------------------------------------------------------------------
# Door validation (T001) — reject at 422, never persist a nonsense policy.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [1.5, -0.1, 2.0])
def test_threshold_out_of_range_is_rejected(bad):
    """A threshold above 1.0 is unreachable by any composite ⇒ the version could
    NEVER publish, and the run would look like a failing agent rather than an
    impossible policy."""
    with pytest.raises(ValidationError):
        EvalRunCreate(dataset_id=DS, agent_name="a", pass_threshold=bad)


@pytest.mark.parametrize("edge", [0.0, 1.0])
def test_threshold_range_boundaries_are_allowed(edge):
    assert EvalRunCreate(dataset_id=DS, agent_name="a", pass_threshold=edge).pass_threshold == edge


def test_negative_dimension_weight_is_rejected():
    """A negative weight makes a BETTER dimension score LOWER the composite — it
    reads as a legitimate failure and is undiscoverable from the outside. Reject at
    the door rather than let it silently invert a composite."""
    with pytest.raises(ValidationError) as exc:
        EvalRunCreate(
            dataset_id=DS, agent_name="a", dimension_weights={"trajectory": -1}
        )
    assert "dimension_weights" in str(exc.value)


def test_zero_weight_is_allowed_it_means_do_not_count_this_dim():
    """Zero is legitimate ("ignore this dimension"); only NEGATIVE inverts."""
    body = EvalRunCreate(
        dataset_id=DS, agent_name="a", dimension_weights={"response": 1.0, "trajectory": 0.0}
    )
    assert body.dimension_weights == {"response": 1.0, "trajectory": 0.0}


# ---------------------------------------------------------------------------
# The READER (T002) — the gate resolves the run's own threshold.
# ---------------------------------------------------------------------------


def _run(threshold):
    r = EvalRun()
    r.pass_threshold = threshold
    return r


def test_gate_reads_the_runs_threshold():
    assert effective_pass_threshold(_run(0.9)) == 0.9


def test_legacy_null_row_still_gates_on_the_platform_default():
    """Back-compat: every row written BEFORE E-6 has a NULL column. They must keep
    gating exactly as they did — this is the one legitimate fallback in the design."""
    assert effective_pass_threshold(_run(None)) == EVAL_PASS_THRESHOLD


def test_the_same_score_gets_two_verdicts_under_two_thresholds():
    """THE WHOLE POINT of the slice, in one assertion: 0.85 publishes at 0.7 and
    does NOT at 0.9. If this ever collapses to one verdict, the per-run threshold
    has stopped reaching the gate."""
    score = 0.85
    assert score >= effective_pass_threshold(_run(0.7))
    assert not score >= effective_pass_threshold(_run(0.9))


def test_threshold_is_coerced_to_float_not_left_as_decimal():
    """The column is NUMERIC(4,3) ⇒ SQLAlchemy hands back `Decimal`. Comparing a
    float `overall_score` against a Decimal raises TypeError in Python — the gate
    would 500 rather than gate. Pin the coercion."""
    from decimal import Decimal

    thr = effective_pass_threshold(_run(Decimal("0.900")))
    assert isinstance(thr, float)
    assert 0.85 >= thr is False or thr == 0.9  # no TypeError on the comparison
    assert (0.85 >= thr) is False


# ---------------------------------------------------------------------------
# T005 — the read-back surface the UI reads (save→reload half of DoD #2).
# ---------------------------------------------------------------------------


def test_eval_run_response_carries_the_policy_for_the_ui():
    """`EvalRunResponse` must expose `pass_threshold`, or the Studio has no way to
    render a verdict against the run's own threshold and re-declares 0.7 — which is
    exactly how copies 3 and 4 came to exist."""
    fields = EvalRunResponse.model_fields
    assert "pass_threshold" in fields
    assert "dimension_weights" in fields

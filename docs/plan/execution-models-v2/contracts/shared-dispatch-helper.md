# Contract — shared durable-dispatch helper + shape-aware triggered dispatch

This is the **parity core** of WS-0 and the retro's root-cause fix. The durable-run `/run` POST
lives in **one** module both the sandbox (`playground.py`) and production (`internal.py`) paths import.
Grep proves no divergent copy (plan Task T8 parity assertion).

---

## New module: `services/registry-api/durable_dispatch.py`

```python
"""Shared durable-run dispatch — the ONE place a durable run is handed to the
declarative-runner's /run endpoint. Both the sandbox playground path and the
production internal-run path call this; the only per-caller differences (the
step-update callback URL and which run-status table to mark failed) are explicit
parameters. Parity rule: sandbox-production-parity-architecture.md."""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def default_runner_url() -> str:
    return os.getenv(
        "DECLARATIVE_RUNNER_URL",
        "http://declarative-runner.agentshield-platform.svc.cluster.local:8080",
    )


def registry_internal_base() -> str:
    return os.getenv(
        "REGISTRY_API_INTERNAL_URL",
        "http://registry-api.agentshield-platform.svc.cluster.local:8000",
    )


async def dispatch_durable_run(
    *,
    run_id: str,
    agent_name: str,
    input_payload: dict | None,
    callback_url: str,
    runner_url: str | None = None,
    timeout_s: float = 10.0,
) -> tuple[bool, str | None]:
    """POST a durable run to the declarative-runner /run. Returns (accepted, error).

    Never raises — a dispatch failure returns (False, "<reason>") so the caller can mark
    ITS OWN run row failed (PlaygroundRun for sandbox, AgentRun for production). Fire-and-forget:
    step progress + terminal status arrive asynchronously at `callback_url`.
    """
    url = f"{(runner_url or default_runner_url()).rstrip('/')}/run"
    body = {
        "agent_name": agent_name,
        "run_id": run_id,
        "input_payload": input_payload or {},
        "callback_url": callback_url,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=body)
        if resp.status_code in (200, 201, 202):
            logger.info("dispatch_durable_run: accepted run=%s agent=%s -> %s", run_id, agent_name, url)
            return True, None
        err = f"runner returned {resp.status_code}: {resp.text[:200]}"
        logger.warning("dispatch_durable_run: %s (run=%s)", err, run_id)
        return False, err
    except Exception as exc:  # network / pod not ready
        logger.error("dispatch_durable_run: failed run=%s: %s", run_id, exc)
        return False, f"dispatch failed: {exc}"
```

### Caller 1 — sandbox (`routers/playground.py`): refactor `_dispatch_durable_run` to a thin wrapper
Body becomes: build the playground callback URL, call the shared helper, mark `PlaygroundRun` failed on
`(False, err)`. **No behavior change** for the playground UI (the `/run` POST + failure marking are identical
to today; only the POST logic moved into `durable_dispatch`).
```python
async def _dispatch_durable_run(run_id, agent_name, input_payload, db):
    from durable_dispatch import dispatch_durable_run, registry_internal_base
    callback = f"{registry_internal_base()}/api/v1/playground/runs/{run_id}/step-update"
    ok, err = await dispatch_durable_run(
        run_id=run_id, agent_name=agent_name, input_payload=input_payload, callback_url=callback)
    if not ok:
        async with AsyncSessionLocal() as s:
            run = (await s.execute(select(PlaygroundRun).where(PlaygroundRun.id == uuid.UUID(run_id)))).scalar_one_or_none()
            if run:
                run.status = "failed"; run.completed_at = datetime.now(tz=timezone.utc)
                await s.commit()
```

### Caller 2 — production (`routers/internal.py`): shape-aware `_dispatch_and_complete`
Thread the resolved `execution_shape` in as an **explicit parameter** (no re-query inside the helper —
No-Bandaid rule):
```python
async def _dispatch_and_complete(run_id, agent_name, team, message, execution_shape, input_payload,
                                 trigger_id=None):
    if execution_shape == "durable":
        from durable_dispatch import dispatch_durable_run, registry_internal_base
        callback = f"{registry_internal_base()}/api/v1/internal/runs/{run_id}/step-update"
        ok, err = await dispatch_durable_run(
            run_id=run_id, agent_name=agent_name, input_payload=input_payload, callback_url=callback)
        if not ok:
            # mark the AgentRun failed + fire failure alert (reuse existing block)
            await _mark_agent_run_failed(run_id, err, agent_name, trigger_id)
        # durable success: run stays 'running'; the callback completes it. Return here.
        return
    # reactive: EXISTING synchronous /chat path (unchanged) — POST /chat, record completed/failed.
    ...
```
`start_internal_run` (`internal.py:199`) resolves `agent.execution_shape` (already loads the `Agent` at
`:208`) and passes it + `effective_payload` into `_dispatch_and_complete`.

## New endpoint — production step-update callback (the durable-branch producer, not orphaned)

`POST /api/v1/internal/runs/{run_id}/step-update` in `routers/internal.py` — the production twin of
`playground.py:284`. Writes `RunStep` against the **AgentRun** and completes the run on the terminal step.

Request body (posted by declarative-runner `_execute_durable_run`):
```json
{ "step_number": 2, "step_name": "agent_execution", "status": "completed|running|failed|awaiting_approval",
  "output": {"...": "..."}, "output_text": "final text", "run_completed": true,
  "error_message": null, "approval_id": null }
```
Behavior (mirror the sandbox callback, but target `AgentRun` + `RunStep`):
- Upsert `RunStep(run_id=<AgentRun.id>, step_number, name=step_name, status, output, error_message)`
  respecting `UniqueConstraint(run_id, step_number)`.
- `status == "awaiting_approval"` → set `AgentRun.status = "awaiting_approval"`.
- `run_completed` truthy → set `AgentRun.status = <status>`, `completed_at = now`, `output = output_text`.
- Return `{"status":"ok"}`. 404 if the `AgentRun` is missing; 422 on a non-UUID `run_id`.

> **WS-1 extends here, does not replace:** WS-1 swaps the declarative-runner's 2-step skeleton for real
> per-node steps and adds HITL-park emit — those arrive through this **same** callback + `run_steps` ledger.
> WS-0 only needs the branch + the callback wired so `run_steps` rows appear for a production durable run.

---

## M6 — reactive workflow = awaited + capped; durable = background (`internal.py:_start_workflow_run`)

```python
REACTIVE_WORKFLOW_TIMEOUT_S = float(os.getenv("WORKFLOW_REACTIVE_TIMEOUT_S", "120"))

# ... after the AgentRun parent row is committed (internal.py ~:151):
if wf.execution_shape == "reactive":
    # Synchronous, hold the caller's connection, hard wall-clock cap. Skip the orchestrator pod.
    try:
        await asyncio.wait_for(
            orchestrate(str(run.id), wf.team, str(wf.id), message, wf.orchestration, shape="reactive"),
            timeout=REACTIVE_WORKFLOW_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        from workflow_orchestrator import _fail_parent
        await _fail_parent(str(run.id),
            f"reactive workflow exceeded {REACTIVE_WORKFLOW_TIMEOUT_S:.0f}s wall-clock cap")
    # re-read the final row so the response carries output/status
    await db.refresh(run)
    return run
# durable: existing background path (orchestrator pod, else create_task), shape="durable"
...
asyncio.create_task(orchestrate(str(run.id), wf.team, str(wf.id), message, wf.orchestration, shape="durable"))
```

## S2 — runtime fail-closed on an approval gate in a reactive workflow (`workflow_orchestrator.py`)

Thread an explicit `shape` parameter (default `"durable"`, so all existing callers are unchanged) and
route every `awaiting_approval` branch through ONE new helper — no scattered conditionals:

```python
async def _park_or_fail(parent_run_id, mode, team, workflow_id, shape) -> None:
    """Durable → checkpoint + park (existing behavior). Reactive → fail-closed with a clear message.
    Fail-closed is the authoritative S2 seam (a reactive run can't durably park for async approval)."""
    if shape == "reactive":
        await _fail_parent(parent_run_id,
            "approval gate hit in a reactive workflow — set shape=durable to allow approvals")
        logger.info("workflow %s (%s, reactive): approval gate → fail-closed", parent_run_id, mode)
        return
    await _halt_for_approval(parent_run_id, mode, team, workflow_id)   # durable, unchanged
```

- `orchestrate(..., mode, shape="durable")` passes `shape` to `orchestrate_conditional/handoff/supervisor`
  and `orchestrate_graph_sequential`.
- Non-sequential (`:467,513,564,577`): replace `_halt_for_approval(...)` with `_park_or_fail(..., shape)`.
- Sequential (`_run_sequential_from`, `:383`): add `shape` param; in the `awaiting_approval` branch, if
  `shape == "reactive"` call `_fail_parent(...)` and return instead of the checkpoint block.
- **Durable behavior is byte-for-byte unchanged** (park + the deferred non-sequential auto-advance stay as-is;
  WS-1/D3 finishes auto-advance). WS-0 only adds the reactive-fail arm.

### Fail-loud + fail-closed (retro gate #4)
`_park_or_fail` for reactive **denies the run** (marks it `failed` with a diagnostic message) — it never
swallows the gate and proceeds. That is the fail-closed contract; a reactive workflow can never silently
run a tool that should have been approved.

## Golden-path / parity acceptance (bash suite-54)

- **Shape branch (production):** deploy a durable agent, fire `POST /api/v1/internal/runs/start` →
  assert the run gets `RunStep` rows (dispatch hit `/run`). Deploy a reactive agent, fire → assert
  **no** `RunStep` rows and the run has an `output` (dispatch hit `/chat`).
- **Parity assertion (grep):** exactly one `/run` POST implementation.
  `grep -rn 'dispatch_durable_run' services/registry-api/routers/playground.py services/registry-api/routers/internal.py`
  → both files call it; `grep -rn '"/run"' services/registry-api/routers/{playground,internal}.py` → the
  raw POST literal appears **only** inside `durable_dispatch.py` (0 divergent copies in the routers).
- **Reactive workflow fail-closed (S2):** a reactive workflow whose member trips an approval gate →
  parent run `failed`, `error_message` contains "set shape=durable", caller not blocked.
- **Fails-not-skips:** the suite `exit 1`s (not skip) if the durable agent cannot be created/deployed —
  a broken fixture is a failure, per the retro.

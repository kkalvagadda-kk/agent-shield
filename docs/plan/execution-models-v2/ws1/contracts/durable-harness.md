# WS-1 Contract — the shared durable harness (`agentshield_sdk/durable.py`)

The single durable engine, built once, consumed by declarative-runner (`/run`) and SDK (`/run`). It must
**not** import registry-api — it POSTs to a callback URL passed in. This is the parity core; there is exactly
one copy.

## Types

```python
@dataclass
class StepUpdate:
    step_number: int
    step_name: str                 # the LangGraph node/tool name (real, not "agent_execution")
    status: str                    # "running" | "completed" | "failed" | "awaiting_approval"
    output: dict | None = None
    output_text: str | None = None
    run_completed: bool = False
    error_message: str | None = None
    approval_id: str | None = None # set on awaiting_approval after the callback creates the Approval

@dataclass
class RunResult:
    status: str                    # "completed" | "failed" | "awaiting_approval"
    thread_id: str
    steps_emitted: int
```

## `StepEmitter`

```python
class StepEmitter:
    def __init__(self, callback_url: str, http: httpx.AsyncClient, *, bookmark: Bookmark | None = None): ...
    async def emit(self, upd: StepUpdate) -> dict:
        """POST upd to callback_url (/internal/runs/{id}/step-update or /playground/runs/{id}/step-update).
        Idempotent: if bookmark.last_completed_step >= upd.step_number and status=='completed', skip the POST
        (a mid-run pod restart already wrote it). Returns the callback's JSON (may include approval_id)."""
```

## `run_durable` — the drive loop

```python
async def run_durable(
    graph,                 # compiled LangGraph with a PostgresSaver checkpointer
    input: dict,
    *,
    thread_id: str,
    callback_url: str,
    emitter: StepEmitter,
) -> RunResult:
    """
    Drive graph.astream_events(input, config={'configurable': {'thread_id': thread_id}}) and, at each node/
    tool boundary, emitter.emit(StepUpdate(step_name=<node>, status='running'/'completed'/'failed', ...)).

    On interrupt() (OPA require_approval surfaced as a LangGraph interrupt):
      1. emitter.emit(StepUpdate(status='awaiting_approval', step_name=<node>, ...))
         → the callback creates an Approval and returns {'approval_id': ...}.
      2. FAIL-CLOSED: if the callback errors or omits approval_id, emit status='failed' with the full signal
         and return RunResult('failed') — NEVER return 'completed' or silently proceed (bug 009 guard).
      3. Otherwise return RunResult('awaiting_approval', thread_id) — the graph state is durably parked in
         PostgresSaver; the process may exit.
    On normal end: emitter.emit(run_completed=True); return RunResult('completed').
    """
```

## `resume_durable` — re-entry after a decision (or after a crash)

```python
async def resume_durable(graph, *, thread_id: str, decision: str | None, callback_url: str,
                         emitter: StepEmitter) -> RunResult:
    """
    Re-enter from the PostgresSaver checkpoint keyed by thread_id.
      - decision != None  → an approval was decided; pass Command(resume=decision) into the interrupted node.
      - decision == None   → crash recovery (_resume_interrupted_runs): continue from the last checkpoint
                             with no new input.
    Continue the astream_events drive loop exactly as run_durable, emitting steps, until completion or the
    next interrupt. Same fail-closed contract.
    """
```

## Callback endpoints (both already/soon exist — WS-0 built the production one)

| Consumer | callback_url | Row table | Notes |
|---|---|---|---|
| production (internal.py) | `/api/v1/internal/runs/{run_id}/step-update` | `RunStep`(AgentRun) | WS-0 built the happy path; WS-1 adds `awaiting_approval`→Approval + park. |
| sandbox (playground.py) | `/api/v1/playground/runs/{run_id}/step-update` | `PlaygroundRun` step | Unchanged behavior; same emitter. |

**The only difference between the two consumers is the callback_url + which row table the registry-api side
writes** — both call the identical `run_durable`. Any divergence is a parity violation (suite-55 greps for a
single `run_durable` definition and zero mirrored drive loops).

## Idempotency bookmark

```python
@dataclass
class Bookmark:               # declarative-runner/checkpoint.py, reduced from a full checkpoint
    run_id: str
    last_completed_step: int  # the only field that survives; graph state lives in PostgresSaver
```

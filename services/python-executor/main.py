"""
Python Executor — sandboxed Python code runner for AgentShield.

POST /execute accepts {code, args, timeout_ms}, forks a subprocess,
executes run_tool(args) defined in the user-supplied code, and returns
{result, error}. Hard-kills subprocess on timeout.

GET /health returns 200 for liveness probes.
"""
from __future__ import annotations

import logging
import multiprocessing
import textwrap
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="python-executor", version="0.1.0")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    code: str = Field(..., description="Python source defining run_tool(args: dict) -> str")
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(10_000, ge=100, le=60_000)


class ExecuteResponse(BaseModel):
    result: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Subprocess worker
# ---------------------------------------------------------------------------

def _run_in_subprocess(code: str, args: dict, result_queue: multiprocessing.Queue) -> None:
    """Run user code in an isolated subprocess. Result sent via queue."""
    try:
        namespace: dict = {}
        exec(compile(code, "<tool>", "exec"), namespace)  # noqa: S102

        if "run_tool" not in namespace:
            result_queue.put({"error": "Code must define a run_tool(args: dict) -> str function"})
            return

        result = namespace["run_tool"](args)
        result_queue.put({"result": str(result)})
    except Exception as exc:  # noqa: BLE001
        result_queue.put({"error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest) -> ExecuteResponse:
    timeout_s = req.timeout_ms / 1000.0

    # Normalise indentation so tools pasted from editors with leading whitespace work.
    code = textwrap.dedent(req.code)

    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    proc = ctx.Process(target=_run_in_subprocess, args=(code, req.args, result_queue))

    try:
        proc.start()
        proc.join(timeout=timeout_s)

        if proc.is_alive():
            proc.kill()
            proc.join()
            logger.warning("python-executor: subprocess timed out after %dms", req.timeout_ms)
            return ExecuteResponse(error=f"Execution timed out after {req.timeout_ms}ms")

        if result_queue.empty():
            return ExecuteResponse(error="Subprocess exited without returning a result")

        outcome: dict = result_queue.get_nowait()
        return ExecuteResponse(**outcome)

    except Exception as exc:  # noqa: BLE001
        logger.exception("python-executor: unexpected error")
        return ExecuteResponse(error=f"Executor error: {exc}")
    finally:
        if proc.is_alive():
            proc.kill()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

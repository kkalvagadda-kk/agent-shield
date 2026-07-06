"""
Run executor for durable production runs.

Manages step lifecycle: creates RunStep rows via registry-api as
the workflow progresses through its graph nodes. Handles:
  - Step transitions (pending → running → completed/failed)
  - HITL approval links (sets approval_id on step)
  - Completion callbacks to update the parent AgentRun
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

import config as cfg

logger = logging.getLogger(__name__)


@dataclass
class StepState:
    step_number: int
    name: str
    status: str = "pending"
    started_ms: int = 0
    output: dict[str, Any] | None = None
    approval_id: str | None = None
    error_message: str | None = None


@dataclass
class RunExecutor:
    """Tracks and reports step transitions for a durable production run."""

    run_id: str
    agent_name: str
    registry_url: str = field(default_factory=lambda: cfg.REGISTRY_API_URL)
    steps: list[StepState] = field(default_factory=list)
    _start_ms: int = field(default_factory=lambda: int(time.perf_counter() * 1000))

    async def begin_step(self, step_number: int, name: str) -> None:
        step = StepState(step_number=step_number, name=name, status="running", started_ms=int(time.perf_counter() * 1000))
        self.steps.append(step)
        await self._post_step(step)

    async def complete_step(self, step_number: int, output: dict[str, Any] | None = None) -> None:
        step = self._find(step_number)
        if step:
            step.status = "completed"
            step.output = output
            await self._post_step(step)

    async def fail_step(self, step_number: int, error: str) -> None:
        step = self._find(step_number)
        if step:
            step.status = "failed"
            step.error_message = error
            await self._post_step(step)

    async def await_approval(self, step_number: int, approval_id: str) -> None:
        step = self._find(step_number)
        if step:
            step.status = "awaiting_approval"
            step.approval_id = approval_id
            await self._post_step(step)

    async def complete_run(self, status: str = "completed", output: str | None = None) -> None:
        elapsed = int(time.perf_counter() * 1000) - self._start_ms
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.patch(
                    f"{self.registry_url}/api/v1/agent-runs/{self.run_id}",
                    json={
                        "status": status,
                        "output": (output[:4000] if output else None),
                        "latency_ms": elapsed,
                    },
                )
        except Exception as exc:
            logger.warning("RunExecutor.complete_run failed for %s: %s", self.run_id, exc)

    def _find(self, step_number: int) -> StepState | None:
        for s in self.steps:
            if s.step_number == step_number:
                return s
        return None

    async def _post_step(self, step: StepState) -> None:
        """Report step state to registry-api (upsert via callback endpoint)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self.registry_url}/api/v1/agent-runs/{self.run_id}/steps",
                    json={
                        "step_number": step.step_number,
                        "name": step.name,
                        "status": step.status,
                        "output": step.output,
                        "approval_id": step.approval_id,
                        "error_message": step.error_message,
                    },
                )
        except Exception as exc:
            logger.warning("RunExecutor._post_step failed for run=%s step=%d: %s", self.run_id, step.step_number, exc)

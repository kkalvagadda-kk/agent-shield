"""
WorkflowOrchestrator — target-state composite-workflow orchestration (Decision 22).

This module is the FUTURE home of workflow orchestration (research doc Option A:
a declarative-runner pod per composite workflow, activated via
COMPOSITE_WORKFLOW_ID). It is written here so the logic exists once and can be
promoted without a rewrite when supervisor/handoff modes and the deploy-controller
workflow-pod wiring land.

⚠ MVP NOTE: for Phase W3 the ACTIVE sequential dispatch runs as a background task
INSIDE registry-api (services/registry-api/workflow_orchestrator.py, research doc
Option C). This class is not on the MVP execution path — the /workflow-run
endpoint returns 404 unless COMPOSITE_WORKFLOW_ID is set, which the platform does
not set until the deferred deploy-controller extension exists.
"""
from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger("declarative-runner.orchestrator")


class WorkflowOrchestrator:
    def __init__(self, workflow_id: str, parent_run_id: str, registry_url: str) -> None:
        self.workflow_id = workflow_id
        self.parent_run_id = parent_run_id
        self.registry_url = registry_url.rstrip("/")

    async def _create_child_run(self, agent_name: str, team: str, input_msg: str) -> str | None:
        """Create a child AgentRun (parent_run_id → this workflow run) via registry-api."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.post(
                    f"{self.registry_url}/api/v1/internal/runs/start",
                    json={
                        "agent_name": agent_name,
                        "trigger_type": "workflow",
                        "run_by": f"workflow:{self.workflow_id}",
                        "trigger_payload": {"message": input_msg, "parent_run_id": self.parent_run_id},
                    },
                )
            return r.json().get("id") if r.status_code in (200, 201) else None
        except Exception as exc:
            logger.error("create child run failed for %s: %s", agent_name, exc)
            return None

    async def _dispatch_agent(self, agent_name: str, team: str, input_msg: str) -> tuple[str, str | None]:
        ns = f"agents-{(team or 'platform').lower().replace(' ', '-')}"
        url = f"http://{agent_name}-production.{ns}.svc.cluster.local:8080/chat"
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(url, json={"message": input_msg})
            if r.status_code == 200:
                data = r.json()
                return "completed", (data.get("output") or data.get("response") or json.dumps(data))
            return "failed", None
        except Exception as exc:
            logger.error("dispatch %s failed: %s", agent_name, exc)
            return "failed", None

    async def run_sequential(self, members: list[dict], input_payload: dict) -> None:
        """Iterate members (each {'agent_name','team','position'}) in order, threading output→input."""
        current = input_payload.get("message", "") if isinstance(input_payload, dict) else str(input_payload)
        for member in sorted(members, key=lambda m: (m.get("position") is None, m.get("position") or 0)):
            status, output = await self._dispatch_agent(member["agent_name"], member.get("team", ""), current)
            if status == "failed":
                logger.warning("workflow %s: member %s failed (fail-fast)", self.workflow_id, member["agent_name"])
                return
            current = output or ""
        logger.info("workflow %s sequential run complete", self.workflow_id)

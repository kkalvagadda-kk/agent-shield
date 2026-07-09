"""
WorkflowOrchestrator — composite-workflow orchestration (Decision 22).

Runs as a dedicated pod per composite workflow, activated via
COMPOSITE_WORKFLOW_ID env var set by the deploy-controller.
Dispatches to member agent production pods and manages parent/child
run lifecycle via registry-api internal endpoints.
"""
from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger("declarative-runner.orchestrator")


class WorkflowOrchestrator:
    def __init__(
        self, workflow_id: str, parent_run_id: str, registry_url: str, team: str = ""
    ) -> None:
        self.workflow_id = workflow_id
        self.parent_run_id = parent_run_id
        self.registry_url = registry_url.rstrip("/")
        self.team = team

    async def _mark_parent_status(self, status: str, output: str | None = None) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                body: dict = {"status": status}
                if output is not None:
                    body["output"] = output
                await c.patch(
                    f"{self.registry_url}/api/v1/agent-runs/{self.parent_run_id}",
                    json=body,
                )
        except Exception as exc:
            logger.error("failed to update parent run %s status: %s", self.parent_run_id, exc)

    async def _create_child_run(self, agent_name: str, team: str, input_msg: str) -> str | None:
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
        ns = f"agents-{(team or self.team or 'platform').lower().replace(' ', '-')}"
        url = f"http://{agent_name}-production.{ns}.svc.cluster.local:8080/chat"
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.post(url, json={"message": input_msg})
            if r.status_code == 200:
                data = r.json()
                return "completed", (data.get("output") or data.get("response") or json.dumps(data))
            return "failed", f"HTTP {r.status_code}"
        except Exception as exc:
            logger.error("dispatch %s failed: %s", agent_name, exc)
            return "failed", str(exc)

    async def run_sequential(self, members: list[dict], input_payload: dict) -> None:
        """Iterate members in order, threading output to input. Reports parent run status."""
        await self._mark_parent_status("running")

        current = input_payload.get("message", "") if isinstance(input_payload, dict) else str(input_payload)
        sorted_members = sorted(members, key=lambda m: (m.get("position") is None, m.get("position") or 0))

        for member in sorted_members:
            agent_name = member["agent_name"]
            team = member.get("team", self.team or "")

            await self._create_child_run(agent_name, team, current)
            status, output = await self._dispatch_agent(agent_name, team, current)

            if status == "failed":
                logger.warning("workflow %s: member %s failed (fail-fast)", self.workflow_id, agent_name)
                await self._mark_parent_status("failed", f"Member {agent_name} failed: {output}")
                return

            current = output or ""

        logger.info("workflow %s sequential run complete", self.workflow_id)
        await self._mark_parent_status("completed", current)

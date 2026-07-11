"""Shared machine-identity registration for the deploy reconcilers.

For an agent pod's tool calls to be governed by OPA, its ServiceAccount subject
must be registered so the bundle generator can key `data.agents` on it. Both the
sandbox reconciler (`reconciler.py`) and the production reconciler
(`production_reconciler.py`) must do this. It used to live inline in the sandbox
path only — production pods were never registered, so their SA subject never
entered the OPA bundle and every production tool call failed closed
(agent_unauthenticated). Extracting it here lets both paths call the same code so
they can't drift again (see docs/design/sandbox-production-parity-architecture.md).
"""
import logging

import httpx

from config import Settings

logger = logging.getLogger(__name__)


async def register_agent_identity(
    agent_name: str,
    sa_subject: str,
    sa_namespace: str,
    settings: Settings,
    deployment_id: str | None = None,
    production_deployment_id: str | None = None,
) -> None:
    """Best-effort POST /api/v1/agents/{name}/identities.

    Exactly one of ``deployment_id`` (sandbox) / ``production_deployment_id``
    (production) should be set — they map to the two FK columns on
    ``agent_identities``. Failure is logged, not raised: the bundle re-derives
    from the DB on the next ~30s poll, and a crash here must not fail the deploy.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.registry_api_url, timeout=10.0
        ) as http:
            await http.post(
                f"/api/v1/agents/{agent_name}/identities",
                json={
                    "sa_subject": sa_subject,
                    "sa_namespace": sa_namespace,
                    "deployment_id": deployment_id,
                    "production_deployment_id": production_deployment_id,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to register identity for agent '%s' in Registry API: %s — "
            "bundle generator will sync on next deploy.",
            agent_name,
            exc,
        )

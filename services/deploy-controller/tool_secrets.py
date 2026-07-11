"""Shared tool-credential resolution for the deploy reconcilers.

Both the sandbox reconciler (`reconciler.py`) and the production reconciler
(`production_reconciler.py`) must copy an agent's tool-credential K8s Secrets
(e.g. the Serper API key) into the pod's namespace and expose them via `envFrom`.
This logic used to live inline in the sandbox path only — production pods shipped
without tool credentials, so every external-API tool call 401'd ("authentication
issue with the search tool"). Extracting it here lets both paths call the same
code so they can't drift again.
"""
import asyncio
import logging

import httpx

from config import Settings
from k8s_client import K8sClient

logger = logging.getLogger(__name__)


async def resolve_and_copy_tool_secrets(
    agent_name: str, namespace: str, k8s: K8sClient, settings: Settings
) -> list[str]:
    """Resolve the agent's tool-credential secret refs and copy them into ``namespace``.

    Returns the list of ``k8s_secret_ref`` names for ``envFrom`` injection into the
    agent container. Best-effort: any resolution/copy failure is logged and skipped
    (a missing credential surfaces later as a tool auth error, not a crash-loop).
    """
    tool_secret_refs: list[str] = []
    try:
        async with httpx.AsyncClient(
            base_url=settings.registry_api_url, timeout=10.0
        ) as http:
            tools_resp = await http.get(f"/api/v1/agents/{agent_name}/tools")
            if tools_resp.status_code == 200:
                tools_data = tools_resp.json().get("items", [])
                seen_config_ids: set[str] = set()
                for tool in tools_data:
                    ac_id = tool.get("auth_config_id")
                    if not ac_id or ac_id in seen_config_ids:
                        continue
                    seen_config_ids.add(ac_id)
                    ref_resp = await http.get(f"/api/v1/auth-configs/{ac_id}/secret-ref")
                    if ref_resp.status_code == 200:
                        secret_ref = ref_resp.json().get("k8s_secret_ref")
                        if secret_ref:
                            tool_secret_refs.append(secret_ref)
    except Exception as exc:
        logger.warning("Failed to fetch tool auth configs for %s: %s", agent_name, exc)

    loop = asyncio.get_event_loop()
    for secret_ref in tool_secret_refs:
        try:
            await loop.run_in_executor(
                None,
                lambda ref=secret_ref: k8s.copy_secret(
                    ref, settings.platform_namespace, namespace
                ),
            )
        except Exception as exc:
            logger.warning("Failed to copy secret %s to %s: %s", secret_ref, namespace, exc)

    return tool_secret_refs

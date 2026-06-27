import asyncio
import base64
import json
import logging
import time

import httpx

from config import Settings
from k8s_client import K8sClient
from manifest_builder import build_deployment, build_service, build_httproute

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SECONDS = 60
_POLL_INTERVAL_SECONDS = 5


async def _fetch_workflow(http: httpx.AsyncClient, workflow_id: str) -> dict | None:
    """Fetch the workflow definition from the Registry API.

    Args:
        http:        httpx.AsyncClient already pointed at registry_api_url.
        workflow_id: The workflow UUID to fetch.

    Returns:
        The parsed workflow JSON dict, or None on error.
    """
    try:
        resp = await http.get(f"/api/v1/workflows/{workflow_id}")
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Failed to fetch workflow %s: %s", workflow_id, exc)
        return None


async def reconcile(
    deployment: dict,
    agent: dict,
    version: dict,
    k8s: K8sClient,
    settings: Settings,
) -> tuple[str, str | None, str | None]:
    """
    Reconcile a single pending Deployment record.

    Returns:
        (new_status, k8s_deployment_name, error_message)
        new_status is "running" on success or "failed" on timeout / error.
    """
    agent_name = agent["name"]
    environment = deployment["environment"]
    namespace = deployment["k8s_namespace"]
    k8s_deployment_name = f"{agent_name}-{environment}"
    policy_cm_name = f"{agent_name}-policy"

    loop = asyncio.get_event_loop()

    try:
        # --- Declarative agent handling ---
        # If the agent type is "declarative", fetch the workflow definition from
        # the Registry API, base64-encode it, and inject it as WORKFLOW_JSON.
        # Also override the image tag with the declarative-runner image.
        if agent.get("agent_type") == "declarative":
            workflow_id: str | None = version.get("workflow_id")
            if not workflow_id:
                error_msg = (
                    f"Declarative agent {agent_name} version has no workflow_id — "
                    "cannot deploy without a workflow definition."
                )
                logger.error(error_msg)
                return ("failed", k8s_deployment_name, error_msg)

            async with httpx.AsyncClient(
                base_url=settings.registry_api_url, timeout=10.0
            ) as http:
                workflow = await _fetch_workflow(http, workflow_id)

            if workflow is None:
                error_msg = (
                    f"Could not fetch workflow {workflow_id} for agent {agent_name}. "
                    "See logs for details."
                )
                logger.error(error_msg)
                return ("failed", k8s_deployment_name, error_msg)

            # Base64-encode the workflow definition JSON so it is safe to inject
            # as a Kubernetes env var (avoids quoting and length issues with raw JSON).
            workflow_json_b64 = base64.b64encode(
                json.dumps(workflow).encode()
            ).decode()

            # Override the image tag with the declarative-runner image.
            version = dict(version)  # shallow copy to avoid mutating the caller's dict
            version["image_tag"] = settings.declarative_runner_image

            # Signal to build_deployment to inject WORKFLOW_JSON env var.
            deployment = dict(deployment)
            deployment["workflow_json_b64"] = workflow_json_b64

            logger.info(
                "Declarative agent %s: using declarative-runner image, "
                "injecting WORKFLOW_JSON for workflow %s",
                agent_name,
                workflow_id,
            )

        # 1. Build the K8s manifest
        manifest = build_deployment(
            deployment, agent, version, settings.opa_image, settings.registry_api_url
        )

        # 2. Ensure the OPA policy ConfigMap exists (empty default so pod doesn't crashloop)
        await loop.run_in_executor(
            None,
            lambda: k8s.create_configmap_if_missing(
                namespace,
                policy_cm_name,
                {"default.rego": "package main\n\ndefault allow = true\n"},
            ),
        )

        # 3. Apply (create or update) the Deployment
        await loop.run_in_executor(
            None,
            lambda: k8s.create_or_update_deployment(namespace, manifest),
        )

        # 4. Ensure a ClusterIP Service exists so Envoy can route to the agent pod
        team = agent.get("team", "platform")
        labels = {
            "app.kubernetes.io/name": agent_name,
            "agentshield.io/team": team,
            "agentshield.io/environment": environment,
        }
        svc_manifest = build_service(agent_name, environment, namespace, labels)
        await loop.run_in_executor(
            None,
            lambda: k8s.create_or_update_service(namespace, svc_manifest),
        )

        # 5. Create / update the Envoy HTTPRoute (best-effort — Envoy Gateway may not be
        #    deployed in all environments; 403/404 logged as warning, not a fatal error)
        httproute_manifest = build_httproute(
            agent_name=agent_name,
            environment=environment,
            namespace=namespace,
            team=team,
        )
        try:
            await loop.run_in_executor(
                None,
                lambda: k8s.apply_httproute("agentshield-platform", httproute_manifest),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HTTPRoute creation skipped for %s (Envoy Gateway may not be installed): %s",
                agent_name,
                exc,
            )

        # 6. Poll until at least 1 replica is available (up to _POLL_TIMEOUT_SECONDS)
        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            available = await loop.run_in_executor(
                None,
                lambda: k8s.get_deployment_available_replicas(namespace, k8s_deployment_name),
            )
            if available >= 1:
                logger.info(
                    "Deployment %s/%s is running (%d available replicas)",
                    namespace,
                    k8s_deployment_name,
                    available,
                )
                return ("running", k8s_deployment_name, None)

            logger.debug(
                "Waiting for Deployment %s/%s to become available ...",
                namespace,
                k8s_deployment_name,
            )
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        # Timed out
        error_msg = (
            f"Deployment {namespace}/{k8s_deployment_name} did not become available "
            f"within {_POLL_TIMEOUT_SECONDS}s"
        )
        logger.error(error_msg)
        return ("failed", k8s_deployment_name, error_msg)

    except Exception as exc:  # noqa: BLE001
        error_msg = f"Reconcile error for {k8s_deployment_name}: {exc}"
        logger.exception(error_msg)
        return ("failed", k8s_deployment_name, error_msg)

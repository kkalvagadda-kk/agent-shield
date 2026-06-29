"""
Lazy per-user playground ServiceAccount creation.
Called on first playground run for a given user.

Follows the same pattern as deploy-controller/k8s_client.py:ensure_service_account().
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_PLAYGROUND_NAMESPACE = "agentshield-playground"


def _ensure_playground_sa_sync(username: str) -> str:
    """Synchronous core: create playground-runner-{username}-sa in agentshield-playground if absent."""
    import kubernetes
    from kubernetes import client as k8s_client
    from kubernetes.client.exceptions import ApiException

    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()

    core_v1 = k8s_client.CoreV1Api()
    sa_name = f"playground-runner-{username}-sa"
    namespace = _PLAYGROUND_NAMESPACE

    try:
        core_v1.read_namespaced_service_account(name=sa_name, namespace=namespace)
        logger.debug("Playground SA %s/%s already exists", namespace, sa_name)
    except ApiException as e:
        if e.status != 404:
            raise
        sa = k8s_client.V1ServiceAccount(
            metadata=k8s_client.V1ObjectMeta(
                name=sa_name,
                namespace=namespace,
                labels={
                    "agentshield.io/managed-by": "registry-api",
                    "agentshield.io/playground-user": username,
                },
            )
        )
        core_v1.create_namespaced_service_account(namespace=namespace, body=sa)
        logger.info("Created playground SA %s/%s", namespace, sa_name)

    return sa_name


async def ensure_playground_sa(username: str) -> str:
    """Create playground-runner-{username}-sa in agentshield-playground if not exists.

    Runs the sync Kubernetes client in a thread pool so it doesn't block the
    async event loop. Returns sa_name regardless (idempotent).
    """
    try:
        sa_name = await asyncio.to_thread(_ensure_playground_sa_sync, username)
        return sa_name
    except Exception as exc:
        logger.warning(
            "ensure_playground_sa: could not ensure SA for user=%s: %s",
            username, exc,
        )
        return f"playground-runner-{username}-sa"

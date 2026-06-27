"""
Kubernetes client helpers for the Registry API.

Used only for writing K8s Secrets at agent deploy time (LLM provider
credentials). The registry-api pod must have a ServiceAccount with
Role: create/update Secrets in the agentshield-platform namespace.
"""

import asyncio
import logging

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

_k8s_initialized = False


def _init_k8s() -> None:
    global _k8s_initialized
    if _k8s_initialized:
        return
    try:
        config.load_incluster_config()
        logger.info("k8s: loaded in-cluster config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("k8s: loaded kube config (local dev)")
    _k8s_initialized = True


def _upsert_secret_sync(name: str, namespace: str, data: dict[str, str]) -> None:
    _init_k8s()
    v1 = client.CoreV1Api()
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels={"app.kubernetes.io/managed-by": "agentshield-registry-api"},
        ),
        string_data=data,
    )
    try:
        v1.create_namespaced_secret(namespace, secret)
        logger.info("k8s: created secret %s/%s", namespace, name)
    except ApiException as exc:
        if exc.status == 409:
            v1.replace_namespaced_secret(name, namespace, secret)
            logger.info("k8s: updated secret %s/%s", namespace, name)
        else:
            raise


async def upsert_secret(name: str, namespace: str, data: dict[str, str]) -> None:
    """Create or update a K8s Secret (runs sync k8s client in a thread)."""
    await asyncio.to_thread(_upsert_secret_sync, name, namespace, data)


def apply_configmap(namespace: str, name: str, data: dict[str, str]) -> None:
    """Create or replace a K8s ConfigMap (synchronous — call from asyncio.to_thread if needed)."""
    _init_k8s()
    v1 = client.CoreV1Api()
    cm = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels={"app.kubernetes.io/managed-by": "agentshield-registry-api"},
        ),
        data=data,
    )
    try:
        v1.create_namespaced_config_map(namespace, cm)
        logger.info("k8s: created configmap %s/%s", namespace, name)
    except ApiException as exc:
        if exc.status == 409:
            v1.replace_namespaced_config_map(name, namespace, cm)
            logger.info("k8s: updated configmap %s/%s", namespace, name)
        else:
            raise

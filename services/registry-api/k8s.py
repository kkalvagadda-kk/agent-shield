"""
Kubernetes client helpers for the Registry API.

Permissions required on the registry-api ServiceAccount:
  - Secrets:    create, update  in agentshield-platform  (LLM credential secrets)
  - ConfigMaps: create, update  in agentshield-platform  (OPA policy ConfigMaps)
  - Jobs:       create, get     in agentshield-platform  (eval-runner batch jobs)
"""

import asyncio
import logging
import os

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

_k8s_initialized = False

_PLATFORM_NAMESPACE = "agentshield-platform"
_REGISTRY_API_URL = os.getenv(
    "REGISTRY_API_URL",
    "http://agentshield-registry-api.agentshield-platform:8000",
)
_EVAL_RUNNER_IMAGE = os.getenv(
    "EVAL_RUNNER_IMAGE",
    "registry.internal/agentshield/eval-runner:0.1.4",
)


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


def _secret_exists_sync(name: str, namespace: str) -> bool:
    _init_k8s()
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_secret(name=name, namespace=namespace)
        return True
    except ApiException as exc:
        if exc.status == 404:
            return False
        # Any OTHER API error (403, timeout, …) is NOT evidence of absence. Raising
        # keeps the caller from "healing" a secret that is actually there, or from
        # reporting a phantom as present — either way, never infer from an error we
        # did not ask about.
        raise


async def secret_exists(name: str, namespace: str) -> bool:
    """True iff the Secret exists. 404 ⇒ False; any other API error raises."""
    return await asyncio.to_thread(_secret_exists_sync, name, namespace)


def _delete_secret_sync(name: str, namespace: str) -> None:
    _init_k8s()
    v1 = client.CoreV1Api()
    try:
        v1.delete_namespaced_secret(name, namespace)
        logger.info("k8s: deleted secret %s/%s", namespace, name)
    except ApiException as exc:
        if exc.status == 404:
            logger.warning("k8s: secret %s/%s already gone", namespace, name)
        else:
            raise


async def delete_secret(name: str, namespace: str) -> None:
    """Delete a K8s Secret (runs sync k8s client in a thread)."""
    await asyncio.to_thread(_delete_secret_sync, name, namespace)


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


def _create_eval_job_sync(
    eval_run_id: str,
    agent_name: str,
    dataset_id: str,
    workflow_id: str | None = None,
    agent_version_id: str | None = None,
    mode: str = "reactive",
) -> None:
    """Create a K8s batch Job that runs the eval-runner container (synchronous)."""
    _init_k8s()
    batch_v1 = client.BatchV1Api()

    job_name = f"eval-{eval_run_id[:8]}"

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=_PLATFORM_NAMESPACE,
            labels={
                "app.kubernetes.io/name": "eval-runner",
                "app.kubernetes.io/managed-by": "agentshield-registry-api",
                "agentshield.io/eval-run-id": eval_run_id,
            },
        ),
        spec=client.V1JobSpec(
            backoff_limit=1,
            ttl_seconds_after_finished=3600,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={
                        "app.kubernetes.io/name": "eval-runner",
                        "agentshield.io/eval-run-id": eval_run_id,
                    }
                ),
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    containers=[
                        client.V1Container(
                            name="eval-runner",
                            image=_EVAL_RUNNER_IMAGE,
                            env=[
                                e for e in [
                                    client.V1EnvVar(name="EVAL_RUN_ID", value=eval_run_id),
                                    client.V1EnvVar(name="AGENT_NAME", value=agent_name),
                                    client.V1EnvVar(name="DATASET_ID", value=dataset_id),
                                    client.V1EnvVar(name="REGISTRY_API_URL", value=_REGISTRY_API_URL),
                                    # Eval v2 E-0: the runner's interpretation mode
                                    # (resolved from the executable == dataset.mode).
                                    client.V1EnvVar(name="MODE", value=mode),
                                    client.V1EnvVar(name="WORKFLOW_ID", value=workflow_id) if workflow_id else None,
                                    client.V1EnvVar(name="AGENT_VERSION_ID", value=agent_version_id) if agent_version_id else None,
                                ] if e is not None
                            ],
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": "100m", "memory": "256Mi"},
                                limits={"cpu": "500m", "memory": "512Mi"},
                            ),
                        )
                    ],
                ),
            ),
        ),
    )

    try:
        batch_v1.create_namespaced_job(_PLATFORM_NAMESPACE, job)
        logger.info("k8s: created eval-runner job %s/%s", _PLATFORM_NAMESPACE, job_name)
    except ApiException as exc:
        if exc.status == 409:
            logger.warning("k8s: eval-runner job %s already exists — skipping", job_name)
        else:
            raise


async def create_eval_job(
    eval_run_id: str,
    agent_name: str,
    dataset_id: str,
    workflow_id: str | None = None,
    agent_version_id: str | None = None,
    mode: str = "reactive",
) -> None:
    """Create a K8s eval-runner Job (runs sync k8s client in a thread)."""
    await asyncio.to_thread(_create_eval_job_sync, eval_run_id, agent_name, dataset_id, workflow_id, agent_version_id, mode)

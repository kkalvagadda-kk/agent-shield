"""
Production deployment reconciler — watches production_deployments via the
catalog internal API and provisions/upgrades/suspends K8s workloads from
config_snapshot data (independent of the sandbox deployments path).
"""
import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from config import Settings
from k8s_client import K8sClient
from manifest_builder import build_deployment, build_service

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_SECONDS = 120
_POLL_INTERVAL_SECONDS = 5


def _build_agent_dict(dep_info: dict) -> dict:
    """Synthesize an agent-like dict from config_snapshot for manifest_builder."""
    config = dep_info.get("config_snapshot", {})
    return {
        "name": dep_info["artifact_name"],
        "team": dep_info["artifact_team"],
        "agent_type": config.get("agent_type", "declarative"),
        "agent_class": config.get("agent_class", "user_delegated"),
        "execution_shape": config.get("execution_shape", "reactive"),
    }


def _build_version_dict(dep_info: dict, settings: Settings) -> dict:
    """Synthesize a version-like dict from config_snapshot for manifest_builder."""
    config = dep_info.get("config_snapshot", {})
    return {
        "image_tag": settings.declarative_runner_image,
        "version_number": dep_info.get("version_label", "v1"),
        "tool_snapshot": config.get("tools", []),
        "eval_passed": True,
    }


def _build_deployment_dict(dep_info: dict) -> dict:
    """Synthesize a deployment-like dict for manifest_builder."""
    config = dep_info.get("config_snapshot", {})
    namespace = dep_info.get("namespace") or f"production-{dep_info['artifact_name']}"

    deployment = {
        "id": dep_info["id"],
        "environment": "production",
        "k8s_namespace": namespace,
        "replicas": 1,
    }

    # If config has workflow definition, encode as base64 for WORKFLOW_JSON env
    workflow_def = config.get("workflow_definition")
    if workflow_def:
        deployment["workflow_json_b64"] = base64.b64encode(
            json.dumps(workflow_def).encode()
        ).decode()

    # LLM provider config (injected by internal endpoint)
    if dep_info.get("llm_secret_name"):
        deployment["llm_secret_name"] = dep_info["llm_secret_name"]
        deployment["llm_env_keys"] = dep_info.get("llm_env_keys", [])
        deployment["llm_provider_type"] = dep_info.get("llm_provider_type")
        deployment["llm_provider_model"] = dep_info.get("llm_provider_model")

    return deployment


async def _patch_status(
    http: httpx.AsyncClient, deployment_id: str, status: str
) -> None:
    try:
        resp = await http.patch(
            f"/api/v1/catalog/internal/production-deployments/{deployment_id}/status",
            json={"status": status},
        )
        resp.raise_for_status()
        logger.info("Production deployment %s → %s", deployment_id, status)
    except Exception as exc:
        logger.error("Failed to patch production deployment %s: %s", deployment_id, exc)


async def reconcile_production(
    dep_info: dict, k8s: K8sClient, settings: Settings
) -> tuple[str, str | None]:
    """Reconcile a single production deployment. Returns (new_status, error_message)."""
    agent_dict = _build_agent_dict(dep_info)
    version_dict = _build_version_dict(dep_info, settings)
    deployment_dict = _build_deployment_dict(dep_info)

    agent_name = agent_dict["name"]
    namespace = deployment_dict["k8s_namespace"]
    k8s_deployment_name = f"{agent_name}-production"

    loop = asyncio.get_event_loop()

    try:
        # Ensure namespace exists
        await loop.run_in_executor(
            None, lambda: k8s.ensure_namespace(namespace)
        )

        # Ensure ServiceAccount exists (pod spec references it)
        await loop.run_in_executor(
            None, lambda: k8s.ensure_service_account(agent_name, namespace)
        )

        # Ensure LLM credentials secret exists in the production namespace
        llm_credentials = dep_info.get("llm_credentials")
        llm_secret_name = dep_info.get("llm_secret_name")
        if llm_credentials and llm_secret_name:
            await loop.run_in_executor(
                None, lambda: k8s.ensure_secret(llm_secret_name, namespace, llm_credentials)
            )

        # Ensure OPA sidecar ConfigMap exists
        await loop.run_in_executor(
            None, lambda: k8s.ensure_opa_configmap(namespace)
        )

        # Build and apply K8s Deployment (add restart annotation to force clean rollout)
        manifest = build_deployment(
            deployment_dict, agent_dict, version_dict,
            settings.opa_image, settings.registry_api_url
        )
        restart_ts = datetime.now(timezone.utc).isoformat()
        tmpl_meta = manifest.spec.template.metadata
        if tmpl_meta.annotations is None:
            tmpl_meta.annotations = {}
        tmpl_meta.annotations["kubectl.kubernetes.io/restartedAt"] = restart_ts
        await loop.run_in_executor(
            None, lambda: k8s.create_or_update_deployment(namespace, manifest)
        )

        # Ensure Service
        labels = {
            "app.kubernetes.io/name": agent_name,
            "agentshield.io/team": agent_dict["team"],
            "agentshield.io/environment": "production",
        }
        svc_manifest = build_service(agent_name, "production", namespace, labels)
        await loop.run_in_executor(
            None, lambda: k8s.create_or_update_service(namespace, svc_manifest)
        )

        # Poll until ready
        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            available = await loop.run_in_executor(
                None,
                lambda: k8s.get_deployment_available_replicas(namespace, k8s_deployment_name),
            )
            if available >= 1:
                logger.info(
                    "Production deployment %s/%s running (%d replicas)",
                    namespace, k8s_deployment_name, available,
                )
                return ("running", None)
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        return ("failed", f"Timed out waiting for {namespace}/{k8s_deployment_name}")

    except Exception as exc:
        error_msg = f"Production reconcile error for {agent_name}: {exc}"
        logger.exception(error_msg)
        return ("failed", error_msg)


async def production_poll_loop(settings: Settings) -> None:
    """Poll for pending production deployments and reconcile them."""
    k8s = K8sClient()
    logger.info("Production reconciler starting")

    async with httpx.AsyncClient(
        base_url=settings.registry_api_url, timeout=30.0
    ) as http:
        while True:
            try:
                resp = await http.get("/api/v1/catalog/internal/pending-deployments")
                resp.raise_for_status()
                pending = resp.json()

                if pending:
                    logger.info("Found %d pending production deployment(s)", len(pending))

                for dep_info in pending:
                    dep_id = dep_info["id"]
                    dep_status = dep_info.get("status", "pending")
                    agent_name = dep_info.get("artifact_name", "unknown")
                    namespace = dep_info.get("namespace") or f"production-{agent_name}"
                    k8s_deployment_name = f"{agent_name}-production"

                    if dep_status == "suspending":
                        logger.info("Suspending %s/%s (scale to 0)", namespace, k8s_deployment_name)
                        try:
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None, lambda: k8s.scale_deployment(namespace, k8s_deployment_name, 0)
                            )
                            await _patch_status(http, dep_id, "suspended")
                        except Exception as exc:
                            logger.warning("Scale-down failed for %s: %s", dep_id, exc)
                            await _patch_status(http, dep_id, "failed")
                        continue

                    if dep_status == "terminating":
                        logger.info("Terminating %s/%s (delete resources)", namespace, k8s_deployment_name)
                        try:
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None, lambda: k8s.delete_deployment(namespace, k8s_deployment_name)
                            )
                            await loop.run_in_executor(
                                None, lambda: k8s.delete_service(namespace, k8s_deployment_name)
                            )
                            await _patch_status(http, dep_id, "terminated")
                        except Exception as exc:
                            logger.warning("Terminate cleanup failed for %s: %s", dep_id, exc)
                            await _patch_status(http, dep_id, "terminated")
                        continue

                    # Mark deploying
                    await _patch_status(http, dep_id, "deploying")

                    # Route based on artifact type
                    artifact_type = dep_info.get("artifact_type", "agent")
                    if artifact_type == "workflow":
                        new_status, error_msg = await reconcile_workflow_production(
                            dep_info, k8s, settings, http
                        )
                    else:
                        new_status, error_msg = await reconcile_production(
                            dep_info, k8s, settings
                        )

                    if error_msg:
                        logger.warning(
                            "Production deploy %s failed: %s", dep_id, error_msg
                        )

                    # Report final status
                    await _patch_status(http, dep_id, new_status)

            except httpx.RequestError as exc:
                logger.error("Registry API unreachable (production loop): %s", exc)
            except Exception as exc:
                logger.exception("Production poll loop error: %s", exc)

            await asyncio.sleep(settings.poll_interval_seconds)


async def _preflight_check_members(
    dep_info: dict, http: httpx.AsyncClient
) -> str | None:
    """Verify all member agents have active production deployments. Returns error string or None."""
    config = dep_info.get("config_snapshot", {})
    members = config.get("members", [])
    if not members:
        return "Workflow has no members in config_snapshot."
    agent_names = [m["agent_name"] for m in members]
    try:
        resp = await http.post(
            "/api/v1/catalog/internal/verify-members",
            json={"agent_names": agent_names},
        )
        data = resp.json()
        if not data.get("ok"):
            missing = data.get("missing", [])
            return f"Members without production deployments: {', '.join(missing)}. Deploy them first."
    except Exception as exc:
        return f"Pre-flight member check failed: {exc}"
    return None


def _build_workflow_deployment_dict(dep_info: dict) -> dict:
    """Build a deployment dict for a composite workflow orchestrator pod."""
    config = dep_info.get("config_snapshot", {})
    namespace = dep_info.get("namespace") or f"production-{dep_info['artifact_name']}"

    workflow_config = {
        "members": config.get("members", []),
        "edges": config.get("edges", []),
        "orchestration": config.get("orchestration", "sequential"),
        "execution_shape": config.get("execution_shape", "durable"),
    }

    return {
        "id": dep_info["id"],
        "environment": "production",
        "k8s_namespace": namespace,
        "replicas": 1,
        "composite_workflow_id": dep_info["artifact_id"],
        "workflow_config_b64": base64.b64encode(
            json.dumps(workflow_config).encode()
        ).decode(),
    }


async def reconcile_workflow_production(
    dep_info: dict, k8s: K8sClient, settings: Settings, http: httpx.AsyncClient
) -> tuple[str, str | None]:
    """Reconcile a composite workflow production deployment (orchestrator pod)."""
    error = await _preflight_check_members(dep_info, http)
    if error:
        return ("failed", error)

    agent_dict = _build_agent_dict(dep_info)
    version_dict = _build_version_dict(dep_info, settings)
    deployment_dict = _build_workflow_deployment_dict(dep_info)

    agent_name = agent_dict["name"]
    namespace = deployment_dict["k8s_namespace"]
    k8s_deployment_name = f"{agent_name}-production"

    loop = asyncio.get_event_loop()

    try:
        await loop.run_in_executor(None, lambda: k8s.ensure_namespace(namespace))
        await loop.run_in_executor(None, lambda: k8s.ensure_service_account(agent_name, namespace))
        await loop.run_in_executor(None, lambda: k8s.ensure_opa_configmap(namespace))

        manifest = build_deployment(
            deployment_dict, agent_dict, version_dict,
            settings.opa_image, settings.registry_api_url
        )
        restart_ts = datetime.now(timezone.utc).isoformat()
        tmpl_meta = manifest.spec.template.metadata
        if tmpl_meta.annotations is None:
            tmpl_meta.annotations = {}
        tmpl_meta.annotations["kubectl.kubernetes.io/restartedAt"] = restart_ts
        await loop.run_in_executor(None, lambda: k8s.create_or_update_deployment(namespace, manifest))

        labels = {
            "app.kubernetes.io/name": agent_name,
            "agentshield.io/team": agent_dict["team"],
            "agentshield.io/environment": "production",
        }
        svc_manifest = build_service(agent_name, "production", namespace, labels)
        await loop.run_in_executor(None, lambda: k8s.create_or_update_service(namespace, svc_manifest))

        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            available = await loop.run_in_executor(
                None,
                lambda: k8s.get_deployment_available_replicas(namespace, k8s_deployment_name),
            )
            if available >= 1:
                logger.info("Workflow orchestrator %s/%s running", namespace, k8s_deployment_name)
                return ("running", None)
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        return ("failed", f"Timed out waiting for workflow orchestrator {namespace}/{k8s_deployment_name}")

    except Exception as exc:
        error_msg = f"Workflow production reconcile error for {agent_name}: {exc}"
        logger.exception(error_msg)
        return ("failed", error_msg)

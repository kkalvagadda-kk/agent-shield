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


async def _run_preflight_checks(
    http: httpx.AsyncClient,
    deployment: dict,
    agent: dict,
    version: dict,
) -> str | None:
    """Run pre-flight checks before provisioning K8s resources.

    Returns an error message if any check fails, or None if all pass.

    Checks:
        1. Deployer team == agent team (or a cross-team AssetGrant exists)
        2. All tools in the version snapshot have an active grant for the deployer team
        3. Version eval has passed
        4. No critical-risk tool in the snapshot
        5. Agent identity provisioning is handled in reconcile() step 1
    """
    agent_name = agent["name"]
    agent_team = agent.get("team", "")
    deployer_team = deployment.get("deployer_team", agent_team)

    # Check 1: deployer team == agent team (or cross-team grant)
    if deployer_team and deployer_team != agent_team:
        try:
            resp = await http.get(
                "/api/v1/admin/grants",
                params={"asset_id": str(agent["id"]), "grantee_team": deployer_team},
            )
            grants = resp.json().get("items", [])
            active = [g for g in grants if not g.get("revoked_at")]
            if not active:
                return (
                    f"deployer_team={deployer_team} does not own agent "
                    f"team={agent_team} and no cross-team grant exists"
                )
        except Exception as exc:
            return f"grant check failed: {exc}"

    # Check 2: all tools have active grant for deployer's team
    # FAIL CLOSED: grant check errors are blocking, not skipped.
    tool_snapshot = version.get("tool_snapshot") or []
    if tool_snapshot:
        missing: list[str] = []
        for tool in tool_snapshot:
            tool_id = tool.get("id") or tool.get("tool_id")
            if not tool_id:
                continue  # no ID in snapshot — skip grant check for this tool
            try:
                resp = await http.get(
                    "/api/v1/admin/grants",
                    params={"asset_id": str(tool_id), "grantee_team": deployer_team},
                )
                grants = resp.json().get("items", [])
                active = [g for g in grants if not g.get("revoked_at")]
                if not active:
                    missing.append(tool.get("name", str(tool_id)))
            except Exception as exc:
                return f"grant check failed for team {deployer_team}: {exc}"
        if missing:
            return (
                f"tool grants missing for deployer team {deployer_team}: {missing}"
            )

    # Check 3: eval must not have explicitly failed
    # (eval_passed=None means eval not yet run — allowed for now; False = hard block)
    if version.get("eval_passed") is False:
        return "version eval has not passed"

    # Check 4: no critical-risk tool in snapshot
    # Checks both risk_level (registry schema) and risk (older tool snapshot schema)
    if tool_snapshot:
        critical = [
            t.get("name", "")
            for t in tool_snapshot
            if t.get("risk_level") == "critical" or t.get("risk") == "critical"
        ]
        if critical:
            return f"critical risk tools not deployable: {critical}"

    # Check 5: agent identity provisioning — handled in reconcile() step 1
    return None


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

    loop = asyncio.get_event_loop()

    # ── Pre-flight gate ───────────────────────────────────────────────────────
    # Run checks before provisioning any K8s resources. A gate failure marks
    # the deployment 'gate_failed' so the Registry API can surface it to the
    # user without wasting K8s resources.
    async with httpx.AsyncClient(base_url=settings.registry_api_url, timeout=10.0) as http:
        gate_error = await _run_preflight_checks(http, deployment, agent, version)
    if gate_error:
        logger.warning(
            "Pre-flight gate failed for %s: %s", agent_name, gate_error
        )
        return ("gate_failed", None, gate_error)

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

        # 1. Provision per-agent ServiceAccount (Phase 9.1: machine identity)
        #    ensure_service_account is idempotent; the SA subject is recorded in
        #    the Registry API so the OPA bundle data.json can key on it.
        sa_subject = await loop.run_in_executor(
            None,
            lambda: k8s.ensure_service_account(agent_name, namespace),
        )
        logger.info("Agent '%s' SA subject: %s", agent_name, sa_subject)

        # Record the identity in Registry API (best-effort — deploy continues even if
        # this call fails; the bundle generator will pick it up on the next sync)
        deployment_id = deployment.get("id")
        try:
            async with httpx.AsyncClient(
                base_url=settings.registry_api_url, timeout=10.0
            ) as http:
                await http.post(
                    f"/api/v1/agents/{agent_name}/identities",
                    json={
                        "sa_subject": sa_subject,
                        "sa_namespace": namespace,
                        "deployment_id": deployment_id,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to register identity for agent '%s' in Registry API: %s — "
                "bundle generator will sync on next deploy.",
                agent_name,
                exc,
            )

        # 2. Build the K8s manifest
        manifest = build_deployment(
            deployment, agent, version, settings.opa_image, settings.registry_api_url
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

import asyncio
import logging
import signal

import httpx

from config import settings
from k8s_client import K8sClient
from production_reconciler import production_poll_loop
from reconciler import reconcile
from timeout_worker import timeout_worker
from ttl_worker import ttl_worker

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def _fetch_agent(http: httpx.AsyncClient, agent_name: str) -> dict | None:
    try:
        resp = await http.get(f"/api/v1/agents/{agent_name}")
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch agent %s: %s", agent_name, exc)
        return None


async def _fetch_version(http: httpx.AsyncClient, version_id: str) -> dict | None:
    try:
        resp = await http.get(f"/api/v1/versions/{version_id}")
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch version %s: %s", version_id, exc)
        return None


async def _patch_deployment_status(
    http: httpx.AsyncClient,
    deployment_id: str,
    status: str,
    k8s_deployment_name: str | None = None,
    error_message: str | None = None,
) -> None:
    body: dict = {"status": status}
    if k8s_deployment_name is not None:
        body["k8s_deployment_name"] = k8s_deployment_name
    if error_message is not None:
        body["error_message"] = error_message
    try:
        resp = await http.patch(f"/api/v1/deployments/{deployment_id}", json=body)
        resp.raise_for_status()
        logger.info("Patched deployment %s → status=%s", deployment_id, status)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to patch deployment %s: %s", deployment_id, exc)


async def _handle_lifecycle_transitions(http: httpx.AsyncClient, k8s: K8sClient) -> None:
    """Act on deployments the API has moved to a transitional lifecycle state:
    'suspending' → scale to 0 → 'suspended'; 'terminating' → delete → 'terminated'.
    (Resume/Upgrade go back to 'pending' and flow through the normal reconcile.)
    empty environment = all environments."""
    loop = asyncio.get_event_loop()
    for st in ("suspending", "terminating"):
        try:
            resp = await http.get(
                "/api/v1/deployments/",
                params={"status": st, "environment": "", "limit": 50},
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("lifecycle fetch (%s) failed: %s", st, exc)
            continue

        for dep in items:
            dep_id = dep["id"]
            agent_name = dep.get("agent_name") or "unknown"
            environment = dep.get("environment", "sandbox")
            namespace = dep.get("k8s_namespace")
            k8s_name = dep.get("k8s_deployment_name") or f"{agent_name}-{environment}"

            if st == "suspending":
                try:
                    await loop.run_in_executor(
                        None, lambda ns=namespace, kn=k8s_name: k8s.scale_deployment(ns, kn, 0)
                    )
                    await _patch_deployment_status(http, dep_id, "suspended")
                    logger.info("Suspended %s/%s (scaled to 0)", namespace, k8s_name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Suspend failed for %s: %s", dep_id, exc)
            else:  # terminating
                try:
                    await loop.run_in_executor(
                        None, lambda ns=namespace, kn=k8s_name: k8s.delete_deployment(ns, kn)
                    )
                    await loop.run_in_executor(
                        None, lambda ns=namespace, kn=k8s_name: k8s.delete_service(ns, kn)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Terminate cleanup for %s: %s", dep_id, exc)
                await _patch_deployment_status(http, dep_id, "terminated")
                logger.info("Terminated %s/%s (resources deleted)", namespace, k8s_name)


async def _handle_sandbox_running_drift(http: httpx.AsyncClient, k8s: K8sClient) -> None:
    """Detect sandbox deployments the DB says are 'running' but whose k8s
    Deployment object no longer exists (e.g. a cluster restart wiped all pods).

    Sandbox is developer-facing: we do NOT auto-reprovision (the dev may have
    moved on). Instead mark the row 'terminated' with a clear message so the
    fleet UI stops showing "pod unreachable" and the developer can redeploy.
    Only the absence of the Deployment OBJECT triggers this — a healthy agent
    mid-rolling-restart still has its Deployment, so it is never touched.
    """
    loop = asyncio.get_event_loop()
    try:
        resp = await http.get(
            "/api/v1/deployments/",
            params={"status": "running", "environment": "", "limit": 100},
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("running-drift fetch (sandbox) failed: %s", exc)
        return

    for dep in items:
        namespace = dep.get("k8s_namespace")
        k8s_name = dep.get("k8s_deployment_name")
        if not namespace or not k8s_name:
            continue
        try:
            obj = await loop.run_in_executor(
                None, lambda ns=namespace, kn=k8s_name: k8s.get_deployment(ns, kn)
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("drift check failed for %s/%s: %s", namespace, k8s_name, exc)
            continue
        if obj is None:
            await _patch_deployment_status(
                http,
                dep["id"],
                "terminated",
                error_message=(
                    "k8s Deployment missing (cluster restart?) — sandbox is not "
                    "auto-reprovisioned; redeploy to restore."
                ),
            )
            logger.info(
                "Sandbox drift: %s/%s has no Deployment — marked terminated",
                namespace, k8s_name,
            )


async def poll_loop() -> None:
    """Main reconciliation loop. Polls every POLL_INTERVAL_SECONDS."""
    k8s = K8sClient()
    logger.info(
        "Deploy Controller starting — registry=%s interval=%ds",
        settings.registry_api_url,
        settings.poll_interval_seconds,
    )

    async with httpx.AsyncClient(
        base_url=settings.registry_api_url,
        timeout=30.0,
    ) as http:
        while True:
            try:
                # 1. Fetch pending deployments (environment="" = all environments)
                resp = await http.get(
                    "/api/v1/deployments/",
                    params={"status": "pending", "environment": "", "limit": 50},
                )
                resp.raise_for_status()
                payload = resp.json()
                pending = payload.get("items", [])

                if not pending:
                    logger.debug("No pending deployments found")
                else:
                    logger.info("Found %d pending deployment(s)", len(pending))

                for dep in pending:
                    dep_id = dep["id"]
                    agent_id = dep.get("agent_id")
                    version_id = dep.get("version_id")

                    # Mark as deploying immediately so we don't re-pick it on the next poll
                    await _patch_deployment_status(http, dep_id, "deploying")

                    # 2. Resolve agent info.  The Registry stores agent_id as a UUID but the
                    #    GET /api/v1/agents/{name} endpoint expects a name.  Try fetching by
                    #    the name field if present, otherwise fall back to agent_id.
                    agent_name_or_id = dep.get("agent_name") or agent_id
                    agent = await _fetch_agent(http, agent_name_or_id)
                    if agent is None:
                        await _patch_deployment_status(
                            http,
                            dep_id,
                            "failed",
                            error_message=f"Could not fetch agent {agent_name_or_id}",
                        )
                        continue

                    # 3. Resolve version info
                    version = await _fetch_version(http, version_id)
                    if version is None:
                        await _patch_deployment_status(
                            http,
                            dep_id,
                            "failed",
                            error_message=f"Could not fetch version {version_id}",
                        )
                        continue

                    # 4. Reconcile (apply K8s resources and wait for ready)
                    new_status, k8s_name, error_msg = await reconcile(
                        dep, agent, version, k8s, settings
                    )

                    # 5. Update Registry with the final status
                    await _patch_deployment_status(
                        http,
                        dep_id,
                        new_status,
                        k8s_deployment_name=k8s_name,
                        error_message=error_msg,
                    )

                # 6. Handle suspend/terminate transitions (sandbox lifecycle)
                await _handle_lifecycle_transitions(http, k8s)

                # 7. Detect sandbox 'running' rows whose k8s Deployment vanished
                #    (cluster wipe) → mark terminated so the dev can redeploy.
                await _handle_sandbox_running_drift(http, k8s)

            except httpx.RequestError as exc:
                logger.error("Registry API unreachable: %s — will retry next cycle", exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected error in poll loop: %s", exc)

            await asyncio.sleep(settings.poll_interval_seconds)


async def _async_main() -> None:
    """Async entry point: runs poll_loop, timeout_worker, and ttl_worker concurrently."""
    loop = asyncio.get_event_loop()

    # Start background workers
    tw_task = asyncio.create_task(timeout_worker(), name="timeout_worker")
    ttl_task = asyncio.create_task(ttl_worker(), name="ttl_worker")
    poll_task = asyncio.create_task(poll_loop(), name="poll_loop")
    prod_task = asyncio.create_task(production_poll_loop(settings), name="production_poll_loop")

    # Graceful shutdown on SIGTERM
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop.set)

    await stop.wait()
    logger.info("deploy-controller: SIGTERM received — shutting down")
    tw_task.cancel()
    ttl_task.cancel()
    poll_task.cancel()
    prod_task.cancel()
    await asyncio.gather(tw_task, ttl_task, poll_task, prod_task, return_exceptions=True)
    logger.info("deploy-controller: shutdown complete")


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()

import asyncio
import logging
import signal

import httpx

from config import settings
from k8s_client import K8sClient
from production_reconciler import production_poll_loop
from reconciler import reconcile
from timeout_worker import timeout_worker

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
                # 1. Fetch pending deployments
                resp = await http.get(
                    "/api/v1/deployments/",
                    params={"status": "pending", "limit": 50},
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

            except httpx.RequestError as exc:
                logger.error("Registry API unreachable: %s — will retry next cycle", exc)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected error in poll loop: %s", exc)

            await asyncio.sleep(settings.poll_interval_seconds)


async def _async_main() -> None:
    """Async entry point: runs poll_loop and timeout_worker concurrently."""
    loop = asyncio.get_event_loop()

    # Start background workers
    tw_task = asyncio.create_task(timeout_worker(), name="timeout_worker")
    poll_task = asyncio.create_task(poll_loop(), name="poll_loop")
    prod_task = asyncio.create_task(production_poll_loop(settings), name="production_poll_loop")

    # Graceful shutdown on SIGTERM
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop.set)

    await stop.wait()
    logger.info("deploy-controller: SIGTERM received — shutting down")
    tw_task.cancel()
    poll_task.cancel()
    prod_task.cancel()
    await asyncio.gather(tw_task, poll_task, prod_task, return_exceptions=True)
    logger.info("deploy-controller: shutdown complete")


def main() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()

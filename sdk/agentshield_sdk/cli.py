"""
AgentShield CLI — entry point for the ``agentshield`` command.

Commands:
    agentshield dev [--safety] [--port N] [--agent module:variable]
        Start a development server with mock backends.
        --safety: use the real Safety Orchestrator (requires AGENTSHIELD_SAFETY_URL).

    agentshield register
        Register this agent with the Registry API.

    agentshield deploy
        Trigger a deployment via the Registry API.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys

import click

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@click.group()
def app() -> None:
    """AgentShield SDK CLI."""


@app.command()
@click.option(
    "--safety/--no-safety",
    default=False,
    help="Connect to the real Safety Orchestrator (requires AGENTSHIELD_SAFETY_URL).",
)
@click.option("--port", default=8080, show_default=True, help="Port to listen on.")
@click.option(
    "--agent",
    "agent_module",
    default="agent:agent",
    show_default=True,
    help="module:variable path to the Agent instance (e.g. 'agent:agent').",
)
def dev(safety: bool, port: int, agent_module: str) -> None:
    """Start the agent server with mock backends (or real safety with --safety)."""
    # If --no-safety, clear the safety URL so mock_safety is used.
    if not safety:
        os.environ.setdefault("AGENTSHIELD_SAFETY_URL", "")
        os.environ["AGENTSHIELD_SAFETY_URL"] = ""

    # Parse "module:variable" specification.
    if ":" not in agent_module:
        click.echo(
            f"Error: --agent must be in 'module:variable' form (got {agent_module!r})",
            err=True,
        )
        sys.exit(1)

    module_path, variable_name = agent_module.rsplit(":", 1)

    # Add cwd to sys.path so the module can be imported.
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        click.echo(f"Error importing '{module_path}': {exc}", err=True)
        sys.exit(1)

    agent_instance = getattr(mod, variable_name, None)
    if agent_instance is None:
        click.echo(
            f"Error: '{variable_name}' not found in module '{module_path}'", err=True
        )
        sys.exit(1)

    from .agent import Agent

    if not isinstance(agent_instance, Agent):
        click.echo(
            f"Error: '{module_path}:{variable_name}' is not an Agent instance "
            f"(got {type(agent_instance).__name__})",
            err=True,
        )
        sys.exit(1)

    click.echo(
        f"Starting {agent_instance.name} on port {port} "
        f"(safety={'real' if safety else 'mock'})"
    )

    # Wire the runner into the FastAPI server.
    from .runner import Runner
    from . import server

    runner = Runner(agent_instance)

    async def lifespan_setup() -> None:
        await runner.setup()
        server.runner = runner

    asyncio.run(lifespan_setup())

    import uvicorn  # type: ignore[import]

    uvicorn.run(
        "agentshield_sdk.server:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


@app.command()
@click.option(
    "--agent",
    "agent_module",
    default="agent:agent",
    show_default=True,
    help="module:variable path to the Agent instance.",
)
def register(agent_module: str) -> None:
    """Register this agent with the Registry API."""
    import httpx
    from . import config

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    if ":" not in agent_module:
        click.echo(f"Error: --agent must be 'module:variable' (got {agent_module!r})", err=True)
        sys.exit(1)

    module_path, variable_name = agent_module.rsplit(":", 1)
    mod = importlib.import_module(module_path)
    agent_instance = getattr(mod, variable_name)

    payload = {
        "name": agent_instance.name,
        "tools": [
            {"name": t.tool_name, "risk": t.risk, "description": t.__doc__ or ""}
            for t in agent_instance.tools
        ],
    }

    try:
        resp = httpx.post(
            f"{config.AGENTSHIELD_REGISTRY_URL}/api/v1/agents", json=payload
        )
        resp.raise_for_status()
        click.echo(f"Registered: {resp.json()}")
    except Exception as exc:
        click.echo(f"Registration failed: {exc}", err=True)
        sys.exit(1)


@app.command()
@click.option("--image", required=True, help="Container image tag to deploy.")
@click.option(
    "--agent",
    "agent_module",
    default="agent:agent",
    show_default=True,
    help="module:variable path to the Agent instance.",
)
def deploy(image: str, agent_module: str) -> None:
    """Trigger a deployment via the Registry API."""
    import httpx
    from . import config

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    if ":" not in agent_module:
        click.echo(f"Error: --agent must be 'module:variable' (got {agent_module!r})", err=True)
        sys.exit(1)

    module_path, variable_name = agent_module.rsplit(":", 1)
    mod = importlib.import_module(module_path)
    agent_instance = getattr(mod, variable_name)

    # 1. Create a version.
    version_payload = {"image_tag": image, "agent_name": agent_instance.name}
    try:
        resp = httpx.post(
            f"{config.AGENTSHIELD_REGISTRY_URL}/api/v1/agents/{agent_instance.name}/versions",
            json=version_payload,
        )
        resp.raise_for_status()
        version_id = resp.json().get("id")
        click.echo(f"Created version: {version_id}")
    except Exception as exc:
        click.echo(f"Version creation failed: {exc}", err=True)
        sys.exit(1)

    # 2. Trigger deploy.
    try:
        resp = httpx.post(
            f"{config.AGENTSHIELD_REGISTRY_URL}/api/v1/agents/{agent_instance.name}/deploy",
            json={"version_id": version_id},
        )
        resp.raise_for_status()
        click.echo(f"Deploy triggered: {resp.json()}")
    except Exception as exc:
        click.echo(f"Deploy failed: {exc}", err=True)
        sys.exit(1)

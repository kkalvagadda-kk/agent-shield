"""
AgentShield Registry API — application factory.

Startup / shutdown lifecycle
-----------------------------
- On startup: dispose any stale engine connections from a previous process,
  then immediately warm up the pool with a lightweight ``SELECT 1``.
- On shutdown: cleanly dispose the engine pool so connections are returned to
  PgBouncer before the process exits.

Routers mounted
---------------
  /api/v1/agents          — agents CRUD  (Phase 1)
  /api/v1/agents          — versions     (Phase 1, same prefix as agents)
  /api/v1/agents          — deployments  (Phase 1, same prefix as agents)
  /api/v1/workflows       — workflows    (Phase 1)
  /api/v1/approvals       — approvals CRUD     (Phase 2)
  /api/v1/opa-decisions   — OPA audit log      (Phase 2)
  /api/v1/teams           — teams CRUD         (Phase 2)
  /api/v1/tools           — tools CRUD
  /api/v1/auth-configs    — auth-configs CRUD
  /api/v1/agent-tools     — agent-tool bindings
  /api/v1/llm-providers   — LLM provider CRUD (Fernet-encrypted credentials)
  /api/v1/skills          — skills CRUD (canvas redesign)
  /api/v1/playground      — playground runs, approvals, datasets, eval-runner (Phase 9.3/10.x)
  /api/v1/playground      — /runs/{id}/trace, /save-to-dataset, /feedback (Phase G4)
  /api/v1/bundle          — live OPA data.json + policy.rego (bundle server polling)
  /api/v1/agent-runs      — agent run records (observability primitive)

System endpoints
----------------
  GET /health  — liveness probe (always 200 if process is alive)
  GET /ready   — readiness probe (checks DB reachability)
"""

from __future__ import annotations

import logging
import logging.config
import uuid
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from config import settings
from db import AsyncSessionLocal, engine
from routers.admin import router as admin_router
from routers.agent_runs import router as agent_runs_router
from routers.bundle import router as bundle_router
from routers.agents import router as agents_router
from routers.approvals import router as approvals_router
from routers.auth_configs import router as auth_configs_router
from routers.datasets import router as datasets_router
from routers.deployments import global_deployments_router, router as deployments_router
from routers.eval_runner import router as eval_runner_router
from routers.llm_providers import router as llm_providers_router
from routers.opa_decisions import router as opa_decisions_router
from routers.playground import router as playground_router
from routers.playground_approvals import router as playground_approvals_router
from routers.skills import router as skills_router
from routers.teams import router as teams_router
from routers.tools import router as tools_router
from routers.versions import router as versions_router, versions_global_router
from routers.workflows import router as workflows_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

from routers.agent_tools import router as agent_tools_router
from routers.admin_users import router as admin_users_router, teams_router as admin_teams_router


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the SQLAlchemy connection pool across the application lifetime."""
    # --- startup ---
    logger.info(
        "registry-api starting up (log_level=%s, port=%d)",
        settings.log_level,
        settings.port,
    )

    # Dispose any stale connections that may have survived a previous process
    # restart (important when running behind PgBouncer in transaction mode).
    await engine.dispose()

    # Warm up the pool: acquire one connection to verify DB reachability at
    # startup.  Failures are logged but do NOT abort startup — the /ready
    # probe will surface the problem gracefully.
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        logger.info("registry-api: database pool warmed up successfully")
    except Exception as exc:  # pragma: no cover
        logger.warning("registry-api: database warm-up failed: %s", exc)

    yield  # application runs here

    # --- shutdown ---
    logger.info("registry-api shutting down — disposing DB connection pool")
    await engine.dispose()
    logger.info("registry-api: shutdown complete")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AgentShield Registry API",
        description=(
            "Central registry for AI agents, versions, deployments, and workflows "
            "in the AgentShield platform."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- CORS (open for MVP; tighten per-environment later) ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Trace ID propagation middleware ---
    # Reads X-AgentShield-Trace-ID from request; generates one if absent.
    # Injects into request.state and echoes in response header so callers
    # can stitch spans across service boundaries.
    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-AgentShield-Trace-ID") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-AgentShield-Trace-ID"] = trace_id
        return response

    # --- Phase 1 routers ---
    app.include_router(agents_router)
    app.include_router(versions_router)
    app.include_router(versions_global_router)
    app.include_router(deployments_router)
    app.include_router(global_deployments_router)
    app.include_router(workflows_router)

    # --- Phase 2 gap routers ---
    app.include_router(approvals_router)
    app.include_router(opa_decisions_router)
    app.include_router(teams_router)

    # --- Skills router (canvas redesign) ---
    app.include_router(skills_router)

    # --- Tool, auth-config, agent-tool, LLM provider routers ---
    app.include_router(tools_router)
    app.include_router(auth_configs_router)
    app.include_router(agent_tools_router)
    app.include_router(llm_providers_router)
    app.include_router(admin_router)

    # --- Admin user management (Keycloak-backed) ---
    app.include_router(admin_users_router)
    app.include_router(admin_teams_router)

    # --- Agent Runs router (observability primitive) ---
    app.include_router(agent_runs_router)

    # --- Bundle router (live OPA data.json + policy.rego for bundle server) ---
    app.include_router(bundle_router)

    # --- Playground routers (Phase 9.3 + 10.1 + 10.3) ---
    # Note: playground_approvals and playground share the /api/v1/playground prefix
    # Mount playground first (runs endpoints), then approvals, datasets, eval-runner
    app.include_router(playground_router)
    app.include_router(playground_approvals_router)
    app.include_router(datasets_router)
    app.include_router(eval_runner_router)

    # --- System endpoints ---
    @app.get(
        "/health",
        tags=["system"],
        summary="Liveness probe",
        response_model=dict[str, str],
    )
    async def health() -> dict[str, Any]:
        """Returns 200 as long as the process is alive."""
        return {"status": "ok", "service": "registry-api", "version": "0.1.0"}

    @app.get(
        "/ready",
        tags=["system"],
        summary="Readiness probe",
        response_model=dict[str, str],
        responses={
            200: {"description": "Service is ready"},
            503: {"description": "Database unreachable"},
        },
    )
    async def ready(response: Response) -> dict[str, Any]:
        """Checks DB connectivity.  Returns 503 when the DB is unreachable."""
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception as exc:
            logger.error("ready: DB check failed: %s", exc)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable", "detail": str(exc)}

    return app


# ---------------------------------------------------------------------------
# Application instance (used by Gunicorn / uvicorn workers and tests)
# ---------------------------------------------------------------------------
app = create_app()

# ---------------------------------------------------------------------------
# Local development entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)

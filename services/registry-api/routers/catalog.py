"""
Catalog router — production artifact isolation.

  GET    /api/v1/catalog                        — list published artifacts (filtered by grants)
  GET    /api/v1/catalog/{id}                   — artifact detail + versions + deployments
  POST   /api/v1/catalog/{id}/deploy            — deploy a version
  PATCH  /api/v1/catalog/{id}/deployments/{did} — upgrade/suspend/resume
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import (
    AgentRun,
    AssetGrant,
    PublishedArtifact,
    PublishedVersion,
    ProductionDeployment,
)
from schemas import (
    AgentRunResponse,
    CatalogArtifactResponse,
    CatalogDeploymentResponse,
    CatalogDeploymentUpdateRequest,
    CatalogDeployRequest,
    CatalogDetailResponse,
    CatalogVersionResponse,
    MemberTopologyEntry,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])


@router.get("", response_model=list[CatalogArtifactResponse])
async def list_catalog(
    team: str | None = Query(None),
    type_filter: str | None = Query(None, alias="type"),
    db: AsyncSession = Depends(get_db),
    x_user_team: str = Header(default="", alias="X-User-Team"),
) -> list[CatalogArtifactResponse]:
    """List published artifacts visible to the caller's team."""
    q = select(PublishedArtifact)

    if type_filter:
        q = q.where(PublishedArtifact.type == type_filter)

    if team:
        q = q.where(PublishedArtifact.team == team)

    # Filter by grants: show artifacts where caller's team is owner or grantee
    if x_user_team:
        granted_ids = select(AssetGrant.asset_id).where(
            AssetGrant.grantee_team == x_user_team
        )
        q = q.where(
            (PublishedArtifact.team == x_user_team)
            | (PublishedArtifact.id.in_(granted_ids))
        )

    q = q.order_by(PublishedArtifact.updated_at.desc())
    rows = list((await db.execute(q)).scalars().all())

    items: list[CatalogArtifactResponse] = []
    for art in rows:
        # Fetch latest version label
        latest_ver = (await db.execute(
            select(PublishedVersion.version_label)
            .where(PublishedVersion.artifact_id == art.id)
            .order_by(PublishedVersion.promoted_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        # Deployment count
        dep_count = (await db.execute(
            select(func.count(ProductionDeployment.id))
            .where(ProductionDeployment.artifact_id == art.id)
            .where(ProductionDeployment.status.in_(("pending", "deploying", "running")))
        )).scalar() or 0

        resp = CatalogArtifactResponse.model_validate(art)
        resp.latest_version = latest_ver
        resp.deployment_count = dep_count
        items.append(resp)

    return items


# ---------------------------------------------------------------------------
# Fleet-wide deployments view (all production deployments across artifacts)
# ---------------------------------------------------------------------------


@router.get("/deployments", response_model=list[dict])
async def list_all_production_deployments(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all production deployments across all artifacts (fleet view)."""
    q = (
        select(ProductionDeployment, PublishedArtifact.name.label("artifact_name"), PublishedArtifact.type.label("artifact_type"))
        .join(PublishedArtifact, ProductionDeployment.artifact_id == PublishedArtifact.id)
        .order_by(ProductionDeployment.updated_at.desc())
    )
    if status_filter:
        q = q.where(ProductionDeployment.status == status_filter)
    rows = (await db.execute(q.limit(limit).offset(offset))).all()

    version_ids = list({dep.version_id for dep, _, _ in rows})
    ver_map: dict[uuid.UUID, str] = {}
    if version_ids:
        ver_rows = (await db.execute(
            select(PublishedVersion.id, PublishedVersion.version_label)
            .where(PublishedVersion.id.in_(version_ids))
        )).all()
        ver_map = {vid: vlabel for vid, vlabel in ver_rows}

    items = []
    for dep, artifact_name, artifact_type in rows:
        items.append({
            "id": str(dep.id),
            "artifact_id": str(dep.artifact_id),
            "artifact_name": artifact_name,
            "artifact_type": artifact_type,
            "version_id": str(dep.version_id),
            "version_label": ver_map.get(dep.version_id),
            "status": dep.status,
            "namespace": dep.namespace,
            "deployed_at": dep.deployed_at.isoformat() if dep.deployed_at else None,
            "suspended_at": dep.suspended_at.isoformat() if dep.suspended_at else None,
            "updated_at": dep.updated_at.isoformat() if dep.updated_at else None,
        })
    return items


@router.get("/{artifact_id}", response_model=CatalogDetailResponse)
async def get_catalog_detail(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CatalogDetailResponse:
    """Get artifact detail with versions and deployments."""
    art = (await db.execute(
        select(PublishedArtifact).where(PublishedArtifact.id == artifact_id)
    )).scalar_one_or_none()
    if art is None:
        raise HTTPException(status_code=404, detail="Published artifact not found.")

    versions = list((await db.execute(
        select(PublishedVersion)
        .where(PublishedVersion.artifact_id == artifact_id)
        .order_by(PublishedVersion.promoted_at.desc())
    )).scalars().all())

    deployments_raw = list((await db.execute(
        select(ProductionDeployment)
        .where(ProductionDeployment.artifact_id == artifact_id)
        .order_by(ProductionDeployment.updated_at.desc())
    )).scalars().all())

    # Build version label lookup
    ver_map = {v.id: v.version_label for v in versions}

    deployment_items = []
    for d in deployments_raw:
        resp = CatalogDeploymentResponse.model_validate(d)
        resp.version_label = ver_map.get(d.version_id)
        deployment_items.append(resp)

    # Granted teams
    grants = list((await db.execute(
        select(AssetGrant.grantee_team)
        .where(AssetGrant.asset_id == artifact_id)
    )).scalars().all())

    latest_ver = versions[0].version_label if versions else None
    dep_count = len([d for d in deployments_raw if d.status in ("pending", "deploying", "running")])

    art_resp = CatalogArtifactResponse.model_validate(art)
    art_resp.latest_version = latest_ver
    art_resp.deployment_count = dep_count

    # Resolve member topology for workflow artifacts
    member_topology: list[MemberTopologyEntry] = []
    if art.type == "workflow" and versions:
        members = (versions[0].config_snapshot or {}).get("members", [])
        for m in members:
            agent_name = m.get("agent_name", "")
            member_art = (await db.execute(
                select(PublishedArtifact).where(
                    PublishedArtifact.name == agent_name,
                    PublishedArtifact.type == "agent",
                )
            )).scalar_one_or_none()
            has_dep = False
            if member_art:
                dep = (await db.execute(
                    select(ProductionDeployment).where(
                        ProductionDeployment.artifact_id == member_art.id,
                        ProductionDeployment.status == "running",
                    )
                )).scalar_one_or_none()
                has_dep = dep is not None
            member_topology.append(MemberTopologyEntry(
                agent_name=agent_name,
                agent_id=m.get("agent_id", ""),
                agent_version_id=m.get("agent_version_id"),
                role=m.get("role"),
                position=m.get("position"),
                has_production_deployment=has_dep,
            ))

    return CatalogDetailResponse(
        artifact=art_resp,
        versions=[CatalogVersionResponse.model_validate(v) for v in versions],
        deployments=deployment_items,
        granted_teams=list(set(grants)),
        member_topology=member_topology,
    )


@router.post(
    "/{artifact_id}/deploy",
    response_model=CatalogDeploymentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def deploy_version(
    artifact_id: uuid.UUID,
    body: CatalogDeployRequest,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> CatalogDeploymentResponse:
    """Deploy a specific published version."""
    art = (await db.execute(
        select(PublishedArtifact).where(PublishedArtifact.id == artifact_id)
    )).scalar_one_or_none()
    if art is None:
        raise HTTPException(status_code=404, detail="Published artifact not found.")

    version = (await db.execute(
        select(PublishedVersion).where(
            PublishedVersion.id == body.version_id,
            PublishedVersion.artifact_id == artifact_id,
        )
    )).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found for this artifact.")

    dep_id = uuid.uuid4()
    namespace = f"production-{art.name}-{str(dep_id)[:8]}"
    deployment = ProductionDeployment(
        id=dep_id,
        artifact_id=artifact_id,
        version_id=body.version_id,
        status="pending",
        namespace=namespace,
    )
    db.add(deployment)
    await db.commit()
    await db.refresh(deployment)

    logger.info(
        "catalog_deploy: artifact=%s version=%s deployment=%s by=%s",
        artifact_id, body.version_id, deployment.id, x_user_sub,
    )
    resp = CatalogDeploymentResponse.model_validate(deployment)
    resp.version_label = version.version_label
    return resp


@router.patch(
    "/{artifact_id}/deployments/{deployment_id}",
    response_model=CatalogDeploymentResponse,
)
async def update_deployment(
    artifact_id: uuid.UUID,
    deployment_id: uuid.UUID,
    body: CatalogDeploymentUpdateRequest,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> CatalogDeploymentResponse:
    """Upgrade, suspend, or resume a production deployment."""
    dep = (await db.execute(
        select(ProductionDeployment).where(
            ProductionDeployment.id == deployment_id,
            ProductionDeployment.artifact_id == artifact_id,
        )
    )).scalar_one_or_none()
    if dep is None:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    now = datetime.now(tz=timezone.utc)

    if body.action == "upgrade":
        if not body.version_id:
            raise HTTPException(status_code=400, detail="version_id required for upgrade.")
        version = (await db.execute(
            select(PublishedVersion).where(
                PublishedVersion.id == body.version_id,
                PublishedVersion.artifact_id == artifact_id,
            )
        )).scalar_one_or_none()
        if version is None:
            raise HTTPException(status_code=404, detail="Target version not found.")
        dep.version_id = body.version_id
        dep.status = "deploying"
        dep.updated_at = now
    elif body.action == "suspend":
        dep.status = "suspending"
        dep.suspended_at = now
        dep.updated_at = now
    elif body.action == "resume":
        dep.status = "deploying"
        dep.suspended_at = None
        dep.updated_at = now
    elif body.action == "terminate":
        dep.status = "terminating"
        dep.updated_at = now
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    await db.commit()
    await db.refresh(dep)

    logger.info(
        "catalog_deployment_update: deployment=%s action=%s by=%s",
        deployment_id, body.action, x_user_sub,
    )

    # Look up version label
    ver_label = (await db.execute(
        select(PublishedVersion.version_label).where(PublishedVersion.id == dep.version_id)
    )).scalar_one_or_none()

    resp = CatalogDeploymentResponse.model_validate(dep)
    resp.version_label = ver_label
    return resp


# ---------------------------------------------------------------------------
# Production runs (filtered to this artifact's deployments)
# ---------------------------------------------------------------------------


@router.get("/{artifact_id}/runs", response_model=list[AgentRunResponse])
async def list_catalog_runs(
    artifact_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    """List runs for production deployments of this artifact."""
    import os

    # Get all deployment IDs for this artifact
    dep_ids = list((await db.execute(
        select(ProductionDeployment.id)
        .where(ProductionDeployment.artifact_id == artifact_id)
    )).scalars().all())

    if not dep_ids:
        return []

    q = (
        select(AgentRun)
        .where(AgentRun.production_deployment_id.in_(dep_ids))
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list((await db.execute(q)).scalars().all())

    lf_public_url = os.getenv("LANGFUSE_PUBLIC_URL", "")
    lf_project_id = os.getenv("LANGFUSE_PROJECT_ID", "")
    items: list[AgentRunResponse] = []
    for r in rows:
        resp = AgentRunResponse.model_validate(r)
        if r.langfuse_trace_id and lf_public_url and lf_project_id:
            resp.trace_url = f"{lf_public_url}/project/{lf_project_id}/traces/{r.langfuse_trace_id}"
        items.append(resp)
    return items


# ---------------------------------------------------------------------------
# Production stats (24h aggregates)
# ---------------------------------------------------------------------------


@router.get("/{artifact_id}/stats")
async def get_catalog_stats(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return 24h run aggregates for a production artifact."""
    from datetime import timedelta
    from sqlalchemy import case

    # Get all deployment IDs for this artifact
    dep_ids = list((await db.execute(
        select(ProductionDeployment.id)
        .where(ProductionDeployment.artifact_id == artifact_id)
    )).scalars().all())

    if not dep_ids:
        return {"run_count": 0, "error_rate": 0.0, "p50_latency_ms": None, "total_cost_usd": 0.0}

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)

    stats_q = select(
        func.count(AgentRun.id).label("run_count"),
        func.sum(case((AgentRun.status == "failed", 1), else_=0)).label("error_count"),
        func.sum(AgentRun.cost_usd).label("total_cost"),
    ).where(
        AgentRun.production_deployment_id.in_(dep_ids),
        AgentRun.started_at >= cutoff,
    )
    row = (await db.execute(stats_q)).first()
    run_count = row.run_count or 0
    error_count = row.error_count or 0
    total_cost = float(row.total_cost or 0)
    error_rate = (error_count / run_count) if run_count > 0 else 0.0

    p50 = None
    if run_count > 0:
        latency_q = select(AgentRun.latency_ms).where(
            AgentRun.production_deployment_id.in_(dep_ids),
            AgentRun.started_at >= cutoff,
            AgentRun.latency_ms.isnot(None),
        ).order_by(AgentRun.latency_ms)
        latencies = [r[0] for r in (await db.execute(latency_q)).all()]
        if latencies:
            import math
            p50 = latencies[min(len(latencies) - 1, math.floor(len(latencies) * 0.5))]

    return {
        "run_count": run_count,
        "error_rate": error_rate,
        "p50_latency_ms": p50,
        "total_cost_usd": total_cost,
    }


# ---------------------------------------------------------------------------
# Internal endpoints for deploy-controller
# ---------------------------------------------------------------------------


@router.get("/internal/pending-deployments")
async def list_pending_production_deployments(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Return production deployments needing reconciliation (pending or deploying)."""
    rows = list((await db.execute(
        select(ProductionDeployment)
        .where(ProductionDeployment.status.in_(("pending", "deploying", "suspending", "terminating")))
        .order_by(ProductionDeployment.updated_at)
        .limit(50)
    )).scalars().all())

    results = []
    for dep in rows:
        # Fetch version config snapshot
        version = (await db.execute(
            select(PublishedVersion).where(PublishedVersion.id == dep.version_id)
        )).scalar_one_or_none()
        # Fetch artifact metadata
        artifact = (await db.execute(
            select(PublishedArtifact).where(PublishedArtifact.id == dep.artifact_id)
        )).scalar_one_or_none()

        if not version or not artifact:
            continue

        # Resolve LLM provider from source agent (needed for secret injection)
        from models import Agent, LLMProvider
        from crypto import decrypt_json
        llm_info: dict = {}
        if artifact.source_id:
            source_agent = (await db.execute(
                select(Agent).where(Agent.id == artifact.source_id)
            )).scalar_one_or_none()
            if source_agent and source_agent.llm_provider_id:
                provider = (await db.execute(
                    select(LLMProvider).where(LLMProvider.id == source_agent.llm_provider_id)
                )).scalar_one_or_none()
                if provider:
                    credentials = decrypt_json(provider.credentials_encrypted)
                    llm_info = {
                        "llm_secret_name": f"agentshield-llm-{provider.id}",
                        "llm_env_keys": list(credentials.keys()),
                        "llm_provider_type": provider.provider,
                        "llm_provider_model": provider.default_model,
                        "llm_credentials": credentials,
                    }

        results.append({
            "id": str(dep.id),
            "artifact_id": str(dep.artifact_id),
            "version_id": str(dep.version_id),
            "status": dep.status,
            "namespace": dep.namespace,
            "artifact_name": artifact.name,
            "artifact_type": artifact.type,
            "artifact_team": artifact.team,
            "version_label": version.version_label,
            "config_snapshot": version.config_snapshot,
            **llm_info,
        })

    return results


@router.patch("/internal/production-deployments/{deployment_id}/status")
async def patch_production_deployment_status(
    deployment_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Update production deployment status (called by deploy-controller)."""
    dep = (await db.execute(
        select(ProductionDeployment).where(ProductionDeployment.id == deployment_id)
    )).scalar_one_or_none()
    if dep is None:
        raise HTTPException(status_code=404, detail="Production deployment not found.")

    now = datetime.now(tz=timezone.utc)
    new_status = body.get("status")
    if new_status:
        dep.status = new_status
        dep.updated_at = now
        if new_status == "running":
            dep.deployed_at = now
        elif new_status == "suspended":
            dep.suspended_at = now

    await db.commit()
    return {"updated": True, "status": dep.status}


@router.post("/internal/verify-members")
async def verify_member_deployments(
    body: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check which member agents have active production deployments."""
    agent_names = body.get("agent_names", [])
    deployed = set()
    for name in agent_names:
        art = (await db.execute(
            select(PublishedArtifact).where(
                PublishedArtifact.name == name,
                PublishedArtifact.type == "agent",
            )
        )).scalar_one_or_none()
        if art:
            dep = (await db.execute(
                select(ProductionDeployment).where(
                    ProductionDeployment.artifact_id == art.id,
                    ProductionDeployment.status == "running",
                )
            )).scalar_one_or_none()
            if dep:
                deployed.add(name)
    missing = [n for n in agent_names if n not in deployed]
    return {"ok": len(missing) == 0, "missing": missing}

"""
Bundle router — GET /api/v1/bundle/data.json, GET /api/v1/bundle/policy.rego

Serves the live OPA bundle content. The OPA bundle server's bundle-sync sidecar
polls these endpoints every 30s and writes the results to the shared volume that
nginx serves to OPA sidecars.

This makes bundle updates automatic: deploy an agent → bundle_generator is called
by the deploy endpoint → the next poll picks it up → OPA sidecars reload within 60s.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from bundle_generator import generate_bundle_data
from db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bundle", tags=["bundle"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/data.json", response_class=JSONResponse)
async def get_bundle_data(db: AsyncSession = Depends(get_db)) -> dict:
    """Live OPA data.json — polled every 30s by the bundle-sync sidecar."""
    try:
        return await generate_bundle_data(db)
    except Exception as exc:
        logger.error("bundle data generation failed: %s", exc)
        # Return safe empty bundle rather than 500 — OPA sidecars must keep working
        return {"agents": {}, "grants": {}}


@router.get("/policy.rego", response_class=PlainTextResponse)
async def get_bundle_policy(db: AsyncSession = Depends(get_db)) -> str:
    """Live policy.rego — polled every 30s by the bundle-sync sidecar."""
    try:
        from sqlalchemy import text
        # Fetch all stored agent policies and concatenate
        rows = await db.execute(text("SELECT policy_rego FROM agent_policies WHERE policy_rego IS NOT NULL"))
        policies = [row[0] for row in rows.fetchall() if row[0]]
        if policies:
            return "\n\n".join(policies)
        # Fallback: minimal default deny policy
        return (
            "package agentshield.agent\n"
            "default allow = false\n"
            'default action = "deny"\n'
        )
    except Exception as exc:
        logger.error("bundle policy generation failed: %s", exc)
        return 'package agentshield.agent\ndefault allow = false\n'

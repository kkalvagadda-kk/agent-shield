"""
Bundle router — serves the live OPA bundle for the central bundle server.

Endpoints:
  GET /api/v1/bundle/bundle.tar.gz  — the REAL OPA bundle (a gzipped tar of
                                      data.json + policy.rego). This is what the
                                      OPA sidecars load (resource /bundles/agentshield).
  GET /api/v1/bundle/data.json      — loose data.json (backward-compat / debugging).
  GET /api/v1/bundle/policy.rego     — the unified static policy (backward-compat / debugging).

The bundle server's bundle-sync sidecar polls bundle.tar.gz every 30s and writes it
to the volume nginx serves. Deploy an agent → bundle_generator runs → the next poll
picks it up → OPA sidecars reload within ~60s.

Phase 9.1 completion:
  - The unified policy (package agentshield) is a single STATIC asset shared by every
    agent; all per-request variation comes from data.json. It is checked in at
    services/registry-api/opa_policy/agentshield.rego — no per-agent Rego is generated.
  - policy.rego is served from that file (the old endpoint queried a non-existent
    `policy_rego` column and silently served a default-deny fallback).
"""
from __future__ import annotations

import gzip
import io
import logging
import tarfile
from pathlib import Path

from fastapi import Depends, APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from bundle_generator import generate_bundle_data
from db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bundle", tags=["bundle"])

# The single, static unified policy shipped inside the registry-api image.
# routers/bundle.py -> parent.parent == the registry-api app root (/app in the image).
_POLICY_PATH = Path(__file__).resolve().parent.parent / "opa_policy" / "agentshield.rego"

# Fail-closed fallback if the checked-in policy is somehow missing from the image.
_FALLBACK_POLICY = (
    "package agentshield\n"
    "import rego.v1\n"
    "default allow := false\n"
    'default require_approval := false\n'
    'default reason := "policy_asset_missing"\n'
    'default deny_reason := "policy_asset_missing"\n'
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _load_policy_rego() -> str:
    """Return the unified static policy text (fail-closed if the asset is missing)."""
    try:
        return _POLICY_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("unified policy asset missing at %s: %s", _POLICY_PATH, exc)
        return _FALLBACK_POLICY


@router.get("/data.json", response_class=JSONResponse)
async def get_bundle_data(db: AsyncSession = Depends(get_db)) -> dict:
    """Live OPA data.json (loose form — kept for debugging/back-compat)."""
    try:
        return await generate_bundle_data(db)
    except Exception as exc:
        logger.error("bundle data generation failed: %s", exc)
        # Return safe empty bundle rather than 500 — OPA sidecars must keep working
        return {"agents": {}, "grants": {}}


@router.get("/policy.rego", response_class=PlainTextResponse)
async def get_bundle_policy() -> str:
    """The unified static policy (loose form — kept for debugging/back-compat)."""
    return _load_policy_rego()


@router.get("/bundle.tar.gz")
async def get_bundle_tarball(db: AsyncSession = Depends(get_db)) -> Response:
    """The real OPA bundle: a gzipped tar of data.json + policy.rego.

    This is the artifact OPA sidecars load (resource /bundles/agentshield). A valid
    OPA bundle tarball carries data.json (→ `data`) and one or more .rego files at the
    archive root. Built entirely in Python so bundle assembly stays server-side.
    """
    try:
        data = await generate_bundle_data(db)
    except Exception as exc:
        logger.error("bundle data generation failed: %s — serving empty bundle", exc)
        data = {"agents": {}, "grants": {}}

    import json

    data_bytes = json.dumps(data, indent=2).encode("utf-8")
    policy_bytes = _load_policy_rego().encode("utf-8")

    raw = io.BytesIO()
    # mtime=0 → deterministic gzip output (stable bytes for identical bundles).
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name, payload in (("data.json", data_bytes), ("policy.rego", policy_bytes)):
                info = tarfile.TarInfo(name=name)
                info.size = len(payload)
                info.mtime = 0
                tar.addfile(info, io.BytesIO(payload))

    return Response(
        content=raw.getvalue(),
        media_type="application/gzip",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )

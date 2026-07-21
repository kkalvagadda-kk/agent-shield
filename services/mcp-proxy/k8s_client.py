"""
In-cluster Kubernetes client for the MCP Proxy — READ-ONLY + AuthN only.

Mirrors registry-api/k8s.py's `_init_k8s()` shape, but the proxy is granted ONLY:
  - system:auth-delegator  → create TokenReview  (AuthN, design §3b)
  - get on secrets in MCP_SECRETS_NAMESPACE → read per-server credential Secrets

There are deliberately NO write verbs here (no upsert/replace/delete/patch) and no
access outside MCP_SECRETS_NAMESPACE. registry-api owns all Secret writes; the proxy
only reads. Never widen this module to reach agentshield-platform (the master
encryption key lives there) — that would defeat path (b) least-privilege (B13).
"""
from __future__ import annotations

import asyncio
import base64
import logging

from kubernetes import client, config
from kubernetes.client.rest import ApiException

import config as proxy_config  # local module (flat layout, run as `uvicorn main:app`)

logger = logging.getLogger(__name__)

_k8s_initialized = False


def _init_k8s() -> None:
    """Load in-cluster config (falls back to kubeconfig for local dev)."""
    global _k8s_initialized
    if _k8s_initialized:
        return
    try:
        config.load_incluster_config()
        logger.info("mcp-proxy k8s: loaded in-cluster config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("mcp-proxy k8s: loaded kube config (local dev)")
    _k8s_initialized = True


# ---------------------------------------------------------------------------
# AuthN — TokenReview (system:auth-delegator)
# ---------------------------------------------------------------------------

def _create_token_review_sync(token: str):
    _init_k8s()
    api = client.AuthenticationV1Api()
    review = client.V1TokenReview(
        spec=client.V1TokenReviewSpec(
            token=token,
            audiences=[proxy_config.MCP_PROXY_AUDIENCE],
        )
    )
    # create_token_review is a subresource create; it needs system:auth-delegator.
    return api.create_token_review(review)


async def create_token_review(token: str):
    """Run a TokenReview for the given bearer token (audience-scoped).

    Returns the V1TokenReview whose .status carries authenticated/audiences/user.
    Runs the sync k8s client in a thread to keep the event loop free.
    """
    return await asyncio.to_thread(_create_token_review_sync, token)


# ---------------------------------------------------------------------------
# Credential Secret read (get on secrets in MCP_SECRETS_NAMESPACE only)
# ---------------------------------------------------------------------------

def _read_secret_sync(name: str) -> dict[str, str]:
    _init_k8s()
    v1 = client.CoreV1Api()
    # Raises ApiException (404 on missing) — the caller maps 404 to a typed
    # "secret not found" error which surfaces as a 200 error body, never a 5xx.
    secret = v1.read_namespaced_secret(
        name=name, namespace=proxy_config.MCP_SECRETS_NAMESPACE
    )
    data = secret.data or {}
    # V1Secret.data values are base64-encoded strings; decode to plaintext.
    return {k: base64.b64decode(v).decode("utf-8") for k, v in data.items()}


async def read_secret(name: str) -> dict[str, str]:
    """Read a Secret in MCP_SECRETS_NAMESPACE, returning its decoded string data.

    Raises kubernetes.client.rest.ApiException on any API error (404 == missing).
    """
    return await asyncio.to_thread(_read_secret_sync, name)


# Re-export for callers that want to branch on status codes (e.g. 404).
__all__ = ["create_token_review", "read_secret", "ApiException"]

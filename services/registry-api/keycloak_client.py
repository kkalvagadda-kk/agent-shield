"""Keycloak Admin API client — thin wrapper around the REST API.

Uses KEYCLOAK_URL (already injected into registry-api pods) and
KEYCLOAK_ADMIN_PASSWORD to obtain and cache an admin token from the
master realm, then forwards calls to the agentshield realm.
"""
import asyncio
import os
import time
from typing import Any

import httpx

_KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://agentshield-keycloak")
_ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_USER", "admin")
_ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "")
_REALM = "agentshield"

_token_cache: dict[str, Any] = {}
_token_lock = asyncio.Lock()


async def _admin_token() -> str:
    async with _token_lock:
        now = time.time()
        if _token_cache.get("token") and now < _token_cache.get("expires_at", 0) - 30:
            return _token_cache["token"]

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": _ADMIN_USER,
                    "password": _ADMIN_PASS,
                },
            )
            r.raise_for_status()
            body = r.json()
            _token_cache["token"] = body["access_token"]
            _token_cache["expires_at"] = now + body.get("expires_in", 60)
            return _token_cache["token"]


def _admin_url(path: str) -> str:
    return f"{_KEYCLOAK_URL}/admin/realms/{_REALM}/{path.lstrip('/')}"


async def list_users(max: int = 500) -> list[dict]:
    token = await _admin_token()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            _admin_url("users"),
            params={"max": max, "briefRepresentation": "false"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_user(kc_id: str) -> dict:
    token = await _admin_token()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            _admin_url(f"users/{kc_id}"),
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()


async def create_user(
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    temp_password: str,
    enabled: bool = True,
) -> str:
    """Returns the new Keycloak user ID (UUID)."""
    token = await _admin_token()
    payload = {
        "username": username,
        "email": email,
        "firstName": first_name,
        "lastName": last_name,
        "enabled": enabled,
        "credentials": [
            {"type": "password", "value": temp_password, "temporary": True}
        ],
        "requiredActions": ["UPDATE_PASSWORD"],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            _admin_url("users"),
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        location = r.headers.get("Location", "")
        return location.rstrip("/").split("/")[-1]


async def update_user(kc_id: str, **fields: Any) -> None:
    """Update arbitrary Keycloak user fields (enabled, email, firstName, etc.)."""
    token = await _admin_token()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.put(
            _admin_url(f"users/{kc_id}"),
            json=fields,
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()


async def delete_user(kc_id: str) -> None:
    token = await _admin_token()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.delete(
            _admin_url(f"users/{kc_id}"),
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()


async def reset_password(kc_id: str, new_password: str, temporary: bool = True) -> None:
    token = await _admin_token()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.put(
            _admin_url(f"users/{kc_id}/reset-password"),
            json={"type": "password", "value": new_password, "temporary": temporary},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()


async def get_realm_roles() -> list[dict]:
    token = await _admin_token()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            _admin_url("roles"),
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_user_realm_roles(kc_id: str) -> list[str]:
    token = await _admin_token()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            _admin_url(f"users/{kc_id}/role-mappings/realm"),
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return [role["name"] for role in r.json()]


async def set_user_realm_role(kc_id: str, role_name: str) -> None:
    """Replace the user's platform roles (admin/operator/viewer) with role_name."""
    platform_roles = {"admin", "operator", "viewer"}
    all_roles = await get_realm_roles()
    role_map = {r["name"]: r for r in all_roles}

    token = await _admin_token()
    async with httpx.AsyncClient(timeout=10) as client:
        current = await get_user_realm_roles(kc_id)
        to_remove = [role_map[n] for n in current if n in platform_roles and n in role_map]
        if to_remove:
            await client.request(
                "DELETE",
                _admin_url(f"users/{kc_id}/role-mappings/realm"),
                json=to_remove,
                headers={"Authorization": f"Bearer {token}"},
            )

        if role_name in role_map:
            r = await client.post(
                _admin_url(f"users/{kc_id}/role-mappings/realm"),
                json=[role_map[role_name]],
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()

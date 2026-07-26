"""
AgentShield Registry API — pluggable credential provider seam (Decision 31).

A ``CredentialRef`` is an opaque pointer — ``<scheme>://<path>`` — that says *where*
a secret lives, never the secret itself. A Postgres row keeps only the ref; the value
moves behind the provider the scheme names.

This module ships the **behavior-preserving** default backend, ``FernetPgProvider``:
the credential *value* stays in Postgres, Fernet-encrypted with the same
``AGENTSHIELD_ENCRYPTION_KEY`` and the same ``crypto.encrypt_json`` / ``decrypt_json``
door as before — it is merely relocated from ``auth_configs.credentials_encrypted``
(inline column) to the generic KV table ``credential_blobs`` (keyed by the ref path).
On ``pg-fernet`` every resolved secret is therefore **byte-identical** to today.

``get_provider()`` selects the backend from ``config.CREDENTIAL_PROVIDER_BACKEND``.
The default ``pg-fernet`` keeps the value in Postgres. The opt-in ``aws-sm`` backend
(``AwsSecretsManagerProvider``) stores the value in AWS Secrets Manager, reached via
IRSA; ``boto3`` is imported **lazily** so the default ``pg-fernet`` path (and every
existing deploy) never needs the AWS libraries at import time.

Design: ``docs/design/credential-provider-architecture.md`` §2.
Data model: ``docs/plan/mcp-tool-source-phase4/data-model.md`` §1.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import delete as sa_delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from config import settings
from crypto import decrypt_json, encrypt_json
from models import CredentialBlob

# ---------------------------------------------------------------------------
# Scheme + path conventions
# ---------------------------------------------------------------------------
# The ``pg-fernet`` ref path is always prefixed with the table name so the
# provider is a pure KV over ``credential_blobs.path`` (data-model §1a). The
# stored PK strips that prefix (e.g. ref path ``credential-blobs/auth-configs/{id}``
# → PK ``auth-configs/{id}``), matching migration 0073's backfill VERBATIM.
_SCHEME_PG_FERNET = "pg-fernet"
_SCHEME_AWS_SM = "aws-sm"
_PG_FERNET_BLOB_PREFIX = "credential-blobs/"


# ---------------------------------------------------------------------------
# Value object + errors
# ---------------------------------------------------------------------------
class CredentialNotFound(Exception):
    """No secret at the given ref (a ``get``/``rotate`` on an absent ref).

    Callers map this to a 404 / self-heal path. A ``credential_ref`` that is set
    but resolves to nothing is, by construction (migration 0073 writes the blob
    and the ref together; the provider writes value-then-ref), a structurally
    unexpected state — it is surfaced loudly rather than silently swallowed.
    """


@dataclass(frozen=True)
class CredentialRef:
    """An opaque ``<scheme>://<path>`` pointer to a credential value.

    ``scheme`` names the backend (``pg-fernet`` | ``aws-sm`` | …); ``path`` is the
    backend-agnostic locator (e.g. ``credential-blobs/auth-configs/{id}``).
    """

    scheme: str
    path: str

    def __str__(self) -> str:
        return f"{self.scheme}://{self.path}"

    @classmethod
    def parse(cls, s: str) -> "CredentialRef":
        scheme, _, path = s.partition("://")
        return cls(scheme=scheme, path=path)


class CredentialProvider(Protocol):
    """Backend-agnostic durable store for credential *values*.

    The pointer (``CredentialRef``) lives in Postgres; the value lives here.
    """

    async def put(self, ref: CredentialRef, value: dict) -> None:
        """Create-or-replace the secret at ``ref``."""

    async def get(self, ref: CredentialRef) -> dict:
        """Resolve ``ref`` to its plaintext dict. Raises ``CredentialNotFound`` if absent."""

    async def rotate(self, ref: CredentialRef, value: dict) -> CredentialRef:
        """Write a new value; MAY return a new (versioned) ref. For an in-place
        backend the same ref is returned."""

    async def delete(self, ref: CredentialRef) -> None:
        """Remove the secret. Absent = no-op (idempotent)."""


# ---------------------------------------------------------------------------
# Canonical ref builders (keep the runtime ref string == the migration string)
# ---------------------------------------------------------------------------
def auth_config_credential_ref(config_id) -> CredentialRef:
    """Canonical ``CredentialRef`` for an ``AuthConfig``'s stored credentials.

    Byte-identity contract: this string MUST equal what migration 0073 writes into
    ``auth_configs.credential_ref`` during backfill —
    ``pg-fernet://credential-blobs/auth-configs/{id}`` — so a row created/updated
    through the provider resolves identically to a row the backfill migrated.

    ``pg-fernet``-only this phase (WS-1); P3 makes the scheme backend-aware.
    """
    return CredentialRef(
        scheme=_SCHEME_PG_FERNET,
        path=f"{_PG_FERNET_BLOB_PREFIX}auth-configs/{config_id}",
    )


def mcp_oauth_refresh_ref(server_id, user_sub: str) -> CredentialRef:
    """Canonical ``CredentialRef`` for a user's stored OAuth refresh token on one
    external MCP server (Phase 4 WS-2).

    Shape ``pg-fernet://credential-blobs/mcp-oauth-refresh/{server_id}/{user_sub}``
    (data-model §1a). The pointer string is stored in
    ``mcp_oauth_grants.credential_ref``; the refresh-token value lives behind the
    provider, NEVER in a column. ``pg-fernet``-only this phase (WS-2); P3 makes the
    scheme backend-aware (mirrors :func:`auth_config_credential_ref`).
    """
    return CredentialRef(
        scheme=_SCHEME_PG_FERNET,
        path=f"{_PG_FERNET_BLOB_PREFIX}mcp-oauth-refresh/{server_id}/{user_sub}",
    )


def mcp_oauth_client_ref(server_id) -> CredentialRef:
    """Canonical ``CredentialRef`` for a server's DCR-registered OAuth client
    credentials ``{client_id, client_secret?}`` (Phase 4 WS-2).

    Shape ``pg-fernet://credential-blobs/mcp-oauth-client/{server_id}`` (data-model
    §1a). The pointer string is stored in ``mcp_servers.oauth_client_ref``; the
    client secret lives behind the provider. ``pg-fernet``-only this phase; P3 makes
    the scheme backend-aware.
    """
    return CredentialRef(
        scheme=_SCHEME_PG_FERNET,
        path=f"{_PG_FERNET_BLOB_PREFIX}mcp-oauth-client/{server_id}",
    )


# ---------------------------------------------------------------------------
# FernetPgProvider — value → credential_blobs (Fernet, Postgres). Dev/default.
# ---------------------------------------------------------------------------
class FernetPgProvider:
    """Behavior-preserving default backend.

    Stores the Fernet-encrypted JSON blob (via ``crypto.encrypt_json`` /
    ``decrypt_json`` — the same master key as before) in ``credential_blobs``,
    keyed by the ref path. This is exactly today's Fernet round-trip, relocated
    from ``auth_configs.credentials_encrypted`` to ``credential_blobs.value_encrypted``.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    def _blob_key(self, ref: CredentialRef) -> str:
        """Map a ``pg-fernet`` ref to its ``credential_blobs.path`` PK.

        Strips the ``credential-blobs/`` scheme prefix (data-model §1a). A wrong
        scheme is a precondition violation, not a fallback case — raise loudly.
        """
        if ref.scheme != _SCHEME_PG_FERNET:
            raise ValueError(
                f"FernetPgProvider cannot resolve ref scheme {ref.scheme!r} "
                f"(expected {_SCHEME_PG_FERNET!r}): {ref}"
            )
        path = ref.path
        if path.startswith(_PG_FERNET_BLOB_PREFIX):
            path = path[len(_PG_FERNET_BLOB_PREFIX):]
        return path

    async def put(self, ref: CredentialRef, value: dict) -> None:
        key = self._blob_key(ref)
        token = encrypt_json(value)
        async with self._session_factory() as session:
            stmt = (
                pg_insert(CredentialBlob)
                .values(path=key, value_encrypted=token)
                .on_conflict_do_update(
                    index_elements=[CredentialBlob.path],
                    set_={"value_encrypted": token, "updated_at": func.now()},
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def get(self, ref: CredentialRef) -> dict:
        key = self._blob_key(ref)
        async with self._session_factory() as session:
            row = await session.get(CredentialBlob, key)
            if row is None:
                raise CredentialNotFound(str(ref))
            # Same Fernet key, same door → byte-identical to the legacy column read.
            return decrypt_json(row.value_encrypted)

    async def rotate(self, ref: CredentialRef, value: dict) -> CredentialRef:
        # pg-fernet has no per-secret versioning: overwrite in place, keep the ref.
        await self.put(ref, value)
        return ref

    async def delete(self, ref: CredentialRef) -> None:
        key = self._blob_key(ref)
        async with self._session_factory() as session:
            await session.execute(
                sa_delete(CredentialBlob).where(CredentialBlob.path == key)
            )
            await session.commit()


# ---------------------------------------------------------------------------
# AwsSecretsManagerProvider — value → AWS Secrets Manager (IRSA). Prod, opt-in.
# ---------------------------------------------------------------------------
class AwsSecretsManagerProvider:
    """Opt-in production backend — the credential *value* lives in AWS Secrets
    Manager, never in Postgres (only the ref does).

    Auth is **IRSA**: the pod's ServiceAccount is annotated with an IAM role ARN
    (``eks.amazonaws.com/role-arn``) and boto3's default credential chain assumes
    that role — no static keys are ever handed to this class. ``boto3`` (and
    ``botocore``) are imported **lazily**, inside ``_get_client`` / the sync
    helpers, so a dev/CI checkout on the default ``pg-fernet`` backend never needs
    the AWS libraries at import time (HARD invariant — see the module docstring).

    boto3 is blocking, so every API call runs in a worker thread via
    ``loop.run_in_executor`` (the same idiom as ``blob_store.MinioBlobStore`` and
    ``judge._invoke_bedrock_sync``) so it never stalls the event loop.

    The secret id is ``prefix + ref.path`` (or just ``ref.path`` when the prefix is
    empty). The ref is backend-agnostic: ``get_provider()`` is config-selected, so
    the *same* ref a caller builds resolves through whichever backend is configured.
    This provider therefore keys off ``ref.path`` and does not gate on the scheme.
    """

    def __init__(self, prefix: str, region: str) -> None:
        self._prefix = prefix or ""
        # Empty region → let boto3 resolve it from AWS_REGION / the pod config.
        self._region = region or None
        self._client = None  # built lazily (also defers the boto3 import)

    def _get_client(self):
        if self._client is None:
            import boto3  # LAZY — the default pg-fernet path never imports boto3.

            kwargs: dict = {}
            if self._region:
                kwargs["region_name"] = self._region
            self._client = boto3.client("secretsmanager", **kwargs)
        return self._client

    def _secret_id(self, ref: CredentialRef) -> str:
        return f"{self._prefix}{ref.path}" if self._prefix else ref.path

    # -- sync (boto3) bodies — run in an executor by the async methods --------
    def _put_sync(self, ref: CredentialRef, value: dict) -> None:
        from botocore.exceptions import ClientError

        client = self._get_client()
        secret_id = self._secret_id(ref)
        payload = json.dumps(value)
        try:
            client.create_secret(Name=secret_id, SecretString=payload)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code == "ResourceExistsException":
                # Already exists → overwrite the value in place.
                client.put_secret_value(SecretId=secret_id, SecretString=payload)
            else:
                raise

    def _get_sync(self, ref: CredentialRef) -> dict:
        from botocore.exceptions import ClientError

        client = self._get_client()
        secret_id = self._secret_id(ref)
        try:
            resp = client.get_secret_value(SecretId=secret_id)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code == "ResourceNotFoundException":
                raise CredentialNotFound(str(ref)) from exc
            raise
        return json.loads(resp["SecretString"])

    def _rotate_sync(self, ref: CredentialRef, value: dict) -> None:
        client = self._get_client()
        client.put_secret_value(
            SecretId=self._secret_id(ref), SecretString=json.dumps(value)
        )

    def _delete_sync(self, ref: CredentialRef) -> None:
        from botocore.exceptions import ClientError

        client = self._get_client()
        try:
            client.delete_secret(SecretId=self._secret_id(ref))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code == "ResourceNotFoundException":
                return  # idempotent — absent is a no-op
            raise

    # -- async surface (matches the CredentialProvider Protocol) --------------
    async def put(self, ref: CredentialRef, value: dict) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._put_sync, ref, value)

    async def get(self, ref: CredentialRef) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_sync, ref)

    async def rotate(self, ref: CredentialRef, value: dict) -> CredentialRef:
        # ASM versions on every PutSecretValue; the logical ref is unchanged.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._rotate_sync, ref, value)
        return ref

    async def delete(self, ref: CredentialRef) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._delete_sync, ref)


# ---------------------------------------------------------------------------
# Factory — config-selected singleton
# ---------------------------------------------------------------------------
_provider_singleton: CredentialProvider | None = None


def get_provider() -> CredentialProvider:
    """Return the config-selected ``CredentialProvider`` singleton.

    Keyed on ``config.CREDENTIAL_PROVIDER_BACKEND`` (``pg-fernet`` default). The
    ``pg-fernet`` provider binds the async session factory; the opt-in ``aws-sm``
    provider binds the ASM prefix + region (``boto3`` is imported lazily inside
    ``AwsSecretsManagerProvider`` so a dev/CI checkout on ``pg-fernet`` never needs
    the AWS libraries). Any other value fails loud (the existing scheme guard).
    """
    global _provider_singleton
    if _provider_singleton is None:
        backend = settings.credential_provider_backend
        if backend == _SCHEME_PG_FERNET:
            # Imported lazily to avoid binding the engine at module import time.
            from db import AsyncSessionLocal

            _provider_singleton = FernetPgProvider(AsyncSessionLocal)
        elif backend == _SCHEME_AWS_SM:
            _provider_singleton = AwsSecretsManagerProvider(
                prefix=settings.aws_secrets_manager_prefix,
                region=settings.aws_region,
            )
        else:
            raise ValueError(
                f"Unsupported CREDENTIAL_PROVIDER_BACKEND {backend!r} "
                f"(supported: {_SCHEME_PG_FERNET!r}, {_SCHEME_AWS_SM!r})."
            )
    return _provider_singleton

"""BlobStore port + MinIO/S3 adapter (POC-4).

Mirrors the POC-0 ConversationStore seam: callers depend on the `BlobStore`
Protocol, and `store_factory.get_blob_store()` is the ONLY construction choke
point. `MinioBlobStore` is the ONLY place that talks to object storage. A future
backend (real S3, GCS, …) ships as a new class + a `BLOB_STORE` value — zero
caller change.

boto3 is blocking, so every network call is wrapped in `loop.run_in_executor`
(the same idiom as judge.py:_invoke_bedrock_sync) so it never stalls the event
loop.

Env (registry-api pod):
  BLOB_STORE_ENDPOINT   default http://agentshield-minio…:9000
  BLOB_STORE_BUCKET     default knowledge-sources (created on first put)
  BLOB_STORE_ACCESS_KEY minio root-user
  BLOB_STORE_SECRET_KEY minio root-password
"""
from __future__ import annotations

import asyncio
import os
from typing import Protocol

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

_DEFAULT_ENDPOINT = (
    "http://agentshield-minio.agentshield-platform.svc.cluster.local:9000"
)
_DEFAULT_BUCKET = "knowledge-sources"


class BlobStore(Protocol):
    async def put(
        self, key: str, data: bytes, content_type: str | None = None
    ) -> str:
        """Store bytes at `key`. Returns the key. Creates the bucket on first use
        (head_bucket → create_bucket on 404). Idempotent overwrite."""
        ...

    async def get(self, key: str) -> bytes:
        """Fetch bytes at `key`. Raises KeyError if the object does not exist."""
        ...


class MinioBlobStore:
    """S3/MinIO adapter (boto3, path-style addressing). Holds a reusable client;
    cache the instance via store_factory.get_blob_store()."""

    def __init__(self) -> None:
        self._endpoint = os.getenv("BLOB_STORE_ENDPOINT", _DEFAULT_ENDPOINT)
        self._bucket = os.getenv("BLOB_STORE_BUCKET", _DEFAULT_BUCKET)
        self._access_key = os.getenv("BLOB_STORE_ACCESS_KEY", "")
        self._secret_key = os.getenv("BLOB_STORE_SECRET_KEY", "")
        self._client = None  # built lazily
        self._bucket_ready = False

    def _get_client(self):
        if self._client is None:
            # MinIO requires path-style addressing; region is nominal for MinIO.
            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name="us-east-1",
                config=BotoConfig(s3={"addressing_style": "path"}),
            )
        return self._client

    def _ensure_bucket_sync(self, client) -> None:
        if self._bucket_ready:
            return
        try:
            client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            err = exc.response.get("Error", {})
            code = str(err.get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("404", "NoSuchBucket") or status == 404:
                client.create_bucket(Bucket=self._bucket)
            else:
                raise
        self._bucket_ready = True

    def _put_sync(self, key: str, data: bytes, content_type: str | None) -> str:
        client = self._get_client()
        self._ensure_bucket_sync(client)
        kwargs: dict = {"Bucket": self._bucket, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        client.put_object(**kwargs)
        return key

    def _get_sync(self, key: str) -> bytes:
        client = self._get_client()
        try:
            resp = client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except ClientError as exc:
            err = exc.response.get("Error", {})
            code = str(err.get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("NoSuchKey", "404", "NoSuchBucket") or status == 404:
                raise KeyError(key) from exc
            raise

    async def put(
        self, key: str, data: bytes, content_type: str | None = None
    ) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._put_sync, key, data, content_type
        )

    async def get(self, key: str) -> bytes:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_sync, key)

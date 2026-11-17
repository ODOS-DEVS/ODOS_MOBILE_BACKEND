"""Thin wrapper over an S3-compatible bucket (Contabo Object Storage).

Contabo speaks the S3 API but only supports path-style addressing, and its
public URLs embed the tenant id: `<endpoint>/<tenant-id>:<bucket>/<key>`.
`S3_PUBLIC_BASE_URL` is therefore configured explicitly rather than derived.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import boto3
from botocore.config import Config

from app.core.config import settings

logger = logging.getLogger(__name__)

#: Immutable: every upload gets a fresh uuid directory, so a URL never changes
#: content and can be cached for a year.
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"

_MANAGED_ROOT = "odos/"


@lru_cache(maxsize=4)
def _build_client(endpoint: str, region: str, access_key: str, secret_key: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=10,
            read_timeout=30,
        ),
    )


def _client():
    return _build_client(
        settings.s3_endpoint_url.strip(),
        settings.s3_region.strip(),
        settings.s3_access_key.strip(),
        settings.s3_secret_key.strip(),
    )


def _base_url() -> str:
    return settings.s3_public_base_url.strip().rstrip("/")


def public_url(key: str) -> str:
    return f"{_base_url()}/{key}"


def key_from_url(url: str | None) -> str | None:
    """The object key for a URL we issued, or None for anything else."""
    if not url or not settings.s3_is_configured:
        return None
    prefix = f"{_base_url()}/"
    if not url.startswith(prefix):
        return None
    key = url[len(prefix) :].split("?", 1)[0]
    return key or None


def put_object(
    key: str,
    body: bytes,
    content_type: str,
    *,
    content_disposition: str | None = None,
) -> None:
    extra: dict[str, str] = {}
    if content_disposition:
        extra["ContentDisposition"] = content_disposition
    _client().put_object(
        Bucket=settings.s3_bucket.strip(),
        Key=key,
        Body=body,
        ContentType=content_type,
        CacheControl=IMMUTABLE_CACHE_CONTROL,
        ACL="public-read",
        **extra,
    )


def delete_prefix(prefix: str) -> None:
    """Delete every object under `prefix`.

    Refuses anything outside `odos/` or shallower than `odos/<folder>/<id>/`,
    so a malformed URL can never turn into a bucket-wide delete.
    """
    if not prefix.startswith(_MANAGED_ROOT) or prefix.count("/") < 3 or not prefix.endswith("/"):
        return

    client = _client()
    bucket = settings.s3_bucket.strip()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if objects:
            client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})

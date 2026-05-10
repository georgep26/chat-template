"""S3 artifact fetch for chunk markdown files.

Reads chunk artifacts from ``s3://{kb_bucket}/kb_index/...`` with strict
prefix validation and a size cap. The ``location`` URI passed in must come
from a Aurora ``document_chunks`` row already authorized for the caller —
never trust client input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

from utils.logger import get_logger

log = get_logger(__name__)


DEFAULT_MAX_BYTES = 262_144  # 256 KB
ALLOWED_KEY_PREFIX = "kb_index/"


class ArtifactFetchError(Exception):
    """Raised when an artifact cannot be fetched safely."""


@dataclass
class FetchedArtifact:
    content: str
    bucket: str
    key: str
    content_length: int
    content_type: Optional[str]


def fetch_chunk_content(
    *,
    location: str,
    expected_bucket: str,
    s3_client: Any | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> FetchedArtifact:
    """Fetch a chunk artifact from S3, validating prefix and size first.

    Args:
        location: ``s3://bucket/key`` URI from a trusted ``document_chunks`` row.
        expected_bucket: The KB bucket configured for this environment.
        s3_client: Injected boto3 ``s3`` client (for tests). Defaults to a fresh client.
        max_bytes: Hard cap on object size; HeadObject is used to check before download.

    Raises:
        ArtifactFetchError: on any prefix mismatch, size overflow, or S3 error.
    """
    bucket, key = _parse_s3_uri(location)
    if bucket != expected_bucket:
        raise ArtifactFetchError(
            f"Artifact bucket mismatch: expected '{expected_bucket}', got '{bucket}'."
        )
    if not key.startswith(ALLOWED_KEY_PREFIX):
        raise ArtifactFetchError(
            f"Artifact key '{key}' is outside the allowed prefix '{ALLOWED_KEY_PREFIX}'."
        )

    client = s3_client or boto3.client("s3")

    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise ArtifactFetchError(f"Artifact not found: s3://{bucket}/{key}") from exc
        raise ArtifactFetchError(f"S3 HeadObject failed: {exc}") from exc

    content_length = int(head.get("ContentLength", 0) or 0)
    content_type = head.get("ContentType")
    if content_length > max_bytes:
        raise ArtifactFetchError(
            f"Artifact size {content_length} exceeds max_bytes {max_bytes}: s3://{bucket}/{key}"
        )

    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
    except ClientError as exc:
        raise ArtifactFetchError(f"S3 GetObject failed: {exc}") from exc

    if len(body) > max_bytes:
        raise ArtifactFetchError(
            f"Artifact body size {len(body)} exceeds max_bytes {max_bytes}: s3://{bucket}/{key}"
        )

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactFetchError(
            f"Artifact at s3://{bucket}/{key} is not valid UTF-8."
        ) from exc

    return FetchedArtifact(
        content=text,
        bucket=bucket,
        key=key,
        content_length=content_length,
        content_type=content_type,
    )


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri or not isinstance(uri, str):
        raise ArtifactFetchError("Artifact location is empty.")
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ArtifactFetchError(f"Artifact location is not an s3:// URI: {uri!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not key:
        raise ArtifactFetchError(f"Artifact location has no key: {uri!r}")
    return bucket, key

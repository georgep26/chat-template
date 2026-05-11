"""S3 presigned-URL builder for raw original documents.

Reads from ``s3://{kb_bucket}/kb_raw/{document_id}/original/...`` with strict
prefix validation. The ``location`` URI passed in must come from a Aurora
``document_catalog`` row already authorized for the caller — never trust
client input.

The presigned URL inherits the Lambda execution role's permissions; the
holder of the URL does not need their own IAM grant. Keep TTLs short so a
leaked URL has a small blast radius.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import boto3

from utils.logger import get_logger

log = get_logger(__name__)


DEFAULT_TTL_SECONDS = 300        # 5 min
MIN_TTL_SECONDS = 60             # 1 min
MAX_TTL_SECONDS = 900            # 15 min
ALLOWED_KEY_PREFIX = "kb_raw/"
ALLOWED_DISPOSITIONS = ("inline", "attachment")

_CONTENT_TYPE_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class RawUrlError(Exception):
    """Raised when a presigned URL cannot be issued safely."""


@dataclass
class RawDocumentUrl:
    url: str
    expires_at: str        # ISO8601 UTC
    filename: str
    content_type: str
    disposition: str
    ttl_seconds: int


def build_raw_document_url(
    *,
    location: str,
    expected_bucket: str,
    s3_client: Any | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    disposition: str = "inline",
    page: Optional[int] = None,
    response_filename: Optional[str] = None,
    allowed_prefix: str = ALLOWED_KEY_PREFIX,
) -> RawDocumentUrl:
    """Issue a short-lived presigned GET URL for a raw document.

    Args:
        location: ``s3://bucket/key`` URI from a trusted ``document_catalog`` row.
        expected_bucket: KB bucket configured for this environment.
        s3_client: Injected boto3 ``s3`` client (for tests). Defaults to a fresh client.
        ttl_seconds: URL lifetime; clamped to ``[MIN_TTL_SECONDS, MAX_TTL_SECONDS]``.
        disposition: ``"inline"`` (open in viewer) or ``"attachment"`` (force download).
        page: Optional page hint appended as ``#page=N`` after the signed query.
        response_filename: Override the filename echoed in Content-Disposition.
        allowed_prefix: Configurable prefix gate; defaults to ``kb_raw/``.

    Raises:
        RawUrlError: on any prefix mismatch or invalid parameter.
    """
    if disposition not in ALLOWED_DISPOSITIONS:
        raise RawUrlError(
            f"Invalid disposition {disposition!r}; must be one of {ALLOWED_DISPOSITIONS}."
        )
    ttl = _clamp_ttl(ttl_seconds)

    bucket, key = _parse_s3_uri(location)
    if bucket != expected_bucket:
        raise RawUrlError(
            f"Document bucket mismatch: expected {expected_bucket!r}, got {bucket!r}."
        )
    if not key.startswith(allowed_prefix):
        raise RawUrlError(
            f"Document key {key!r} is outside the allowed prefix {allowed_prefix!r}."
        )

    filename = response_filename or os.path.basename(key) or "document"
    content_type = _infer_content_type(filename)

    client = s3_client or boto3.client("s3")
    presigned = client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": _content_disposition(disposition, filename),
            "ResponseContentType": content_type,
        },
        ExpiresIn=ttl,
    )

    if page is not None:
        if not isinstance(page, int) or page < 1:
            raise RawUrlError(f"Invalid page {page!r}; must be a positive integer.")
        presigned = f"{presigned}#page={page}"

    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    return RawDocumentUrl(
        url=presigned,
        expires_at=expires_at,
        filename=filename,
        content_type=content_type,
        disposition=disposition,
        ttl_seconds=ttl,
    )


def _clamp_ttl(ttl_seconds: int) -> int:
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError):
        raise RawUrlError(f"Invalid ttl_seconds: {ttl_seconds!r}")
    if ttl < MIN_TTL_SECONDS:
        return MIN_TTL_SECONDS
    if ttl > MAX_TTL_SECONDS:
        return MAX_TTL_SECONDS
    return ttl


def _content_disposition(disposition: str, filename: str) -> str:
    # RFC 6266; the simple ASCII form is sufficient for catalog-supplied filenames.
    safe = filename.replace('"', "")
    return f'{disposition}; filename="{safe}"'


def _infer_content_type(filename: str) -> str:
    _, ext = os.path.splitext(filename.lower())
    return _CONTENT_TYPE_BY_EXTENSION.get(ext, "application/octet-stream")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri or not isinstance(uri, str):
        raise RawUrlError("Document location is empty.")
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise RawUrlError(f"Document location is not an s3:// URI: {uri!r}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not key:
        raise RawUrlError(f"Document location has no key: {uri!r}")
    return bucket, key

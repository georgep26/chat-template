"""Fetch document hierarchy artifacts from S3 with strict validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError


ALLOWED_HIERARCHY_PREFIX = "kb_raw/"
DEFAULT_HIERARCHY_MAX_BYTES = 1_048_576


class HierarchyFetchError(Exception):
    """Raised when a hierarchy artifact cannot be fetched safely."""


@dataclass
class FetchedHierarchy:
    """Fetched hierarchy artifact."""

    payload: dict[str, Any]
    bucket: str
    key: str
    content_length: int


def fetch_hierarchy(
    *,
    location: str,
    expected_bucket: str,
    s3_client: Any | None = None,
    max_bytes: int = DEFAULT_HIERARCHY_MAX_BYTES,
) -> FetchedHierarchy:
    """Fetch and parse a hierarchy JSON artifact from S3."""
    bucket, key = _parse_s3_uri(location)
    if bucket != expected_bucket:
        raise HierarchyFetchError(
            f"Hierarchy bucket mismatch: expected '{expected_bucket}', got '{bucket}'."
        )
    if not key.startswith(ALLOWED_HIERARCHY_PREFIX):
        raise HierarchyFetchError(
            f"Hierarchy key '{key}' is outside allowed prefix '{ALLOWED_HIERARCHY_PREFIX}'."
        )
    client = s3_client or boto3.client("s3")
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise HierarchyFetchError(f"S3 HeadObject failed: {exc}") from exc
    content_length = int(head.get("ContentLength", 0) or 0)
    if content_length > max_bytes:
        raise HierarchyFetchError(
            f"Hierarchy artifact size {content_length} exceeds max_bytes {max_bytes}."
        )
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
    except ClientError as exc:
        raise HierarchyFetchError(f"S3 GetObject failed: {exc}") from exc
    if len(body) > max_bytes:
        raise HierarchyFetchError(
            f"Hierarchy artifact body size {len(body)} exceeds max_bytes {max_bytes}."
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HierarchyFetchError("Hierarchy artifact is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HierarchyFetchError("Hierarchy artifact JSON root is not an object.")
    return FetchedHierarchy(
        payload=payload,
        bucket=bucket,
        key=key,
        content_length=content_length,
    )


def find_hierarchy_node(payload: dict[str, Any], hierarchy_node_id: str) -> dict[str, Any] | None:
    """Find a node by id in a hierarchy payload."""
    for node in payload.get("nodes") or []:
        if isinstance(node, dict) and node.get("hierarchy_node_id") == hierarchy_node_id:
            return node
    return None


def summarize_hierarchy_nodes(
    payload: dict[str, Any],
    *,
    depth: int,
) -> list[dict[str, Any]]:
    """Return bounded node summaries to a requested depth."""
    depth = max(1, min(depth, 10))
    summaries = []
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        heading_path = node.get("heading_path") if isinstance(node.get("heading_path"), list) else []
        if heading_path and len(heading_path) > depth:
            continue
        summaries.append(
            {
                "hierarchy_node_id": node.get("hierarchy_node_id"),
                "label": node.get("label"),
                "title": node.get("title"),
                "node_type": node.get("node_type"),
                "heading_path": heading_path,
                "page_start": node.get("page_start"),
                "page_end": node.get("page_end"),
                "token_estimate": node.get("token_estimate"),
                "children_available": bool(node.get("children")),
            }
        )
    return summaries


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri or not isinstance(uri, str):
        raise HierarchyFetchError("Hierarchy location is empty.")
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise HierarchyFetchError(f"Hierarchy location is not an s3:// URI: {uri!r}")
    key = parsed.path.lstrip("/")
    if not key:
        raise HierarchyFetchError(f"Hierarchy location has no key: {uri!r}")
    return parsed.netloc, key

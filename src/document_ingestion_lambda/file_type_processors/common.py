"""Shared helpers for document type processors."""

from __future__ import annotations


def infer_landing_scope(
    key: str,
    landing_prefix: str,
    default_tenant_id: str,
    default_collection_id: str,
) -> tuple[str, str]:
    """Infer tenant and collection from the landing-zone key."""
    relative_key = key[len(landing_prefix) :] if key.startswith(landing_prefix) else key
    parts = [part for part in relative_key.split("/") if part]
    if len(parts) >= 3:
        return parts[0], parts[1]
    return default_tenant_id, default_collection_id

"""Shared helpers for document type processors."""

from __future__ import annotations

VALID_ACCESS_TIERS = {"restricted", "internal", "public"}


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


def require_default_access_tier(ingestion_config: dict[str, object]) -> str:
    """Return the configured default document access tier."""
    raw_value = ingestion_config.get("default_access_tier")
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(
            "ingestion.default_access_tier is required and must be one of: "
            f"{', '.join(sorted(VALID_ACCESS_TIERS))}"
        )

    access_tier = raw_value.strip().lower()
    if access_tier not in VALID_ACCESS_TIERS:
        raise ValueError(
            "ingestion.default_access_tier must be one of: "
            f"{', '.join(sorted(VALID_ACCESS_TIERS))}"
        )
    return access_tier

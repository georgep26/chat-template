"""Server-side authorization filter builders for the knowledge primitives.

The v1 scope is intentionally minimal:
  - ``tenant_id`` must equal the authenticated user's tenant — this is the
    one filter we trust at the chunk layer, since it is set consistently
    by ingestion and never edited after the fact.
  - ``access_tier`` filtering is **disabled by default at the chunk layer**.
    Reason: chunk sidecars are written once at ingestion time (default
    ``restricted``), while the catalog row's ``access_tier`` can be
    promoted later. Filtering chunks on ``access_tier`` therefore excludes
    chunks whose catalog row says ``public``. The Library API enforces
    ``access_tier`` against the catalog, which is the durable source of
    truth. The Knowledge API will gain a catalog post-filter step in a
    follow-up phase; until then, ``tenant_id`` is the boundary.

Per-document ACL enforcement (``document_access_grants``) is also deferred.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from .filters import build_bedrock_filter, merge_with_and_all

# Empty tuple ⇒ access_tier filter is not applied at the Bedrock layer.
# Override per-call when an explicit list of tiers is desired.
DEFAULT_ACCESS_TIERS: Tuple[str, ...] = ()


def build_bedrock_authz_filter(
    *,
    tenant_id: str,
    access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
) -> Dict[str, Any]:
    """Build the server-side Bedrock retrieval filter for the current user.

    When ``access_tiers`` is empty, only ``tenant_id`` is applied. Otherwise
    a tier constraint is intersected on top.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required to build an authorization filter.")
    tiers = [tier for tier in access_tiers if tier]

    if not tiers:
        return build_bedrock_filter({"key": "tenant_id", "equals": tenant_id})

    spec: Dict[str, Any] = {
        "and_all": [
            {"key": "tenant_id", "equals": tenant_id},
            (
                {"key": "access_tier", "equals": tiers[0]}
                if len(tiers) == 1
                else {"key": "access_tier", "in": list(tiers)}
            ),
        ]
    }
    return build_bedrock_filter(spec)


def merge_user_filter_with_authz(
    *,
    tenant_id: str,
    access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
    user_filter: Any = None,
) -> Dict[str, Any]:
    """Apply the server authz filter as a hard ``andAll`` over the user filter.

    The caller's filter can never widen scope: tenant_id and access_tier are
    always intersected on top.
    """
    authz = build_bedrock_authz_filter(tenant_id=tenant_id, access_tiers=access_tiers)
    user = build_bedrock_filter(user_filter) if user_filter else {}
    return merge_with_and_all(authz, user)


def build_sql_authz_clause(
    *,
    tenant_id: str,
    access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
    parameter_prefix: str = "_authz",
) -> Tuple[str, List[Dict[str, Any]]]:
    """Build a SQL fragment + RDS Data API parameter list for ``document_chunks`` reads.

    Returns ``(clause, parameters)`` where ``clause`` is meant to be appended
    to a ``WHERE`` already in the query, e.g.::

        WHERE chunk_id = :chunk_id { + clause }

    The clause always begins with ``" AND "``.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required to build a SQL authorization clause.")
    tiers = [tier for tier in access_tiers if tier]

    clause = f" AND tenant_id = :{parameter_prefix}_tenant_id"
    params: List[Dict[str, Any]] = [
        {
            "name": f"{parameter_prefix}_tenant_id",
            "value": {"stringValue": tenant_id},
        }
    ]
    if tiers:
        tier_placeholders = ", ".join(
            f":{parameter_prefix}_tier_{i}" for i in range(len(tiers))
        )
        clause += f" AND access_tier IN ({tier_placeholders})"
        for i, tier in enumerate(tiers):
            params.append(
                {
                    "name": f"{parameter_prefix}_tier_{i}",
                    "value": {"stringValue": tier},
                }
            )
    return clause, params

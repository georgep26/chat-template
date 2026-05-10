"""Lambda handler for the Library API (read-side document catalog access)."""

import os
import re
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from auth import (
    AuthConfigurationError,
    AuthError,
    authenticate_request,
    extract_http_metadata,
    json_response,
    normalize_headers,
)
from utils.config import read_config
from utils.logger import get_logger

from .api.models import (
    CatalogDocument,
    GetDocumentResponse,
    ListDocumentsResponse,
)
from .catalog_read_store import CatalogReadStore, create_catalog_read_store

log = get_logger(__name__)


# Cache the store across warm invocations.
_STORE: Optional[CatalogReadStore] = None
_DEFAULT_TENANT_ID: Optional[str] = None


_DEFAULT_ACCESS_TIERS = ("public", "internal")
_DOCUMENT_PATTERN = re.compile(r"^/documents/([^/]+)$")


def _build_store_and_defaults() -> CatalogReadStore:
    """Build the catalog read store and capture the default tenant id from config."""
    global _DEFAULT_TENANT_ID

    config_path = os.getenv("APP_CONFIG_PATH", "config/app_config.yml")
    config = read_config(config_path)

    store = create_catalog_read_store(config)
    if store is None:
        raise RuntimeError(
            "Catalog read store could not be created — Data API settings are missing."
        )

    ingestion_config = config.get("ingestion", {}) or {}
    config_default = ingestion_config.get("default_tenant_id") or ""
    # If the config value is unhydrated (e.g. literal "${DEFAULT_TENANT_ID}"), fall through.
    if config_default.startswith("${"):
        config_default = ""
    _DEFAULT_TENANT_ID = (
        config_default
        or os.getenv("DEFAULT_TENANT_ID")
        or os.getenv("ENVIRONMENT")
        or "default"
    )
    return store


def _get_store() -> CatalogReadStore:
    global _STORE
    if _STORE is None:
        _STORE = _build_store_and_defaults()
    return _STORE


def _get_default_tenant_id() -> str:
    if _DEFAULT_TENANT_ID is None:
        # Triggers tenant id resolution as a side effect of building the store.
        _get_store()
    return _DEFAULT_TENANT_ID or "default"


def _resolve_tenant_id(claims: Dict[str, Any]) -> str:
    """Pick a tenant id from JWT claims, falling back to the configured default."""
    for key in ("custom:tenant_id", "tenant_id"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value
    return _get_default_tenant_id()


def _parse_int(
    value: Optional[str],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid integer value: {value!r}")
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"Value out of range [{minimum}, {maximum}]: {parsed}")
    return parsed


def _route(method: str, path: str) -> Optional[Dict[str, Any]]:
    if path == "/documents" and method == "GET":
        return {"name": "list"}
    match = _DOCUMENT_PATTERN.match(path)
    if match and method == "GET":
        return {"name": "get", "document_id": match.group(1)}
    return None


def _to_catalog_document(row: Dict[str, Any]) -> CatalogDocument:
    return CatalogDocument(**row)


def _handle_list(event: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    qs = event.get("queryStringParameters") or {}
    if not isinstance(qs, dict):
        qs = {}
    limit = _parse_int(qs.get("limit"), default=50, minimum=1, maximum=200)
    offset = _parse_int(qs.get("offset"), default=0, minimum=0, maximum=10_000)

    store = _get_store()
    rows = store.list_documents(
        tenant_id=tenant_id,
        access_tiers=list(_DEFAULT_ACCESS_TIERS),
        limit=limit,
        offset=offset,
    )
    documents: List[CatalogDocument] = [_to_catalog_document(row) for row in rows]
    next_offset = offset + limit if len(rows) == limit else None
    response = ListDocumentsResponse(documents=documents, next_offset=next_offset)
    return json_response(200, response.model_dump())


def _handle_get(document_id: str, tenant_id: str) -> Dict[str, Any]:
    store = _get_store()
    row = store.get_document(
        document_id=document_id,
        tenant_id=tenant_id,
        access_tiers=list(_DEFAULT_ACCESS_TIERS),
    )
    if row is None:
        return json_response(
            404,
            {"error": "DocumentNotFound", "detail": "Document not found."},
        )
    response = GetDocumentResponse(document=_to_catalog_document(row))
    return json_response(200, response.model_dump())


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler for Library API requests."""
    event = event if isinstance(event, dict) else {}
    headers = normalize_headers(event.get("headers"))
    method, path, _trace_id = extract_http_metadata(event)

    if not method:
        return json_response(400, {"error": "BadRequest", "detail": "Missing HTTP method."})

    method = method.upper()
    if method == "OPTIONS":
        return json_response(204, {})

    route = _route(method, path or "/")
    if route is None:
        return json_response(404, {"error": "NotFound", "detail": "Path not found."})

    try:
        auth_context = authenticate_request(headers)
    except AuthError as exc:
        log.warning("Authentication failed: %s", exc.code)
        return json_response(exc.status_code, {"error": exc.code, "detail": exc.detail})
    except AuthConfigurationError as exc:
        log.error("Authentication configuration error: %s", str(exc))
        return json_response(
            500,
            {
                "error": "AuthConfigurationError",
                "detail": "Server auth configuration is invalid.",
            },
        )

    tenant_id = _resolve_tenant_id(auth_context.claims or {})

    try:
        if route["name"] == "list":
            return _handle_list(event, tenant_id)
        if route["name"] == "get":
            return _handle_get(route["document_id"], tenant_id)
        return json_response(404, {"error": "NotFound", "detail": "Path not found."})
    except ValidationError as exc:
        return json_response(400, {"error": "ValidationError", "detail": str(exc)})
    except ValueError as exc:
        return json_response(400, {"error": "BadRequest", "detail": str(exc)})
    except Exception:
        log.exception("Unhandled error while processing Library API request.")
        return json_response(
            500,
            {"error": "InternalServerError", "detail": "An unexpected error occurred."},
        )

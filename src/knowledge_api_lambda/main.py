"""Lambda handler for the Knowledge API (agent-callable retrieval primitives)."""

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
    parse_event_body,
)
from knowledge.artifact_fetch import (
    ArtifactFetchError,
    DEFAULT_MAX_BYTES,
    fetch_chunk_content,
)
from knowledge.chunk_read import ChunkReadStore, create_chunk_read_store
from knowledge.filters import FilterError
from knowledge.vector_search import search
from utils.config import read_config
from utils.logger import get_logger

from .api.models import (
    ChunkDetail,
    ChunkSummary,
    GetChunkResponse,
    ListChunksResponse,
    SearchRequest,
    SearchResponse,
    SearchSource,
)

log = get_logger(__name__)


# Cache state across warm invocations.
_STORE: Optional[ChunkReadStore] = None
_DEFAULT_TENANT_ID: Optional[str] = None
_KB_ID: Optional[str] = None
_KB_REGION: Optional[str] = None
_DOCUMENT_BUCKET: Optional[str] = None
_ARTIFACT_MAX_BYTES: int = DEFAULT_MAX_BYTES


_DOCUMENT_CHUNKS_PATTERN = re.compile(r"^/documents/([^/]+)/chunks$")
_CHUNK_PATTERN = re.compile(r"^/chunks/([^/]+)$")


def _load_runtime_config() -> ChunkReadStore:
    """Read the hydrated app config and cache the values we need at request time."""
    global _DEFAULT_TENANT_ID, _KB_ID, _KB_REGION, _DOCUMENT_BUCKET, _ARTIFACT_MAX_BYTES

    config_path = os.getenv("APP_CONFIG_PATH", "config/app_config.yml")
    config = read_config(config_path)

    store = create_chunk_read_store(config)
    if store is None:
        raise RuntimeError(
            "Chunk read store could not be created — Data API settings are missing."
        )

    rag_chat_config = config.get("rag_chat", {}) or {}
    retrieval_config = rag_chat_config.get("retrieval", {}) or {}
    _KB_ID = (retrieval_config.get("knowledge_base_id") or "").strip() or None
    _KB_REGION = retrieval_config.get("region") or "us-east-1"

    ingestion_config = config.get("ingestion", {}) or {}
    bucket = (ingestion_config.get("document_bucket_name") or "").strip()
    if bucket and not bucket.startswith("${"):
        _DOCUMENT_BUCKET = bucket
    else:
        _DOCUMENT_BUCKET = os.getenv("DOCUMENT_BUCKET_NAME") or None

    config_default = (ingestion_config.get("default_tenant_id") or "").strip()
    if config_default.startswith("${"):
        config_default = ""
    _DEFAULT_TENANT_ID = (
        config_default
        or os.getenv("DEFAULT_TENANT_ID")
        or os.getenv("ENVIRONMENT")
        or "default"
    )

    max_bytes_env = os.getenv("ARTIFACT_MAX_BYTES")
    if max_bytes_env:
        try:
            _ARTIFACT_MAX_BYTES = max(1024, int(max_bytes_env))
        except (TypeError, ValueError):
            log.warning("Ignoring invalid ARTIFACT_MAX_BYTES=%r", max_bytes_env)

    return store


def _get_store() -> ChunkReadStore:
    global _STORE
    if _STORE is None:
        _STORE = _load_runtime_config()
    return _STORE


def _get_kb_id() -> str:
    if _KB_ID is None:
        _get_store()
    if not _KB_ID:
        raise RuntimeError("knowledge_base_id is not configured.")
    return _KB_ID


def _get_kb_region() -> str:
    if _KB_REGION is None:
        _get_store()
    return _KB_REGION or "us-east-1"


def _get_document_bucket() -> str:
    if _DOCUMENT_BUCKET is None:
        _get_store()
    if not _DOCUMENT_BUCKET:
        raise RuntimeError("document_bucket_name is not configured.")
    return _DOCUMENT_BUCKET


def _get_default_tenant_id() -> str:
    if _DEFAULT_TENANT_ID is None:
        _get_store()
    return _DEFAULT_TENANT_ID or "default"


def _resolve_tenant_id(claims: Dict[str, Any]) -> str:
    for key in ("custom:tenant_id", "tenant_id"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value
    return _get_default_tenant_id()


def _parse_int(
    value: Optional[str], *, default: int, minimum: int, maximum: int
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
    if path == "/search" and method == "POST":
        return {"name": "search"}
    chunks_match = _DOCUMENT_CHUNKS_PATTERN.match(path)
    if chunks_match and method == "GET":
        return {"name": "list_chunks", "document_id": chunks_match.group(1)}
    chunk_match = _CHUNK_PATTERN.match(path)
    if chunk_match and method == "GET":
        return {"name": "get_chunk", "chunk_id": chunk_match.group(1)}
    return None


# Handlers ---------------------------------------------------------------


def _row_to_chunk_summary(row: Dict[str, Any]) -> ChunkSummary:
    return ChunkSummary(**{k: row.get(k) for k in ChunkSummary.model_fields})


def _row_to_chunk_detail(row: Dict[str, Any], content: str) -> ChunkDetail:
    payload = {k: row.get(k) for k in ChunkDetail.model_fields if k != "content"}
    payload["content"] = content
    return ChunkDetail(**payload)


def _handle_search(event: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    body = parse_event_body(event)
    request = SearchRequest(**body)

    results = search(
        query=request.query,
        knowledge_base_id=_get_kb_id(),
        region=_get_kb_region(),
        tenant_id=tenant_id,
        user_filter=request.filters,
        top_k=request.top_k,
    )

    sources = [
        SearchSource(
            document_id=r.document_id,
            chunk_id=r.chunk_id,
            score=r.score,
            source_type=r.source_type,
            chunk=r.chunk,
            metadata=r.metadata,
        )
        for r in results
    ]
    response = SearchResponse(sources=sources, result_count=len(sources))
    return json_response(200, response.model_dump())


def _handle_list_chunks(
    event: Dict[str, Any], document_id: str, tenant_id: str
) -> Dict[str, Any]:
    qs = event.get("queryStringParameters") or {}
    if not isinstance(qs, dict):
        qs = {}
    limit = _parse_int(qs.get("limit"), default=50, minimum=1, maximum=200)
    offset = _parse_int(qs.get("offset"), default=0, minimum=0, maximum=10_000)

    rows = _get_store().list_chunks_for_document(
        document_id=document_id,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
    )
    chunks: List[ChunkSummary] = [_row_to_chunk_summary(row) for row in rows]
    next_offset = offset + limit if len(rows) == limit else None
    response = ListChunksResponse(chunks=chunks, next_offset=next_offset)
    return json_response(200, response.model_dump())


def _handle_get_chunk(chunk_id: str, tenant_id: str) -> Dict[str, Any]:
    row = _get_store().get_chunk(chunk_id=chunk_id, tenant_id=tenant_id)
    if row is None:
        return json_response(
            404, {"error": "ChunkNotFound", "detail": "Chunk not found."}
        )

    location = row.get("location") or ""
    try:
        artifact = fetch_chunk_content(
            location=location,
            expected_bucket=_get_document_bucket(),
            max_bytes=_ARTIFACT_MAX_BYTES,
        )
    except ArtifactFetchError as exc:
        log.warning("Artifact fetch failed for chunk %s: %s", chunk_id, exc)
        return json_response(
            502,
            {
                "error": "ArtifactFetchError",
                "detail": "Could not fetch chunk content from storage.",
            },
        )

    response = GetChunkResponse(chunk=_row_to_chunk_detail(row, artifact.content))
    return json_response(200, response.model_dump())


# Lambda entrypoint ------------------------------------------------------


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda handler for Knowledge API requests."""
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
        if route["name"] == "search":
            return _handle_search(event, tenant_id)
        if route["name"] == "list_chunks":
            return _handle_list_chunks(event, route["document_id"], tenant_id)
        if route["name"] == "get_chunk":
            return _handle_get_chunk(route["chunk_id"], tenant_id)
        return json_response(404, {"error": "NotFound", "detail": "Path not found."})
    except FilterError as exc:
        return json_response(400, {"error": "InvalidFilter", "detail": str(exc)})
    except ValidationError as exc:
        return json_response(400, {"error": "ValidationError", "detail": str(exc)})
    except ValueError as exc:
        return json_response(400, {"error": "BadRequest", "detail": str(exc)})
    except Exception:
        log.exception("Unhandled error while processing Knowledge API request.")
        return json_response(
            500,
            {"error": "InternalServerError", "detail": "An unexpected error occurred."},
        )

"""Aurora Data API read store for ``document_chunks``.

Read-side counterpart to ``src/document_ingestion_lambda/catalog_store.py``'s
``upsert_chunks``. Mirrors the shape and retry policy of
``src/library_api_lambda/catalog_read_store.py``.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Sequence

import boto3
from botocore.exceptions import ClientError

from utils.logger import get_logger

from .authz import DEFAULT_ACCESS_TIERS, build_sql_authz_clause

log = get_logger(__name__)


_SELECT_COLUMNS = (
    "chunk_id, document_id, tenant_id, collection_id, chunk_type, chunk_index, "
    "document_title, title, chapter, section, page_start, page_end, "
    "location, source_location, content_type, token_count, char_count, "
    "access_tier, metadata::text"
)

_COLUMN_ORDER = [
    "chunk_id",
    "document_id",
    "tenant_id",
    "collection_id",
    "chunk_type",
    "chunk_index",
    "document_title",
    "title",
    "chapter",
    "section",
    "page_start",
    "page_end",
    "location",
    "source_location",
    "content_type",
    "token_count",
    "char_count",
    "access_tier",
    "metadata",
]


class ChunkReadStore:
    """Read-only access to ``document_chunks`` via RDS Data API."""

    def __init__(
        self,
        db_cluster_arn: str,
        db_credentials_secret_arn: str,
        database_name: str,
        region: str = "us-east-1",
        rds_data_client: Any | None = None,
        max_retries: int = 10,
    ) -> None:
        self._db_cluster_arn = db_cluster_arn
        self._db_credentials_secret_arn = db_credentials_secret_arn
        self._database_name = database_name
        self._region = region
        self._rds_data = rds_data_client or boto3.client("rds-data", region_name=region)
        self._max_retries = max_retries

    # Public read API ---------------------------------------------------

    def get_chunk(
        self,
        *,
        chunk_id: str,
        tenant_id: str,
        access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single authorized chunk row or ``None``."""
        clause, authz_params = build_sql_authz_clause(
            tenant_id=tenant_id, access_tiers=access_tiers
        )
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM document_chunks
            WHERE chunk_id = :chunk_id{clause}
            LIMIT 1
        """
        params: List[Dict[str, Any]] = [self._string_parameter("chunk_id", chunk_id)]
        params.extend(authz_params)

        response = self._execute_statement(sql, params)
        records = response.get("records", [])
        if not records:
            return None
        return self._row_to_dict(records[0])

    def list_chunks_for_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        limit: int,
        offset: int,
        access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
    ) -> List[Dict[str, Any]]:
        """List authorized chunks for a document ordered by chunk_index."""
        clause, authz_params = build_sql_authz_clause(
            tenant_id=tenant_id, access_tiers=access_tiers
        )
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM document_chunks
            WHERE document_id = :document_id{clause}
            ORDER BY chunk_index ASC NULLS LAST, chunk_id ASC
            LIMIT :limit OFFSET :offset
        """
        params: List[Dict[str, Any]] = [
            self._string_parameter("document_id", document_id),
            self._long_parameter("limit", limit),
            self._long_parameter("offset", offset),
        ]
        params.extend(authz_params)

        response = self._execute_statement(sql, params)
        return [self._row_to_dict(record) for record in response.get("records", [])]

    def get_chunks_by_ids(
        self,
        *,
        chunk_ids: List[str],
        tenant_id: str,
        access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch multiple authorized chunks keyed by chunk_id."""
        unique_ids = list(dict.fromkeys([chunk_id for chunk_id in chunk_ids if chunk_id]))
        if not unique_ids:
            return {}
        clause, authz_params = build_sql_authz_clause(
            tenant_id=tenant_id, access_tiers=access_tiers
        )
        id_placeholders = ", ".join(f":chunk_id_{i}" for i in range(len(unique_ids)))
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM document_chunks
            WHERE chunk_id IN ({id_placeholders}){clause}
        """
        params: List[Dict[str, Any]] = [
            self._string_parameter(f"chunk_id_{index}", chunk_id)
            for index, chunk_id in enumerate(unique_ids)
        ]
        params.extend(authz_params)
        response = self._execute_statement(sql, params)
        rows = [self._row_to_dict(record) for record in response.get("records", [])]
        return {row["chunk_id"]: row for row in rows if row.get("chunk_id")}

    # Helpers -----------------------------------------------------------

    def _row_to_dict(self, record: List[Dict[str, Any]]) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        for index, column in enumerate(_COLUMN_ORDER):
            row[column] = self._convert(record[index])

        metadata_raw = row.get("metadata")
        if isinstance(metadata_raw, str) and metadata_raw:
            try:
                row["metadata"] = json.loads(metadata_raw)
            except (json.JSONDecodeError, TypeError):
                row["metadata"] = {}
        elif metadata_raw is None:
            row["metadata"] = {}

        return row

    def _convert(self, col: Dict[str, Any]) -> Any:
        if "isNull" in col and col["isNull"]:
            return None
        if "stringValue" in col:
            return col["stringValue"]
        if "longValue" in col:
            return col["longValue"]
        if "doubleValue" in col:
            return col["doubleValue"]
        if "booleanValue" in col:
            return col["booleanValue"]
        return None

    def _string_parameter(self, name: str, value: str) -> Dict[str, Any]:
        return {"name": name, "value": {"stringValue": str(value)}}

    def _long_parameter(self, name: str, value: int) -> Dict[str, Any]:
        return {"name": name, "value": {"longValue": int(value)}}

    def _execute_statement(
        self,
        sql: str,
        parameters: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        retry_delay = 1.0
        last_exception: Optional[ClientError] = None

        for attempt in range(self._max_retries):
            try:
                request_params = {
                    "resourceArn": self._db_cluster_arn,
                    "secretArn": self._db_credentials_secret_arn,
                    "database": self._database_name,
                    "sql": sql,
                }
                if parameters:
                    request_params["parameters"] = parameters
                return self._rds_data.execute_statement(**request_params)
            except ClientError as exc:
                last_exception = exc
                error_code = exc.response.get("Error", {}).get("Code", "")
                error_msg = str(exc).lower()
                is_retryable = (
                    error_code
                    in {
                        "BadRequestException",
                        "DatabaseResumingException",
                        "ForbiddenException",
                        "ServiceUnavailableError",
                        "StatementTimeoutException",
                    }
                    or "throttling" in error_msg
                    or "toomanyrequests" in error_msg
                )
                if not is_retryable or attempt >= self._max_retries - 1:
                    raise
                if error_code == "DatabaseResumingException" or "databaseresuming" in error_msg:
                    log.warning("Database is resuming before chunk read.")
                    time.sleep(5)
                else:
                    log.warning(
                        "Knowledge chunk Data API call failed; retrying %s/%s: %s",
                        attempt + 1,
                        self._max_retries,
                        exc,
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30.0)

        if last_exception:
            raise last_exception
        raise RuntimeError("Failed to execute chunk read statement")


def create_chunk_read_store(config: Dict[str, Any]) -> Optional[ChunkReadStore]:
    """Create a chunk read store from hydrated app config when Data API settings are present."""
    store_config = config.get("rag_chat", {}).get("chat_history_store", {})
    db_cluster_arn = store_config.get("db_cluster_arn", "")
    secret_arn = store_config.get("db_credentials_secret_arn", "")
    database_name = store_config.get("database_name", "")
    region = store_config.get("region") or config.get("rag_chat", {}).get(
        "retrieval", {}
    ).get("region", "us-east-1")

    required_values = [db_cluster_arn, secret_arn, database_name]
    if not all(required_values) or any(
        str(value).startswith("${") for value in required_values
    ):
        log.warning("Chunk read store is disabled because Data API settings are missing.")
        return None

    return ChunkReadStore(
        db_cluster_arn=db_cluster_arn,
        db_credentials_secret_arn=secret_arn,
        database_name=database_name,
        region=region,
    )

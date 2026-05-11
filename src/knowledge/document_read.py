"""Aurora Data API read store for ``document_catalog`` rows.

Shared primitive used by the Library API (single-document fetch path) and the
Knowledge API (raw-document URL lookup). Mirrors the shape and retry policy of
``src/knowledge/chunk_read.py``; the catalog list endpoint stays in the
Library API package since its filtered-list shape is library-specific.
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
    "document_id, tenant_id, collection_id, document_title, document_description, "
    "filename, document_type, source_status, access_tier, location, source_location, "
    "publish_date::text, version_label, jurisdiction, code_family, "
    "uploaded_at::text, ingested_at::text, metadata::text"
)

_COLUMN_ORDER = [
    "document_id",
    "tenant_id",
    "collection_id",
    "document_title",
    "document_description",
    "filename",
    "document_type",
    "source_status",
    "access_tier",
    "location",
    "source_location",
    "publish_date",
    "version_label",
    "jurisdiction",
    "code_family",
    "uploaded_at",
    "ingested_at",
    "metadata",
]


class DocumentReadStore:
    """Read-only single-document access to ``document_catalog`` via RDS Data API."""

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

    def get_document(
        self,
        *,
        document_id: str,
        tenant_id: str,
        access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single authorized catalog row or ``None``.

        Failed ingestion rows are excluded — the caller cannot read raw bytes
        or metadata for a document that never finished processing.
        """
        clause, authz_params = build_sql_authz_clause(
            tenant_id=tenant_id, access_tiers=access_tiers
        )
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM document_catalog
            WHERE document_id = :document_id
              AND source_status <> 'failed'{clause}
            LIMIT 1
        """
        params: List[Dict[str, Any]] = [
            self._string_parameter("document_id", document_id),
        ]
        params.extend(authz_params)

        response = self._execute_statement(sql, params)
        records = response.get("records", [])
        if not records:
            return None
        return self._row_to_dict(records[0])

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
                    log.warning("Database is resuming before document read.")
                    time.sleep(5)
                else:
                    log.warning(
                        "Document read Data API call failed; retrying %s/%s: %s",
                        attempt + 1,
                        self._max_retries,
                        exc,
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30.0)

        if last_exception:
            raise last_exception
        raise RuntimeError("Failed to execute document read statement")


def create_document_read_store(config: Dict[str, Any]) -> Optional[DocumentReadStore]:
    """Create a document read store from hydrated app config when Data API settings are present."""
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
        log.warning("Document read store is disabled because Data API settings are missing.")
        return None

    return DocumentReadStore(
        db_cluster_arn=db_cluster_arn,
        db_credentials_secret_arn=secret_arn,
        database_name=database_name,
        region=region,
    )

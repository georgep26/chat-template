"""Aurora Data API document catalog store for ingestion tracking."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

from utils.logger import get_logger

log = get_logger(__name__)


class DocumentCatalogStore:
    """Persists document ingestion jobs and catalog rows through RDS Data API."""

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

    def start_ingestion_job(
        self,
        *,
        job_id: str,
        landing_s3_uri: str,
        tenant_id: str,
        collection_id: str,
        document_type: str,
        filename: str,
        input_event: dict[str, Any],
    ) -> None:
        """Create or replace the ingestion job row for a landing object."""
        sql = """
            INSERT INTO document_ingestion_jobs (
                job_id,
                landing_s3_uri,
                tenant_id,
                collection_id,
                document_type,
                filename,
                status,
                stage,
                input_event,
                error,
                metadata,
                started_at,
                completed_at,
                updated_at
            )
            VALUES (
                CAST(:job_id AS uuid),
                :landing_s3_uri,
                :tenant_id,
                :collection_id,
                :document_type,
                :filename,
                'processing',
                'started',
                CAST(:input_event AS jsonb),
                NULL,
                '{}'::jsonb,
                NOW(),
                NULL,
                NOW()
            )
            ON CONFLICT (landing_s3_uri) DO UPDATE SET
                job_id = EXCLUDED.job_id,
                document_id = NULL,
                tenant_id = EXCLUDED.tenant_id,
                collection_id = EXCLUDED.collection_id,
                document_type = EXCLUDED.document_type,
                filename = EXCLUDED.filename,
                status = 'processing',
                stage = 'started',
                input_event = EXCLUDED.input_event,
                error = NULL,
                metadata = '{}'::jsonb,
                started_at = NOW(),
                completed_at = NULL,
                updated_at = NOW()
        """
        self._execute_statement(
            sql,
            [
                self._string_parameter("job_id", job_id),
                self._string_parameter("landing_s3_uri", landing_s3_uri),
                self._string_parameter("tenant_id", tenant_id),
                self._string_parameter("collection_id", collection_id),
                self._string_parameter("document_type", document_type),
                self._string_parameter("filename", filename),
                self._json_parameter("input_event", input_event),
            ],
        )

    def upsert_document(self, metadata: dict[str, Any]) -> None:
        """Insert or update the app-owned document catalog row."""
        sql = """
            INSERT INTO document_catalog (
                document_id,
                tenant_id,
                collection_id,
                metadata_schema_version,
                document_title,
                document_description,
                document_type,
                filename,
                landing_s3_uri,
                source_location,
                location,
                source_status,
                access_tier,
                publish_date,
                version_label,
                jurisdiction,
                code_family,
                uploaded_at,
                ingested_at,
                metadata,
                processing,
                updated_at
            )
            VALUES (
                :document_id,
                :tenant_id,
                :collection_id,
                :metadata_schema_version,
                :document_title,
                :document_description,
                :document_type,
                :filename,
                :landing_s3_uri,
                :source_location,
                :location,
                :source_status,
                :access_tier,
                CAST(:publish_date AS date),
                :version_label,
                :jurisdiction,
                :code_family,
                CAST(:uploaded_at AS timestamptz),
                CAST(:ingested_at AS timestamptz),
                CAST(:metadata AS jsonb),
                CAST(:processing AS jsonb),
                NOW()
            )
            ON CONFLICT (document_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                collection_id = EXCLUDED.collection_id,
                metadata_schema_version = EXCLUDED.metadata_schema_version,
                document_title = EXCLUDED.document_title,
                document_description = EXCLUDED.document_description,
                document_type = EXCLUDED.document_type,
                filename = EXCLUDED.filename,
                landing_s3_uri = EXCLUDED.landing_s3_uri,
                source_location = EXCLUDED.source_location,
                location = EXCLUDED.location,
                source_status = EXCLUDED.source_status,
                access_tier = EXCLUDED.access_tier,
                publish_date = EXCLUDED.publish_date,
                version_label = EXCLUDED.version_label,
                jurisdiction = EXCLUDED.jurisdiction,
                code_family = EXCLUDED.code_family,
                uploaded_at = EXCLUDED.uploaded_at,
                ingested_at = EXCLUDED.ingested_at,
                metadata = EXCLUDED.metadata,
                processing = EXCLUDED.processing,
                updated_at = NOW()
        """
        self._execute_statement(
            sql,
            [
                self._string_parameter("document_id", metadata["document_id"]),
                self._string_parameter("tenant_id", metadata["tenant_id"]),
                self._string_parameter("collection_id", metadata["collection_id"]),
                self._string_parameter(
                    "metadata_schema_version",
                    metadata.get("metadata_schema_version", "1.0"),
                ),
                self._string_parameter("document_title", metadata["document_title"]),
                self._nullable_string_parameter(
                    "document_description",
                    metadata.get("document_description"),
                ),
                self._string_parameter("document_type", metadata["document_type"]),
                self._string_parameter("filename", metadata["filename"]),
                self._nullable_string_parameter("landing_s3_uri", metadata.get("landing_s3_uri")),
                self._string_parameter("source_location", metadata["source_location"]),
                self._string_parameter("location", metadata["location"]),
                self._string_parameter(
                    "source_status",
                    metadata.get("source_status", "processing"),
                ),
                self._string_parameter("access_tier", metadata.get("access_tier", "restricted")),
                self._nullable_string_parameter("publish_date", metadata.get("publish_date")),
                self._nullable_string_parameter("version_label", metadata.get("version_label")),
                self._nullable_string_parameter("jurisdiction", metadata.get("jurisdiction")),
                self._nullable_string_parameter("code_family", metadata.get("code_family")),
                self._nullable_string_parameter("uploaded_at", metadata.get("uploaded_at")),
                self._nullable_string_parameter("ingested_at", metadata.get("ingested_at")),
                self._json_parameter("metadata", self._catalog_metadata_payload(metadata)),
                self._json_parameter("processing", metadata.get("processing", {})),
            ],
        )

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """Insert or update app-owned document chunk rows."""
        if not chunks:
            return

        sql = """
            INSERT INTO document_chunks (
                chunk_id,
                document_id,
                tenant_id,
                collection_id,
                metadata_schema_version,
                chunk_type,
                chunk_index,
                document_title,
                title,
                chapter,
                section,
                page_start,
                page_end,
                location,
                source_location,
                content_type,
                token_count,
                char_count,
                embedding_model_id,
                access_tier,
                metadata,
                processing,
                updated_at
            )
            VALUES (
                :chunk_id,
                :document_id,
                :tenant_id,
                :collection_id,
                :metadata_schema_version,
                :chunk_type,
                :chunk_index,
                :document_title,
                :title,
                :chapter,
                :section,
                :page_start,
                :page_end,
                :location,
                :source_location,
                :content_type,
                :token_count,
                :char_count,
                :embedding_model_id,
                :access_tier,
                CAST(:metadata AS jsonb),
                CAST(:processing AS jsonb),
                NOW()
            )
            ON CONFLICT (chunk_id) DO UPDATE SET
                document_id = EXCLUDED.document_id,
                tenant_id = EXCLUDED.tenant_id,
                collection_id = EXCLUDED.collection_id,
                metadata_schema_version = EXCLUDED.metadata_schema_version,
                chunk_type = EXCLUDED.chunk_type,
                chunk_index = EXCLUDED.chunk_index,
                document_title = EXCLUDED.document_title,
                title = EXCLUDED.title,
                chapter = EXCLUDED.chapter,
                section = EXCLUDED.section,
                page_start = EXCLUDED.page_start,
                page_end = EXCLUDED.page_end,
                location = EXCLUDED.location,
                source_location = EXCLUDED.source_location,
                content_type = EXCLUDED.content_type,
                token_count = EXCLUDED.token_count,
                char_count = EXCLUDED.char_count,
                embedding_model_id = EXCLUDED.embedding_model_id,
                access_tier = EXCLUDED.access_tier,
                metadata = EXCLUDED.metadata,
                processing = EXCLUDED.processing,
                updated_at = NOW()
        """
        parameter_sets = [self._chunk_parameters(chunk) for chunk in chunks]
        for start in range(0, len(parameter_sets), 100):
            self._execute_batch_statement(sql, parameter_sets[start : start + 100])

    def complete_ingestion_job(
        self,
        *,
        job_id: str,
        document_id: str | None,
        status: str,
        stage: str,
        metadata: dict[str, Any],
    ) -> None:
        """Mark an ingestion job as completed."""
        sql = """
            UPDATE document_ingestion_jobs
            SET document_id = :document_id,
                status = :status,
                stage = :stage,
                metadata = CAST(:metadata AS jsonb),
                completed_at = NOW(),
                updated_at = NOW()
            WHERE job_id = CAST(:job_id AS uuid)
        """
        self._execute_statement(
            sql,
            [
                self._string_parameter("job_id", job_id),
                self._nullable_string_parameter("document_id", document_id),
                self._string_parameter("status", status),
                self._string_parameter("stage", stage),
                self._json_parameter("metadata", metadata),
            ],
        )

    def fail_ingestion_job(
        self,
        *,
        job_id: str,
        stage: str,
        error: dict[str, Any],
    ) -> None:
        """Mark an ingestion job as failed."""
        sql = """
            UPDATE document_ingestion_jobs
            SET status = 'failed',
                stage = :stage,
                error = CAST(:error AS jsonb),
                completed_at = NOW(),
                updated_at = NOW()
            WHERE job_id = CAST(:job_id AS uuid)
        """
        self._execute_statement(
            sql,
            [
                self._string_parameter("job_id", job_id),
                self._string_parameter("stage", stage),
                self._json_parameter("error", error),
            ],
        )

    def _execute_statement(
        self,
        sql: str,
        parameters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        retry_delay = 1.0
        last_exception: ClientError | None = None

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
                    log.warning("Database is resuming before document catalog write.")
                    time.sleep(5)
                else:
                    log.warning(
                        "Document catalog Data API call failed; retrying attempt %s/%s: %s",
                        attempt + 1,
                        self._max_retries,
                        exc,
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30.0)

        if last_exception:
            raise last_exception
        raise RuntimeError("Failed to execute document catalog statement")

    def _execute_batch_statement(
        self,
        sql: str,
        parameter_sets: list[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        retry_delay = 1.0
        last_exception: ClientError | None = None

        for attempt in range(self._max_retries):
            try:
                return self._rds_data.batch_execute_statement(
                    resourceArn=self._db_cluster_arn,
                    secretArn=self._db_credentials_secret_arn,
                    database=self._database_name,
                    sql=sql,
                    parameterSets=parameter_sets,
                )
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
                    log.warning("Database is resuming before document chunk catalog write.")
                    time.sleep(5)
                else:
                    log.warning(
                        "Document chunk Data API batch failed; retrying attempt %s/%s: %s",
                        attempt + 1,
                        self._max_retries,
                        exc,
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30.0)

        if last_exception:
            raise last_exception
        raise RuntimeError("Failed to execute document chunk catalog batch")

    def _chunk_parameters(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            self._string_parameter("chunk_id", chunk["chunk_id"]),
            self._string_parameter("document_id", chunk["document_id"]),
            self._string_parameter("tenant_id", chunk["tenant_id"]),
            self._string_parameter("collection_id", chunk["collection_id"]),
            self._string_parameter(
                "metadata_schema_version",
                chunk.get("metadata_schema_version", "1.0"),
            ),
            self._string_parameter("chunk_type", chunk["chunk_type"]),
            self._nullable_integer_parameter("chunk_index", chunk.get("chunk_index")),
            self._nullable_string_parameter("document_title", chunk.get("document_title")),
            self._nullable_string_parameter("title", chunk.get("title")),
            self._nullable_string_parameter("chapter", chunk.get("chapter")),
            self._nullable_string_parameter("section", chunk.get("section")),
            self._nullable_integer_parameter("page_start", chunk.get("page_start")),
            self._nullable_integer_parameter("page_end", chunk.get("page_end")),
            self._string_parameter("location", chunk["location"]),
            self._string_parameter("source_location", chunk["source_location"]),
            self._string_parameter("content_type", chunk.get("content_type", "text/markdown")),
            self._nullable_integer_parameter("token_count", chunk.get("token_count")),
            self._nullable_integer_parameter("char_count", chunk.get("char_count")),
            self._nullable_string_parameter("embedding_model_id", chunk.get("embedding_model_id")),
            self._string_parameter("access_tier", chunk.get("access_tier", "restricted")),
            self._json_parameter("metadata", self._chunk_metadata_payload(chunk)),
            self._json_parameter("processing", chunk.get("processing", {})),
        ]

    def _catalog_metadata_payload(self, metadata: dict[str, Any]) -> dict[str, Any]:
        excluded = {
            "document_id",
            "tenant_id",
            "collection_id",
            "metadata_schema_version",
            "document_title",
            "document_description",
            "document_type",
            "filename",
            "landing_s3_uri",
            "source_location",
            "location",
            "source_status",
            "access_tier",
            "publish_date",
            "version_label",
            "jurisdiction",
            "code_family",
            "uploaded_at",
            "ingested_at",
            "processing",
        }
        return {key: value for key, value in metadata.items() if key not in excluded}

    def _chunk_metadata_payload(self, chunk: dict[str, Any]) -> dict[str, Any]:
        excluded = {
            "chunk_id",
            "document_id",
            "tenant_id",
            "collection_id",
            "metadata_schema_version",
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
            "embedding_model_id",
            "access_tier",
            "processing",
        }
        return {key: value for key, value in chunk.items() if key not in excluded}

    def _string_parameter(self, name: str, value: str) -> dict[str, Any]:
        return {"name": name, "value": {"stringValue": str(value)}}

    def _nullable_string_parameter(self, name: str, value: Any) -> dict[str, Any]:
        if value is None or value == "":
            return {"name": name, "value": {"isNull": True}}
        if isinstance(value, datetime):
            value = value.isoformat()
        return {"name": name, "value": {"stringValue": str(value)}}

    def _json_parameter(self, name: str, value: dict[str, Any]) -> dict[str, Any]:
        return {"name": name, "value": {"stringValue": json.dumps(value, default=str)}}

    def _nullable_integer_parameter(self, name: str, value: Any) -> dict[str, Any]:
        if value is None or value == "":
            return {"name": name, "value": {"isNull": True}}
        return {"name": name, "value": {"longValue": int(value)}}


def create_document_catalog_store(config: dict[str, Any]) -> DocumentCatalogStore | None:
    """Create a document catalog store from hydrated app config when available."""
    store_config = config.get("rag_chat", {}).get("chat_history_store", {})
    db_cluster_arn = store_config.get("db_cluster_arn", "")
    secret_arn = store_config.get("db_credentials_secret_arn", "")
    database_name = store_config.get("database_name", "")
    region = store_config.get("region") or config.get("rag_chat", {}).get("retrieval", {}).get(
        "region",
        "us-east-1",
    )

    required_values = [db_cluster_arn, secret_arn, database_name]
    if not all(required_values) or any(str(value).startswith("${") for value in required_values):
        log.warning(
            "Document catalog store is disabled because hydrated Data API settings are missing."
        )
        return None

    return DocumentCatalogStore(
        db_cluster_arn=db_cluster_arn,
        db_credentials_secret_arn=secret_arn,
        database_name=database_name,
        region=region,
    )

"""S3 landing-zone document ingestion Lambda.

The first implementation focuses on PDFs. Other document types are recognized
with explicit branches so future implementation routes are visible, but they
raise ``NotImplementedError`` until those routes are built.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote_plus

import boto3

from document_ingestion_lambda.catalog_store import (
    DocumentCatalogStore,
    create_document_catalog_store,
)
from document_ingestion_lambda.file_type_processors.common import infer_landing_scope
from document_ingestion_lambda.file_type_processors.pdf import PdfProcessor
from document_ingestion_lambda.models import IngestionResult, S3ObjectRef
from utils.config import read_config
from utils.logger import get_logger

log = get_logger(__name__)


class DocumentIngestionService:
    """Processes landing-zone objects into the durable document layout."""

    def __init__(
        self,
        s3_client: Any,
        ingestion_config: dict[str, Any],
        catalog_store: DocumentCatalogStore | None = None,
        random_suffix: Callable[[], str] | None = None,
    ) -> None:
        self._s3 = s3_client
        self._ingestion_config = ingestion_config
        self._catalog_store = catalog_store
        self._random_suffix = random_suffix or (lambda: secrets.token_hex(5))
        self._pdf_processor = PdfProcessor(
            s3_client=s3_client,
            ingestion_config=ingestion_config,
            random_suffix=self._random_suffix,
        )

    def process_object(self, obj: S3ObjectRef) -> IngestionResult:
        landing_prefix = self._ingestion_config["landing_prefix"]
        if obj.key.endswith("/"):
            return IngestionResult(
                landing_s3_uri=obj.uri,
                status="skipped",
                message="Object is an S3 folder marker.",
            )

        if not obj.key.startswith(landing_prefix):
            return IngestionResult(
                landing_s3_uri=obj.uri,
                status="skipped",
                message=f"Object is outside landing prefix {landing_prefix}",
            )

        destination_bucket = self._ingestion_config["document_bucket_name"]
        head = self._s3.head_object(Bucket=obj.bucket, Key=obj.key)
        document_type = identify_document_type(obj.key, head.get("ContentType", ""))
        tenant_id, collection_id = infer_landing_scope(
            obj.key,
            landing_prefix,
            self._ingestion_config["default_tenant_id"],
            self._ingestion_config["default_collection_id"],
        )
        filename = Path(obj.key).name
        job_id = str(uuid.uuid4())

        if self._catalog_store:
            self._catalog_store.start_ingestion_job(
                job_id=job_id,
                landing_s3_uri=obj.uri,
                tenant_id=tenant_id,
                collection_id=collection_id,
                document_type=document_type,
                filename=filename,
                input_event={
                    "bucket": obj.bucket,
                    "key": obj.key,
                    "content_type": head.get("ContentType", ""),
                },
            )

        try:
            result = self._route_object(obj, destination_bucket, head, document_type)
            result.job_id = job_id
            result.tenant_id = result.tenant_id or tenant_id
            result.collection_id = result.collection_id or collection_id
            result.document_type = result.document_type or document_type

            if self._catalog_store:
                if result.document_metadata:
                    self._catalog_store.upsert_document(result.document_metadata)
                if result.chunks:
                    self._catalog_store.upsert_chunks(result.chunks)
                self._catalog_store.complete_ingestion_job(
                    job_id=job_id,
                    document_id=result.document_id,
                    status=result.status,
                    stage="chunk_artifacts_complete"
                    if result.chunks
                    else "initial_metadata_complete",
                    metadata={
                        "metadata_s3_uri": result.metadata_s3_uri,
                        "raw_s3_uri": result.raw_s3_uri,
                        "chunk_count": len(result.chunks),
                    },
                )

            return result
        except Exception as exc:
            if self._catalog_store:
                try:
                    self._catalog_store.fail_ingestion_job(
                        job_id=job_id,
                        stage="route_not_implemented"
                        if isinstance(exc, NotImplementedError)
                        else "failed",
                        error={
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                            "document_type": document_type,
                        },
                    )
                except Exception:
                    log.exception("Failed to mark document ingestion job as failed.")
            raise

    def _route_object(
        self,
        obj: S3ObjectRef,
        destination_bucket: str,
        head: dict[str, Any],
        document_type: str,
    ) -> IngestionResult:
        """Route an object to the processor for its document type."""
        if document_type == "pdf":
            return self._pdf_processor.process(obj, destination_bucket, head)
        if document_type == "word_doc":
            raise NotImplementedError("Word document ingestion is not implemented yet.")
        if document_type == "image":
            raise NotImplementedError("Image ingestion is not implemented yet.")
        if document_type == "web_page":
            raise NotImplementedError("Web page ingestion is not implemented yet.")
        if document_type == "csv":
            raise NotImplementedError("CSV ingestion is not implemented yet.")
        if document_type == "excel":
            raise NotImplementedError("Excel ingestion is not implemented yet.")
        if document_type == "powerpoint":
            raise NotImplementedError("PowerPoint ingestion is not implemented yet.")

        raise NotImplementedError(f"Document type '{document_type}' is not implemented yet.")


def main(event: dict[str, Any]) -> dict[str, Any]:
    """Process document ingestion events."""
    config_path = os.getenv("APP_CONFIG_PATH", "config/app_config.yml")
    log.info("Loading configuration from: %s", config_path)
    config = read_config(config_path)
    ingestion_config = config["ingestion"]
    catalog_store = create_document_catalog_store(config)
    log.info("Configuration loaded successfully")

    service = DocumentIngestionService(boto3.client("s3"), ingestion_config, catalog_store)
    records = list(extract_s3_object_refs(event))
    log.info("Processing %s ingestion record(s)", len(records))

    results: list[dict[str, Any]] = []
    for record in records:
        try:
            result = service.process_object(record)
            log.info(
                "Processed ingestion object: bucket=%s key=%s status=%s document_id=%s",
                record.bucket,
                record.key,
                result.status,
                result.document_id,
            )
            results.append(asdict(result))
        except Exception as exc:
            log.exception(
                "Failed to process ingestion object: bucket=%s key=%s",
                record.bucket,
                record.key,
            )
            results.append(
                asdict(
                    IngestionResult(
                        landing_s3_uri=record.uri,
                        status="failed",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
            )

    return {
        "statusCode": 200,
        "processed_count": len(results),
        "results": results,
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """AWS Lambda handler for document ingestion events."""
    del context
    event = event if isinstance(event, dict) else {}
    try:
        return main(event)
    except Exception:
        log.exception("Unhandled error while processing document ingestion event.")
        return {
            "statusCode": 500,
            "error": "InternalServerError",
            "detail": "An unexpected error occurred.",
        }


def extract_s3_object_refs(event: dict[str, Any]) -> Iterable[S3ObjectRef]:
    """Extract S3 object references from S3, EventBridge, or direct test events."""
    if "Records" in event:
        for record in event.get("Records", []):
            s3 = record.get("s3", {})
            bucket = s3.get("bucket", {}).get("name")
            key = s3.get("object", {}).get("key")
            if bucket and key:
                yield S3ObjectRef(bucket=bucket, key=unquote_plus(key))
        return

    if event.get("source") == "aws.s3" and event.get("detail-type") == "Object Created":
        detail = event.get("detail", {})
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key")
        if bucket and key:
            yield S3ObjectRef(bucket=bucket, key=unquote_plus(key))
        return

    bucket = event.get("bucket") or event.get("Bucket")
    key = event.get("key") or event.get("Key")
    if bucket and key:
        yield S3ObjectRef(bucket=bucket, key=unquote_plus(key))


def identify_document_type(key: str, content_type: str = "") -> str:
    """Identify the logical document type from object key and content type."""
    lower_key = key.lower()
    lower_type = content_type.lower()

    if lower_key.endswith(".pdf") or lower_type == "application/pdf":
        return "pdf"
    if lower_key.endswith(".docx"):
        return "word_doc"
    if lower_key.endswith((".png", ".jpg", ".jpeg")):
        return "image"
    if lower_key.endswith((".html", ".htm")):
        return "web_page"
    if lower_key.endswith(".csv"):
        return "csv"
    if lower_key.endswith((".xlsx", ".xls")):
        return "excel"
    if lower_key.endswith((".pptx", ".ppt")):
        return "powerpoint"
    return "unknown"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Document ingestion Lambda - process S3 landing-zone events"
    )
    event_source = parser.add_mutually_exclusive_group(required=True)
    event_source.add_argument(
        "--event",
        type=str,
        help='JSON string containing the event, e.g. \'{"bucket": "my-bucket", "key": "kb_landing/file.pdf"}\'',
    )
    event_source.add_argument(
        "--event-file",
        type=str,
        help="Path to a JSON file containing the event.",
    )
    args = parser.parse_args()

    if args.event_file:
        with open(args.event_file, "r", encoding="utf-8") as event_file:
            event = json.load(event_file)
    else:
        event = json.loads(args.event)

    response = main(event)
    print(json.dumps(response, indent=2))

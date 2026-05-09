"""S3 landing-zone document ingestion Lambda.

The first implementation focuses on PDFs. Other document types are recognized
with explicit branches so future implementation routes are visible, but they
raise ``NotImplementedError`` until those routes are built.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote_plus

import boto3

from utils.config import read_config
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class S3ObjectRef:
    bucket: str
    key: str

    @property
    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


@dataclass
class PdfScanResult:
    extraction_status: str
    title: str | None = None
    page_count: int | None = None
    probed_pages: int = 0
    extracted_text_char_count: int = 0
    text_available: bool | None = None
    ocr_required: bool | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class IngestionResult:
    landing_s3_uri: str
    status: str
    document_type: str | None = None
    document_id: str | None = None
    metadata_s3_uri: str | None = None
    raw_s3_uri: str | None = None
    message: str | None = None


class DocumentIngestionService:
    """Processes landing-zone objects into the durable document layout."""

    def __init__(
        self,
        s3_client: Any,
        ingestion_config: dict[str, Any],
        random_suffix: Callable[[], str] | None = None,
    ) -> None:
        self._s3 = s3_client
        self._ingestion_config = ingestion_config
        self._random_suffix = random_suffix or (lambda: secrets.token_hex(5))

    def process_object(self, obj: S3ObjectRef) -> IngestionResult:
        landing_prefix = self._ingestion_config["landing_prefix"]
        if not obj.key.startswith(landing_prefix):
            return IngestionResult(
                landing_s3_uri=obj.uri,
                status="skipped",
                message=f"Object is outside landing prefix {landing_prefix}",
            )

        destination_bucket = self._ingestion_config["document_bucket_name"]
        head = self._s3.head_object(Bucket=obj.bucket, Key=obj.key)
        document_type = identify_document_type(obj.key, head.get("ContentType", ""))

        if document_type == "pdf":
            return self._process_pdf(obj, destination_bucket, head)
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

    def _process_pdf(
        self,
        obj: S3ObjectRef,
        destination_bucket: str,
        head: dict[str, Any],
    ) -> IngestionResult:
        filename = Path(obj.key).name
        tenant_id, collection_id = infer_landing_scope(
            obj.key,
            self._ingestion_config["landing_prefix"],
            self._ingestion_config["default_tenant_id"],
            self._ingestion_config["default_collection_id"],
        )

        tmp_path = Path(tempfile.gettempdir()) / f"ingest-{uuid.uuid4()}-{filename}"
        try:
            self._s3.download_file(obj.bucket, obj.key, str(tmp_path))
            pdf_scan = scan_pdf(
                tmp_path,
                int(self._ingestion_config["pdf_text_probe_pages"]),
                int(self._ingestion_config["pdf_text_available_min_chars"]),
            )

            title = pdf_scan.title or title_from_filename(filename)
            document_id = generate_document_id("pdf", title, filename, self._random_suffix())

            raw_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "original",
                filename,
            )
            metadata_key = join_s3_key(
                self._ingestion_config["raw_prefix"],
                document_id,
                "document_metadata.json",
            )

            self._s3.copy_object(
                Bucket=destination_bucket,
                Key=raw_key,
                CopySource={"Bucket": obj.bucket, "Key": obj.key},
                MetadataDirective="COPY",
            )

            uploaded_at = _isoformat(head.get("LastModified"))
            now = datetime.now(timezone.utc).isoformat()
            metadata = {
                "metadata_schema_version": "0.1",
                "landing_s3_uri": obj.uri,
                "document_id": document_id,
                "document_title": title,
                "document_description": build_document_description("pdf", title, pdf_scan),
                "document_type": "pdf",
                "filename": filename,
                "location": f"s3://{destination_bucket}/{raw_key}",
                "uploaded_at": uploaded_at,
                "ingested_at": now,
                "source_status": "processing",
                "access_tier": "restricted",
                "tenant_id": tenant_id,
                "collection_id": collection_id,
                "processing": {
                    "stage": "initial_metadata_complete",
                    "pdf": asdict(pdf_scan),
                    "chunking": {
                        "status": "placeholder",
                        "message": "Chunk generation is deferred to the next implementation slice.",
                    },
                },
            }

            self._s3.put_object(
                Bucket=destination_bucket,
                Key=metadata_key,
                Body=json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8"),
                ContentType="application/json",
            )

            return IngestionResult(
                landing_s3_uri=obj.uri,
                status="processed",
                document_type="pdf",
                document_id=document_id,
                raw_s3_uri=f"s3://{destination_bucket}/{raw_key}",
                metadata_s3_uri=f"s3://{destination_bucket}/{metadata_key}",
            )
        finally:
            tmp_path.unlink(missing_ok=True)


def main(event: dict[str, Any]) -> dict[str, Any]:
    """Process document ingestion events."""
    config_path = os.getenv("APP_CONFIG_PATH", "config/app_config.yml")
    log.info("Loading configuration from: %s", config_path)
    config = read_config(config_path)
    ingestion_config = config["ingestion"]
    log.info("Configuration loaded successfully")

    service = DocumentIngestionService(boto3.client("s3"), ingestion_config)
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


def scan_pdf(path: Path, probe_pages: int, text_available_min_chars: int) -> PdfScanResult:
    try:
        from pypdf import PdfReader
    except ImportError:
        return PdfScanResult(
            extraction_status="pypdf_not_available",
            text_available=None,
            ocr_required=None,
            warnings=["pypdf is not installed; PDF text probing was skipped."],
        )

    try:
        reader = PdfReader(str(path))
        page_count = len(reader.pages)
        metadata = reader.metadata or {}
        title = _clean_pdf_metadata_value(getattr(metadata, "title", None))

        pages_to_probe = min(max(probe_pages, 0), page_count)
        text_char_count = 0
        warnings: list[str] = []
        for page_index in range(pages_to_probe):
            try:
                text = reader.pages[page_index].extract_text() or ""
                text_char_count += len(text.strip())
            except Exception as exc:  # pragma: no cover - depends on PDF internals
                warnings.append(f"page_{page_index + 1}_text_extract_failed:{type(exc).__name__}")

        text_available = text_char_count >= text_available_min_chars
        return PdfScanResult(
            extraction_status="completed",
            title=title,
            page_count=page_count,
            probed_pages=pages_to_probe,
            extracted_text_char_count=text_char_count,
            text_available=text_available,
            ocr_required=not text_available,
            warnings=warnings,
        )
    except Exception as exc:
        return PdfScanResult(
            extraction_status="failed",
            text_available=None,
            ocr_required=None,
            warnings=[f"pdf_scan_failed:{type(exc).__name__}"],
        )


def infer_landing_scope(
    key: str,
    landing_prefix: str,
    default_tenant_id: str,
    default_collection_id: str,
) -> tuple[str, str]:
    relative_key = key[len(landing_prefix) :] if key.startswith(landing_prefix) else key
    parts = [part for part in relative_key.split("/") if part]
    if len(parts) >= 3:
        return parts[0], parts[1]
    return default_tenant_id, default_collection_id


def generate_document_id(
    document_type: str,
    title: str | None,
    filename: str,
    suffix: str,
) -> str:
    base_title = title or title_from_filename(filename)
    year = first_year(base_title) or first_year(filename)
    title_slug = slugify(base_title)[:48]
    parts = [slugify(document_type), title_slug]
    if year and year not in title_slug:
        parts.append(year)
    parts.append(slugify(suffix))
    return "_".join(part for part in parts if part)


def build_document_description(document_type: str, title: str, pdf_scan: PdfScanResult) -> str:
    details = [f"{document_type.upper()} document", title]
    if pdf_scan.page_count is not None:
        details.append(f"{pdf_scan.page_count} pages")
    return " - ".join(details)


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return re.sub(r"\s+", " ", cleaned) or "Untitled document"


def first_year(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return match.group(0) if match else ""


def slugify(value: str | None) -> str:
    if not value:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def join_s3_key(*parts: str) -> str:
    return "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _clean_pdf_metadata_value(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


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

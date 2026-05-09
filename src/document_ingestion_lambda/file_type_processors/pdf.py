"""PDF document processor for the document ingestion Lambda."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from document_ingestion_lambda.models import IngestionResult, S3ObjectRef

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


@dataclass
class PdfScanResult:
    """Metadata collected during an initial PDF scan."""

    extraction_status: str
    title: str | None = None
    page_count: int | None = None
    probed_pages: int = 0
    extracted_text_char_count: int = 0
    text_available: bool | None = None
    ocr_required: bool | None = None
    warnings: list[str] = field(default_factory=list)


class PdfProcessor:
    """Processes PDF landing-zone objects into the durable document layout."""

    def __init__(
        self,
        s3_client: Any,
        ingestion_config: dict[str, Any],
        random_suffix: Callable[[], str],
    ) -> None:
        self._s3 = s3_client
        self._ingestion_config = ingestion_config
        self._random_suffix = random_suffix

    def process(
        self,
        obj: S3ObjectRef,
        destination_bucket: str,
        head: dict[str, Any],
    ) -> IngestionResult:
        """Process a PDF S3 object into raw storage and metadata."""
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


def scan_pdf(path: Path, probe_pages: int, text_available_min_chars: int) -> PdfScanResult:
    """Scan a PDF for basic metadata and embedded text availability."""
    if PdfReader is None:
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
    """Infer tenant and collection from the landing-zone key."""
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
    """Generate a readable document identifier with a unique suffix."""
    base_title = title or title_from_filename(filename)
    year = first_year(base_title) or first_year(filename)
    title_slug = slugify(base_title)[:48]
    parts = [slugify(document_type), title_slug]
    if year and year not in title_slug:
        parts.append(year)
    parts.append(slugify(suffix))
    return "_".join(part for part in parts if part)


def build_document_description(document_type: str, title: str, pdf_scan: PdfScanResult) -> str:
    """Build a short document description for catalog metadata."""
    details = [f"{document_type.upper()} document", title]
    if pdf_scan.page_count is not None:
        details.append(f"{pdf_scan.page_count} pages")
    return " - ".join(details)


def title_from_filename(filename: str) -> str:
    """Create a readable title from a filename."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return re.sub(r"\s+", " ", cleaned) or "Untitled document"


def first_year(value: str | None) -> str:
    """Return the first 19xx or 20xx year in a string."""
    if not value:
        return ""
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return match.group(0) if match else ""


def slugify(value: str | None) -> str:
    """Convert a value into a lowercase underscore slug."""
    if not value:
        return ""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def join_s3_key(*parts: str) -> str:
    """Join S3 key parts without duplicate slashes."""
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

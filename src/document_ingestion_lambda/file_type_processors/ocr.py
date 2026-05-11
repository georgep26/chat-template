"""Textract-backed OCR fallback for scanned PDFs.

The multimodal triage decides whether a PDF needs OCR. When it does, this
module replaces the deterministic pypdf path with a Textract
``StartDocumentTextDetection`` job, polls until completion, and returns
the text in the same ``PdfTextExtractionResult`` shape ``pdf.py`` already
consumes — so the downstream section/page chunkers don't care that the
text came from OCR.

Like Phase 3, we run async (single job over the whole PDF) and poll
within the Lambda invocation. For documents that would exceed the
600 s function timeout we'd need to graduate to an SNS callback flow;
the plan calls that future work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from document_ingestion_lambda.file_type_processors.pdf import (
    PdfPageText,
    PdfTextExtractionResult,
    clean_extracted_text,
)


@dataclass
class OcrConfig:
    enabled: bool = True
    poll_interval_seconds: float = 5.0
    max_wait_seconds: float = 540.0


@dataclass
class OcrResult:
    status: str = "completed"  # "completed" | "skipped" | "failed"
    job_id: str | None = None
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)


def extract_pdf_text_via_ocr(
    *,
    raw_s3_bucket: str,
    raw_s3_key: str,
    textract_client: Any,
    config: OcrConfig,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[PdfTextExtractionResult, OcrResult]:
    """Run Textract OCR over the raw PDF and return per-page text.

    Always-recoverable: any Textract failure produces an empty result so
    the caller can log a warning and emit a "no chunks" record without
    crashing the whole ingestion.
    """
    if not config.enabled:
        return (
            PdfTextExtractionResult(extraction_status="ocr_disabled"),
            OcrResult(status="skipped", warnings=["ocr_disabled"]),
        )

    try:
        start_response = textract_client.start_document_text_detection(
            DocumentLocation={"S3Object": {"Bucket": raw_s3_bucket, "Name": raw_s3_key}},
        )
        job_id = start_response["JobId"]
    except Exception as exc:
        return (
            PdfTextExtractionResult(
                extraction_status="ocr_start_failed",
                warnings=[f"start_failed:{type(exc).__name__}"],
            ),
            OcrResult(status="failed", warnings=[f"start_failed:{type(exc).__name__}"]),
        )

    try:
        elapsed = 0.0
        while elapsed <= config.max_wait_seconds:
            sleep(config.poll_interval_seconds)
            elapsed += config.poll_interval_seconds
            response = textract_client.get_document_text_detection(
                JobId=job_id, MaxResults=1
            )
            status = response.get("JobStatus")
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "PARTIAL_SUCCESS"):
                msg = response.get("StatusMessage") or "no_status_message"
                return (
                    PdfTextExtractionResult(
                        extraction_status="ocr_job_failed",
                        warnings=[f"job_{status.lower()}:{msg}"],
                    ),
                    OcrResult(
                        status="failed",
                        job_id=job_id,
                        warnings=[f"job_{status.lower()}:{msg}"],
                    ),
                )
        else:
            return (
                PdfTextExtractionResult(
                    extraction_status="ocr_timeout",
                    warnings=[f"timeout_after_{config.max_wait_seconds}s"],
                ),
                OcrResult(
                    status="failed",
                    job_id=job_id,
                    warnings=[f"timeout_after_{config.max_wait_seconds}s"],
                ),
            )

        # Paginate the full result.
        blocks: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"JobId": job_id}
            if next_token:
                kwargs["NextToken"] = next_token
            page = textract_client.get_document_text_detection(**kwargs)
            blocks.extend(page.get("Blocks", []))
            next_token = page.get("NextToken")
            if not next_token:
                break
    except Exception as exc:
        return (
            PdfTextExtractionResult(
                extraction_status="ocr_collect_failed",
                warnings=[f"collect_failed:{type(exc).__name__}"],
            ),
            OcrResult(
                status="failed",
                job_id=job_id,
                warnings=[f"collect_failed:{type(exc).__name__}"],
            ),
        )

    page_texts = _blocks_to_pages(blocks)
    return (
        PdfTextExtractionResult(
            extraction_status="completed",
            pages=page_texts,
        ),
        OcrResult(
            status="completed",
            job_id=job_id,
            page_count=len(page_texts),
        ),
    )


def _blocks_to_pages(blocks: list[dict[str, Any]]) -> list[PdfPageText]:
    """Aggregate Textract LINE blocks into per-page text in reading order.

    Textract returns ``PAGE`` blocks plus ``LINE``/``WORD`` children. We
    group LINEs by ``Page`` and sort within each page by bounding-box top
    so the joined text reads naturally.
    """
    lines_by_page: dict[int, list[tuple[float, str]]] = {}
    for block in blocks:
        if block.get("BlockType") != "LINE":
            continue
        page_number = int(block.get("Page", 1))
        text = (block.get("Text") or "").strip()
        if not text:
            continue
        top = float(
            (block.get("Geometry") or {}).get("BoundingBox", {}).get("Top", 0.0)
        )
        lines_by_page.setdefault(page_number, []).append((top, text))

    result: list[PdfPageText] = []
    for page_number in sorted(lines_by_page):
        ordered = [text for _top, text in sorted(lines_by_page[page_number], key=lambda x: x[0])]
        joined = clean_extracted_text("\n".join(ordered))
        if joined:
            result.append(PdfPageText(page_number=page_number, text=joined))
    return result


__all__ = [
    "OcrConfig",
    "OcrResult",
    "extract_pdf_text_via_ocr",
]

"""Tests for the Textract OCR fallback.

When the multimodal triage decides a document needs OCR, we replace the
deterministic pypdf path with Textract's async ``DetectDocumentText``
job. The downstream chunkers receive a ``PdfTextExtractionResult`` in
the same shape regardless of OCR being used or not — that contract is
what these tests pin down.
"""

from __future__ import annotations

from typing import Any

from document_ingestion_lambda.file_type_processors.ocr import (
    OcrConfig,
    extract_pdf_text_via_ocr,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubTextractClient:
    """Replays a queue of synthetic Textract responses."""

    def __init__(
        self,
        *,
        blocks: list[dict[str, Any]] | None = None,
        polls_until_success: int = 1,
        fail_with: str | None = None,
        start_raises: Exception | None = None,
    ) -> None:
        self.blocks = blocks or []
        self.polls_until_success = polls_until_success
        self.fail_with = fail_with
        self.start_raises = start_raises
        self.start_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self._poll_count = 0

    def start_document_text_detection(self, *, DocumentLocation: dict[str, Any]) -> dict[str, Any]:
        self.start_calls.append({"DocumentLocation": DocumentLocation})
        if self.start_raises:
            raise self.start_raises
        return {"JobId": "ocr-job-1"}

    def get_document_text_detection(
        self,
        *,
        JobId: str,
        MaxResults: int | None = None,
        NextToken: str | None = None,
    ) -> dict[str, Any]:
        self.get_calls.append({"JobId": JobId, "MaxResults": MaxResults, "NextToken": NextToken})

        # Polling phase: MaxResults=1 and no NextToken.
        if MaxResults == 1 and NextToken is None:
            self._poll_count += 1
            if self.fail_with:
                return {"JobStatus": self.fail_with, "StatusMessage": "oh no"}
            if self._poll_count < self.polls_until_success:
                return {"JobStatus": "IN_PROGRESS", "Blocks": []}
            return {"JobStatus": "SUCCEEDED", "Blocks": []}

        # Pagination phase.
        return {"JobStatus": "SUCCEEDED", "Blocks": self.blocks}


def _no_sleep(_seconds: float) -> None:
    """Bypass real polling in tests."""


def _line(page: int, text: str, top: float) -> dict[str, Any]:
    return {
        "BlockType": "LINE",
        "Page": page,
        "Text": text,
        "Geometry": {"BoundingBox": {"Top": top, "Left": 0.1, "Width": 0.8, "Height": 0.04}},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ocr_groups_lines_by_page_in_reading_order():
    blocks = [
        _line(1, "Heading", 0.10),
        _line(1, "First line of body text.", 0.20),
        # Out of order on purpose to verify top-sort.
        _line(2, "Bottom of page two.", 0.80),
        _line(2, "Top of page two.", 0.10),
    ]
    textract = StubTextractClient(blocks=blocks)

    extraction, result = extract_pdf_text_via_ocr(
        raw_s3_bucket="bucket",
        raw_s3_key="kb_raw/doc/original/file.pdf",
        textract_client=textract,
        config=OcrConfig(),
        sleep=_no_sleep,
    )

    assert result.status == "completed"
    assert result.page_count == 2
    assert extraction.extraction_status == "completed"
    assert extraction.pages[0].page_number == 1
    assert "Heading" in extraction.pages[0].text
    assert "First line" in extraction.pages[0].text
    # Page 2 is sorted by top — "Top of page two" comes before "Bottom".
    page2_text = extraction.pages[1].text
    assert page2_text.index("Top of page two") < page2_text.index("Bottom of page two")


def test_start_call_passes_s3_location():
    textract = StubTextractClient(blocks=[_line(1, "x", 0.1)])

    extract_pdf_text_via_ocr(
        raw_s3_bucket="bucket",
        raw_s3_key="kb_raw/doc/original/file.pdf",
        textract_client=textract,
        config=OcrConfig(),
        sleep=_no_sleep,
    )

    assert textract.start_calls[0]["DocumentLocation"] == {
        "S3Object": {"Bucket": "bucket", "Name": "kb_raw/doc/original/file.pdf"}
    }


def test_ocr_disabled_returns_skipped():
    textract = StubTextractClient(blocks=[_line(1, "x", 0.1)])

    extraction, result = extract_pdf_text_via_ocr(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        textract_client=textract,
        config=OcrConfig(enabled=False),
        sleep=_no_sleep,
    )

    assert result.status == "skipped"
    assert extraction.extraction_status == "ocr_disabled"
    assert textract.start_calls == []


def test_ocr_start_failure_returns_failed():
    textract = StubTextractClient(start_raises=RuntimeError("textract down"))

    extraction, result = extract_pdf_text_via_ocr(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        textract_client=textract,
        config=OcrConfig(),
        sleep=_no_sleep,
    )

    assert result.status == "failed"
    assert extraction.extraction_status == "ocr_start_failed"
    assert any("RuntimeError" in w for w in result.warnings)


def test_ocr_job_failed_returns_failed():
    textract = StubTextractClient(fail_with="FAILED")

    extraction, result = extract_pdf_text_via_ocr(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        textract_client=textract,
        config=OcrConfig(),
        sleep=_no_sleep,
    )

    assert result.status == "failed"
    assert extraction.extraction_status == "ocr_job_failed"


def test_ocr_drops_empty_pages():
    blocks = [
        _line(1, "Real text on page 1.", 0.1),
        _line(2, "   ", 0.1),  # whitespace-only LINE — should be skipped
    ]
    textract = StubTextractClient(blocks=blocks)

    extraction, _ = extract_pdf_text_via_ocr(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        textract_client=textract,
        config=OcrConfig(),
        sleep=_no_sleep,
    )

    # Only page 1 yielded text; page 2 was all whitespace.
    assert len(extraction.pages) == 1
    assert extraction.pages[0].page_number == 1


def test_ocr_polls_until_succeeded():
    blocks = [_line(1, "x", 0.1)]
    textract = StubTextractClient(blocks=blocks, polls_until_success=3)

    extract_pdf_text_via_ocr(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        textract_client=textract,
        config=OcrConfig(),
        sleep=_no_sleep,
    )

    # 3 polling calls (MaxResults=1) before pagination starts.
    poll_calls = [c for c in textract.get_calls if c["MaxResults"] == 1]
    assert len(poll_calls) == 3

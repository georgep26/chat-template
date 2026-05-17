"""Tests for deterministic PDF text-quality comparison."""

from document_ingestion_lambda.file_type_processors.pdf import (
    PdfPageText,
    PdfTextExtractionResult,
)
from document_ingestion_lambda.file_type_processors.text_quality import (
    build_text_quality_report,
)
from document_ingestion_lambda.triage import (
    PageTriageObservation,
    PdfTriageResult,
)


def test_text_quality_report_marks_matching_text_not_suspicious():
    report = build_text_quality_report(
        text_extraction=PdfTextExtractionResult(
            extraction_status="completed",
            pages=[PdfPageText(page_number=1, text="Required heating shall be 68 F.")],
        ),
        triage=PdfTriageResult(
            triage_status="completed",
            page_observations=[
                PageTriageObservation(
                    page_number=1,
                    raw_visible_text="Required heating shall be 68 F.",
                )
            ],
        ),
        page_mismatch_threshold=0.5,
        document_mismatch_threshold=0.3,
        min_visible_chars=10,
    )

    assert report.suspicious_page_count == 0
    assert report.ocr_recommended is False


def test_text_quality_report_recommends_ocr_when_visible_text_missing_from_embedded():
    report = build_text_quality_report(
        text_extraction=PdfTextExtractionResult(
            extraction_status="completed",
            pages=[PdfPageText(page_number=1, text="Short unrelated text.")],
        ),
        triage=PdfTriageResult(
            triage_status="completed",
            page_observations=[
                PageTriageObservation(
                    page_number=1,
                    raw_visible_text=(
                        "Every dwelling unit shall be provided with heating facilities "
                        "capable of maintaining a room temperature of not less than 68 F."
                    ),
                )
            ],
        ),
        page_mismatch_threshold=0.5,
        document_mismatch_threshold=0.3,
        min_visible_chars=10,
    )

    assert report.suspicious_page_count == 1
    assert report.pages_over_text_mismatch_threshold == [1]
    assert report.ocr_recommended is True

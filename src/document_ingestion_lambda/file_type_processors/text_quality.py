"""Deterministic PDF text-quality checks for ingestion.

The multimodal triage model extracts visible text from rendered pages. This
module compares those observations with the embedded/OCR text extracted by the
backend. It is the source of truth for whether the embedded text layer looks
incomplete enough to recommend OCR/Textract.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from document_ingestion_lambda.file_type_processors.pdf import PdfTextExtractionResult
from document_ingestion_lambda.triage import PdfTriageResult


_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")
_SOFT_WRAP_HYPHEN_PATTERN = re.compile(r"(\w)[-‐‑­]\s+(\w)")


@dataclass
class PageTextQuality:
    """Comparison between embedded page text and model-visible page text."""

    page_number: int
    embedded_char_count: int
    visible_char_count: int
    token_overlap_ratio: float | None
    mismatch_ratio: float | None
    suspicious: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class TextQualityReport:
    """Document-level text quality report."""

    triaged_page_count: int
    full_page_triage_enabled: bool
    suspicious_page_count: int
    suspicious_page_ratio: float
    pages_over_text_mismatch_threshold: list[int]
    ocr_recommended: bool
    textract_recommended: bool
    selected_pages_for_ocr: list[int]
    reason: str
    page_reports: list[PageTextQuality] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        return asdict(self)


def build_text_quality_report(
    *,
    text_extraction: PdfTextExtractionResult,
    triage: PdfTriageResult,
    page_mismatch_threshold: float,
    document_mismatch_threshold: float,
    min_visible_chars: int,
) -> TextQualityReport:
    """Compare embedded text pages with visible-text observations."""
    observations = [obs for obs in triage.page_observations if not obs.parse_error]
    embedded_by_page = {
        page.page_number: page.text for page in text_extraction.pages
    }
    if not observations and not text_extraction.pages:
        return TextQualityReport(
            triaged_page_count=0,
            full_page_triage_enabled=triage.full_page_triage_enabled,
            suspicious_page_count=0,
            suspicious_page_ratio=0.0,
            pages_over_text_mismatch_threshold=[],
            ocr_recommended=True,
            textract_recommended=True,
            selected_pages_for_ocr=[],
            reason="No triage observations or embedded text pages were available; OCR/Textract is recommended.",
            page_reports=[],
        )

    page_reports: list[PageTextQuality] = []
    for observation in observations:
        embedded_text = embedded_by_page.get(observation.page_number, "")
        visible_text = observation.raw_visible_text or ""
        page_reports.append(
            compare_page_text(
                page_number=observation.page_number,
                embedded_text=embedded_text,
                visible_text=visible_text,
                page_mismatch_threshold=page_mismatch_threshold,
                min_visible_chars=min_visible_chars,
            )
        )

    suspicious_pages = [report for report in page_reports if report.suspicious]
    triaged_count = len(page_reports)
    suspicious_ratio = len(suspicious_pages) / triaged_count if triaged_count else 0.0
    document_recommended = (
        triaged_count > 0 and suspicious_ratio >= document_mismatch_threshold
    )
    selected_pages = [report.page_number for report in suspicious_pages]

    if document_recommended:
        reason = (
            f"{len(suspicious_pages)} of {triaged_count} triaged pages exceeded "
            f"the mismatch threshold."
        )
    elif selected_pages:
        reason = "Selected pages appear suspicious, but the document-level threshold was not met."
    elif triaged_count == 0:
        reason = "No triage observations were available for text-quality comparison."
    else:
        reason = "Embedded text appears adequate for the triaged pages."

    return TextQualityReport(
        triaged_page_count=triaged_count,
        full_page_triage_enabled=triage.full_page_triage_enabled,
        suspicious_page_count=len(suspicious_pages),
        suspicious_page_ratio=round(suspicious_ratio, 3),
        pages_over_text_mismatch_threshold=selected_pages,
        ocr_recommended=document_recommended,
        textract_recommended=document_recommended or bool(selected_pages),
        selected_pages_for_ocr=selected_pages,
        reason=reason,
        page_reports=page_reports,
    )


def compare_page_text(
    *,
    page_number: int,
    embedded_text: str,
    visible_text: str,
    page_mismatch_threshold: float,
    min_visible_chars: int,
) -> PageTextQuality:
    """Compare one page of embedded text against model-visible text."""
    embedded_tokens = set(_tokens(embedded_text))
    visible_tokens = set(_tokens(visible_text))
    embedded_char_count = len((embedded_text or "").strip())
    visible_char_count = len((visible_text or "").strip())

    reasons: list[str] = []
    token_overlap_ratio: float | None = None
    mismatch_ratio: float | None = None

    if visible_char_count < min_visible_chars:
        # The model did not extract enough visible text for a meaningful drift
        # comparison. Keep the page non-suspicious unless embedded text is also
        # empty; figures/tables can legitimately have little prose.
        if embedded_char_count == 0 and visible_char_count > 0:
            reasons.append("embedded_text_empty")
        else:
            reasons.append("visible_text_too_short_for_comparison")
    elif not embedded_tokens:
        mismatch_ratio = 1.0
        token_overlap_ratio = 0.0
        reasons.append("embedded_text_empty")
    elif visible_tokens:
        overlap = len(embedded_tokens.intersection(visible_tokens))
        token_overlap_ratio = overlap / len(visible_tokens)
        mismatch_ratio = 1.0 - token_overlap_ratio
        if mismatch_ratio >= page_mismatch_threshold:
            reasons.append("visible_text_missing_from_embedded_text")
    else:
        reasons.append("visible_text_empty")

    suspicious = any(
        reason in {"embedded_text_empty", "visible_text_missing_from_embedded_text"}
        for reason in reasons
    )
    return PageTextQuality(
        page_number=page_number,
        embedded_char_count=embedded_char_count,
        visible_char_count=visible_char_count,
        token_overlap_ratio=round(token_overlap_ratio, 3)
        if token_overlap_ratio is not None
        else None,
        mismatch_ratio=round(mismatch_ratio, 3) if mismatch_ratio is not None else None,
        suspicious=suspicious,
        reasons=reasons,
    )


def _tokens(text: str) -> Iterable[str]:
    normalized = _SOFT_WRAP_HYPHEN_PATTERN.sub(r"\1\2", text or "")
    for match in _WORD_PATTERN.finditer(normalized.lower()):
        yield match.group(0)

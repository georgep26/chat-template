"""Tests for the section-aware chunker.

The heading detection is deterministic and regex-based, so these tests
synthesize ``PdfPageText`` fixtures that look like real building-code
content and assert on the boundaries the detector finds plus the chunk
artifacts the builder emits.
"""

from __future__ import annotations

from typing import Any

import pytest

from document_ingestion_lambda.file_type_processors.pdf import (
    PdfPageText,
    PdfTextExtractionResult,
)
from document_ingestion_lambda.file_type_processors.section_chunking import (
    SectionBoundary,
    SectionDetectionConfig,
    _chapter_for,
    _heading_path_for,
    build_section_chunk_artifacts,
    detect_section_boundaries,
    should_use_section_chunking,
)
from document_ingestion_lambda.triage import PdfTriageResult


# ---------------------------------------------------------------------------
# Heading-path helpers
# ---------------------------------------------------------------------------


def test_heading_path_alphanumeric_prefix():
    assert _heading_path_for("R311.7.5.1") == ["R311", "R311.7", "R311.7.5", "R311.7.5.1"]


def test_heading_path_pure_numeric():
    assert _heading_path_for("5.4.1.2") == ["5", "5.4", "5.4.1", "5.4.1.2"]


def test_heading_path_returns_singleton_when_unparseable():
    assert _heading_path_for("Foreword") == ["Foreword"]


def test_chapter_picks_first_segment():
    assert _chapter_for("R311.7.5.1") == "R311"
    assert _chapter_for("5.4.1") == "5"
    assert _chapter_for("Foreword") == "Foreword"


# ---------------------------------------------------------------------------
# Boundary detection
# ---------------------------------------------------------------------------


def _pages(*texts: str) -> list[PdfPageText]:
    """Build a fixture page list. Page numbers are 1-indexed by order."""
    return [
        PdfPageText(page_number=i, text=text)
        for i, text in enumerate(texts, start=1)
    ]


def test_section_codes_pattern_finds_irc_style_sections():
    pages = _pages(
        (
            "R311.7 Stairways\n"
            "Interior stairways shall comply with this section. Lorem ipsum dolor "
            "sit amet, consectetur adipiscing elit. Sed do eiusmod tempor.\n"
            "R311.7.5 Risers\n"
            "Maximum riser height shall be 7 3/4 inches (196 mm). The riser height "
            "shall be measured vertically between leading edges of adjacent treads.\n"
            "R311.7.5.1 Risers\n"
            "Risers shall be vertical or sloped from the underside of the nosing of "
            "the tread above at an angle not more than 30 degrees from the vertical."
        ),
    )
    boundaries, _ = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="section_codes")
    )

    ids = [b.section_id for b in boundaries]
    assert ids == ["R311.7", "R311.7.5", "R311.7.5.1"]
    assert boundaries[2].heading_path == ["R311", "R311.7", "R311.7.5", "R311.7.5.1"]
    assert boundaries[2].chapter == "R311"


def test_section_codes_drops_tiny_false_positives():
    """A "section heading" with no body should be filtered out via min_section_chars."""
    pages = _pages(
        (
            "5.1 General\n"   # heading-only, no body → dropped
            "5.2 Scope\n"     # heading-only, no body → dropped
            "5.3 Materials\n"
            "This section governs allowable materials and their use in "
            "foundation systems. The provisions herein cover concrete, "
            "masonry, wood, and steel components.\n"
        ),
    )
    boundaries, _ = detect_section_boundaries(
        pages,
        config=SectionDetectionConfig(
            hierarchy_kind="section_codes",
            min_section_chars=40,
        ),
    )
    # Only "5.3 Materials" has enough body length to survive min_section_chars.
    ids = [b.section_id for b in boundaries]
    assert ids == ["5.3"]
    assert boundaries[0].title == "Materials"


def test_chapter_heading_pattern_finds_prose_chapters():
    pages = _pages(
        (
            "CHAPTER 5 FOUNDATIONS\n"
            "Foundations shall be designed in accordance with this chapter. "
            "Footings shall extend below the frost line. Lorem ipsum dolor sit amet, "
            "consectetur adipiscing elit.\n"
            "Chapter 6 — Wall Construction\n"
            "Wall construction shall comply with the requirements of this chapter. "
            "Studs, plates, headers, and bracing shall be as specified herein."
        ),
    )
    boundaries, _ = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="chapter_heading")
    )
    ids = [b.section_id for b in boundaries]
    titles = [b.title for b in boundaries]
    assert ids == ["5", "6"]
    assert "FOUNDATIONS" in titles[0] or titles[0] == "FOUNDATIONS"
    assert "Wall Construction" in titles[1]


def test_skip_pages_excludes_toc_pages_from_detection():
    """TOC pages listed via skip_pages must not contribute boundaries."""
    pages = [
        PdfPageText(
            page_number=10,  # TOC page — these "headings" are TOC entries
            text=(
                "R401 General . . . . . . . . . . . . . . . . . . . . . . . . 4-1\n"
                "R501 General . . . . . . . . . . . . . . . . . . . . . . . . 5-1\n"
                "R601 General . . . . . . . . . . . . . . . . . . . . . . . . 6-1\n"
            ),
        ),
        PdfPageText(
            page_number=40,  # Real body page
            text=(
                "R401 General\n"
                "The provisions of this chapter shall control the design and "
                "construction of the foundation and foundation spaces for "
                "buildings. Foundations shall be capable of accommodating all "
                "loads of Section R301.\n"
            ),
        ),
    ]
    boundaries, _ = detect_section_boundaries(
        pages,
        config=SectionDetectionConfig(
            hierarchy_kind="section_codes", skip_pages=(10,),
        ),
    )
    # Only the body page produces a boundary.
    assert len(boundaries) == 1
    assert boundaries[0].section_id == "R401"
    assert boundaries[0].start_page == 40


def test_dot_leader_entries_rejected_even_on_non_toc_pages():
    """Body pages can carry dot-leader cross-refs; reject those too."""
    pages = _pages(
        (
            "R401 General . . . . . . . . . . . . . . . . . . 4-1\n"  # leader noise
            "R402 Materials\nThis section governs allowable materials and "
            "their use in foundation systems. The provisions herein cover "
            "concrete, masonry, wood, and steel components."
        )
    )
    boundaries, _ = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="section_codes")
    )
    ids = [b.section_id for b in boundaries]
    assert ids == ["R402"]


def test_max_title_words_rejects_sentence_style_false_positives():
    """A line like 'Chapter 4 provides the requirements for the design and construction...'
    matches the chapter_heading regex but is a prose summary, not a heading."""
    pages = _pages(
        (
            "Chapter 4 provides the requirements for the design and construction "
            "of foundations and foundation spaces for buildings.\n"
            "Chapter 5 — Floors\n"
            "Floor systems shall be designed in accordance with this chapter. "
            "Joist span tables provide allowable spans for various species and grades."
        )
    )
    boundaries, _ = detect_section_boundaries(
        pages,
        config=SectionDetectionConfig(
            hierarchy_kind="chapter_heading", max_title_words=8
        ),
    )
    ids = [b.section_id for b in boundaries]
    # Only "Chapter 5 — Floors" survives — the long prose summary is rejected.
    assert ids == ["5"]
    assert boundaries[0].title == "Floors"


def test_unknown_hierarchy_kind_returns_empty():
    pages = _pages("Some plain text without headings.")
    boundaries, _ = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="page_only")
    )
    assert boundaries == []


def test_empty_pages_return_empty_boundaries():
    pages = _pages("", "", "")
    boundaries, joined = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="section_codes")
    )
    assert boundaries == []
    assert joined.text.strip() == ""


def test_boundaries_track_page_numbers_across_page_breaks():
    pages = [
        PdfPageText(
            page_number=10,
            text="R401.1 General\n" + ("The scope of this chapter is here. " * 4),
        ),
        PdfPageText(
            page_number=11,
            text=("continued body content " * 5)
            + "\nR401.2 Design loads\n"
            + ("Design load requirements continue here. " * 4),
        ),
    ]
    boundaries, _ = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="section_codes")
    )
    assert [b.section_id for b in boundaries] == ["R401.1", "R401.2"]
    assert boundaries[0].start_page == 10
    assert boundaries[0].end_page >= 10
    assert boundaries[1].start_page == 11


# ---------------------------------------------------------------------------
# should_use_section_chunking gate
# ---------------------------------------------------------------------------


def _completed_triage(**overrides: Any) -> PdfTriageResult:
    defaults = dict(
        triage_status="completed",
        ocr_required=False,
        ocr_confidence=0.9,
        has_hierarchy=True,
        hierarchy_kind="section_codes",
        hierarchy_confidence=0.8,
    )
    defaults.update(overrides)
    return PdfTriageResult(**defaults)


def test_gate_true_when_triage_completed_and_enough_boundaries():
    triage = _completed_triage()
    boundaries = [
        SectionBoundary(section_id=f"5.{i}", title="X", chapter="5", start_offset=0, end_offset=200)
        for i in range(6)
    ]
    assert should_use_section_chunking(
        triage, boundaries, min_boundaries=5, min_confidence=0.7
    ) is True


def test_gate_false_when_triage_did_not_complete():
    triage = PdfTriageResult(triage_status="failed")
    boundaries = [
        SectionBoundary(section_id=f"5.{i}", title="X", chapter="5", start_offset=0, end_offset=200)
        for i in range(10)
    ]
    assert should_use_section_chunking(
        triage, boundaries, min_boundaries=5, min_confidence=0.7
    ) is False


def test_gate_false_when_hierarchy_confidence_too_low():
    triage = _completed_triage(hierarchy_confidence=0.4)
    boundaries = [
        SectionBoundary(section_id=f"5.{i}", title="X", chapter="5", start_offset=0, end_offset=200)
        for i in range(10)
    ]
    assert should_use_section_chunking(
        triage, boundaries, min_boundaries=5, min_confidence=0.7
    ) is False


def test_gate_false_when_too_few_boundaries():
    triage = _completed_triage()
    boundaries = [
        SectionBoundary(section_id="5.1", title="X", chapter="5", start_offset=0, end_offset=200)
    ]
    assert should_use_section_chunking(
        triage, boundaries, min_boundaries=5, min_confidence=0.7
    ) is False


# ---------------------------------------------------------------------------
# Chunk artifact construction
# ---------------------------------------------------------------------------


def _document_metadata(document_id: str = "pdf_test_doc") -> dict[str, Any]:
    return {
        "metadata_schema_version": "1.0",
        "document_id": document_id,
        "document_title": "Test Building Code",
        "document_type": "pdf",
        "filename": "test.pdf",
        "source_location": "s3://bucket/kb_raw/pdf_test_doc/original/test.pdf",
        "tenant_id": "tenant-a",
        "collection_id": "default",
        "access_tier": "restricted",
    }


def test_build_section_chunks_one_chunk_per_section():
    pages = _pages(
        (
            "R311.7 Stairways\n"
            "Interior stairways shall comply with this section. Lorem ipsum dolor "
            "sit amet, consectetur adipiscing elit. Body text body text body text.\n"
            "R311.7.5 Risers\n"
            "Maximum riser height shall be 7 3/4 inches (196 mm). Riser body text "
            "continues here at a reasonable length to clear min_section_chars."
        ),
    )
    boundaries, joined = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="section_codes")
    )

    artifacts = build_section_chunk_artifacts(
        text_extraction=PdfTextExtractionResult(extraction_status="completed", pages=pages),
        boundaries=boundaries,
        joined=joined,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        embedding_chunk_token_limit=8192,
    )

    assert len(artifacts) == 2
    catalog = [a.catalog_metadata for a in artifacts]
    assert catalog[0]["chunk_id"] == "pdf_test_doc_section_r311_7"
    assert catalog[1]["chunk_id"] == "pdf_test_doc_section_r311_7_5"
    assert catalog[0]["chunk_type"] == "section"
    assert catalog[0]["section"] == "R311.7"
    assert catalog[0]["chapter"] == "R311"
    assert "R311" in catalog[0]["heading_path"]
    assert "R311.7" in catalog[0]["heading_path"]
    # Section chunk key lives under .../sections/
    assert artifacts[0].key.startswith("kb_index/pdf_test_doc/sections/")


def test_build_section_chunks_splits_oversized_sections_into_parts():
    long_body = (
        "This is a very long section body that will exceed the tiny token cap "
        "used in this test. " * 50
    )
    pages = _pages(f"5.4 Very Long Section\n{long_body}")
    boundaries, joined = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="section_codes")
    )

    artifacts = build_section_chunk_artifacts(
        text_extraction=PdfTextExtractionResult(extraction_status="completed", pages=pages),
        boundaries=boundaries,
        joined=joined,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        embedding_chunk_token_limit=50,  # tiny cap → forced split
    )

    assert len(artifacts) > 1
    ids = [a.catalog_metadata["chunk_id"] for a in artifacts]
    assert all(cid.startswith("pdf_test_doc_section_5_4_part_") for cid in ids)
    # All parts share the same section metadata.
    sections = {a.catalog_metadata["section"] for a in artifacts}
    assert sections == {"5.4"}
    # part_count should match the number of artifacts.
    part_counts = {a.catalog_metadata["custom_metadata"]["part_count"] for a in artifacts}
    assert part_counts == {len(artifacts)}


def test_build_section_chunks_emits_bedrock_sidecar_with_section_fields():
    pages = _pages(
        "R311.7.5.1 Risers\nRiser body content goes here at a reasonable length."
    )
    boundaries, joined = detect_section_boundaries(
        pages, config=SectionDetectionConfig(hierarchy_kind="section_codes")
    )
    artifacts = build_section_chunk_artifacts(
        text_extraction=PdfTextExtractionResult(extraction_status="completed", pages=pages),
        boundaries=boundaries,
        joined=joined,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        embedding_chunk_token_limit=8192,
    )
    sidecar = artifacts[0].sidecar_metadata
    attrs = sidecar["metadataAttributes"]
    assert attrs["chunk_type"]["value"]["stringValue"] == "section"
    assert attrs["chunk_id"]["value"]["stringValue"] == "pdf_test_doc_section_r311_7_5_1"

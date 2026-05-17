"""Tests for deterministic hierarchy generation and hierarchy-aware chunks."""

from document_ingestion_lambda.file_type_processors.hierarchy import (
    build_deterministic_hierarchy,
)
from document_ingestion_lambda.file_type_processors.hierarchy_chunking import (
    HierarchyChunkingConfig,
    build_hierarchy_chunk_artifacts,
)
from document_ingestion_lambda.file_type_processors.parser_profiles import get_parser_profile
from document_ingestion_lambda.file_type_processors.pdf import (
    PdfPageText,
    PdfTextExtractionResult,
)
from document_ingestion_lambda.triage import PdfTriageResult


def _document_metadata() -> dict[str, object]:
    return {
        "metadata_schema_version": "1.0",
        "document_id": "irc_doc",
        "document_title": "2021 International Residential Code",
        "document_type": "pdf",
        "filename": "irc.pdf",
        "source_location": "s3://bucket/kb_raw/irc_doc/original/irc.pdf",
        "tenant_id": "sandbox",
        "collection_id": "default",
        "access_tier": "public",
    }


def _text_extraction() -> PdfTextExtractionResult:
    return PdfTextExtractionResult(
        extraction_status="completed",
        pages=[
            PdfPageText(
                page_number=99,
                text=(
                    "R303 LIGHT, VENTILATION AND HEATING\n"
                    "General heating and ventilation requirements.\n"
                    "R303.10 Required heating. Every dwelling unit shall be provided "
                    "with heating facilities capable of maintaining a room temperature "
                    "of not less than 68 F."
                ),
            )
        ],
    )


def test_deterministic_hierarchy_finds_deep_subsection():
    result = build_deterministic_hierarchy(
        document_metadata=_document_metadata(),
        text_extraction=_text_extraction(),
        triage=PdfTriageResult(triage_status="completed"),
        profile=get_parser_profile("building_code"),
    )

    by_id = {node.hierarchy_node_id: node for node in result.hierarchy.nodes}
    assert "R303" in by_id
    assert "R303.10" in by_id
    assert by_id["R303.10"].parent_id == "R303"
    assert by_id["R303.10"].title == "Required heating"


def test_hierarchy_chunk_includes_inline_index_for_child_subsection():
    result = build_deterministic_hierarchy(
        document_metadata=_document_metadata(),
        text_extraction=_text_extraction(),
        triage=PdfTriageResult(triage_status="completed"),
        profile=get_parser_profile("building_code"),
    )
    artifacts = build_hierarchy_chunk_artifacts(
        joined_text=result.joined_text,
        hierarchy=result.hierarchy,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        raw_prefix="kb_raw/",
        config=HierarchyChunkingConfig(),
    )

    assert artifacts
    r303 = next(
        artifact
        for artifact in artifacts
        if artifact.catalog_metadata["hierarchy_node_id"] == "R303"
    )
    index = r303.catalog_metadata["custom_metadata"]["hierarchy_index_inline"]
    assert any(item["hierarchy_node_id"] == "R303.10" for item in index)
    assert r303.extra_objects

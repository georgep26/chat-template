"""Tests for the Textract-driven figure extractor.

We never render a real PDF or call Textract/Bedrock. The figure renderer
is a stub returning a tiny PIL image; Bedrock returns canned JSON.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from document_ingestion_lambda.file_type_processors.figure_extraction import (
    FigureExtractionConfig,
    extract_figure_chunk_artifacts,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _figure_block(
    *,
    figure_id: str,
    page: int,
    top: float,
    bbox: dict[str, float] | None = None,
    caption_block_id: str | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "Id": figure_id,
        "BlockType": "LAYOUT_FIGURE",
        "Page": page,
        "Geometry": {"BoundingBox": bbox or {"Top": top, "Left": 0.1, "Width": 0.5, "Height": 0.4}},
    }
    if caption_block_id:
        block["Relationships"] = [{"Type": "CHILD", "Ids": [caption_block_id]}]
    return block


def _layout_text_block(text_id: str, page: int, words: list[str]) -> list[dict[str, Any]]:
    word_blocks: list[dict[str, Any]] = []
    word_ids: list[str] = []
    for i, w in enumerate(words):
        wid = f"{text_id}_w{i}"
        word_ids.append(wid)
        word_blocks.append({"Id": wid, "BlockType": "WORD", "Text": w, "Page": page})
    text_block = {
        "Id": text_id,
        "BlockType": "LAYOUT_TEXT",
        "Page": page,
        "Relationships": [{"Type": "CHILD", "Ids": word_ids}],
    }
    return [text_block, *word_blocks]


def _document_metadata(document_id: str = "pdf_test_doc") -> dict[str, Any]:
    return {
        "metadata_schema_version": "1.0",
        "document_id": document_id,
        "document_title": "Test Doc",
        "document_type": "pdf",
        "filename": "test.pdf",
        "source_location": "s3://bucket/kb_raw/pdf_test_doc/original/test.pdf",
        "tenant_id": "tenant-a",
        "collection_id": "default",
        "access_tier": "restricted",
    }


class StubImage:
    """Tiny PIL-like image that supports .size, .crop, and .save."""

    def __init__(self, width: int = 200, height: int = 200):
        self.size = (width, height)
        self.crops: list[tuple[int, int, int, int]] = []

    def crop(self, box: tuple[int, int, int, int]) -> "StubImage":
        self.crops.append(box)
        out = StubImage(box[2] - box[0], box[3] - box[1])
        out.crops = list(self.crops)
        return out

    def save(self, buffer: Any, format: str) -> None:  # noqa: A002 (PIL signature)
        buffer.write(b"\x89PNG-stub")


def stub_renderer(_path: Path, _page: int, _scale: float) -> StubImage:
    return StubImage()


class StubS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> dict[str, Any]:
        self.objects[(Bucket, Key)] = Body
        return {"ETag": "stub"}


class StubBedrockClient:
    def __init__(self, *, description: str = "A line graph of beam deflection.", caption: str | None = None):
        self.calls: list[dict[str, Any]] = []
        self.description = description
        self.caption = caption

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        import json as _json

        payload = {"description": self.description, "caption": self.caption}
        return {"output": {"message": {"content": [{"text": _json.dumps(payload)}]}}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_figure_chunks_one_per_layout_figure(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    blocks = [
        _figure_block(figure_id="f1", page=5, top=0.1),
        _figure_block(figure_id="f2", page=5, top=0.6),
        _figure_block(figure_id="f3", page=7, top=0.2),
    ]
    s3 = StubS3Client()
    bedrock = StubBedrockClient(description="Diagram of stair geometry.")

    artifacts, result = extract_figure_chunk_artifacts(
        blocks=blocks,
        pdf_path=pdf,
        document_metadata=_document_metadata(),
        destination_bucket="documents-bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        bedrock_client=bedrock,
        s3_client=s3,
        config=FigureExtractionConfig(),
        page_renderer=stub_renderer,
    )

    assert result.status == "completed"
    assert result.figure_count == 3
    chunk_ids = [a.catalog_metadata["chunk_id"] for a in artifacts]
    assert chunk_ids == [
        "pdf_test_doc_figure_0005_01",
        "pdf_test_doc_figure_0005_02",
        "pdf_test_doc_figure_0007_01",
    ]
    # Every chunk has the figure description and the asset_location pointer.
    for a in artifacts:
        assert a.catalog_metadata["chunk_type"] == "figure"
        assert a.catalog_metadata["description"] == "Diagram of stair geometry."
        assert a.catalog_metadata["asset_location"].startswith(
            "s3://documents-bucket/kb_artifacts/pdf_test_doc/figures/"
        )
    # The cropped PNG bytes landed in S3 once per figure.
    assert len(s3.objects) == 3

    sidecar_attrs = artifacts[0].sidecar_metadata["metadataAttributes"]
    assert sidecar_attrs["chunk_type"]["value"]["stringValue"] == "figure"
    assert sidecar_attrs["description"]["value"]["stringValue"] == "Diagram of stair geometry."
    assert sidecar_attrs["asset_location"]["value"]["stringValue"].startswith(
        "s3://documents-bucket/kb_artifacts/pdf_test_doc/figures/"
    )
    assert sidecar_attrs["description"]["includeForEmbedding"] is True
    assert sidecar_attrs["asset_location"]["includeForEmbedding"] is False


def test_figure_caption_copied_from_layout_text_child(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    blocks: list[dict[str, Any]] = []
    blocks.append(
        _figure_block(figure_id="f1", page=2, top=0.1, caption_block_id="cap1")
    )
    blocks.extend(_layout_text_block("cap1", page=2, words=["Figure", "3-1", "Stair", "geometry"]))

    artifacts, _ = extract_figure_chunk_artifacts(
        blocks=blocks,
        pdf_path=pdf,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        bedrock_client=None,  # no model summary; caption should still come through
        s3_client=None,
        config=FigureExtractionConfig(),
        page_renderer=stub_renderer,
    )
    assert len(artifacts) == 1
    assert artifacts[0].catalog_metadata["caption"] == "Figure 3-1 Stair geometry"
    attrs = artifacts[0].sidecar_metadata["metadataAttributes"]
    assert attrs["caption"]["value"]["stringValue"] == "Figure 3-1 Stair geometry"
    assert attrs["caption"]["includeForEmbedding"] is True


def test_skipped_when_disabled(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    artifacts, result = extract_figure_chunk_artifacts(
        blocks=[_figure_block(figure_id="f1", page=1, top=0.1)],
        pdf_path=pdf,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        bedrock_client=None,
        s3_client=None,
        config=FigureExtractionConfig(enabled=False),
        page_renderer=stub_renderer,
    )
    assert artifacts == []
    assert result.status == "skipped"


def test_no_layout_figure_blocks_returns_empty(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    artifacts, result = extract_figure_chunk_artifacts(
        blocks=[{"Id": "x", "BlockType": "TABLE", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.1}}}],
        pdf_path=pdf,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        bedrock_client=None,
        s3_client=None,
        config=FigureExtractionConfig(),
        page_renderer=stub_renderer,
    )
    assert artifacts == []
    assert result.figure_count == 0


def test_max_figures_cap(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")
    blocks = [
        _figure_block(figure_id=f"f{i}", page=1 + i, top=0.1)
        for i in range(5)
    ]
    artifacts, result = extract_figure_chunk_artifacts(
        blocks=blocks,
        pdf_path=pdf,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        bedrock_client=None,
        s3_client=None,
        config=FigureExtractionConfig(max_figures=2),
        page_renderer=stub_renderer,
    )
    assert len(artifacts) == 2
    assert any("max_figures_hit" in w for w in result.warnings)


def test_bedrock_failure_falls_through_with_empty_description(tmp_path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF stub")

    class FailingBedrock:
        def converse(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("bedrock down")

    artifacts, result = extract_figure_chunk_artifacts(
        blocks=[_figure_block(figure_id="f1", page=1, top=0.1)],
        pdf_path=pdf,
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        bedrock_client=FailingBedrock(),
        s3_client=None,
        config=FigureExtractionConfig(),
        page_renderer=stub_renderer,
    )
    assert result.status == "completed"
    assert len(artifacts) == 1
    assert artifacts[0].catalog_metadata.get("description") is None

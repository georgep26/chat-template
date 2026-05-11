"""Tests for Textract-driven table extraction.

We never hit Textract or Bedrock in tests — both clients are stubs. The
Textract stub returns synthetic ``Block`` graphs that mirror real Textract
output shape (TABLE → CELL → WORD via Relationships).
"""

from __future__ import annotations

from typing import Any

import pytest

from document_ingestion_lambda.file_type_processors.table_extraction import (
    TableExtractionConfig,
    _grid_to_markdown,
    _table_block_to_grid,
    extract_table_chunk_artifacts,
)


# ---------------------------------------------------------------------------
# Block fixtures
# ---------------------------------------------------------------------------


def _table_blocks(
    *,
    table_id: str,
    page: int,
    top: float,
    cells: list[tuple[int, int, str]],
) -> list[dict[str, Any]]:
    """Build a synthetic Textract block list for one table.

    `cells` is a list of (row_index, column_index, text) tuples.
    """
    cell_blocks: list[dict[str, Any]] = []
    word_blocks: list[dict[str, Any]] = []
    cell_ids: list[str] = []

    for index, (row, col, text) in enumerate(cells):
        cell_id = f"{table_id}_cell_{index}"
        cell_ids.append(cell_id)
        # One WORD per cell — tests with multi-word cells should split text on " ".
        word_ids: list[str] = []
        for word_idx, word in enumerate(text.split()):
            word_id = f"{cell_id}_word_{word_idx}"
            word_ids.append(word_id)
            word_blocks.append(
                {
                    "Id": word_id,
                    "BlockType": "WORD",
                    "Text": word,
                    "Page": page,
                }
            )
        cell_blocks.append(
            {
                "Id": cell_id,
                "BlockType": "CELL",
                "RowIndex": row,
                "ColumnIndex": col,
                "Page": page,
                "Relationships": [{"Type": "CHILD", "Ids": word_ids}] if word_ids else [],
            }
        )

    return [
        {
            "Id": table_id,
            "BlockType": "TABLE",
            "Page": page,
            "Geometry": {"BoundingBox": {"Top": top, "Left": 0.1, "Width": 0.8, "Height": 0.2}},
            "Relationships": [{"Type": "CHILD", "Ids": cell_ids}],
        }
    ] + cell_blocks + word_blocks


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


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubTextractClient:
    """Records Textract calls and returns pre-baked responses."""

    def __init__(self, *, blocks: list[dict[str, Any]], job_id: str = "job-1") -> None:
        self.blocks = blocks
        self.job_id = job_id
        self.start_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self._poll_count = 0
        self._poll_count_until_success = 1

    def start_document_analysis(
        self,
        *,
        DocumentLocation: dict[str, Any],
        FeatureTypes: list[str],
    ) -> dict[str, Any]:
        self.start_calls.append({"DocumentLocation": DocumentLocation, "FeatureTypes": FeatureTypes})
        return {"JobId": self.job_id}

    def get_document_analysis(
        self, *, JobId: str, MaxResults: int | None = None, NextToken: str | None = None
    ) -> dict[str, Any]:
        self.get_calls.append({"JobId": JobId, "MaxResults": MaxResults, "NextToken": NextToken})
        # Polling phase: only return SUCCEEDED after a few polls when MaxResults=1.
        if MaxResults == 1 and NextToken is None:
            self._poll_count += 1
            if self._poll_count < self._poll_count_until_success:
                return {"JobStatus": "IN_PROGRESS", "Blocks": []}
            return {"JobStatus": "SUCCEEDED", "Blocks": []}
        # Pagination phase.
        return {"JobStatus": "SUCCEEDED", "Blocks": self.blocks}


class StubBedrockClient:
    """Returns a canned summary for every converse call."""

    def __init__(self, summary: str = "A small example table.") -> None:
        self.calls: list[dict[str, Any]] = []
        self.summary = summary

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self.summary}]}}}


# ---------------------------------------------------------------------------
# Grid + markdown unit tests
# ---------------------------------------------------------------------------


def test_grid_to_markdown_emits_pipe_table_with_header_row():
    grid = [
        ["Header A", "Header B"],
        ["a1", "b1"],
        ["a2", "b2"],
    ]
    out = _grid_to_markdown(grid)
    lines = out.split("\n")
    assert lines[0] == "| Header A | Header B |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| a1 | b1 |"
    assert lines[3] == "| a2 | b2 |"


def test_grid_to_markdown_escapes_pipe_characters():
    grid = [["A | B"], ["data"]]
    out = _grid_to_markdown(grid)
    # Pipe in cell text is escaped so it doesn't confuse markdown.
    assert "A \\| B" in out


def test_table_block_to_grid_reassembles_2x2():
    blocks = _table_blocks(
        table_id="t1",
        page=3,
        top=0.1,
        cells=[
            (1, 1, "Species"),
            (1, 2, "Fb"),
            (2, 1, "DF-L"),
            (2, 2, "1000"),
        ],
    )
    by_id = {b["Id"]: b for b in blocks}
    table = next(b for b in blocks if b["BlockType"] == "TABLE")
    grid = _table_block_to_grid(table, by_id)
    assert grid == [["Species", "Fb"], ["DF-L", "1000"]]


# ---------------------------------------------------------------------------
# Public API: extract_table_chunk_artifacts
# ---------------------------------------------------------------------------


def _no_sleep(seconds: float) -> None:
    """Bypass real polling in tests."""


def test_extract_table_chunks_one_per_table():
    blocks = _table_blocks(
        table_id="t1", page=2, top=0.1,
        cells=[
            (1, 1, "Species"),
            (1, 2, "Fb"),
            (2, 1, "DF-L"),
            (2, 2, "1000"),
        ],
    ) + _table_blocks(
        table_id="t2", page=2, top=0.5,
        cells=[
            (1, 1, "Grade"),
            (1, 2, "Fc"),
            (2, 1, "No.2"),
            (2, 2, "850"),
        ],
    )
    textract = StubTextractClient(blocks=blocks)
    bedrock = StubBedrockClient(summary="Table of design values for southern pine.")

    artifacts, result = extract_table_chunk_artifacts(
        raw_s3_bucket="bucket",
        raw_s3_key="kb_raw/pdf_test_doc/original/test.pdf",
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        textract_client=textract,
        bedrock_client=bedrock,
        config=TableExtractionConfig(),
        sleep=_no_sleep,
    )

    assert result.status == "completed"
    assert result.table_count == 2
    assert len(artifacts) == 2
    # Both tables on page 2; chunk ids include the page number and table index.
    chunk_ids = [a.catalog_metadata["chunk_id"] for a in artifacts]
    assert chunk_ids == [
        "pdf_test_doc_table_0002_01",
        "pdf_test_doc_table_0002_02",
    ]
    assert all(a.catalog_metadata["chunk_type"] == "table" for a in artifacts)
    assert all(a.catalog_metadata["page_start"] == 2 for a in artifacts)
    # Summary is embedded in chunk body for retrieval.
    md = artifacts[0].markdown
    assert "Table of design values" in md
    # Markdown table also present in the body.
    assert "| Species | Fb |" in md
    # Bedrock was called with the markdown table.
    assert len(bedrock.calls) == 2
    user_msg = bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert "| Species | Fb |" in user_msg


def test_textract_start_called_with_tables_and_layout_feature_types():
    blocks = _table_blocks(
        table_id="t1", page=1, top=0.1,
        cells=[(1, 1, "x"), (1, 2, "y")],
    )
    textract = StubTextractClient(blocks=blocks)

    extract_table_chunk_artifacts(
        raw_s3_bucket="bucket",
        raw_s3_key="kb_raw/pdf_test_doc/original/test.pdf",
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        textract_client=textract,
        bedrock_client=None,
        config=TableExtractionConfig(),
        sleep=_no_sleep,
    )

    assert textract.start_calls[0]["FeatureTypes"] == ["TABLES", "LAYOUT"]
    assert textract.start_calls[0]["DocumentLocation"] == {
        "S3Object": {"Bucket": "bucket", "Name": "kb_raw/pdf_test_doc/original/test.pdf"}
    }


def test_extract_skipped_when_disabled():
    textract = StubTextractClient(blocks=[])
    artifacts, result = extract_table_chunk_artifacts(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        textract_client=textract,
        bedrock_client=None,
        config=TableExtractionConfig(enabled=False),
        sleep=_no_sleep,
    )
    assert artifacts == []
    assert result.status == "skipped"
    assert textract.start_calls == []


def test_extract_failed_when_textract_job_fails():
    class FailingTextract(StubTextractClient):
        def get_document_analysis(self, **kwargs: Any) -> dict[str, Any]:
            return {"JobStatus": "FAILED", "StatusMessage": "oh no"}

    textract = FailingTextract(blocks=[])
    artifacts, result = extract_table_chunk_artifacts(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        textract_client=textract,
        bedrock_client=None,
        config=TableExtractionConfig(),
        sleep=_no_sleep,
    )
    assert artifacts == []
    assert result.status == "failed"
    assert any("textract_failed" in w for w in result.warnings)


def test_chunks_include_asset_location_pointing_at_artifacts_prefix():
    blocks = _table_blocks(
        table_id="t1", page=1, top=0.1,
        cells=[(1, 1, "x"), (1, 2, "y"), (2, 1, "1"), (2, 2, "2")],
    )
    textract = StubTextractClient(blocks=blocks)
    artifacts, _ = extract_table_chunk_artifacts(
        raw_s3_bucket="bucket",
        raw_s3_key="kb_raw/pdf_test_doc/original/test.pdf",
        document_metadata=_document_metadata(),
        destination_bucket="documents-bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        textract_client=textract,
        bedrock_client=None,
        config=TableExtractionConfig(),
        sleep=_no_sleep,
    )
    asset = artifacts[0].catalog_metadata["asset_location"]
    assert asset.startswith("s3://documents-bucket/kb_artifacts/pdf_test_doc/tables/")


def test_chunks_emitted_without_bedrock_have_no_summary():
    blocks = _table_blocks(
        table_id="t1", page=1, top=0.1,
        cells=[(1, 1, "x"), (1, 2, "y"), (2, 1, "1"), (2, 2, "2")],
    )
    textract = StubTextractClient(blocks=blocks)
    artifacts, result = extract_table_chunk_artifacts(
        raw_s3_bucket="bucket",
        raw_s3_key="kb_raw/pdf_test_doc/original/test.pdf",
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        textract_client=textract,
        bedrock_client=None,
        config=TableExtractionConfig(),
        sleep=_no_sleep,
    )
    assert result.status == "completed"
    assert len(artifacts) == 1
    assert artifacts[0].catalog_metadata.get("description") is None


def test_max_tables_cap_stops_emission():
    # Six tables: two on page 1, two on page 2, two on page 3.
    blocks: list[dict[str, Any]] = []
    for i, (page, top) in enumerate(
        [(1, 0.1), (1, 0.5), (2, 0.1), (2, 0.5), (3, 0.1), (3, 0.5)]
    ):
        blocks.extend(
            _table_blocks(
                table_id=f"t{i}", page=page, top=top,
                cells=[(1, 1, "x"), (1, 2, "y")],
            )
        )
    textract = StubTextractClient(blocks=blocks)
    artifacts, result = extract_table_chunk_artifacts(
        raw_s3_bucket="bucket",
        raw_s3_key="x.pdf",
        document_metadata=_document_metadata(),
        destination_bucket="bucket",
        index_prefix="kb_index/",
        artifacts_prefix="kb_artifacts/",
        textract_client=textract,
        bedrock_client=None,
        config=TableExtractionConfig(max_tables=3),
        sleep=_no_sleep,
    )
    assert len(artifacts) == 3
    assert any("max_tables_hit" in w for w in result.warnings)

"""Textract-based table extraction for structured PDFs.

This module drives a single async ``StartDocumentAnalysis`` call over the
whole PDF and turns each detected table into a standalone chunk artifact
in the same shape the page/section chunkers produce. Each table chunk
carries:

  * a 1–2 sentence summary (Nova Lite) — embedded for retrieval
  * the table rendered as markdown — included in the chunk body so the
    answer LLM can read cell values directly when the chunk is retrieved

We never inline tables as raw text in section/page chunks; tables are
always extracted into their own chunks so they're citable on their own
and so the embedding doesn't get diluted by tabular structure.

Textract returns a flat list of ``Block`` records linked through a
``Relationships`` graph. We rebuild the (row, column) grid from those
relationships and emit canonical pipe-style markdown.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from document_ingestion_lambda.file_type_processors.pdf import (
    PdfChunkArtifact,
    build_bedrock_sidecar_metadata,
    build_chunk_markdown,
    estimate_token_count,
    join_s3_key,
)


# ---------------------------------------------------------------------------
# Config + result types
# ---------------------------------------------------------------------------


@dataclass
class TableExtractionConfig:
    """Knobs read from ``ingestion.pdf_*`` config keys."""

    enabled: bool = True
    summary_model_id: str = "amazon.nova-lite-v1:0"
    summary_region: str = "us-east-1"
    summary_temperature: float = 0.0
    summary_max_tokens: int = 256
    poll_interval_seconds: float = 5.0
    max_wait_seconds: float = 540.0
    # Hard guard: stop at this many tables per document so a runaway
    # detection doesn't blow our cost or Lambda time budget.
    max_tables: int = 500


@dataclass
class TableExtractionResult:
    """Aggregate result for the whole-document Textract pass."""

    status: str  # "completed" | "skipped" | "failed"
    job_id: str | None = None
    table_count: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_textract_analysis(
    *,
    raw_s3_bucket: str,
    raw_s3_key: str,
    textract_client: Any,
    poll_interval_seconds: float,
    max_wait_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run an async Textract AnalyzeDocument job over the raw PDF.

    Used by both Phase 3 (table extraction) and Phase 4 (figure extraction)
    so we pay for one job per document, not two. Returns
    ``(blocks, warnings)``. Empty ``blocks`` with a populated ``warnings``
    list signals a recoverable failure — callers fall back to text-only
    output.
    """
    try:
        blocks = _start_and_collect_blocks(
            textract_client=textract_client,
            bucket=raw_s3_bucket,
            key=raw_s3_key,
            poll_interval=poll_interval_seconds,
            max_wait=max_wait_seconds,
            sleep=sleep,
        )
        return blocks, []
    except Exception as exc:
        return [], [f"textract_failed:{type(exc).__name__}:{exc}"]


def extract_table_chunk_artifacts(
    *,
    blocks: Sequence[dict[str, Any]] | None = None,
    raw_s3_bucket: str | None = None,
    raw_s3_key: str | None = None,
    document_metadata: dict[str, Any],
    destination_bucket: str,
    index_prefix: str,
    artifacts_prefix: str,
    textract_client: Any | None = None,
    bedrock_client: Any | None,
    config: TableExtractionConfig,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[PdfChunkArtifact], TableExtractionResult]:
    """Emit one chunk per table.

    Two call modes:

    * **Pre-fetched blocks** (preferred): pass ``blocks=`` from
      :func:`run_textract_analysis`. No Textract call is made; the
      function just rebuilds grids and emits artifacts.
    * **Self-contained** (back-compat for the test suite): pass
      ``textract_client`` + ``raw_s3_bucket`` + ``raw_s3_key`` and the
      function will run its own Textract job. Convenient for tests but
      pays for a Textract job by itself.

    Returns ``(artifacts, result_summary)``. Always-recoverable.
    """
    if not config.enabled:
        return [], TableExtractionResult(status="skipped", warnings=["table_extraction_disabled"])

    if blocks is None:
        if textract_client is None or not raw_s3_bucket or not raw_s3_key:
            return [], TableExtractionResult(
                status="failed", warnings=["blocks_or_textract_client_required"]
            )
        blocks, fetch_warnings = run_textract_analysis(
            raw_s3_bucket=raw_s3_bucket,
            raw_s3_key=raw_s3_key,
            textract_client=textract_client,
            poll_interval_seconds=config.poll_interval_seconds,
            max_wait_seconds=config.max_wait_seconds,
            sleep=sleep,
        )
        if fetch_warnings:
            return [], TableExtractionResult(status="failed", warnings=fetch_warnings)

    if not blocks:
        return [], TableExtractionResult(status="completed", table_count=0)

    grids = _build_table_grids(blocks)

    artifacts: list[PdfChunkArtifact] = []
    document_id = document_metadata["document_id"]
    chunk_index = 0
    warnings: list[str] = []
    for (page_number, table_index_on_page), grid in grids:
        if len(artifacts) >= config.max_tables:
            warnings.append(f"max_tables_hit:{config.max_tables}")
            break
        if not grid or not any(row for row in grid):
            continue
        chunk_index += 1
        markdown_table = _grid_to_markdown(grid)
        summary = _summarize_table(
            markdown=markdown_table,
            bedrock_client=bedrock_client,
            config=config,
        )
        chunk_id = f"{document_id}_table_{page_number:04d}_{table_index_on_page:02d}"
        key = join_s3_key(index_prefix, document_id, "tables", f"{chunk_id}.md")
        asset_key = join_s3_key(
            artifacts_prefix, document_id, "tables", f"{chunk_id}.md"
        )
        chunk_body = (
            f"{summary}\n\n{markdown_table}" if summary else markdown_table
        )
        metadata = {
            "metadata_schema_version": document_metadata["metadata_schema_version"],
            "record_type": "chunk",
            "document_id": document_id,
            "chunk_id": chunk_id,
            "tenant_id": document_metadata["tenant_id"],
            "collection_id": document_metadata["collection_id"],
            "document_title": document_metadata["document_title"],
            "title": f"{document_metadata['document_title']} - table on page {page_number}",
            "document_type": document_metadata["document_type"],
            "filename": document_metadata["filename"],
            "location": f"s3://{destination_bucket}/{key}",
            "source_location": document_metadata["source_location"],
            "asset_location": f"s3://{destination_bucket}/{asset_key}",
            "chunk_type": "table",
            "chunk_index": chunk_index,
            "page_start": page_number,
            "page_end": page_number,
            "content_type": "text/markdown",
            "token_count": estimate_token_count(chunk_body),
            "char_count": len(chunk_body),
            "access_tier": document_metadata.get("access_tier", "restricted"),
            "description": summary or None,
            "custom_metadata": {
                "table_index_on_page": table_index_on_page,
                "row_count": len(grid),
                "column_count": max(len(row) for row in grid) if grid else 0,
            },
            "processing": {
                "strategy": "textract_table_extraction",
                "summary_model_id": config.summary_model_id if summary else None,
            },
        }
        artifacts.append(
            PdfChunkArtifact(
                key=key,
                sidecar_key=f"{key}.metadata.json",
                markdown=build_chunk_markdown(metadata, chunk_body),
                sidecar_metadata=build_bedrock_sidecar_metadata(metadata),
                catalog_metadata=metadata,
            )
        )

    return artifacts, TableExtractionResult(
        status="completed",
        table_count=len(artifacts),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Textract orchestration
# ---------------------------------------------------------------------------


def _start_and_collect_blocks(
    *,
    textract_client: Any,
    bucket: str,
    key: str,
    poll_interval: float,
    max_wait: float,
    sleep: Callable[[float], None],
) -> list[dict[str, Any]]:
    """Start an async analysis job, poll until done, paginate the result.

    Raises on Textract job failure, timeout, or unexpected JobStatus.
    """
    start_response = textract_client.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES", "LAYOUT"],
    )
    job_id = start_response["JobId"]

    elapsed = 0.0
    while elapsed <= max_wait:
        sleep(poll_interval)
        elapsed += poll_interval
        response = textract_client.get_document_analysis(JobId=job_id, MaxResults=1)
        status = response.get("JobStatus")
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "PARTIAL_SUCCESS"):
            status_message = response.get("StatusMessage") or "no_status_message"
            raise RuntimeError(f"textract_job_{status.lower()}:{status_message}")
        if status not in ("IN_PROGRESS", "SUBMITTED", None):
            raise RuntimeError(f"textract_unexpected_status:{status}")
    else:
        raise TimeoutError(f"textract_timeout_after_{max_wait}s")

    # Paginate full result set.
    blocks: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"JobId": job_id}
        if next_token:
            kwargs["NextToken"] = next_token
        page = textract_client.get_document_analysis(**kwargs)
        blocks.extend(page.get("Blocks", []))
        next_token = page.get("NextToken")
        if not next_token:
            break
    return blocks


# ---------------------------------------------------------------------------
# Block-graph → markdown
# ---------------------------------------------------------------------------


def _build_table_grids(
    blocks: Sequence[dict[str, Any]],
) -> list[tuple[tuple[int, int], list[list[str]]]]:
    """Group blocks by table, rebuild each (row, col) grid of cell text.

    Returns a list of ((page_number, table_index_on_page), grid) tuples in
    document order (page then table index).
    """
    by_id = {b["Id"]: b for b in blocks if isinstance(b, dict) and b.get("Id")}

    tables: list[dict[str, Any]] = [
        b for b in blocks if b.get("BlockType") == "TABLE"
    ]
    # Order tables by (Page, Top y-coord) so they appear in reading order.
    tables.sort(
        key=lambda b: (
            int(b.get("Page", 1)),
            float((b.get("Geometry") or {}).get("BoundingBox", {}).get("Top", 0.0)),
        )
    )

    grids: list[tuple[tuple[int, int], list[list[str]]]] = []
    per_page_index: dict[int, int] = {}
    for table_block in tables:
        page_number = int(table_block.get("Page", 1))
        per_page_index[page_number] = per_page_index.get(page_number, 0) + 1
        table_idx = per_page_index[page_number]
        grid = _table_block_to_grid(table_block, by_id)
        grids.append(((page_number, table_idx), grid))
    return grids


def _table_block_to_grid(
    table_block: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> list[list[str]]:
    """Walk TABLE → CELL → WORD/SELECTION_ELEMENT to materialize a 2D grid."""
    cell_ids = _child_ids(table_block)
    cells = [by_id[cid] for cid in cell_ids if by_id.get(cid, {}).get("BlockType") == "CELL"]
    if not cells:
        return []

    max_row = max((int(c.get("RowIndex", 0)) for c in cells), default=0)
    max_col = max((int(c.get("ColumnIndex", 0)) for c in cells), default=0)
    if max_row == 0 or max_col == 0:
        return []

    grid: list[list[str]] = [["" for _ in range(max_col)] for _ in range(max_row)]
    for cell in cells:
        row_index = int(cell.get("RowIndex", 0))
        col_index = int(cell.get("ColumnIndex", 0))
        if row_index < 1 or col_index < 1:
            continue
        text = _collect_cell_text(cell, by_id)
        grid[row_index - 1][col_index - 1] = text
    return grid


def _child_ids(block: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for relation in block.get("Relationships", []) or []:
        if relation.get("Type") == "CHILD":
            out.extend(relation.get("Ids", []))
    return out


def _collect_cell_text(cell: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    """Reassemble a cell's textual content from its WORD/SELECTION children."""
    pieces: list[str] = []
    for child_id in _child_ids(cell):
        child = by_id.get(child_id)
        if not child:
            continue
        block_type = child.get("BlockType")
        if block_type == "WORD":
            pieces.append(str(child.get("Text") or ""))
        elif block_type == "SELECTION_ELEMENT":
            status = child.get("SelectionStatus")
            pieces.append("[x]" if status == "SELECTED" else "[ ]")
    return " ".join(p for p in pieces if p).strip()


_MARKDOWN_PIPE_RE = re.compile(r"\|")


def _grid_to_markdown(grid: list[list[str]]) -> str:
    """Render a 2D grid as a pipe-delimited markdown table.

    The first row is treated as the header row. Pipe characters in cell
    text are escaped to keep markdown rendering deterministic.
    """
    if not grid:
        return ""

    def escape(cell: str) -> str:
        return _MARKDOWN_PIPE_RE.sub(r"\\|", cell).replace("\n", " ").strip()

    header = grid[0]
    body = grid[1:] if len(grid) > 1 else []
    col_count = max(len(header), max((len(row) for row in body), default=0))

    def pad(row: list[str]) -> list[str]:
        return [escape(row[i]) if i < len(row) else "" for i in range(col_count)]

    lines: list[str] = []
    lines.append("| " + " | ".join(pad(header)) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in body:
        lines.append("| " + " | ".join(pad(row)) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary generation (Nova Lite, text-only)
# ---------------------------------------------------------------------------


def _summarize_table(
    *,
    markdown: str,
    bedrock_client: Any | None,
    config: TableExtractionConfig,
) -> str:
    """Ask Nova Lite for a 1–2 sentence summary of the table.

    Returns "" when no Bedrock client is available or the call fails —
    the chunk is still emitted, the embedding just has to lean on the
    raw markdown for retrieval signal.
    """
    if bedrock_client is None or not markdown.strip():
        return ""

    system = (
        "You summarize a single table for a retrieval index. Produce 1-2 plain "
        "sentences naming the table's subject, what each row represents, and "
        "what the columns measure. Do NOT invent values that aren't in the "
        "table. Do NOT quote individual cells. Output ONLY the summary text — "
        "no JSON, no preamble, no markdown."
    )
    try:
        response = bedrock_client.converse(
            modelId=config.summary_model_id,
            messages=[{"role": "user", "content": [{"text": markdown}]}],
            system=[{"text": system}],
            inferenceConfig={
                "temperature": config.summary_temperature,
                "maxTokens": config.summary_max_tokens,
            },
        )
        output = response.get("output") or {}
        message = output.get("message") or {}
        for item in message.get("content") or []:
            if isinstance(item, dict) and "text" in item:
                return str(item["text"]).strip()
    except Exception:
        # Recoverable: caller still gets the table chunk, just with no summary.
        return ""
    return ""


__all__ = [
    "TableExtractionConfig",
    "TableExtractionResult",
    "extract_table_chunk_artifacts",
    "run_textract_analysis",
]

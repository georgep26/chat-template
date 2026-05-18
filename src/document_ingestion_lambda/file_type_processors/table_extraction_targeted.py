"""Per-page targeted Textract table extraction.

Replaces the whole-document async ``StartDocumentAnalysis`` approach in
``table_extraction.py``. The new flow:

1. Identify table pages from the per-page triage observations Scout produced
   (``contains_table`` / ``tables[]`` non-empty).
2. Render each flagged page to PNG (same render path used by triage).
3. Call Textract sync ``AnalyzeDocument`` with ``FeatureTypes=['TABLES']``
   per page, parallelized via ``ThreadPoolExecutor``.
4. Match Scout's per-page table descriptions to Textract's reconstructed
   grids by order on the page.
5. Emit one chunk per detected table with Scout's vector-search-optimized
   description in the body, the Textract markdown grid below it, and a
   ``parent_hierarchy_node_id`` linking to the deepest hierarchy node that
   contains the table's page.

Cost: $0.015 per Scout-flagged table page (Textract Tables price), versus
$0.015 per page in the whole document under the old flow. For the IRC
100-page sample with ~15 table pages: ~$0.23 vs $1.50.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from document_ingestion_lambda.file_type_processors.hierarchy import (
    DocumentHierarchy,
    nearest_hierarchy_node_for_page,
)
from document_ingestion_lambda.file_type_processors.pdf import (
    PdfChunkArtifact,
    build_bedrock_sidecar_metadata,
    build_chunk_markdown,
    estimate_token_count,
    join_s3_key,
)
from document_ingestion_lambda.file_type_processors.table_extraction import (
    _build_table_grids,
    _grid_to_markdown,
)
from document_ingestion_lambda.triage import PdfTriageResult
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class TargetedTableExtractionConfig:
    """Configuration for per-page Textract table extraction."""

    enabled: bool = True
    max_parallel_pages: int = 6
    sync_timeout_seconds: int = 60
    max_tables: int = 500


@dataclass
class TargetedTableExtractionResult:
    """Aggregate result for the per-page Textract run."""

    status: str
    pages_processed: int = 0
    pages_failed: int = 0
    tables_detected: int = 0
    warnings: list[str] = field(default_factory=list)


def extract_tables_targeted(
    *,
    pdf_path: Path,
    triage: PdfTriageResult,
    hierarchy: DocumentHierarchy | None,
    document_metadata: dict[str, Any],
    destination_bucket: str,
    index_prefix: str,
    artifacts_prefix: str,
    textract_client: Any,
    page_renderer: Any,
    render_scale: float,
    config: TargetedTableExtractionConfig,
) -> tuple[list[PdfChunkArtifact], TargetedTableExtractionResult]:
    """Run Textract only on Scout-flagged table pages and emit table chunks.

    ``page_renderer`` is a ``(pdf_path, page_number, scale) -> bytes`` callable
    returning PNG bytes — same shape as ``triage._render_page_with_pdfium``.
    """
    if not config.enabled:
        return [], TargetedTableExtractionResult(
            status="skipped", warnings=["table_extraction_disabled"]
        )
    if textract_client is None:
        return [], TargetedTableExtractionResult(
            status="skipped", warnings=["textract_client_unavailable"]
        )

    # Build the candidate table-page list from Scout observations.
    table_pages: list[int] = sorted({
        obs.page_number
        for obs in triage.page_observations
        if obs.tables and not obs.parse_error
    })
    if not table_pages:
        return [], TargetedTableExtractionResult(
            status="completed",
            pages_processed=0,
            warnings=["no_scout_flagged_table_pages"],
        )

    # Build a page → Scout descriptions lookup so we can pair Textract grids
    # with descriptions by order on page.
    scout_tables_by_page: dict[int, list[dict[str, Any]]] = {}
    for obs in triage.page_observations:
        if obs.tables:
            scout_tables_by_page[obs.page_number] = [
                {
                    "caption": (t.caption or "").strip() if hasattr(t, "caption") else "",
                    "description": (t.description or "").strip() if hasattr(t, "description") else "",
                }
                for t in obs.tables
            ]

    log.info(
        "Targeted Textract starting: pages=%s workers=%s",
        len(table_pages),
        min(config.max_parallel_pages, len(table_pages)),
    )

    # Parallel per-page Textract calls.
    blocks_by_page: dict[int, list[dict[str, Any]]] = {}
    page_warnings: list[str] = []
    pages_failed = 0
    max_workers = min(config.max_parallel_pages, len(table_pages))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _analyze_page,
                pdf_path=pdf_path,
                page_number=page_number,
                page_renderer=page_renderer,
                render_scale=render_scale,
                textract_client=textract_client,
            ): page_number
            for page_number in table_pages
        }
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                blocks = future.result()
                blocks_by_page[page_number] = blocks
                log.info(
                    "Textract page complete: page=%s blocks=%s",
                    page_number,
                    len(blocks),
                )
            except Exception as exc:
                pages_failed += 1
                page_warnings.append(
                    f"page_{page_number}_textract_failed:{type(exc).__name__}"
                )
                log.warning(
                    "Textract page failed: page=%s error_type=%s",
                    page_number,
                    type(exc).__name__,
                )

    # Build chunks for each Textract-detected table on each page, in page order.
    artifacts: list[PdfChunkArtifact] = []
    document_id = document_metadata["document_id"]
    chunk_index = 0
    tables_detected = 0
    for page_number in sorted(blocks_by_page.keys()):
        blocks = blocks_by_page[page_number]
        # Force the Page field to 1 on these blocks (we only sent one page).
        # _build_table_grids uses block.Page for ordering.
        grids = _build_table_grids(blocks)
        if not grids:
            continue
        scout_tables = scout_tables_by_page.get(page_number, [])

        for table_index_on_page, ((_, _), grid) in enumerate(grids, start=1):
            if len(artifacts) >= config.max_tables:
                page_warnings.append(f"max_tables_hit:{config.max_tables}")
                break
            if not grid or not any(row for row in grid):
                continue

            # Match Scout description by order; fall back to None if Scout
            # found fewer tables than Textract (or no descriptions).
            scout_match = (
                scout_tables[table_index_on_page - 1]
                if table_index_on_page - 1 < len(scout_tables)
                else None
            )
            scout_caption = (scout_match or {}).get("caption") or None
            scout_description = (scout_match or {}).get("description") or None

            chunk_index += 1
            tables_detected += 1
            markdown_table = _grid_to_markdown(grid)
            chunk_id = f"{document_id}_table_{page_number:04d}_{table_index_on_page:02d}"
            key = join_s3_key(index_prefix, document_id, "tables", f"{chunk_id}.summary.md")
            asset_key = join_s3_key(
                artifacts_prefix, document_id, "tables", f"{chunk_id}.json"
            )

            # Compose the chunk body: description (for embeddings) + grid (for
            # the answer LLM to read cells).
            body_parts: list[str] = []
            if scout_caption:
                body_parts.append(f"# {scout_caption}")
            if scout_description:
                body_parts.append("## Description (for vector search)\n\n" + scout_description)
            body_parts.append("## Table\n\n" + markdown_table)
            chunk_body = "\n\n".join(body_parts)

            # Hierarchy linking.
            parent_node = (
                nearest_hierarchy_node_for_page(hierarchy=hierarchy, page_number=page_number)
                if hierarchy is not None
                else None
            )
            parent_node_id = parent_node.hierarchy_node_id if parent_node else None
            heading_path = "/".join(parent_node.heading_path) if parent_node else None

            metadata = {
                "metadata_schema_version": document_metadata["metadata_schema_version"],
                "record_type": "chunk",
                "document_id": document_id,
                "chunk_id": chunk_id,
                "tenant_id": document_metadata["tenant_id"],
                "collection_id": document_metadata["collection_id"],
                "document_title": document_metadata["document_title"],
                "title": scout_caption or f"Table on page {page_number}",
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
                "description": scout_description,
                "caption": scout_caption,
                "parent_hierarchy_node_id": parent_node_id,
                "section": parent_node.label if parent_node else None,
                "heading_path": heading_path,
                "custom_metadata": {
                    "table_index_on_page": table_index_on_page,
                    "row_count": len(grid),
                    "column_count": max(len(row) for row in grid) if grid else 0,
                    "parent_hierarchy_node_id": parent_node_id,
                    "grid_status": "present",
                },
                "processing": {
                    "strategy": "targeted_textract",
                    "scout_caption_matched": scout_caption is not None,
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

    return artifacts, TargetedTableExtractionResult(
        status="completed",
        pages_processed=len(blocks_by_page),
        pages_failed=pages_failed,
        tables_detected=tables_detected,
        warnings=page_warnings,
    )


def _analyze_page(
    *,
    pdf_path: Path,
    page_number: int,
    page_renderer: Any,
    render_scale: float,
    textract_client: Any,
) -> list[dict[str, Any]]:
    """Render one page to PNG and call Textract sync AnalyzeDocument."""
    png_bytes = page_renderer(pdf_path, page_number, render_scale)
    response = textract_client.analyze_document(
        Document={"Bytes": png_bytes},
        FeatureTypes=["TABLES"],
    )
    blocks = response.get("Blocks") or []
    # Tag every block with the original page number so _build_table_grids
    # orders them correctly.
    for block in blocks:
        block["Page"] = page_number
    return blocks

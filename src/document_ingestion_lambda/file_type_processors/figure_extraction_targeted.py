"""Figure extraction via Scout descriptions + full-page PNG artifacts.

Per the implementation plan, figures use a much simpler approach than tables:
no bbox cropping, no separate Bedrock summary call. We:

1. Identify figure pages from Scout triage observations (``figures[]``
   non-empty per page).
2. For each detected figure on a flagged page, emit one chunk whose body
   is Scout's caption + description. The artifact is the full rendered
   page PNG (shared across all figure chunks on the same page).
3. Link to the parent hierarchy node by page.

The RAG Lambda is expected to attach the page PNG to the multimodal answer
model's context when one of these chunks is retrieved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from document_ingestion_lambda.file_type_processors.hierarchy import (
    DocumentHierarchy,
    nearest_hierarchy_node_for_page,
)
from document_ingestion_lambda.file_type_processors.pdf import (
    PdfChunkArtifact,
    PdfExtraArtifact,
    build_bedrock_sidecar_metadata,
    build_chunk_markdown,
    estimate_token_count,
    join_s3_key,
)
from document_ingestion_lambda.triage import PdfTriageResult
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class TargetedFigureExtractionConfig:
    """Configuration for full-page-PNG figure extraction."""

    enabled: bool = True
    render_scale: float = 2.0
    max_figures: int = 200


@dataclass
class TargetedFigureExtractionResult:
    """Aggregate result for the targeted figure extraction run."""

    status: str
    figure_count: int = 0
    pages_with_figures: int = 0
    warnings: list[str] = field(default_factory=list)


def extract_figures_targeted(
    *,
    pdf_path: Path,
    triage: PdfTriageResult,
    hierarchy: DocumentHierarchy | None,
    document_metadata: dict[str, Any],
    destination_bucket: str,
    index_prefix: str,
    artifacts_prefix: str,
    page_renderer: Any,
    config: TargetedFigureExtractionConfig,
) -> tuple[list[PdfChunkArtifact], TargetedFigureExtractionResult]:
    """Emit one chunk per Scout-detected figure, using the full page PNG as the asset.

    ``page_renderer`` is a ``(pdf_path, page_number, scale) -> bytes`` callable
    returning PNG bytes — same shape as ``triage._render_page_with_pdfium``.
    """
    if not config.enabled:
        return [], TargetedFigureExtractionResult(
            status="skipped", warnings=["figure_extraction_disabled"]
        )

    figure_pages: list[int] = sorted({
        obs.page_number
        for obs in triage.page_observations
        if obs.figures and not obs.parse_error
    })
    if not figure_pages:
        return [], TargetedFigureExtractionResult(
            status="completed",
            pages_with_figures=0,
            warnings=["no_scout_flagged_figure_pages"],
        )

    log.info(
        "Targeted figure extraction starting: pages=%s",
        len(figure_pages),
    )

    figures_by_page: dict[int, list[dict[str, Any]]] = {}
    for obs in triage.page_observations:
        if obs.figures:
            figures_by_page[obs.page_number] = [
                {
                    "caption": (f.caption or "").strip() if hasattr(f, "caption") else "",
                    "description": (f.description or "").strip() if hasattr(f, "description") else "",
                }
                for f in obs.figures
            ]

    document_id = document_metadata["document_id"]
    artifacts: list[PdfChunkArtifact] = []
    warnings: list[str] = []
    chunk_index = 0

    for page_number in figure_pages:
        figures = figures_by_page.get(page_number, [])
        if not figures:
            continue

        # Render the full page PNG once per page; share across figure chunks.
        try:
            png_bytes = page_renderer(pdf_path, page_number, config.render_scale)
        except Exception as exc:
            warnings.append(
                f"page_{page_number}_render_failed:{type(exc).__name__}"
            )
            log.warning(
                "Figure page render failed: page=%s error_type=%s",
                page_number,
                type(exc).__name__,
            )
            continue

        page_asset_key = join_s3_key(
            artifacts_prefix, document_id, "pages", f"page_{page_number:04d}.png"
        )
        page_asset_uri = f"s3://{destination_bucket}/{page_asset_key}"

        # Hierarchy parent for the page (computed once).
        parent_node = (
            nearest_hierarchy_node_for_page(hierarchy=hierarchy, page_number=page_number)
            if hierarchy is not None
            else None
        )
        parent_node_id = parent_node.hierarchy_node_id if parent_node else None
        heading_path = "/".join(parent_node.heading_path) if parent_node else None

        for figure_index_on_page, figure in enumerate(figures, start=1):
            if len(artifacts) >= config.max_figures:
                warnings.append(f"max_figures_hit:{config.max_figures}")
                break

            caption = figure.get("caption") or None
            description = figure.get("description") or None
            chunk_index += 1
            chunk_id = (
                f"{document_id}_figure_{page_number:04d}_{figure_index_on_page:02d}"
            )
            key = join_s3_key(
                index_prefix, document_id, "figures", f"{chunk_id}.summary.md"
            )

            # Compose the chunk body: caption + description (the embedded text).
            body_parts: list[str] = []
            if caption:
                body_parts.append(f"# {caption}")
            if description:
                body_parts.append("## Description (for vector search)\n\n" + description)
            body_parts.append(
                "## Visual reference\n\n"
                f"The figure appears on page {page_number} of the source document. "
                "The full rendered page is stored at the asset location and is "
                "provided to the multimodal answer model when this chunk is "
                "retrieved."
            )
            chunk_body = "\n\n".join(body_parts)

            metadata = {
                "metadata_schema_version": document_metadata["metadata_schema_version"],
                "record_type": "chunk",
                "document_id": document_id,
                "chunk_id": chunk_id,
                "tenant_id": document_metadata["tenant_id"],
                "collection_id": document_metadata["collection_id"],
                "document_title": document_metadata["document_title"],
                "title": caption or f"Figure on page {page_number}",
                "document_type": document_metadata["document_type"],
                "filename": document_metadata["filename"],
                "location": f"s3://{destination_bucket}/{key}",
                "source_location": document_metadata["source_location"],
                "asset_location": page_asset_uri,
                "asset_content_type": "image/png",
                "asset_kind": "rendered_page",
                "chunk_type": "figure",
                "chunk_index": chunk_index,
                "page_start": page_number,
                "page_end": page_number,
                "content_type": "text/markdown",
                "token_count": estimate_token_count(chunk_body),
                "char_count": len(chunk_body),
                "access_tier": document_metadata.get("access_tier", "restricted"),
                "description": description,
                "caption": caption,
                "parent_hierarchy_node_id": parent_node_id,
                "section": parent_node.label if parent_node else None,
                "heading_path": heading_path,
                "custom_metadata": {
                    "figure_index_on_page": figure_index_on_page,
                    "parent_hierarchy_node_id": parent_node_id,
                    "asset_kind": "rendered_page",
                    "render_scale": config.render_scale,
                },
                "processing": {
                    "strategy": "scout_description_with_full_page_png",
                },
            }

            extra_objects: list[PdfExtraArtifact] = []
            if figure_index_on_page == 1:
                # Write the shared page PNG once; later figures on the same
                # page reference the same asset location.
                extra_objects.append(
                    PdfExtraArtifact(
                        key=page_asset_key,
                        body=png_bytes,
                        content_type="image/png",
                    )
                )

            artifacts.append(
                PdfChunkArtifact(
                    key=key,
                    sidecar_key=f"{key}.metadata.json",
                    markdown=build_chunk_markdown(metadata, chunk_body),
                    sidecar_metadata=build_bedrock_sidecar_metadata(metadata),
                    catalog_metadata=metadata,
                    extra_objects=extra_objects,
                )
            )

        if len(artifacts) >= config.max_figures:
            break

    return artifacts, TargetedFigureExtractionResult(
        status="completed",
        figure_count=len(artifacts),
        pages_with_figures=len(figure_pages),
        warnings=warnings,
    )

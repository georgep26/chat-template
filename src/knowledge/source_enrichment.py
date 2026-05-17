"""Source enrichment for retrieved Bedrock Knowledge Base chunks."""

from __future__ import annotations

from typing import Any, List

from langchain_core.documents import Document

from utils.logger import get_logger

from .chunk_read import create_chunk_read_store
from .vector_search import VectorSearchResult

log = get_logger(__name__)


def enrich_vector_results(
    *,
    results: List[VectorSearchResult],
    config: dict[str, Any],
    tenant_id: str,
) -> List[VectorSearchResult]:
    """Merge app-owned chunk catalog metadata into vector-search results.

    Bedrock sidecars should carry scalar metadata useful for filtering and
    retrieval. Larger nested metadata, such as hierarchy indexes, lives in the
    app catalog. This function enriches retrieved results by ``chunk_id`` when
    the Data API config is available. Failures degrade to the original Bedrock
    metadata so chat can still answer.
    """
    chunk_ids = [result.chunk_id for result in results if result.chunk_id]
    if not chunk_ids:
        return results

    store = create_chunk_read_store(config)
    if store is None:
        return results

    try:
        rows_by_id = store.get_chunks_by_ids(chunk_ids=chunk_ids, tenant_id=tenant_id)
    except Exception as exc:
        log.warning("Source enrichment skipped after chunk read failure: %s", exc)
        return results

    for result in results:
        if not result.chunk_id:
            continue
        row = rows_by_id.get(result.chunk_id)
        if not row:
            continue
        enriched_metadata = _merge_metadata(result.metadata, row)
        result.metadata = enriched_metadata
        result.document = Document(
            page_content=result.document.page_content,
            metadata={
                **(result.document.metadata or {}),
                "source_metadata": enriched_metadata,
            },
        )
    return results


def _merge_metadata(existing: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    custom_metadata = (
        row_metadata.get("custom_metadata")
        if isinstance(row_metadata.get("custom_metadata"), dict)
        else {}
    )
    merged = {
        **existing,
        **{k: v for k, v in row.items() if k != "metadata" and v is not None},
        **row_metadata,
        **custom_metadata,
    }
    # Preserve Bedrock retrieval score/location if the row metadata had stale values.
    for key in ("score", "retrieval_location"):
        if key in existing:
            merged[key] = existing[key]
    return merged

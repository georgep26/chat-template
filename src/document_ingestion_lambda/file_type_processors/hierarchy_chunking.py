"""Adaptive hierarchy-aware chunk artifact construction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from document_ingestion_lambda.file_type_processors.hierarchy import (
    DocumentHierarchy,
    HierarchyNode,
    hierarchy_index_for_range,
    hierarchy_node_lookup,
)
from document_ingestion_lambda.file_type_processors.pdf import (
    PdfChunkArtifact,
    PdfExtraArtifact,
    build_bedrock_sidecar_metadata,
    build_chunk_markdown,
    estimate_token_count,
    join_s3_key,
    split_text_by_estimated_tokens,
)


@dataclass
class HierarchyChunkingConfig:
    """Tuning knobs for adaptive hierarchy chunking."""

    target_min_tokens: int = 300
    target_max_tokens: int = 3000
    hard_max_tokens: int = 7000
    merge_small_leaf_nodes: bool = True
    split_large_nodes_by_children: bool = True
    store_index: bool = True
    inline_index_max_nodes: int = 40
    embedding_chunk_token_limit: int = 8192
    embedding_chunk_char_limit: int | None = None


def build_hierarchy_chunk_artifacts(
    *,
    joined_text: Any,
    hierarchy: DocumentHierarchy,
    document_metadata: dict[str, Any],
    destination_bucket: str,
    index_prefix: str,
    raw_prefix: str,
    config: HierarchyChunkingConfig,
) -> list[PdfChunkArtifact]:
    """Build retrieval chunks from hierarchy nodes and attach hierarchy indexes."""
    if not hierarchy.nodes:
        return []

    text = joined_text.text
    selected_nodes = _select_chunk_nodes(hierarchy, config)
    artifacts: list[PdfChunkArtifact] = []
    chunk_index = 0

    for node in selected_nodes:
        if node.char_start is None or node.char_end is None:
            continue
        node_text = text[node.char_start : node.char_end].strip()
        if not node_text:
            continue

        max_tokens = min(config.hard_max_tokens, config.embedding_chunk_token_limit)
        parts = split_text_by_estimated_tokens(
            node_text,
            max_tokens=max_tokens,
            overlap_fraction=0.0,
            max_chars=config.embedding_chunk_char_limit,
        )
        part_count = max(1, len(parts))
        running_start = node.char_start

        for part_index, part_text in enumerate(parts, start=1):
            chunk_index += 1
            part_start = running_start
            part_end = running_start + len(part_text)
            running_start = part_end

            chunk_id = _build_hierarchy_chunk_id(
                document_metadata["document_id"],
                node.hierarchy_node_id,
                part_index,
                part_count,
            )
            key = join_s3_key(index_prefix, document_metadata["document_id"], "sections", f"{chunk_id}.md")
            hierarchy_index = hierarchy_index_for_range(
                hierarchy=hierarchy,
                chunk_start=part_start,
                chunk_end=part_end,
            )
            index_key = join_s3_key(
                raw_prefix,
                document_metadata["document_id"],
                "hierarchy_indexes",
                f"{chunk_id}.hierarchy_index.json",
            )
            index_location = f"s3://{destination_bucket}/{index_key}"
            inline_index = (
                hierarchy_index
                if config.store_index and len(hierarchy_index) <= config.inline_index_max_nodes
                else []
            )
            page_start, page_end = _page_range_for_part(
                joined_text=joined_text,
                fallback_start=node.page_start,
                fallback_end=node.page_end,
                char_start=part_start,
                char_end=part_end,
            )
            title = _chunk_title(node, part_index, part_count)
            metadata = {
                "metadata_schema_version": document_metadata["metadata_schema_version"],
                "record_type": "chunk",
                "document_id": document_metadata["document_id"],
                "chunk_id": chunk_id,
                "tenant_id": document_metadata["tenant_id"],
                "collection_id": document_metadata["collection_id"],
                "document_title": document_metadata["document_title"],
                "title": title,
                "document_type": document_metadata["document_type"],
                "filename": document_metadata["filename"],
                "location": f"s3://{destination_bucket}/{key}",
                "source_location": document_metadata["source_location"],
                "chunk_type": "section",
                "chunk_index": chunk_index,
                "chapter": node.heading_path[0] if node.heading_path else None,
                "section": node.label,
                "heading_path": "/".join(node.heading_path),
                "hierarchy_node_id": node.hierarchy_node_id,
                "hierarchy_profile": hierarchy.profile,
                "hierarchy_path": "/".join(node.heading_path),
                "hierarchy_index_location": index_location,
                "page_start": page_start,
                "page_end": page_end,
                "content_type": "text/markdown",
                "token_count": estimate_token_count(part_text),
                "char_count": len(part_text),
                "access_tier": document_metadata.get("access_tier", "restricted"),
                "custom_metadata": {
                    "retrieval_strategy": "adaptive_hierarchy",
                    "hierarchy_node_id": node.hierarchy_node_id,
                    "hierarchy_profile": hierarchy.profile,
                    "hierarchy_index_location": index_location,
                    "hierarchy_index_inline": inline_index,
                    "part_index": part_index,
                    "part_count": part_count,
                    "chunk_char_start": part_start,
                    "chunk_char_end": part_end,
                },
                "processing": {
                    "strategy": "adaptive_hierarchy",
                    "configured_target_min_tokens": config.target_min_tokens,
                    "configured_target_max_tokens": config.target_max_tokens,
                    "configured_hard_max_tokens": config.hard_max_tokens,
                    "configured_embedding_chunk_token_limit": config.embedding_chunk_token_limit,
                    "configured_embedding_chunk_char_limit": config.embedding_chunk_char_limit,
                },
            }
            extra_objects: list[PdfExtraArtifact] = []
            if config.store_index:
                extra_objects.append(
                    PdfExtraArtifact(
                        key=index_key,
                        body=json.dumps(
                            {
                                "schema_version": "1.0",
                                "document_id": document_metadata["document_id"],
                                "chunk_id": chunk_id,
                                "hierarchy_index": hierarchy_index,
                            },
                            indent=2,
                            sort_keys=True,
                        ).encode("utf-8"),
                        content_type="application/json",
                    )
                )
            artifacts.append(
                PdfChunkArtifact(
                    key=key,
                    sidecar_key=f"{key}.metadata.json",
                    markdown=build_chunk_markdown(metadata, part_text),
                    sidecar_metadata=build_bedrock_sidecar_metadata(metadata),
                    catalog_metadata=metadata,
                    extra_objects=extra_objects,
                )
            )

    return artifacts


def _select_chunk_nodes(
    hierarchy: DocumentHierarchy,
    config: HierarchyChunkingConfig,
) -> list[HierarchyNode]:
    node_by_id = hierarchy_node_lookup(hierarchy)
    selected: list[HierarchyNode] = []

    def visit(node: HierarchyNode) -> None:
        if node.char_start is None or node.char_end is None:
            for child_id in node.children:
                child = node_by_id.get(child_id)
                if child:
                    visit(child)
            return

        token_estimate = node.token_estimate or 0
        children = [node_by_id[child_id] for child_id in node.children if child_id in node_by_id]
        if token_estimate <= config.hard_max_tokens or not children:
            selected.append(node)
            return

        for child in children:
            visit(child)

    for root_id in hierarchy.root_nodes:
        root = node_by_id.get(root_id)
        if root:
            visit(root)

    return selected


def _build_hierarchy_chunk_id(
    document_id: str,
    hierarchy_node_id: str,
    part_index: int,
    part_count: int,
) -> str:
    base = f"{document_id}_section_{_slug(hierarchy_node_id)}"
    if part_count > 1:
        return f"{base}_part_{part_index:03d}"
    return base


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "node"


def _chunk_title(node: HierarchyNode, part_index: int, part_count: int) -> str:
    prefix = f"{node.label} " if node.label else ""
    title = f"{prefix}{node.title}".strip()
    if part_count > 1:
        title = f"{title} (part {part_index})"
    return title


def _page_range_for_part(
    *,
    joined_text: Any,
    fallback_start: int | None,
    fallback_end: int | None,
    char_start: int,
    char_end: int,
) -> tuple[int | None, int | None]:
    char_to_page = getattr(joined_text, "char_to_page", [])
    if not char_to_page:
        return fallback_start, fallback_end
    start_idx = min(max(char_start, 0), len(char_to_page) - 1)
    end_idx = min(max(char_end - 1, start_idx), len(char_to_page) - 1)
    return char_to_page[start_idx], char_to_page[end_idx]

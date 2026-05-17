"""Deterministic hierarchy generation for structured PDF text."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from document_ingestion_lambda.file_type_processors.parser_profiles import ParserProfile
from document_ingestion_lambda.file_type_processors.pdf import (
    PdfPageText,
    PdfTextExtractionResult,
    estimate_token_count,
)
from document_ingestion_lambda.file_type_processors.section_chunking import (
    SectionBoundary,
    SectionDetectionConfig,
    _heading_path_for,
    detect_section_boundaries,
)
from document_ingestion_lambda.triage import PdfTriageResult


@dataclass
class HierarchyNode:
    """One logical node in a document hierarchy."""

    hierarchy_node_id: str
    label: str | None
    title: str
    node_type: str
    parent_id: str | None
    children: list[str] = field(default_factory=list)
    heading_path: list[str] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    token_estimate: int | None = None
    source: str = "deterministic"
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DocumentHierarchy:
    """Canonical hierarchy artifact for a processed document."""

    document_id: str
    schema_version: str
    profile: str
    heading_pattern_type: str
    source_location: str
    root_nodes: list[str]
    nodes: list[HierarchyNode]
    generation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)


@dataclass
class HierarchyBuildResult:
    """Hierarchy plus joined text reused by chunking."""

    hierarchy: DocumentHierarchy
    joined_text: Any
    boundaries: list[SectionBoundary]
    diagnostics: dict[str, Any]


def build_deterministic_hierarchy(
    *,
    document_metadata: dict[str, Any],
    text_extraction: PdfTextExtractionResult,
    triage: PdfTriageResult,
    profile: ParserProfile,
    min_section_chars: int = 40,
) -> HierarchyBuildResult:
    """Build a deterministic hierarchy from extracted text and triage observations."""
    pattern = _select_heading_pattern(profile, triage)
    skip_pages = tuple(triage.toc_pages or ())
    boundaries, joined = detect_section_boundaries(
        text_extraction.pages,
        config=SectionDetectionConfig(
            hierarchy_kind=pattern,
            min_section_chars=min_section_chars,
            min_boundaries=1,
            skip_pages=skip_pages,
            max_title_words=16,
        ),
    )
    nodes = _nodes_from_boundaries(boundaries, joined.text)
    root_nodes = _root_nodes(nodes)
    confidence = _hierarchy_confidence(nodes, text_extraction.pages, triage)
    hierarchy_available = bool(nodes)
    hierarchy = DocumentHierarchy(
        document_id=document_metadata["document_id"],
        schema_version="1.0",
        profile=profile.profile_id if hierarchy_available else "plain_document",
        heading_pattern_type=pattern if hierarchy_available else "page_only",
        source_location=document_metadata["source_location"],
        root_nodes=root_nodes,
        nodes=nodes,
        generation={
            "source": "deterministic",
            "status": "complete" if hierarchy_available else "not_detected",
            "confidence": confidence,
            "boundary_count": len(boundaries),
            "toc_pages": list(skip_pages),
        },
    )
    diagnostics = validate_hierarchy(hierarchy)
    hierarchy.generation["validation"] = diagnostics
    return HierarchyBuildResult(
        hierarchy=hierarchy,
        joined_text=joined,
        boundaries=list(boundaries),
        diagnostics=diagnostics,
    )


def validate_hierarchy(hierarchy: DocumentHierarchy) -> dict[str, Any]:
    """Validate basic hierarchy consistency."""
    node_by_id = {node.hierarchy_node_id: node for node in hierarchy.nodes}
    errors: list[str] = []
    warnings: list[str] = []

    if len(node_by_id) != len(hierarchy.nodes):
        errors.append("duplicate_hierarchy_node_ids")

    for node in hierarchy.nodes:
        if node.parent_id and node.parent_id not in node_by_id:
            errors.append(f"missing_parent:{node.hierarchy_node_id}:{node.parent_id}")
        if (
            node.page_start is not None
            and node.page_end is not None
            and node.page_end < node.page_start
        ):
            errors.append(f"invalid_page_range:{node.hierarchy_node_id}")
        if (
            node.char_start is not None
            and node.char_end is not None
            and node.char_end < node.char_start
        ):
            errors.append(f"invalid_char_range:{node.hierarchy_node_id}")

    previous_start = -1
    for node in sorted(
        [n for n in hierarchy.nodes if n.char_start is not None],
        key=lambda item: int(item.char_start or 0),
    ):
        if int(node.char_start or 0) < previous_start:
            warnings.append(f"non_monotonic_node:{node.hierarchy_node_id}")
        previous_start = int(node.char_start or 0)

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def hierarchy_node_lookup(hierarchy: DocumentHierarchy) -> dict[str, HierarchyNode]:
    """Return hierarchy nodes keyed by id."""
    return {node.hierarchy_node_id: node for node in hierarchy.nodes}


def hierarchy_index_for_range(
    *,
    hierarchy: DocumentHierarchy,
    chunk_start: int,
    chunk_end: int,
) -> list[dict[str, Any]]:
    """Build a hierarchy index for nodes overlapping a chunk range."""
    entries: list[dict[str, Any]] = []
    for node in hierarchy.nodes:
        if node.char_start is None or node.char_end is None:
            continue
        if node.char_end <= chunk_start or node.char_start >= chunk_end:
            continue
        entries.append(
            {
                "hierarchy_node_id": node.hierarchy_node_id,
                "label": node.label,
                "title": node.title,
                "node_type": node.node_type,
                "heading_path": node.heading_path,
                "depth": max(1, len(node.heading_path)),
                "char_start": max(0, node.char_start - chunk_start),
                "char_end": max(0, min(node.char_end, chunk_end) - chunk_start),
                "page_start": node.page_start,
                "page_end": node.page_end,
            }
        )
    return sorted(entries, key=lambda item: (item["char_start"], item["depth"]))


def nearest_hierarchy_node_for_page(
    *,
    hierarchy: DocumentHierarchy,
    page_number: int | None,
) -> HierarchyNode | None:
    """Find the deepest hierarchy node that contains a page."""
    if page_number is None:
        return None
    candidates = [
        node
        for node in hierarchy.nodes
        if node.page_start is not None
        and node.page_end is not None
        and node.page_start <= page_number <= node.page_end
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda node: len(node.heading_path))


def _select_heading_pattern(profile: ParserProfile, triage: PdfTriageResult) -> str:
    pattern_counts: dict[str, int] = {}
    for observation in triage.page_observations:
        if observation.heading_pattern in profile.heading_pattern_types:
            pattern_counts[observation.heading_pattern] = (
                pattern_counts.get(observation.heading_pattern, 0) + 1
            )
        for candidate in observation.heading_candidates:
            pattern = candidate.heading_pattern_type
            if pattern in profile.heading_pattern_types:
                pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
    if pattern_counts:
        return max(pattern_counts, key=pattern_counts.get)
    return profile.default_heading_pattern_type


def _nodes_from_boundaries(
    boundaries: Sequence[SectionBoundary], joined_text: str
) -> list[HierarchyNode]:
    explicit_ids = {boundary.section_id for boundary in boundaries}
    nodes: list[HierarchyNode] = []

    for boundary in boundaries:
        text = joined_text[boundary.start_offset : boundary.end_offset]
        parent_id = _nearest_existing_parent(boundary.section_id, explicit_ids)
        node = HierarchyNode(
            hierarchy_node_id=boundary.section_id,
            label=boundary.section_id,
            title=boundary.title,
            node_type=_node_type(boundary.section_id),
            parent_id=parent_id,
            heading_path=boundary.heading_path,
            page_start=boundary.start_page,
            page_end=boundary.end_page,
            char_start=boundary.start_offset,
            char_end=boundary.end_offset,
            token_estimate=estimate_token_count(text),
            evidence=[
                {
                    "source": "extracted_text",
                    "start_offset": boundary.start_offset,
                    "end_offset": boundary.end_offset,
                }
            ],
        )
        nodes.append(node)

    by_id = {node.hierarchy_node_id: node for node in nodes}
    for node in nodes:
        if node.parent_id and node.parent_id in by_id:
            by_id[node.parent_id].children.append(node.hierarchy_node_id)

    for node in sorted(nodes, key=lambda item: len(item.heading_path), reverse=True):
        for child_id in node.children:
            child = by_id.get(child_id)
            if child is None:
                continue
            if child.char_end is not None:
                node.char_end = max(node.char_end or child.char_end, child.char_end)
            if child.page_end is not None:
                node.page_end = max(node.page_end or child.page_end, child.page_end)
        if node.char_start is not None and node.char_end is not None:
            node.token_estimate = estimate_token_count(joined_text[node.char_start : node.char_end])

    return nodes


def _nearest_existing_parent(section_id: str, explicit_ids: set[str]) -> str | None:
    path = _heading_path_for(section_id)
    for candidate in reversed(path[:-1]):
        if candidate in explicit_ids:
            return candidate
    return None


def _root_nodes(nodes: Sequence[HierarchyNode]) -> list[str]:
    return [node.hierarchy_node_id for node in nodes if not node.parent_id]


def _hierarchy_confidence(
    nodes: Sequence[HierarchyNode],
    pages: Sequence[PdfPageText],
    triage: PdfTriageResult,
) -> float:
    if not nodes:
        return 0.0
    page_count = max(1, len(pages))
    coverage_pages = {
        page
        for node in nodes
        if node.page_start is not None and node.page_end is not None
        for page in range(node.page_start, node.page_end + 1)
    }
    coverage = min(1.0, len(coverage_pages) / page_count)
    triage_signal = triage.hierarchy_confidence or 0.0
    boundary_signal = min(1.0, len(nodes) / 10)
    return round(max(coverage, triage_signal, boundary_signal), 3)


def _node_type(section_id: str) -> str:
    depth = len(_heading_path_for(section_id))
    if depth <= 1:
        return "section"
    if depth == 2:
        return "subsection"
    return "subsubsection"

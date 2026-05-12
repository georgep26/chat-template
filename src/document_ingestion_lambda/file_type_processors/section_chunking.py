"""Section-aware chunking for structured PDFs.

When the multimodal triage decides a document has a real heading hierarchy
(building codes, technical specs, etc.), we'd rather emit one chunk per
section than one chunk per page — sections are the natural unit a reader
cites and the right granularity for embedding-driven retrieval.

The detection pass is intentionally deterministic and regex-based. The
triage's ``hierarchy_kind`` picks which pattern to apply:

* ``"section_codes"`` — alphanumeric section identifiers (``R311.7.5.1``,
  ``A.3.2.1``, ``5.4.1.2``) followed by a title.
* ``"chapter_heading"`` — prose chapter headings (``Chapter 5``,
  ``Section 5.4``).

A future enhancement can call Nova Lite over the TOC pages from triage and
intersect the LLM's list of (section_id, title) pairs with the regex output
to repair anything the regex missed. For first ship we trust the regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from document_ingestion_lambda.file_type_processors.pdf import (
    PdfChunkArtifact,
    PdfPageText,
    PdfTextExtractionResult,
    build_bedrock_sidecar_metadata,
    build_chunk_markdown,
    estimate_token_count,
    join_s3_key,
    split_text_by_estimated_tokens,
)


# ---------------------------------------------------------------------------
# Heading patterns
# ---------------------------------------------------------------------------

# Numeric / alphanumeric section codes. Examples:
#   "R311.7.5.1 Risers"
#   "5.4.1.2 Reference Designs"
#   "A.3 Service Conditions"
# We accept an optional 1-3 letter prefix (R, IBC, A, etc.) followed by digit
# components separated by dots, a single space, then the title text.
_SECTION_CODE_RE = re.compile(
    r"^\s*(?P<id>(?:[A-Z]{1,3})?\d+(?:\.\d+)*)\s+(?P<title>\S.+?)\s*$",
    re.MULTILINE,
)

# Prose chapter / section headings. Examples:
#   "CHAPTER 5 FOUNDATIONS"
#   "Chapter 5 — Foundations"
#   "Section 5.4 General"
_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?P<kind>CHAPTER|Chapter|SECTION|Section)\s+"
    r"(?P<id>[A-Z\d]+(?:\.\d+)*)"
    r"(?:\s*[-—:]\s*|\s+)"
    r"(?P<title>\S.+?)\s*$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class SectionBoundary:
    """A heading found in the joined per-page text.

    ``start_offset`` / ``end_offset`` index into ``JoinedText.text``. The
    chunker uses these to slice the section body without paying for another
    re-scan of the source pages.
    """

    section_id: str
    title: str
    chapter: str | None
    heading_path: list[str] = field(default_factory=list)
    start_page: int = 1
    end_page: int = 1
    start_offset: int = 0
    end_offset: int = 0


@dataclass
class SectionDetectionConfig:
    """Tuning knobs for ``detect_section_boundaries``."""

    hierarchy_kind: str = "section_codes"  # from PdfTriageResult.hierarchy_kind
    min_section_chars: int = 40            # drop "section" entries that have less than this
    min_boundaries: int = 5                # below this, fall back to page chunking
    embedding_chunk_token_limit: int = 8192
    multi_part_overlap: float = 0.0        # section parts don't need overlap
    # Pages flagged as TOC by triage are excluded from boundary detection
    # so we don't index TOC entries (with dot-leader noise) as if they were
    # real sections. Body content for those sections lives later in the doc.
    skip_pages: Sequence[int] = field(default_factory=tuple)
    # Real chapter/section headings are short — typically 1-7 words.
    # Sentence fragments that happen to match the regex (e.g. "Chapter 4
    # provides the requirements for the design and construction of one-
    # and two-family dwellings") are long. Reject anything past this cap.
    max_title_words: int = 8


# ---------------------------------------------------------------------------
# Public API: detection
# ---------------------------------------------------------------------------


@dataclass
class _JoinedText:
    """Joined text of all pages + a per-character page-number index."""

    text: str
    char_to_page: list[int]  # parallel array, char_to_page[i] = 1-indexed page


def _join_pages(pages: Sequence[PdfPageText]) -> _JoinedText:
    """Concatenate pages into a single string and remember which char came from where."""
    parts: list[str] = []
    char_to_page: list[int] = []
    for page in pages:
        body = page.text
        if not body:
            continue
        parts.append(body)
        char_to_page.extend([page.page_number] * len(body))
        # Insert a newline separator between pages; track the page number for the
        # newline so we don't accidentally attribute it to the next page.
        parts.append("\n\n")
        char_to_page.extend([page.page_number, page.page_number])
    return _JoinedText(text="".join(parts), char_to_page=char_to_page)


def _heading_path_for(section_id: str) -> list[str]:
    """Decompose "R311.7.5.1" → ["R311", "R311.7", "R311.7.5", "R311.7.5.1"]."""
    # Split into the alphabetic prefix (optional) and the numeric stem.
    match = re.match(r"^([A-Z]{1,3})?(\d+(?:\.\d+)*)$", section_id)
    if not match:
        return [section_id]
    prefix = match.group(1) or ""
    numeric = match.group(2)
    parts = numeric.split(".")
    path: list[str] = []
    for i in range(1, len(parts) + 1):
        path.append(f"{prefix}{'.'.join(parts[:i])}")
    return path


def _chapter_for(section_id: str) -> str | None:
    """Top-level chapter id: the first component of the heading path.

    "R311.7.5.1" → "R311". "5.4.1" → "5". Returns None when we can't decompose.
    """
    path = _heading_path_for(section_id)
    return path[0] if path else None


def detect_section_boundaries(
    pages: Sequence[PdfPageText],
    *,
    config: SectionDetectionConfig,
) -> tuple[list[SectionBoundary], _JoinedText]:
    """Locate section headings in the joined per-page text.

    Pages listed in ``config.skip_pages`` (typically the triage-detected
    TOC pages) are removed before joining, so TOC entries like
    ``R401 General . . . . . . . . . . 4-1`` don't get matched as
    section bodies.

    Returns ``(boundaries, joined_text)``. The joined_text is reused by
    the chunk builder so callers don't recompute it.
    """
    skip_pages = set(config.skip_pages or ())
    filtered_pages = [p for p in pages if p.page_number not in skip_pages]
    joined = _join_pages(filtered_pages)
    if not joined.text.strip():
        return [], joined

    pattern = _select_pattern(config.hierarchy_kind)
    if pattern is None:
        return [], joined

    raw_matches: list[tuple[int, str, str]] = []  # (start_offset, section_id, title)
    for match in pattern.finditer(joined.text):
        section_id = match.group("id")
        title = match.group("title").strip()
        if not section_id or not title:
            continue
        # Belt-and-suspenders: even with TOC pages skipped, individual
        # dot-leader lines can survive on body pages too (e.g. running
        # cross-references). Reject titles that are mostly dots.
        if _looks_like_toc_entry(title):
            continue
        # Length gate: real headings are short. Sentence-style false
        # positives are long ("Chapter 4 provides the requirements for...").
        if _title_word_count(title) > config.max_title_words:
            continue
        raw_matches.append((match.start(), section_id, title))

    if not raw_matches:
        return [], joined

    boundaries: list[SectionBoundary] = []
    for index, (start_offset, section_id, title) in enumerate(raw_matches):
        end_offset = (
            raw_matches[index + 1][0]
            if index + 1 < len(raw_matches)
            else len(joined.text)
        )
        start_page = joined.char_to_page[start_offset] if start_offset < len(joined.char_to_page) else 1
        last_index = max(end_offset - 1, start_offset)
        end_page = (
            joined.char_to_page[last_index]
            if last_index < len(joined.char_to_page)
            else start_page
        )
        boundaries.append(
            SectionBoundary(
                section_id=section_id,
                title=title,
                chapter=_chapter_for(section_id),
                heading_path=_heading_path_for(section_id),
                start_page=start_page,
                end_page=end_page,
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )

    # Drop tiny "sections" that turned out to be just headings with no body —
    # those are usually false positives (e.g. a number-prefixed sentence inside
    # a paragraph rather than a real heading).
    filtered = [
        b
        for b in boundaries
        if (b.end_offset - b.start_offset) >= config.min_section_chars
    ]
    return filtered, joined


def _title_word_count(title: str) -> int:
    """Count whitespace-separated tokens, ignoring trailing punctuation noise."""
    return len([w for w in re.split(r"\s+", title.strip()) if w])


def _looks_like_toc_entry(title: str) -> bool:
    """Detect dot-leader TOC entries like 'FOUNDATIONS . . . . . . 4-1'.

    Any title where ≥30% of the visible characters are dots-or-spaces
    (the leader pattern) is treated as a TOC artifact and rejected. Real
    section titles use prose, not dot-leaders.
    """
    if not title:
        return False
    stripped = title.strip()
    if not stripped:
        return False
    leader_chars = sum(1 for ch in stripped if ch in (".", " "))
    return leader_chars / len(stripped) >= 0.30


def _select_pattern(hierarchy_kind: str) -> re.Pattern[str] | None:
    if hierarchy_kind == "section_codes":
        return _SECTION_CODE_RE
    if hierarchy_kind == "chapter_heading":
        return _CHAPTER_HEADING_RE
    return None


# ---------------------------------------------------------------------------
# Public API: chunk artifact construction
# ---------------------------------------------------------------------------


def _build_section_chunk_id(document_id: str, section_id: str, part: int, total_parts: int) -> str:
    base = f"{document_id}_section_{_slug(section_id)}"
    if total_parts > 1:
        return f"{base}_part_{part:03d}"
    return base


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "section"


def build_section_chunk_artifacts(
    *,
    text_extraction: PdfTextExtractionResult,
    boundaries: Sequence[SectionBoundary],
    joined: _JoinedText,
    document_metadata: dict[str, Any],
    destination_bucket: str,
    index_prefix: str,
    embedding_chunk_token_limit: int,
    embedding_chunk_char_limit: int | None = None,
) -> list[PdfChunkArtifact]:
    """Emit one chunk per section, splitting into parts when a section is too big."""
    document_id = document_metadata["document_id"]
    artifacts: list[PdfChunkArtifact] = []
    chunk_index = 0

    for boundary in boundaries:
        section_text = joined.text[boundary.start_offset : boundary.end_offset].strip()
        if not section_text:
            continue

        # Split the section body into parts that fit the embedding budget.
        parts = split_text_by_estimated_tokens(
            section_text,
            embedding_chunk_token_limit,
            overlap_fraction=0.0,  # sections shouldn't overlap; the heading text appears once
            max_chars=embedding_chunk_char_limit,
        )
        part_count = max(len(parts), 1)

        # Recover per-part page ranges by walking char_to_page in-step with the slices.
        running_offset = boundary.start_offset
        for part_index, part_text in enumerate(parts, start=1):
            chunk_index += 1
            length = len(part_text)
            start_page_for_part = (
                joined.char_to_page[running_offset]
                if running_offset < len(joined.char_to_page)
                else boundary.start_page
            )
            end_idx = min(running_offset + length - 1, len(joined.char_to_page) - 1)
            end_page_for_part = (
                joined.char_to_page[end_idx]
                if end_idx >= 0
                else boundary.end_page
            )
            running_offset += length

            chunk_id = _build_section_chunk_id(
                document_id, boundary.section_id, part_index, part_count
            )
            key = join_s3_key(index_prefix, document_id, "sections", f"{chunk_id}.md")
            title = (
                f"{boundary.section_id} {boundary.title}"
                if part_count == 1
                else f"{boundary.section_id} {boundary.title} (part {part_index})"
            )
            metadata = {
                "metadata_schema_version": document_metadata["metadata_schema_version"],
                "record_type": "chunk",
                "document_id": document_id,
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
                "chapter": boundary.chapter,
                "section": boundary.section_id,
                "heading_path": "/".join(boundary.heading_path),
                "page_start": start_page_for_part,
                "page_end": end_page_for_part,
                "content_type": "text/markdown",
                "token_count": estimate_token_count(part_text),
                "char_count": len(part_text),
                "access_tier": document_metadata.get("access_tier", "restricted"),
                "custom_metadata": {
                    "part_index": part_index,
                    "part_count": part_count,
                    "extraction_status": text_extraction.extraction_status,
                    "boundary_start_offset": boundary.start_offset,
                    "boundary_end_offset": boundary.end_offset,
                },
                "processing": {
                    "strategy": "pdf_section_text",
                    "configured_embedding_chunk_token_limit": embedding_chunk_token_limit,
                    "configured_embedding_chunk_char_limit": embedding_chunk_char_limit,
                },
            }
            artifacts.append(
                PdfChunkArtifact(
                    key=key,
                    sidecar_key=f"{key}.metadata.json",
                    markdown=build_chunk_markdown(metadata, part_text),
                    sidecar_metadata=build_bedrock_sidecar_metadata(metadata),
                    catalog_metadata=metadata,
                )
            )

    return artifacts


def should_use_section_chunking(
    triage: Any,
    boundaries: Sequence[SectionBoundary],
    *,
    min_boundaries: int,
    min_confidence: float,
) -> bool:
    """Decide whether to ship section chunks for this document.

    The triage signal alone isn't enough — we also require the regex to have
    actually found enough headings. If either gate fails we fall back to the
    existing page chunker.
    """
    if triage is None:
        return False
    if getattr(triage, "triage_status", None) != "completed":
        return False
    if not getattr(triage, "has_hierarchy", False):
        return False
    confidence = getattr(triage, "hierarchy_confidence", None) or 0.0
    if confidence < min_confidence:
        return False
    return len(boundaries) >= min_boundaries


__all__ = [
    "SectionBoundary",
    "SectionDetectionConfig",
    "build_section_chunk_artifacts",
    "detect_section_boundaries",
    "should_use_section_chunking",
]

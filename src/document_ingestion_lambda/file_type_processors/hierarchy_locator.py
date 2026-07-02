"""Anchor-based hierarchy node locator.

Converts Sonnet-emitted hierarchy nodes (label + body_anchor + page_start)
into exact ``(char_start, char_end)`` positions in the joined page text.

The algorithm:
1. Find ``body_anchor`` in the joined text (scoped to page_start ± 1).
2. Walk backward from the anchor to the nearest line-start matching the label.
3. Assign ``char_end`` from the next sibling's ``char_start``.
4. Propagate parent ``char_end`` to span the last descendant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from document_ingestion_lambda.file_type_processors.hierarchy import (
    DocumentHierarchy,
    HierarchyNode,
    validate_hierarchy,
)
from document_ingestion_lambda.file_type_processors.hierarchy_sonnet import SonnetHierarchyNode
from document_ingestion_lambda.file_type_processors.pdf import JoinedPageText, estimate_token_count
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class LocatorResult:
    """Result of locating one hierarchy node in the joined text."""

    char_start: int | None
    tier: str  # anchored | anchored_full_doc | anchor_not_found | anchor_orphan


# Soft-hyphen line-break pattern. pypdf emits text like ``Resi-\ndential`` when
# a word was wrapped across two lines in the original PDF (a "soft hyphen").
# Sonnet reads the rendered page where the visual line break has been resolved
# and emits ``Resi-dential`` (hyphen preserved, no whitespace). To align them,
# the haystack normalization must drop ONLY the whitespace between the hyphen
# and the continuation letter — leaving the hyphen in place.
#
# The pattern requires a newline in the whitespace run. This distinguishes
# soft hyphens (which always have ``\n``) from genuine "suspended" hyphens
# like ``One- and Two-family`` where the space after the hyphen is meaningful
# and must be preserved.
_SOFT_HYPHEN_RE = re.compile(r"(?<=[A-Za-z])-[ \t]*\n[ \t\n]*(?=[a-z])")


# Unicode punctuation that PDFs use but models often emit as ASCII variants.
# Sonnet renders curly quotes and en/em dashes as their ASCII equivalents,
# while pypdf preserves the Unicode characters from the PDF source. Normalize
# both haystack and anchor by mapping these to ASCII before comparing.
_PUNCTUATION_TRANSLATE = str.maketrans({
    "‘": "'",   # LEFT SINGLE QUOTATION MARK
    "’": "'",   # RIGHT SINGLE QUOTATION MARK
    "‚": "'",   # SINGLE LOW-9 QUOTATION MARK
    "“": '"',   # LEFT DOUBLE QUOTATION MARK
    "”": '"',   # RIGHT DOUBLE QUOTATION MARK
    "„": '"',   # DOUBLE LOW-9 QUOTATION MARK
    "–": "-",   # EN DASH
    "—": "-",   # EM DASH
    "−": "-",   # MINUS SIGN
    " ": " ",   # NON-BREAKING SPACE
})


def _normalize_for_anchor_match(
    text: str,
    *,
    keep_soft_hyphen: bool,
) -> tuple[str, list[int]]:
    """Build a normalized form of ``text`` suitable for fuzzy anchor matching.

    Sonnet's anchor output is inconsistent about soft hyphens. For pypdf
    text like ``stan-\\ndards``, Sonnet may emit either ``stan-dards`` (keeping
    the hyphen, dropping only the newline) or ``standards`` (dropping both).
    Callers should try both variants.

    Normalization always:
    - Drops whitespace after a soft-hyphen line break.
    - Collapses runs of whitespace (newlines, tabs, spaces) to single spaces.

    When ``keep_soft_hyphen=True``: keeps the hyphen at soft-hyphen positions.
    When ``keep_soft_hyphen=False``: drops the hyphen at soft-hyphen positions
    (joining the two halves of the word).

    Returns ``(normalized_text, orig_positions)`` where ``orig_positions[i]``
    is the char offset in the original ``text`` corresponding to the
    ``i``-th character of ``normalized_text``.
    """
    if not text:
        return "", []

    # Map Unicode punctuation to ASCII variants (preserves length, so offsets
    # in the translated string still correspond 1:1 to the original).
    text = text.translate(_PUNCTUATION_TRANSLATE)

    soft_hyphen_drops: set[int] = set()
    for match in _SOFT_HYPHEN_RE.finditer(text):
        # match.start() = position of '-'.
        if keep_soft_hyphen:
            # Drop the whitespace after the hyphen, keep the hyphen.
            start_drop = match.start() + 1
        else:
            # Drop the hyphen AND the whitespace.
            start_drop = match.start()
        for drop_pos in range(start_drop, match.end()):
            soft_hyphen_drops.add(drop_pos)

    orig_positions: list[int] = []
    norm_chars: list[str] = []
    in_ws = False
    for i, ch in enumerate(text):
        if i in soft_hyphen_drops:
            in_ws = False
            continue
        if ch in " \t\n\r\f\v":
            if not in_ws and norm_chars:
                norm_chars.append(" ")
                orig_positions.append(i)
            in_ws = True
        else:
            norm_chars.append(ch)
            orig_positions.append(i)
            in_ws = False

    return "".join(norm_chars).strip(), orig_positions


def _normalize_anchor(anchor: str) -> str:
    """Normalize an anchor string for matching (Unicode + whitespace collapse).

    Soft hyphens in the anchor itself are left as-is (rare in practice);
    the haystack-side normalization handles both Sonnet behaviors.
    """
    return re.sub(r"\s+", " ", anchor.translate(_PUNCTUATION_TRANSLATE)).strip()


def _find_anchor_normalized(haystack: str, anchor: str) -> int:
    """Find anchor in haystack using normalized comparison.

    Tries two normalizations of the haystack — keeping then dropping the
    soft-hyphen hyphen — to handle Sonnet's inconsistent treatment of PDF
    column-wrap hyphens. Returns the char offset in the ORIGINAL haystack
    where the match begins, or -1 if not found.
    """
    anchor_norm = _normalize_anchor(anchor)
    if not anchor_norm:
        return -1

    for keep in (True, False):
        haystack_norm, orig_positions = _normalize_for_anchor_match(
            haystack, keep_soft_hyphen=keep
        )
        pos = haystack_norm.find(anchor_norm)
        if pos == -1:
            continue
        if pos < len(orig_positions):
            return orig_positions[pos]
    return -1


def locate_heading(
    *,
    label: str,
    body_anchor: str,
    page_start: int | None,
    joined_text: str,
    page_offsets: dict[int, int],
) -> LocatorResult:
    """Find char_start of a heading using its body_anchor.

    Sonnet's body_anchor consistently starts at or near the heading line itself
    (not in body text following the heading), so we simply locate the anchor
    and use that position as char_start. Handles:

    - Whitespace-normalized matching for pypdf's column-wrap newlines.
    - Soft-hyphen normalization (``Resi-\\ndential`` → ``Residential``).
    - Case-insensitive label-presence sanity check on the anchor text.

    Returns a LocatorResult with the char offset and a confidence tier:

    - ``anchored`` — anchor matched within ``page_start ± 1`` window.
    - ``anchored_full_doc`` — anchor found outside the expected page window.
    - ``anchor_label_mismatch`` — anchor found but label doesn't appear near
      its start; the char_start is still returned but flagged for monitoring.
    - ``anchor_not_found`` — anchor text not in joined text at all.
    """
    if not body_anchor:
        return LocatorResult(char_start=None, tier="anchor_not_found")

    page_start = page_start or 1
    lo = page_offsets.get(max(1, page_start - 1), 0)
    hi = page_offsets.get(page_start + 2, len(joined_text))
    haystack = joined_text[lo:hi]

    # Tier 1: exact match within page window.
    anchor_pos = haystack.find(body_anchor)
    if anchor_pos != -1:
        anchor_abs = lo + anchor_pos
        tier = "anchored"
    else:
        # Tier 2: normalized match within page window.
        norm_pos = _find_anchor_normalized(haystack, body_anchor)
        if norm_pos != -1:
            anchor_abs = lo + norm_pos
            tier = "anchored"
        else:
            # Tier 3: normalized match anywhere in the document.
            full_pos = _find_anchor_normalized(joined_text, body_anchor)
            if full_pos != -1:
                anchor_abs = full_pos
                tier = "anchored_full_doc"
            else:
                return LocatorResult(char_start=None, tier="anchor_not_found")

    # Sanity check: the label should appear near the anchor's start (typically
    # at position 0). Search the first 80 chars of the anchor's actual span in
    # joined_text. Case-insensitive to tolerate Sonnet emitting "Chapter 1"
    # for a document heading that reads "CHAPTER 1".
    label_check_span = joined_text[anchor_abs : anchor_abs + 80]
    if label.lower() not in label_check_span.lower():
        return LocatorResult(char_start=anchor_abs, tier="anchor_label_mismatch")

    return LocatorResult(char_start=anchor_abs, tier=tier)


def build_hierarchy_from_sonnet_nodes(
    *,
    sonnet_nodes: list[SonnetHierarchyNode],
    joined: JoinedPageText,
    document_id: str,
    source_location: str,
    profile: str | None = None,
    heading_pattern: str | None = None,
) -> DocumentHierarchy | None:
    """Locate all Sonnet nodes and assemble a DocumentHierarchy.

    Returns None if too few nodes are locatable (< 50% success rate).
    """
    located: list[tuple[SonnetHierarchyNode, int]] = []
    tier_counts: dict[str, int] = {}

    for node in sonnet_nodes:
        result = locate_heading(
            label=node.label,
            body_anchor=node.body_anchor,
            page_start=node.page_start,
            joined_text=joined.text,
            page_offsets=joined.page_offsets,
        )
        tier_counts[result.tier] = tier_counts.get(result.tier, 0) + 1
        if result.char_start is not None:
            located.append((node, result.char_start))

    total = len(sonnet_nodes)
    located_count = len(located)
    log.info(
        "Hierarchy locator: total=%s located=%s tiers=%s",
        total,
        located_count,
        tier_counts,
    )

    if total > 0 and located_count / total < 0.5:
        log.warning(
            "Hierarchy locator success rate below 50%% (%s/%s); returning None for fallback",
            located_count,
            total,
        )
        return None

    # Deduplicate by label (keep first by char_start).
    seen_labels: set[str] = set()
    unique: list[tuple[SonnetHierarchyNode, int]] = []
    for node, char_start in sorted(located, key=lambda x: x[1]):
        if node.label in seen_labels:
            continue
        seen_labels.add(node.label)
        unique.append((node, char_start))

    # Lookup table: label → parent_label. Used to walk the full ancestor chain
    # when building heading_path. Without this, depth gets stuck at 2.
    parent_by_label: dict[str, str | None] = {
        snode.label: snode.parent_label for snode, _ in unique
    }

    # Build HierarchyNodes with char_start populated; char_end from next sibling.
    hierarchy_nodes: list[HierarchyNode] = []
    for i, (snode, char_start) in enumerate(unique):
        next_start = len(joined.text)
        for _, later_start in unique[i + 1:]:
            if later_start > char_start:
                next_start = later_start
                break
        text_slice = joined.text[char_start:next_start]
        heading_path = _build_heading_path(snode, parent_by_label)

        hierarchy_nodes.append(
            HierarchyNode(
                hierarchy_node_id=snode.label,
                label=snode.label,
                title=snode.title,
                node_type=snode.node_type,
                parent_id=snode.parent_label if snode.parent_label in seen_labels else None,
                children=[],
                heading_path=heading_path,
                page_start=snode.page_start,
                page_end=snode.page_end,
                char_start=char_start,
                char_end=next_start,
                token_estimate=estimate_token_count(text_slice),
                source="sonnet_located",
                evidence=[{"source": "body_anchor", "anchor": snode.body_anchor[:80]}],
            )
        )

    # Build parent→children relationships.
    by_id = {n.hierarchy_node_id: n for n in hierarchy_nodes}
    for node in hierarchy_nodes:
        if node.parent_id and node.parent_id in by_id:
            by_id[node.parent_id].children.append(node.hierarchy_node_id)

    # Propagate parent char_end to span last descendant.
    for node in sorted(hierarchy_nodes, key=lambda n: len(n.heading_path), reverse=True):
        for child_id in node.children:
            child = by_id.get(child_id)
            if child and child.char_end is not None:
                node.char_end = max(node.char_end or child.char_end, child.char_end)
            if child and child.page_end is not None:
                node.page_end = max(node.page_end or child.page_end, child.page_end)
        if node.char_start is not None and node.char_end is not None:
            node.token_estimate = estimate_token_count(joined.text[node.char_start:node.char_end])

    root_nodes = [n.hierarchy_node_id for n in hierarchy_nodes if not n.parent_id]

    hierarchy = DocumentHierarchy(
        document_id=document_id,
        schema_version="1.0",
        profile=profile or "unknown",
        heading_pattern_type=heading_pattern or "unknown",
        source_location=source_location,
        root_nodes=root_nodes,
        nodes=hierarchy_nodes,
        generation={
            "source": "sonnet_located",
            "status": "complete",
            "total_sonnet_nodes": total,
            "located_nodes": located_count,
            "tier_counts": tier_counts,
        },
    )

    validation = validate_hierarchy(hierarchy)
    hierarchy.generation["validation"] = validation
    if not validation["valid"]:
        log.warning("Sonnet hierarchy validation errors: %s", validation["errors"])

    return hierarchy


def _build_heading_path(
    node: SonnetHierarchyNode,
    parent_by_label: dict[str, str | None],
) -> list[str]:
    """Reconstruct the heading path by walking up the full parent chain.

    Uses a ``label → parent_label`` lookup so we can follow the chain from a
    leaf node all the way to a root. Cycle-safe via a ``visited`` set.
    """
    path = [node.label]
    visited: set[str] = {node.label}
    current_parent = node.parent_label
    while (
        current_parent
        and current_parent in parent_by_label
        and current_parent not in visited
    ):
        path.insert(0, current_parent)
        visited.add(current_parent)
        current_parent = parent_by_label.get(current_parent)
    return path

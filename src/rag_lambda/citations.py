"""Citation normalization helpers for RAG answers.

The answer model emits two citation forms:

  1. ``<cite source="N">EXACT QUOTE</cite>`` — preferred form, carries a
     verbatim quote from the chunk. The quote is verified against the chunk
     text by case- and whitespace-normalized substring match. Verified quotes
     are attached to the source's ``cited_quotes`` list. Unverified quotes
     are dropped (the citation marker survives, the quote does not).
  2. ``<[N]>`` — fallback form for sentences cited without a quote. No
     quote is attached; the marker is renumbered to ``[N]`` like before.

In both cases the marker becomes a 1-based, first-use-ordered ``[N]`` and
only the cited sources flow through to the response.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


CITE_BLOCK_PATTERN = re.compile(
    r'<cite\s+source="(\d+)"\s*>(.*?)</cite>',
    re.DOTALL,
)
SENTINEL_CITATION_PATTERN = re.compile(r"<\[(\d+)\]>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
# PDF text extraction often leaves soft-wrap hyphens, e.g. "capa- ble" or
# "capa-\nble" where the original word is "capable". Strip these before
# comparison so the LLM's verbatim quote can still verify against the chunk.
_SOFT_WRAP_HYPHEN_PATTERN = re.compile(r"(\w)[-‐‑­]\s+(\w)")


@dataclass(frozen=True)
class CitationNormalizationResult:
    """Normalized answer text and cited source subset."""

    answer: str
    sources: List[Dict[str, Any]]
    citation_map: Dict[int, int]
    invalid_citations: List[int]
    dropped_quotes: List[Dict[str, Any]] = field(default_factory=list)


def _source_number(source: Dict[str, Any], fallback: int) -> int:
    raw_number = source.get("retrieval_source_number", fallback)
    try:
        return int(raw_number)
    except (TypeError, ValueError):
        return fallback


def _compact_removed_citation_spacing(answer: str) -> str:
    answer = re.sub(r"\s+([.,;:!?])", r"\1", answer)
    answer = re.sub(r"[ \t]{2,}", " ", answer)
    return answer


def _normalize_for_match(text: str) -> str:
    """Collapse whitespace, repair PDF soft-wrap hyphens, lowercase."""
    rejoined = _SOFT_WRAP_HYPHEN_PATTERN.sub(r"\1\2", text)
    return _WHITESPACE_PATTERN.sub(" ", rejoined).strip().lower()


def _quote_is_in_chunk(quote: str, chunk: str) -> bool:
    if not quote or not chunk:
        return False
    return _normalize_for_match(quote) in _normalize_for_match(chunk)


def normalize_answer_citations(
    answer: str,
    sources: List[Dict[str, Any]],
) -> CitationNormalizationResult:
    """Extract ``<cite>``-block quotes, renumber citations, and filter sources.

    Two-pass scan:
      1. ``<cite source="N">QUOTE</cite>`` — verifies quote vs source chunk.
         Verified quotes become ``"QUOTE" [N]`` inline and attach to the
         source's ``cited_quotes``. Unverified quotes are dropped — the
         ``[N]`` badge survives but the quote does not.
      2. Bare ``<[N]>`` — renumbers to ``[N]`` like before, no quote attached.

    Citation numbers are reassigned 1..K by first use across both passes.
    Sources that are never cited are dropped from the response.
    """
    sources_by_number = {
        _source_number(source, fallback=index): source
        for index, source in enumerate(sources, start=1)
    }
    citation_map: Dict[int, int] = {}
    normalized_sources_by_retrieval: Dict[int, Dict[str, Any]] = {}
    invalid_citations: List[int] = []
    dropped_quotes: List[Dict[str, Any]] = []

    def _register_citation(retrieval_number: int) -> Tuple[int, Dict[str, Any]]:
        """Ensure the source is recorded and return (renumbered, source_dict)."""
        source = sources_by_number[retrieval_number]
        if retrieval_number not in citation_map:
            citation_number = len(citation_map) + 1
            citation_map[retrieval_number] = citation_number
            normalized_sources_by_retrieval[retrieval_number] = {
                **source,
                "citation_number": citation_number,
                "cited_quotes": [],
                "quote_verified": True,
            }
        return citation_map[retrieval_number], normalized_sources_by_retrieval[retrieval_number]

    def replace_cite_block(match: re.Match[str]) -> str:
        retrieval_number = int(match.group(1))
        quote = (match.group(2) or "").strip()

        source = sources_by_number.get(retrieval_number)
        if source is None:
            if retrieval_number not in invalid_citations:
                invalid_citations.append(retrieval_number)
            # Strip the wrapper entirely — no marker, no quote, no source.
            return ""

        renumbered, normalized_source = _register_citation(retrieval_number)

        if quote and _quote_is_in_chunk(quote, source.get("chunk", "")):
            normalized_source["cited_quotes"].append(quote)
            return f'"{quote}" [{renumbered}]'

        # Unverified or empty quote: keep the marker, drop the quote text.
        if quote:
            dropped_quotes.append(
                {
                    "source_number": retrieval_number,
                    "quote": quote,
                    "reason": "not_in_chunk",
                }
            )
        return f"[{renumbered}]"

    def replace_bare_marker(match: re.Match[str]) -> str:
        retrieval_number = int(match.group(1))
        source = sources_by_number.get(retrieval_number)
        if source is None:
            if retrieval_number not in invalid_citations:
                invalid_citations.append(retrieval_number)
            return ""
        renumbered, _ = _register_citation(retrieval_number)
        return f"[{renumbered}]"

    # Pass 1: cite-blocks (may overlap nothing else because the regex is non-greedy).
    normalized_answer = CITE_BLOCK_PATTERN.sub(replace_cite_block, answer)
    # Pass 2: remaining bare <[N]> markers.
    normalized_answer = SENTINEL_CITATION_PATTERN.sub(replace_bare_marker, normalized_answer)
    normalized_answer = _compact_removed_citation_spacing(normalized_answer)

    # Preserve first-cited-by ordering for the source list.
    ordered_retrieval_numbers = sorted(
        normalized_sources_by_retrieval,
        key=lambda n: citation_map[n],
    )
    normalized_sources = [
        normalized_sources_by_retrieval[n] for n in ordered_retrieval_numbers
    ]

    return CitationNormalizationResult(
        answer=normalized_answer,
        sources=normalized_sources,
        citation_map=citation_map,
        invalid_citations=invalid_citations,
        dropped_quotes=dropped_quotes,
    )

"""Citation normalization helpers for RAG answers."""

import re
from dataclasses import dataclass
from typing import Any, Dict, List


SENTINEL_CITATION_PATTERN = re.compile(r"<\[(\d+)\]>")


@dataclass(frozen=True)
class CitationNormalizationResult:
    """Normalized answer text and cited source subset."""

    answer: str
    sources: List[Dict[str, Any]]
    citation_map: Dict[int, int]
    invalid_citations: List[int]


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


def normalize_answer_citations(
    answer: str,
    sources: List[Dict[str, Any]],
) -> CitationNormalizationResult:
    """Renumber sentinel citations and filter sources to cited sources only.

    The answer model emits internal citation markers in the form ``<[N]>``, where
    ``N`` is the retrieval source number shown in the prompt. This helper maps
    those retrieval numbers to user-facing citation numbers by first use, then
    rewrites valid markers to ``[1]``/``[2]`` and drops uncited sources.
    """
    sources_by_number = {
        _source_number(source, fallback=index): source
        for index, source in enumerate(sources, start=1)
    }
    citation_map: Dict[int, int] = {}
    normalized_sources: List[Dict[str, Any]] = []
    invalid_citations: List[int] = []

    def replace_citation(match: re.Match[str]) -> str:
        retrieval_number = int(match.group(1))
        source = sources_by_number.get(retrieval_number)
        if source is None:
            if retrieval_number not in invalid_citations:
                invalid_citations.append(retrieval_number)
            return ""

        if retrieval_number not in citation_map:
            citation_number = len(citation_map) + 1
            citation_map[retrieval_number] = citation_number
            normalized_sources.append({**source, "citation_number": citation_number})

        return f"[{citation_map[retrieval_number]}]"

    normalized_answer = SENTINEL_CITATION_PATTERN.sub(replace_citation, answer)
    normalized_answer = _compact_removed_citation_spacing(normalized_answer)

    return CitationNormalizationResult(
        answer=normalized_answer,
        sources=normalized_sources,
        citation_map=citation_map,
        invalid_citations=invalid_citations,
    )

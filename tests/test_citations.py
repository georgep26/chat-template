"""Tests for deterministic citation normalization."""

from src.rag_lambda.citations import normalize_answer_citations


def _sources():
    return [
        {"retrieval_source_number": 1, "document_id": "doc-1"},
        {"retrieval_source_number": 3, "document_id": "doc-3"},
        {"retrieval_source_number": 9, "document_id": "doc-9"},
    ]


def test_normalize_answer_citations_renumbers_by_first_use():
    """Display citation numbers follow first use, not retrieval source number."""
    result = normalize_answer_citations("A <[9]>. B <[3]>.", _sources())

    assert result.answer == "A [1]. B [2]."
    assert result.citation_map == {9: 1, 3: 2}
    assert [source["document_id"] for source in result.sources] == ["doc-9", "doc-3"]
    assert [source["citation_number"] for source in result.sources] == [1, 2]


def test_normalize_answer_citations_reuses_display_number_for_repeated_source():
    """Repeated citations to the same source keep the same display number."""
    result = normalize_answer_citations("A <[3]>. B <[3]>.", _sources())

    assert result.answer == "A [1]. B [1]."
    assert result.citation_map == {3: 1}
    assert [source["document_id"] for source in result.sources] == ["doc-3"]


def test_normalize_answer_citations_handles_adjacent_multi_source_markers():
    """Adjacent source markers are normalized independently."""
    result = normalize_answer_citations("A <[9]><[3]>.", _sources())

    assert result.answer == "A [1][2]."
    assert result.citation_map == {9: 1, 3: 2}


def test_normalize_answer_citations_ignores_plain_bracket_text():
    """Plain bracket text is not interpreted as a citation marker."""
    result = normalize_answer_citations("See [1] and [2024], but source says A <[9]>.", _sources())

    assert result.answer == "See [1] and [2024], but source says A [1]."
    assert [source["document_id"] for source in result.sources] == ["doc-9"]


def test_normalize_answer_citations_removes_invalid_markers():
    """Invalid sentinel markers do not create returned source records."""
    result = normalize_answer_citations("A <[999]>.", _sources())

    assert result.answer == "A."
    assert result.sources == []
    assert result.invalid_citations == [999]


def test_normalize_answer_citations_returns_no_sources_without_citations():
    """Answers grounded in conversation history should not return retrieved sources."""
    result = normalize_answer_citations("Based on our prior chat, yes.", _sources())

    assert result.answer == "Based on our prior chat, yes."
    assert result.sources == []
    assert result.citation_map == {}

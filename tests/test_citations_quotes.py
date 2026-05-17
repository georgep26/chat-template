"""Tests for the cite-block quote extraction in normalize_answer_citations."""

from __future__ import annotations

from typing import Any, Dict, List

from src.rag_lambda.citations import normalize_answer_citations


def _source(
    *,
    number: int,
    document_id: str = "doc-1",
    chunk: str = "",
    source_type: str = "pdf",
    score: float = 0.9,
) -> Dict[str, Any]:
    return {
        "retrieval_source_number": number,
        "document_id": document_id,
        "source_type": source_type,
        "score": score,
        "chunk": chunk,
        "metadata": {},
    }


def test_single_cite_block_attaches_verbatim_quote():
    answer = (
        'The maximum riser height is 7 3/4 inches '
        '<cite source="1">Maximum riser height shall be 7 3/4 inches</cite>.'
    )
    sources: List[Dict[str, Any]] = [
        _source(
            number=1,
            chunk=(
                "R311.7.5.1 Risers. The riser height shall be measured vertically "
                "between leading edges of the adjacent treads. Maximum riser height "
                "shall be 7 3/4 inches (196 mm)."
            ),
        )
    ]

    result = normalize_answer_citations(answer, sources)

    assert (
        '"Maximum riser height shall be 7 3/4 inches" [1]' in result.answer
    )
    assert len(result.sources) == 1
    s = result.sources[0]
    assert s["citation_number"] == 1
    assert s["cited_quotes"] == ["Maximum riser height shall be 7 3/4 inches"]
    assert s["quote_verified"] is True
    assert result.dropped_quotes == []


def test_same_source_cited_twice_keeps_both_quotes_in_order():
    answer = (
        'First, <cite source="1">alpha rule applies</cite> for new builds. '
        'Second, <cite source="1">beta rule applies</cite> for remodels.'
    )
    sources = [_source(number=1, chunk="alpha rule applies; beta rule applies.")]

    result = normalize_answer_citations(answer, sources)

    assert len(result.sources) == 1
    assert result.sources[0]["cited_quotes"] == [
        "alpha rule applies",
        "beta rule applies",
    ]
    # Citation marker appears twice in the answer.
    assert result.answer.count("[1]") == 2


def test_mixed_cite_block_and_bare_marker():
    answer = (
        'Quoted: <cite source="1">verbatim text here</cite>. '
        'Bare-cited: claim<[2]>.'
    )
    sources = [
        _source(number=1, chunk="verbatim text here is in the chunk."),
        _source(number=2, document_id="doc-2", chunk="other content."),
    ]

    result = normalize_answer_citations(answer, sources)

    assert '"verbatim text here" [1]' in result.answer
    assert "[2]" in result.answer
    by_num = {s["citation_number"]: s for s in result.sources}
    assert by_num[1]["cited_quotes"] == ["verbatim text here"]
    assert by_num[2]["cited_quotes"] == []


def test_quote_with_whitespace_and_case_differences_still_matches():
    answer = (
        '<cite source="1">Maximum   riser height shall be 7 3/4 INCHES</cite>'
    )
    sources = [
        _source(
            number=1,
            chunk="...maximum riser height shall be 7 3/4 inches...",
        )
    ]

    result = normalize_answer_citations(answer, sources)

    assert result.sources[0]["cited_quotes"] == [
        "Maximum   riser height shall be 7 3/4 INCHES"
    ]
    assert result.dropped_quotes == []


def test_pdf_soft_wrap_hyphen_in_chunk_still_matches():
    """PDF line-break hyphens like 'capa- ble' should not block quote verification."""
    answer = (
        '<cite source="1">heating facilities capable of maintaining a room temperature'
        ' of not less than 68°F</cite>'
    )
    sources = [
        _source(
            number=1,
            chunk=(
                "every dwelling unit shall be provided with heating facilities capa-\n"
                "ble of maintaining a room temperature of not less than 68°F (20°C)"
            ),
        )
    ]

    result = normalize_answer_citations(answer, sources)

    assert result.dropped_quotes == []
    assert result.sources[0]["cited_quotes"] == [
        "heating facilities capable of maintaining a room temperature of not less than 68°F"
    ]


def test_pdf_soft_wrap_hyphen_with_space_after_dash_matches():
    """Some extractors leave 'capa- ble' (dash + space, no newline) — also a soft wrap."""
    answer = '<cite source="1">capable of maintaining</cite>'
    sources = [_source(number=1, chunk="...facilities capa- ble of maintaining a room...")]

    result = normalize_answer_citations(answer, sources)

    assert result.dropped_quotes == []
    assert result.sources[0]["cited_quotes"] == ["capable of maintaining"]


def test_real_hyphen_in_compound_word_still_verifies():
    """Genuine hyphenated compounds (no whitespace around the hyphen) must still match."""
    answer = '<cite source="1">a self-sealing fitting</cite>'
    sources = [_source(number=1, chunk="...must use a self-sealing fitting...")]

    result = normalize_answer_citations(answer, sources)

    assert result.dropped_quotes == []
    assert result.sources[0]["cited_quotes"] == ["a self-sealing fitting"]


def test_paraphrased_quote_is_dropped_but_marker_survives():
    """When the model paraphrases, the quote is discarded; the [N] badge stays."""
    answer = (
        'The maximum riser height is set by code '
        '<cite source="1">risers cannot exceed seven and three quarter inches</cite>.'
    )
    sources = [
        _source(
            number=1,
            chunk="Maximum riser height shall be 7 3/4 inches (196 mm).",
        )
    ]

    result = normalize_answer_citations(answer, sources)

    # Marker survives, quote does not.
    assert "[1]" in result.answer
    assert "risers cannot exceed" not in result.answer
    assert result.sources[0]["cited_quotes"] == []
    assert len(result.dropped_quotes) == 1
    assert result.dropped_quotes[0]["source_number"] == 1
    assert result.dropped_quotes[0]["reason"] == "not_in_chunk"


def test_source_with_only_dropped_quotes_still_ships():
    """The [N] badge is still in the answer, so the source must accompany it."""
    answer = '<cite source="1">paraphrased content</cite>'
    sources = [_source(number=1, chunk="completely unrelated text in the chunk.")]

    result = normalize_answer_citations(answer, sources)

    assert "[1]" in result.answer
    assert len(result.sources) == 1
    assert result.sources[0]["cited_quotes"] == []
    assert len(result.dropped_quotes) == 1


def test_unknown_source_number_recorded_and_stripped():
    answer = (
        'Real cite: <cite source="1">in chunk text</cite>. '
        'Bad cite: <cite source="9">whatever</cite>.'
    )
    sources = [_source(number=1, chunk="in chunk text exists here.")]

    result = normalize_answer_citations(answer, sources)

    assert "9" in [str(n) for n in result.invalid_citations]
    assert "whatever" not in result.answer
    # Only the known source ships.
    assert len(result.sources) == 1
    assert result.sources[0]["citation_number"] == 1


def test_renumbering_assigns_first_use_order():
    """Source 3 cited before source 1 → 3 becomes [1], 1 becomes [2]."""
    answer = (
        '<cite source="3">third source quote</cite> '
        'then <cite source="1">first source quote</cite>.'
    )
    sources = [
        _source(number=1, chunk="first source quote is here."),
        _source(number=3, chunk="third source quote is here."),
    ]

    result = normalize_answer_citations(answer, sources)

    # Renumbered by first use: 3 → 1, 1 → 2.
    by_doc = {s["citation_number"]: s for s in result.sources}
    assert by_doc[1]["cited_quotes"] == ["third source quote"]
    assert by_doc[2]["cited_quotes"] == ["first source quote"]
    assert '"third source quote" [1]' in result.answer
    assert '"first source quote" [2]' in result.answer


def test_no_citations_returns_clean_answer_and_no_sources():
    answer = "This answer has no citations at all."
    sources = [_source(number=1, chunk="unused content.")]

    result = normalize_answer_citations(answer, sources)

    assert result.answer == "This answer has no citations at all."
    assert result.sources == []
    assert result.citation_map == {}


def test_bare_marker_still_works_after_cite_pass():
    """Legacy <[N]> markers continue to function as before."""
    answer = "Claim<[1]>."
    sources = [_source(number=1, chunk="some chunk text.")]

    result = normalize_answer_citations(answer, sources)

    assert result.answer == "Claim[1]."
    assert len(result.sources) == 1
    assert result.sources[0]["cited_quotes"] == []

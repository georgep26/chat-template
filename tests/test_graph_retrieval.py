"""Tests for graph retrieval context formatting."""

from langchain_core.documents import Document

from src.rag_lambda.graph.retrieval import format_retrieved_context, format_source_metadata


def test_format_source_metadata_includes_all_available_fields():
    """All non-null metadata fields are formatted for answer context."""
    metadata = {
        "source": "s3://bucket/docs/example.pdf",
        "page_number": 4,
        "score": 0.82,
        "location": {"type": "S3", "uri": "s3://bucket/docs/example.pdf"},
        "empty": None,
    }

    formatted = format_source_metadata(metadata)

    assert "source: s3://bucket/docs/example.pdf" in formatted
    assert "page_number: 4" in formatted
    assert "score: 0.82" in formatted
    assert 'location: {"type": "S3", "uri": "s3://bucket/docs/example.pdf"}' in formatted
    assert "empty:" not in formatted


def test_format_retrieved_context_includes_metadata_and_chunk_text():
    """Retrieved context sent to the answer prompt includes source metadata."""
    docs = [
        Document(
            page_content="Duct systems distribute conditioned air.",
            metadata={
                "id": "doc-1",
                "source": "duct-guide.pdf",
                "page_number": 12,
                "score": 0.91,
            },
        )
    ]

    context = format_retrieved_context(docs)

    assert '<source number="1">' in context
    assert "source_number: 1" in context
    assert "<metadata>" in context
    assert "id: doc-1" in context
    assert "source: duct-guide.pdf" in context
    assert "page_number: 12" in context
    assert "score: 0.91" in context
    assert "<chunk>\nDuct systems distribute conditioned air.\n</chunk>" in context


def test_format_retrieved_context_handles_no_docs():
    """No-source answers get an explicit empty context marker."""
    assert format_retrieved_context([]) == "Relevant context:\nNo retrieved source chunks."

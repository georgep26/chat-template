"""Tests for graph retrieval context formatting."""

from langchain_core.documents import Document

from src.rag_lambda.graph.retrieval import format_retrieved_context, format_source_metadata, retrieve_node


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


def test_retrieve_node_preserves_retrieval_source_number_and_metadata(monkeypatch):
    """Sources keep the source number shown to the answer LLM."""
    docs = [
        Document(
            page_content="First chunk.",
            metadata={"id": "doc-1", "score": 0.9, "page_number": 1},
        ),
        Document(
            page_content="Second chunk.",
            metadata={"id": "doc-2", "score": 0.8, "page_number": 2},
        ),
    ]

    class FakeRetriever:
        def __init__(self, **kwargs):
            del kwargs

        def invoke(self, query):
            del query
            return docs

    monkeypatch.setattr("src.rag_lambda.graph.retrieval.AmazonKnowledgeBasesRetriever", FakeRetriever)

    state = {
        "messages": [],
        "retrieval_config": {"knowledge_base_id": "kb-123", "region": "us-east-1"},
    }
    from langchain_core.messages import HumanMessage

    state["messages"].append(HumanMessage(content="question"))

    result = retrieve_node(state)

    assert [source["retrieval_source_number"] for source in result["sources"]] == [1, 2]
    assert result["sources"][0]["metadata"]["page_number"] == 1
    assert result["sources"][1]["metadata"]["page_number"] == 2

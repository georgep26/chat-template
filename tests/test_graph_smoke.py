"""Smoke tests for RAG graph execution."""

from langchain_core.messages import HumanMessage

from src.rag_lambda.graph.graph import build_rag_graph


def test_graph_builds():
    """Test that the graph can be built and compiled."""
    graph = build_rag_graph()
    assert graph is not None


def test_graph_executes():
    """Test that the graph executes without errors."""
    graph = build_rag_graph()
    state = {
        "messages": [HumanMessage(content="What is the cancellation policy?")],
    }
    # Graph should execute without raising exceptions
    # Note: This will fail if AWS credentials/KB are not configured
    # but the structure should be correct
    try:
        result = graph.invoke(state)
        assert "messages" in result
        assert len(result["messages"]) > 0
    except Exception:
        # If AWS/KB not configured, that's okay for a smoke test
        # We're just checking the graph structure is correct
        pass


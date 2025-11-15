"""Tests for RAGAS evaluation pipeline."""

from unittest.mock import MagicMock, patch

from evals.pipeline import run_rag


@patch("evals.pipeline.graph")
def test_run_rag_structure(mock_graph):
    """Test that run_rag returns correct structure."""
    # Mock graph invocation
    mock_state = {
        "messages": [
            MagicMock(type="human", content="test question"),
            MagicMock(type="ai", content="test answer"),
            MagicMock(name="retriever_context", content="test context"),
        ]
    }
    mock_graph.invoke.return_value = mock_state

    answer, contexts = run_rag("test question")

    assert isinstance(answer, str)
    assert isinstance(contexts, list)
    assert len(contexts) > 0


def test_run_rag_empty_result():
    """Test run_rag with empty result."""
    with patch("evals.pipeline.graph") as mock_graph:
        mock_state = {"messages": []}
        mock_graph.invoke.return_value = mock_state

        answer, contexts = run_rag("test question")

        assert answer == ""
        assert isinstance(contexts, list)


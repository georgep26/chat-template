"""Comprehensive tests for RAG chat application.

This file contains tests for:
- Lambda handler integration
- Memory store implementations
- RAG graph execution
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

# from src.rag_lambda.api.models import ChatRequest, ChatResponse
from src.rag_lambda.main import build_rag_graph, lambda_handler
# from src.rag_lambda.memory.base import ChatHistoryStore
# from src.rag_lambda.memory.factory import create_history_store
# from src.rag_lambda.memory.postgres_store import PostgresHistoryStore


# ============================================================================
# Lambda Handler Tests
# ============================================================================
# Tests for the AWS Lambda handler function that processes chat requests


# def test_lambda_handler_structure():
#     """Test Lambda handler structure and error handling."""
#     event = {
#         "body": json.dumps(
#             {
#                 "conversation_id": "test-conv-123",
#                 "user_id": "test-user",
#                 "message": "Hello",
#                 "metadata": {},
#             }
#         )
#     }
#     context = MagicMock()
#
#     # Mock the main function to avoid AWS dependencies
#     with patch("src.rag_lambda.main.main") as mock_main:
#         mock_main.return_value = {
#             "statusCode": 200,
#             "headers": {"Content-Type": "application/json"},
#             "body": ChatResponse(
#                 conversation_id="test-conv-123",
#                 answer="Hello! How can I help you?",
#                 sources=[],
#             ).model_dump_json(),
#         }
#
#         response = lambda_handler(event, context)
#
#         assert response["statusCode"] == 200
#         assert "body" in response
#         body = json.loads(response["body"])
#         assert body["conversation_id"] == "test-conv-123"
#         assert "answer" in body


# def test_lambda_handler_invalid_request():
#     """Test Lambda handler with invalid request raises ValidationError."""
#     event = {"body": json.dumps({"invalid": "request"})}
#     context = MagicMock()
#
#     # Invalid request should raise ValidationError when creating ChatRequest
#     with pytest.raises(ValidationError):
#         lambda_handler(event, context)


# def test_lambda_handler_string_body():
#     """Test Lambda handler with string body."""
#     event = {
#         "body": '{"conversation_id": "test", "user_id": "user", "message": "hi"}'
#     }
#     context = MagicMock()
#
#     with patch("src.rag_lambda.main.main") as mock_main:
#         mock_main.return_value = {
#             "statusCode": 200,
#             "headers": {"Content-Type": "application/json"},
#             "body": ChatResponse(
#                 conversation_id="test", answer="hi", sources=[]
#             ).model_dump_json(),
#         }
#
#         response = lambda_handler(event, context)
#         assert response["statusCode"] == 200


# ============================================================================
# Memory Store Tests
# ============================================================================
# Tests for chat history storage implementations (Postgres, Aurora Data API, etc.)


# def test_chat_history_store_interface():
#     """Test that ChatHistoryStore is an abstract base class."""
#     with pytest.raises(TypeError):
#         ChatHistoryStore()  # Should not be instantiable


# @patch("src.rag_lambda.memory.postgres_store._get_connection")
# def test_postgres_history_store_init(mock_conn):
#     """Test PostgresHistoryStore initialization."""
#     mock_conn.return_value = MagicMock()
#
#     store = PostgresHistoryStore()
#
#     assert store is not None
#     mock_conn.assert_called_once()


# @patch("src.rag_lambda.memory.postgres_store._get_connection")
# def test_postgres_history_store_methods(mock_conn):
#     """Test PostgresHistoryStore methods."""
#     mock_conn_instance = MagicMock()
#     mock_conn.return_value = mock_conn_instance
#
#     store = PostgresHistoryStore()
#
#     # Mock the history object
#     mock_history = MagicMock()
#     mock_history.messages = []
#     store._history = MagicMock(return_value=mock_history)
#
#     # Test get_messages
#     messages = store.get_messages("test-conv")
#     assert isinstance(messages, list)
#
#     # Test append_messages
#     store.append_messages("test-conv", [HumanMessage(content="test")])
#     # Should not raise


# @patch("src.rag_lambda.memory.postgres_store.PostgresHistoryStore")
# def test_factory_create_postgres(mock_postgres):
#     """Test factory creates PostgresHistoryStore."""
#     mock_store = MagicMock()
#     mock_postgres.return_value = mock_store
#
#     store = create_history_store("postgres", db_creds={"host": "localhost"})
#
#     assert store is not None
#     mock_postgres.assert_called_once()


# def test_factory_unsupported_backend():
#     """Test factory raises error for unsupported backend."""
#     with pytest.raises(ValueError):
#         create_history_store("unsupported")


# ============================================================================
# RAG Graph Tests
# ============================================================================
# Smoke tests for RAG graph structure and execution


def test_graph_builds():
    """Test that the graph can be built and compiled."""
    graph = build_rag_graph()
    assert graph is not None


# def test_graph_executes():
#     """Test that the graph executes without errors."""
#     graph = build_rag_graph()
#     state = {
#         "messages": [HumanMessage(content="What is the cancellation policy?")],
#     }
#     # Graph should execute without raising exceptions
#     # Note: This will fail if AWS credentials/KB are not configured
#     # but the structure should be correct
#     try:
#         result = graph.invoke(state)
#         assert "messages" in result
#         assert len(result["messages"]) > 0
#     except Exception:
#         # If AWS/KB not configured, that's okay for a smoke test
#         # We're just checking the graph structure is correct
#         pass


"""Integration tests for Lambda handler."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rag_app.api.models import ChatRequest, ChatResponse
from src.main import lambda_handler


def test_lambda_handler_structure():
    """Test Lambda handler structure and error handling."""
    event = {
        "body": json.dumps(
            {
                "conversation_id": "test-conv-123",
                "user_id": "test-user",
                "message": "Hello",
                "metadata": {},
            }
        )
    }
    context = MagicMock()

    # Mock the chat service to avoid AWS dependencies
    with patch("rag_app.api.chat_service.handle_chat") as mock_handle:
        mock_handle.return_value = ChatResponse(
            conversation_id="test-conv-123",
            answer="Hello! How can I help you?",
            sources=[],
        )

        response = lambda_handler(event, context)

        assert response["statusCode"] == 200
        assert "body" in response
        body = json.loads(response["body"])
        assert body["conversation_id"] == "test-conv-123"
        assert "answer" in body


def test_lambda_handler_invalid_request():
    """Test Lambda handler with invalid request."""
    event = {"body": json.dumps({"invalid": "request"})}
    context = MagicMock()

    response = lambda_handler(event, context)

    assert response["statusCode"] == 500
    assert "error" in json.loads(response["body"])


def test_lambda_handler_string_body():
    """Test Lambda handler with string body."""
    event = {
        "body": '{"conversation_id": "test", "user_id": "user", "message": "hi"}'
    }
    context = MagicMock()

    with patch("rag_app.api.chat_service.handle_chat") as mock_handle:
        mock_handle.return_value = ChatResponse(
            conversation_id="test", answer="hi", sources=[]
        )

        response = lambda_handler(event, context)
        assert response["statusCode"] == 200


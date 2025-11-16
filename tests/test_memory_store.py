"""Tests for memory store implementations."""

from unittest.mock import MagicMock, patch

import pytest

from src.rag_lambda.memory.base import ChatHistoryStore
from src.rag_lambda.memory.factory import create_history_store
from src.rag_lambda.memory.postgres_store import PostgresHistoryStore


def test_chat_history_store_interface():
    """Test that ChatHistoryStore is an abstract base class."""
    with pytest.raises(TypeError):
        ChatHistoryStore()  # Should not be instantiable


@patch("src.rag_lambda.memory.postgres_store._get_connection")
def test_postgres_history_store_init(mock_conn):
    """Test PostgresHistoryStore initialization."""
    mock_conn.return_value = MagicMock()

    store = PostgresHistoryStore()

    assert store is not None
    mock_conn.assert_called_once()


@patch("src.rag_lambda.memory.postgres_store._get_connection")
def test_postgres_history_store_methods(mock_conn):
    """Test PostgresHistoryStore methods."""
    mock_conn_instance = MagicMock()
    mock_conn.return_value = mock_conn_instance

    store = PostgresHistoryStore()

    # Mock the history object
    mock_history = MagicMock()
    mock_history.messages = []
    store._history = MagicMock(return_value=mock_history)

    # Test get_messages
    messages = store.get_messages("test-conv")
    assert isinstance(messages, list)

    # Test append_messages
    from langchain_core.messages import HumanMessage

    store.append_messages("test-conv", [HumanMessage(content="test")])
    # Should not raise


@patch("rag_app.memory.postgres_store.PostgresHistoryStore")
def test_factory_create_postgres(mock_postgres):
    """Test factory creates PostgresHistoryStore."""
    mock_store = MagicMock()
    mock_postgres.return_value = mock_store

    store = create_history_store("postgres")

    assert store is not None
    mock_postgres.assert_called_once()


def test_factory_unsupported_backend():
    """Test factory raises error for unsupported backend."""
    with pytest.raises(ValueError):
        create_history_store("unsupported")


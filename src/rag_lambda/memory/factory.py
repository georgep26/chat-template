"""Factory for creating chat history store instances."""

from .base import ChatHistoryStore
from .postgres_store import PostgresHistoryStore


def create_history_store(memory_backend_type: str, table_name: str = "chat_history") -> ChatHistoryStore:
    """
    Create a chat history store instance based on backend type.

    Args:
        memory_backend_type: Backend type (e.g., "postgres", "dynamo", "vector")
        table_name: Table name for postgres backend (default: "chat_history")

    Returns:
        ChatHistoryStore instance

    Raises:
        ValueError: If backend type is not supported
    """
    if memory_backend_type == "postgres":
        return PostgresHistoryStore(table_name=table_name)
    elif memory_backend_type == "dynamo":
        # TODO: Implement DynamoHistoryStore
        raise NotImplementedError("DynamoDB backend not yet implemented")
    elif memory_backend_type == "vector":
        # TODO: Implement VectorHistoryStore
        raise NotImplementedError("Vector store backend not yet implemented")
    else:
        raise ValueError(f"Unsupported memory backend: {memory_backend_type}")


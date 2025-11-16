"""Factory for creating chat history store instances."""

from typing import Any, Dict, Optional

from .base import ChatHistoryStore
from .postgres_store import PostgresHistoryStore


def create_history_store(
    memory_backend_type: str,
    **memory_store_arguments: Optional[Dict[str, Any]] 
) -> ChatHistoryStore:
    """
    Create a chat history store instance based on backend type.

    Args:
        memory_backend_type: Backend type (e.g., "postgres", "dynamo", "vector")
        memory_store_arguments: Dictionary of arguments specific to the backend type.
                               For postgres: {"db_creds": {...}, "table_name": "..."}

    Returns:
        ChatHistoryStore instance

    Raises:
        ValueError: If backend type is not supported or required arguments are missing
    """
    if memory_store_arguments is None:
        memory_store_arguments = {}
    
    if memory_backend_type == "postgres":
        db_creds = memory_store_arguments.get("db_creds")
        if db_creds is None:
            raise ValueError("db_creds is required for postgres backend in memory_store_arguments")
        table_name = memory_store_arguments.get("table_name", "chat_history")
        return PostgresHistoryStore(db_creds=db_creds, table_name=table_name)
    elif memory_backend_type == "dynamo":
        # TODO: Implement DynamoHistoryStore
        raise NotImplementedError("DynamoDB backend not yet implemented")
    elif memory_backend_type == "vector":
        # TODO: Implement VectorHistoryStore
        raise NotImplementedError("Vector store backend not yet implemented")
    elif memory_backend_type == "local_sqlite":
        # TODO: Implement LocalSQLiteHistoryStore
        raise NotImplementedError("Local SQLite backend not yet implemented")
    else:
        raise ValueError(f"Unsupported memory backend: {memory_backend_type}")


"""Factory for creating chat history store instances."""

import os
from typing import Optional

import yaml

from .base import ChatHistoryStore
from .postgres_store import PostgresHistoryStore

# Load configuration
_config_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "..",
    "config",
    "app_config.yml",
)
_config_path = os.path.abspath(_config_path)

with open(_config_path, "r") as f:
    _config = yaml.safe_load(f)

_rag_config = _config.get("rag", {})


def create_history_store(backend: Optional[str] = None) -> ChatHistoryStore:
    """
    Create a chat history store instance based on configuration.

    Args:
        backend: Optional backend type override. If None, uses config value.

    Returns:
        ChatHistoryStore instance

    Raises:
        ValueError: If backend type is not supported
    """
    backend_type = backend or _rag_config.get("memory", {}).get("backend", "postgres")

    if backend_type == "postgres":
        return PostgresHistoryStore()
    elif backend_type == "dynamo":
        # TODO: Implement DynamoHistoryStore
        raise NotImplementedError("DynamoDB backend not yet implemented")
    elif backend_type == "vector":
        # TODO: Implement VectorHistoryStore
        raise NotImplementedError("Vector store backend not yet implemented")
    else:
        raise ValueError(f"Unsupported memory backend: {backend_type}")


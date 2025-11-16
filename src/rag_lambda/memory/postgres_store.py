"""Postgres implementation of chat history storage."""

import os
from typing import List

import psycopg
from langchain_core.messages import BaseMessage
from langchain_postgres import PostgresChatMessageHistory

from .base import ChatHistoryStore

TABLE_NAME = "chat_history"


def _get_connection():
    """Get Postgres connection from environment variable."""
    conn_info = os.getenv("PG_CONN_INFO")
    if not conn_info:
        raise ValueError("PG_CONN_INFO environment variable must be set")
    return psycopg.connect(conn_info)


class PostgresHistoryStore(ChatHistoryStore):
    """Postgres-based chat history storage."""

    def __init__(self):
        """Initialize Postgres connection and create tables if needed."""
        self._conn = _get_connection()
        PostgresChatMessageHistory.create_tables(self._conn, TABLE_NAME)

    def _history(self, conversation_id: str) -> PostgresChatMessageHistory:
        """Get PostgresChatMessageHistory instance for a conversation."""
        return PostgresChatMessageHistory(
            TABLE_NAME, conversation_id, sync_connection=self._conn
        )

    def get_messages(self, conversation_id: str) -> List[BaseMessage]:
        """Retrieve messages for a conversation."""
        return self._history(conversation_id).messages

    def append_messages(self, conversation_id: str, messages: List[BaseMessage]) -> None:
        """Append messages to a conversation."""
        self._history(conversation_id).add_messages(messages)


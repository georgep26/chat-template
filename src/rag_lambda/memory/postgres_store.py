"""Postgres implementation of chat history storage."""

from typing import Dict, List

import psycopg2
from langchain_core.messages import BaseMessage
from langchain_postgres import PostgresChatMessageHistory

from .base import ChatHistoryStore


class PostgresHistoryStore(ChatHistoryStore):
    """Postgres-based chat history storage."""

    def __init__(self, db_creds: Dict[str, str], table_name: str = "chat_history"):
        """
        Initialize Postgres connection and create tables if needed.
        
        Args:
            db_creds: Dictionary containing database connection credentials
                     (e.g., {'host': '...', 'port': '...', 'database': '...', 'user': '...', 'password': '...'})
            table_name: Name of the table to store chat history
        """
        self._db_creds = db_creds
        self._table_name = table_name
        self._conn = self._get_connection()
        PostgresChatMessageHistory.create_tables(self._conn, self._table_name)

    def _get_connection(self):
        """Get Postgres connection using db_creds."""
        return psycopg2.connect(**self._db_creds)

    def _history(self, conversation_id: str) -> PostgresChatMessageHistory:
        """Get PostgresChatMessageHistory instance for a conversation."""
        return PostgresChatMessageHistory(
            self._table_name, conversation_id, sync_connection=self._conn
        )

    def get_messages(self, conversation_id: str) -> List[BaseMessage]:
        """Retrieve messages for a conversation."""
        return self._history(conversation_id).messages

    def append_messages(self, conversation_id: str, messages: List[BaseMessage]) -> None:
        """Append messages to a conversation."""
        self._history(conversation_id).add_messages(messages)


"""Postgres implementation of chat history storage."""

import json
from typing import Any, Dict, List, Optional

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
        self._ensure_metadata_table()

    def _get_connection(self):
        """Get Postgres connection using db_creds."""
        return psycopg2.connect(**self._db_creds)

    def _ensure_metadata_table(self):
        """Ensure the conversation_metadata table exists with JSONB column."""
        from psycopg2 import sql
        with self._conn.cursor() as cur:
            # Create table if it doesn't exist
            metadata_table_name = sql.Identifier(f"{self._table_name}_metadata")
            cur.execute(
                sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {} (
                        conversation_id VARCHAR(255) PRIMARY KEY,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """).format(metadata_table_name)
            )
            self._conn.commit()

    def _history(self, conversation_id: str) -> PostgresChatMessageHistory:
        """Get PostgresChatMessageHistory instance for a conversation."""
        return PostgresChatMessageHistory(
            self._table_name, conversation_id, sync_connection=self._conn
        )

    def get_messages(self, conversation_id: str) -> List[BaseMessage]:
        """Retrieve messages for a conversation."""
        return self._history(conversation_id).messages

    def append_messages(self, conversation_id: str, messages: List[BaseMessage], metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Append messages to a conversation and optionally store metadata.
        
        Args:
            conversation_id: Unique identifier for the conversation
            messages: Messages to append
            metadata: Optional metadata to store (e.g., retrieval_filters)
        """
        self._history(conversation_id).add_messages(messages)
        
        # Store metadata if provided
        if metadata:
            from psycopg2 import sql
            with self._conn.cursor() as cur:
                # Upsert metadata
                metadata_table_name = sql.Identifier(f"{self._table_name}_metadata")
                cur.execute(
                    sql.SQL("""
                        INSERT INTO {} (conversation_id, metadata, updated_at)
                        VALUES (%s, %s::jsonb, CURRENT_TIMESTAMP)
                        ON CONFLICT (conversation_id) 
                        DO UPDATE SET 
                            metadata = {}.metadata || EXCLUDED.metadata,
                            updated_at = CURRENT_TIMESTAMP
                    """).format(metadata_table_name, metadata_table_name),
                    (conversation_id, json.dumps(metadata))
                )
                self._conn.commit()


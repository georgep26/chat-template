"""Postgres implementation of chat history storage."""

import json
import time
from typing import Any, Dict, List, Optional

import psycopg
from psycopg import sql
from psycopg.errors import OperationalError, InterfaceError
from langchain_core.messages import BaseMessage
from langchain_postgres import PostgresChatMessageHistory

from .base import ChatHistoryStore

from ...utils.logger import get_logger

log = get_logger(__name__)


class PostgresHistoryStore(ChatHistoryStore):
    """Postgres-based chat history storage."""

    def __init__(
        self, 
        db_creds: Dict[str, str], 
        table_name: str = "chat_history",
        max_retries: int = 10,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 30.0
    ):
        """
        Initialize Postgres connection and create tables if needed.
        
        Args:
            db_creds: Dictionary containing database connection credentials
                     (e.g., {'host': '...', 'port': '...', 'dbname': '...', 'user': '...', 'password': '...'})
            table_name: Name of the table to store chat history
            max_retries: Maximum number of connection retry attempts (default: 10)
            initial_retry_delay: Initial delay in seconds before first retry (default: 1.0)
            max_retry_delay: Maximum delay in seconds between retries (default: 30.0)
        """
        self._db_creds = db_creds
        self._table_name = table_name
        self._max_retries = max_retries
        self._initial_retry_delay = initial_retry_delay
        self._max_retry_delay = max_retry_delay
        self._conn = self._get_connection()
        PostgresChatMessageHistory.create_tables(self._conn, self._table_name)
        self._ensure_metadata_table()

    def _get_connection(self):
        """
        Get Postgres connection using db_creds with retry logic.
        
        Retries connection attempts with exponential backoff when the database
        is unavailable (e.g., resuming from auto-pause in Aurora Serverless).
        
        Returns:
            psycopg.Connection: Database connection object
            
        Raises:
            OperationalError: If connection fails after all retry attempts
            InterfaceError: If connection fails after all retry attempts
        """
        retry_delay = self._initial_retry_delay
        last_exception = None
        
        log.info(f"Attempting to connect to database with table name: {self._table_name}")
        for attempt in range(self._max_retries):
            try:
                conn = psycopg.connect(**self._db_creds)
                log.info(f"Connected to database with table name: {self._table_name}")
                return conn
            except (OperationalError, InterfaceError) as e:
                log.error(f"Failed to connect to database with table name: {self._table_name} - Error: {e}")
                last_exception = e
                error_msg = str(e).lower()
                
                # Check if this is a connection/resuming error
                is_resuming = any(keyword in error_msg for keyword in [
                    'resuming',
                    'connection refused',
                    'timeout',
                    'could not connect',
                    'network is unreachable',
                    'temporarily unavailable'
                ])
                
                if is_resuming and attempt < self._max_retries - 1:
                    time.sleep(retry_delay)
                    # Exponential backoff: double the delay, but cap at max_retry_delay
                    retry_delay = min(retry_delay * 2, self._max_retry_delay)
                else:
                    # Not a resuming error, or we've exhausted retries
                    if attempt < self._max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, self._max_retry_delay)
                    else:
                        raise
        
        # This should never be reached, but just in case
        if last_exception:
            raise last_exception
        raise OperationalError("Failed to establish database connection")

    def _ensure_connection(self):
        """
        Ensure the database connection is active, reconnecting if necessary.
        
        This method checks if the connection is closed or broken and attempts
        to reconnect with retry logic if needed.
        """
        try:
            # Check if connection is closed or broken
            if self._conn.closed:
                self._conn = self._get_connection()
            else:
                # Try a simple query to verify connection is alive
                with self._conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except (OperationalError, InterfaceError):
            try:
                self._conn.close()
            except Exception:
                pass  # Ignore errors when closing a broken connection
            self._conn = self._get_connection()

    def _ensure_metadata_table(self):
        """Ensure the conversation_metadata table exists with JSONB column."""
        self._ensure_connection()
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
        self._ensure_connection()
        return self._history(conversation_id).messages

    def append_messages(self, conversation_id: str, messages: List[BaseMessage], metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Append messages to a conversation and optionally store metadata.
        
        Args:
            conversation_id: Unique identifier for the conversation
            messages: Messages to append
            metadata: Optional metadata to store (e.g., retrieval_filters)
        """
        self._ensure_connection()
        self._history(conversation_id).add_messages(messages)
        
        # Store metadata if provided
        if metadata:
            self._ensure_connection()
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


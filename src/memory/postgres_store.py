"""Postgres implementation of chat history storage."""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import psycopg
from psycopg import sql
from psycopg.errors import OperationalError, InterfaceError
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict

from .base import ChatHistoryStore, ConversationAccessError, ConversationRecord

from utils.logger import get_logger

log = get_logger(__name__)


class PostgresHistoryStore(ChatHistoryStore):
    """Postgres-based chat history storage."""

    def __init__(
        self, 
        db_creds: Dict[str, str], 
        table_name: str = "chat_history",
        conversation_table_name: str = "chat_conversations",
        turn_events_table_name: str = "chat_turn_events",
        environment: Optional[str] = None,
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
            conversation_table_name: Name of the table to store conversation metadata
            turn_events_table_name: Name of the table to store backend trace events
            environment: Deployment environment for ownership isolation
            max_retries: Maximum number of connection retry attempts (default: 10)
            initial_retry_delay: Initial delay in seconds before first retry (default: 1.0)
            max_retry_delay: Maximum delay in seconds between retries (default: 30.0)
        """
        self._db_creds = db_creds
        self._table_name = table_name
        self._conversation_table_name = conversation_table_name
        self._turn_events_table_name = turn_events_table_name
        self._environment = environment or os.getenv("ENVIRONMENT", "local")
        self._max_retries = max_retries
        self._initial_retry_delay = initial_retry_delay
        self._max_retry_delay = max_retry_delay
        self._conn = self._get_connection()
        self._ensure_tables()

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

    def _ensure_tables(self):
        """Ensure conversation, chat history, and turn event tables exist."""
        self._ensure_connection()
        with self._conn.cursor() as cur:
            conversation_table_name = sql.Identifier(self._conversation_table_name)
            metadata_table_name = sql.Identifier(f"{self._table_name}_metadata")
            history_table_name = sql.Identifier(self._table_name)
            turn_events_table_name = sql.Identifier(self._turn_events_table_name)
            owner_index_name = sql.Identifier(f"idx_{self._conversation_table_name}_owner")
            request_index_name = sql.Identifier(f"idx_{self._conversation_table_name}_request_id")
            history_index_name = sql.Identifier(f"idx_{self._table_name}_conversation_id")
            events_index_name = sql.Identifier(f"idx_{self._turn_events_table_name}_conversation_turn")

            cur.execute(
                sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {} (
                        conversation_id UUID PRIMARY KEY,
                        user_id VARCHAR(255) NOT NULL,
                        environment VARCHAR(80) NOT NULL,
                        title VARCHAR(255),
                        status VARCHAR(40) NOT NULL DEFAULT 'active',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_message_at TIMESTAMPTZ,
                        message_count INTEGER NOT NULL DEFAULT 0,
                        summary TEXT,
                        summary_message_count INTEGER NOT NULL DEFAULT 0,
                        request_id VARCHAR(255),
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                """).format(conversation_table_name)
            )
            cur.execute(
                sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {} (
                        id BIGSERIAL PRIMARY KEY,
                        conversation_id UUID NOT NULL REFERENCES {}(conversation_id) ON DELETE CASCADE,
                        message JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """).format(history_table_name, conversation_table_name)
            )
            cur.execute(
                sql.SQL("""
                    CREATE TABLE IF NOT EXISTS {} (
                        event_id UUID PRIMARY KEY,
                        trace_id VARCHAR(255),
                        conversation_id UUID NOT NULL REFERENCES {}(conversation_id) ON DELETE CASCADE,
                        turn_index INTEGER,
                        step_index INTEGER NOT NULL,
                        step_name VARCHAR(100) NOT NULL,
                        status VARCHAR(50) NOT NULL,
                        started_at TIMESTAMPTZ,
                        completed_at TIMESTAMPTZ,
                        latency_ms INTEGER,
                        model_id VARCHAR(255),
                        input_summary JSONB,
                        output_summary JSONB,
                        input_payload JSONB,
                        output_payload JSONB,
                        error JSONB,
                        frontend_event JSONB,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """).format(turn_events_table_name, conversation_table_name)
            )
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (environment, user_id, conversation_id)").format(
                    owner_index_name, conversation_table_name
                )
            )
            cur.execute(
                sql.SQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (environment, user_id, request_id) WHERE request_id IS NOT NULL"
                ).format(request_index_name, conversation_table_name)
            )
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (conversation_id, id)").format(
                    history_index_name, history_table_name
                )
            )
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (conversation_id, turn_index, step_index)").format(
                    events_index_name, turn_events_table_name
                )
            )

            # Keep the old metadata table available temporarily for callers that
            # still read it outside this store. New metadata lives on conversations.
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

    def _validated_uuid(self, conversation_id: str) -> str:
        try:
            return str(uuid.UUID(conversation_id))
        except ValueError:
            raise ValueError("conversation_id must be a UUID")

    def _derive_title(self, title_seed: Optional[str]) -> str:
        cleaned = " ".join((title_seed or "").split())
        if not cleaned:
            return "New conversation"
        return cleaned[:80]

    def _row_to_conversation(self, row, *, created: bool = False, idempotent_replay: bool = False) -> ConversationRecord:
        metadata = row[7] or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        # Optional timestamp columns (positions 8, 9) — only present when SELECT lists them.
        created_at = row[8] if len(row) > 8 else None
        last_message_at = row[9] if len(row) > 9 else None
        return ConversationRecord(
            conversation_id=str(row[0]),
            user_id=row[1],
            title=row[2],
            status=row[3] or "active",
            message_count=row[4] or 0,
            summary=row[5],
            summary_message_count=row[6] or 0,
            metadata=metadata,
            created_at=created_at,
            last_message_at=last_message_at,
            created=created,
            idempotent_replay=idempotent_replay,
        )

    def resolve_or_create_conversation(
        self,
        *,
        user_id: str,
        conversation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        title_seed: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationRecord:
        """Resolve an owned conversation or create a new backend-generated UUID."""
        self._ensure_connection()
        conversation_table_name = sql.Identifier(self._conversation_table_name)
        with self._conn.cursor() as cur:
            if conversation_id:
                normalized_id = self._validated_uuid(conversation_id)
                cur.execute(
                    sql.SQL("""
                        SELECT conversation_id, user_id, title, status, message_count, summary, summary_message_count, metadata
                        FROM {}
                        WHERE conversation_id = %s
                          AND user_id = %s
                          AND environment = %s
                        LIMIT 1
                    """).format(conversation_table_name),
                    (normalized_id, user_id, self._environment),
                )
                row = cur.fetchone()
                if not row:
                    raise ConversationAccessError("Conversation was not found for this user.")
                return self._row_to_conversation(row)

            if request_id:
                cur.execute(
                    sql.SQL("""
                        SELECT conversation_id, user_id, title, status, message_count, summary, summary_message_count, metadata
                        FROM {}
                        WHERE user_id = %s
                          AND environment = %s
                          AND request_id = %s
                        LIMIT 1
                    """).format(conversation_table_name),
                    (user_id, self._environment, request_id),
                )
                row = cur.fetchone()
                if row:
                    return self._row_to_conversation(row, idempotent_replay=True)

            new_conversation_id = str(uuid.uuid4())
            title = self._derive_title(title_seed)
            conversation_metadata = metadata or {}
            cur.execute(
                sql.SQL("""
                    INSERT INTO {} (conversation_id, user_id, environment, title, request_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """).format(conversation_table_name),
                (new_conversation_id, user_id, self._environment, title, request_id, json.dumps(conversation_metadata)),
            )
            self._conn.commit()
            return ConversationRecord(
                conversation_id=new_conversation_id,
                user_id=user_id,
                title=title,
                metadata=conversation_metadata,
                created=True,
            )

    def get_messages(self, conversation_id: str, limit: Optional[int] = None) -> List[BaseMessage]:
        """Retrieve messages for a conversation."""
        self._ensure_connection()
        normalized_id = self._validated_uuid(conversation_id)
        history_table_name = sql.Identifier(self._table_name)
        query = sql.SQL("""
            SELECT message
            FROM (
                SELECT id, message
                FROM {}
                WHERE conversation_id = %s
                ORDER BY id DESC
                {}
            ) recent_messages
            ORDER BY id ASC
        """).format(
            history_table_name,
            sql.SQL("LIMIT %s") if limit is not None else sql.SQL(""),
        )
        params = (normalized_id, int(limit)) if limit is not None else (normalized_id,)
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        message_dicts = []
        for row in rows:
            value = row[0]
            if isinstance(value, str):
                value = json.loads(value)
            message_dicts.append(value)
        return messages_from_dict(message_dicts)

    def append_messages(self, conversation_id: str, messages: List[BaseMessage], metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Append messages to a conversation and optionally store metadata.
        
        Args:
            conversation_id: Unique identifier for the conversation
            messages: Messages to append
            metadata: Optional metadata to store (e.g., retrieval_filters)
        """
        if not messages:
            return
        self._ensure_connection()
        normalized_id = self._validated_uuid(conversation_id)
        history_table_name = sql.Identifier(self._table_name)
        conversation_table_name = sql.Identifier(self._conversation_table_name)
        with self._conn.cursor() as cur:
            for message in messages:
                cur.execute(
                    sql.SQL("INSERT INTO {} (conversation_id, message) VALUES (%s, %s::jsonb)").format(history_table_name),
                    (normalized_id, json.dumps(message_to_dict(message))),
                )
            cur.execute(
                sql.SQL("""
                    UPDATE {}
                    SET
                        message_count = message_count + %s,
                        last_message_at = NOW(),
                        updated_at = NOW(),
                        metadata = metadata || %s::jsonb
                    WHERE conversation_id = %s
                """).format(conversation_table_name),
                (len(messages), json.dumps(metadata or {}), normalized_id),
            )
            self._conn.commit()
        
    def append_trace_events(self, conversation_id: str, trace_events: List[Any]) -> None:
        """Persist backend trace events for a conversation turn."""
        if not trace_events:
            return

        self._ensure_connection()
        normalized_id = self._validated_uuid(conversation_id)
        turn_events_table_name = sql.Identifier(self._turn_events_table_name)
        with self._conn.cursor() as cur:
            for trace_event in trace_events:
                record = trace_event.to_record() if hasattr(trace_event, "to_record") else trace_event
                if not isinstance(record, dict):
                    continue
                cur.execute(
                    sql.SQL("""
                        INSERT INTO {} (
                            event_id,
                            trace_id,
                            conversation_id,
                            turn_index,
                            step_index,
                            step_name,
                            status,
                            started_at,
                            completed_at,
                            latency_ms,
                            model_id,
                            input_summary,
                            output_summary,
                            input_payload,
                            output_payload,
                            error,
                            frontend_event,
                            metadata
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s::timestamptz, %s::timestamptz, %s, %s,
                            %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                            %s::jsonb, %s::jsonb, %s::jsonb
                        )
                    """).format(turn_events_table_name),
                    (
                        record["event_id"],
                        record.get("trace_id"),
                        normalized_id,
                        record.get("turn_index"),
                        record["step_index"],
                        record["step_name"],
                        record["status"],
                        record.get("started_at"),
                        record.get("completed_at"),
                        record.get("latency_ms"),
                        record.get("model_id"),
                        json.dumps(record.get("input_summary")) if record.get("input_summary") is not None else None,
                        json.dumps(record.get("output_summary")) if record.get("output_summary") is not None else None,
                        json.dumps(record.get("input_payload")) if record.get("input_payload") is not None else None,
                        json.dumps(record.get("output_payload")) if record.get("output_payload") is not None else None,
                        json.dumps(record.get("error")) if record.get("error") is not None else None,
                        json.dumps(record.get("frontend_event")) if record.get("frontend_event") is not None else None,
                        json.dumps(record.get("metadata") or {}),
                    ),
                )
            self._conn.commit()

    def list_conversations(
        self,
        *,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ConversationRecord]:
        """List conversations owned by user_id, newest first, excluding soft-deleted rows."""
        self._ensure_connection()
        conversation_table_name = sql.Identifier(self._conversation_table_name)
        query = sql.SQL("""
            SELECT
                conversation_id,
                user_id,
                title,
                status,
                message_count,
                summary,
                summary_message_count,
                metadata,
                to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                to_char(last_message_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
            FROM {}
            WHERE environment = %s
              AND user_id = %s
              AND status <> 'deleted'
            ORDER BY last_message_at DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s
        """).format(conversation_table_name)
        with self._conn.cursor() as cur:
            cur.execute(query, (self._environment, user_id, int(limit), int(offset)))
            rows = cur.fetchall()
        return [self._row_to_conversation(row) for row in rows]

    def rename_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
        title: str,
    ) -> ConversationRecord:
        """Update title for an owned conversation. Raises if not owned."""
        self._ensure_connection()
        normalized_id = self._validated_uuid(conversation_id)
        clean_title = self._derive_title(title)
        conversation_table_name = sql.Identifier(self._conversation_table_name)
        query = sql.SQL("""
            UPDATE {}
            SET title = %s, updated_at = NOW()
            WHERE conversation_id = %s
              AND user_id = %s
              AND environment = %s
              AND status <> 'deleted'
            RETURNING
                conversation_id,
                user_id,
                title,
                status,
                message_count,
                summary,
                summary_message_count,
                metadata,
                to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                to_char(last_message_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
        """).format(conversation_table_name)
        with self._conn.cursor() as cur:
            cur.execute(query, (clean_title, normalized_id, user_id, self._environment))
            row = cur.fetchone()
            if not row:
                self._conn.rollback()
                raise ConversationAccessError("Conversation was not found for this user.")
            self._conn.commit()
        return self._row_to_conversation(row)

    def delete_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> None:
        """Soft-delete an owned conversation by setting status='deleted'. Raises if not owned."""
        self._ensure_connection()
        normalized_id = self._validated_uuid(conversation_id)
        conversation_table_name = sql.Identifier(self._conversation_table_name)
        query = sql.SQL("""
            UPDATE {}
            SET status = 'deleted', updated_at = NOW()
            WHERE conversation_id = %s
              AND user_id = %s
              AND environment = %s
              AND status <> 'deleted'
        """).format(conversation_table_name)
        with self._conn.cursor() as cur:
            cur.execute(query, (normalized_id, user_id, self._environment))
            if cur.rowcount == 0:
                self._conn.rollback()
                raise ConversationAccessError("Conversation was not found for this user.")
            self._conn.commit()

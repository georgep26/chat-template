"""Aurora Data API implementation of chat history storage."""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict

from .base import ChatHistoryStore, ConversationAccessError, ConversationRecord
from utils.logger import get_logger

log = get_logger(__name__)


class DataApiHistoryStore(ChatHistoryStore):
    """Aurora Data API-based chat history storage."""

    def __init__(
        self,
        db_cluster_arn: str,
        db_credentials_secret_arn: str,
        database_name: str,
        table_name: str = "chat_history",
        conversation_table_name: str = "chat_conversations",
        turn_events_table_name: str = "chat_turn_events",
        region: str = "us-east-1",
        environment: Optional[str] = None,
        max_retries: int = 10,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 30.0
    ):
        """
        Initialize Data API client and ensure tables exist.
        
        Args:
            db_cluster_arn: ARN of the Aurora cluster
            db_credentials_secret_arn: ARN of the Secrets Manager secret with DB credentials
            database_name: Name of the database
            table_name: Name of the table to store chat history
            conversation_table_name: Name of the table to store conversation metadata
            turn_events_table_name: Name of the table to store backend trace events
            region: AWS region
            environment: Deployment environment for ownership isolation
            max_retries: Maximum number of retry attempts
            initial_retry_delay: Initial delay in seconds before first retry
            max_retry_delay: Maximum delay in seconds between retries
        """
        self._db_cluster_arn = db_cluster_arn
        self._db_credentials_secret_arn = db_credentials_secret_arn
        self._database_name = database_name
        self._table_name = table_name
        self._conversation_table_name = conversation_table_name
        self._turn_events_table_name = turn_events_table_name
        self._region = region
        self._environment = environment or os.getenv("ENVIRONMENT", "local")
        self._max_retries = max_retries
        self._initial_retry_delay = initial_retry_delay
        self._max_retry_delay = max_retry_delay
        
        self._rds_data = boto3.client('rds-data', region_name=region)
        
        # Ensure tables exist
        self._ensure_tables()

    def _execute_statement(self, sql: str, parameters: List[Dict[str, Any]] = None) -> Dict:
        """
        Execute a SQL statement using Data API with retry logic.
        
        Args:
            sql: SQL statement to execute
            parameters: Optional parameters for parameterized queries
            
        Returns:
            Response from Data API
        """
        retry_delay = self._initial_retry_delay
        last_exception = None
        db_resuming_logged = False
        
        for attempt in range(self._max_retries):
            try:
                request_params = {
                    'resourceArn': self._db_cluster_arn,
                    'secretArn': self._db_credentials_secret_arn,
                    'database': self._database_name,
                    'sql': sql
                }
                
                if parameters:
                    request_params['parameters'] = parameters
                
                response = self._rds_data.execute_statement(**request_params)
                
                # If we were waiting for DB to resume, log that it's ready
                if db_resuming_logged:
                    log.info("Database is ready")
                
                return response
                
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                error_msg = str(e).lower()
                
                # Check for DatabaseResumingException - handle specially
                is_db_resuming = 'databaseresumingexception' in error_msg or error_code == 'DatabaseResumingException'
                
                if is_db_resuming:
                    # Log initial warning once
                    if not db_resuming_logged:
                        log.warning("Database is resuming after being auto-paused. Waiting for it to become ready...")
                        db_resuming_logged = True
                    else:
                        log.info("DB resuming...")
                    
                    if attempt < self._max_retries - 1:
                        time.sleep(5)  # Wait 5 seconds for DB to resume
                        continue
                    else:
                        log.error(f"Database failed to resume after {self._max_retries} attempts")
                        raise
                
                # Check if this is another retryable error
                is_retryable = any(keyword in error_msg for keyword in [
                    'badrequestexception',
                    'forbiddenexception',
                    'serviceunavailableerror',
                    'throttling',
                    'toomanyrequests',
                    'statementtimeout'
                ]) or error_code in ['BadRequestException', 'ForbiddenException', 'ServiceUnavailableError', 'StatementTimeoutException']
                
                last_exception = e
                log.warning(f"Data API call failed (attempt {attempt + 1}/{self._max_retries}): {e}")
                
                if is_retryable and attempt < self._max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self._max_retry_delay)
                else:
                    raise
        
        if last_exception:
            raise last_exception
        raise Exception("Failed to execute statement after retries")

    def _ensure_tables(self):
        """Ensure conversation, chat history, and turn event tables exist."""
        statements = [
            f"""
            CREATE TABLE IF NOT EXISTS {self._conversation_table_name} (
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
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                id BIGSERIAL PRIMARY KEY,
                conversation_id UUID NOT NULL REFERENCES {self._conversation_table_name}(conversation_id) ON DELETE CASCADE,
                message JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {self._turn_events_table_name} (
                event_id UUID PRIMARY KEY,
                trace_id VARCHAR(255),
                conversation_id UUID NOT NULL REFERENCES {self._conversation_table_name}(conversation_id) ON DELETE CASCADE,
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
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self._conversation_table_name}_owner
            ON {self._conversation_table_name} (environment, user_id, conversation_id)
            """,
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{self._conversation_table_name}_request_id
            ON {self._conversation_table_name} (environment, user_id, request_id)
            WHERE request_id IS NOT NULL
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self._table_name}_conversation_id
            ON {self._table_name} (conversation_id, id)
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{self._turn_events_table_name}_conversation_turn
            ON {self._turn_events_table_name} (conversation_id, turn_index, step_index)
            """,
        ]

        for statement in statements:
            try:
                self._execute_statement(statement)
            except ClientError as e:
                log.warning("Failed to ensure chat history schema element: %s", e)
                raise

        log.info(
            "Chat history schema ensured: %s, %s, %s",
            self._conversation_table_name,
            self._table_name,
            self._turn_events_table_name,
        )

    def _convert_data_api_value(self, col: Dict[str, Any]) -> Any:
        """Convert Data API column value to Python type."""
        # Data API returns values as dicts with type keys like 'stringValue', 'longValue', etc.
        if 'stringValue' in col:
            return col['stringValue']
        elif 'longValue' in col:
            return col['longValue']
        elif 'doubleValue' in col:
            return col['doubleValue']
        elif 'booleanValue' in col:
            return col['booleanValue']
        elif 'blobValue' in col:
            return col['blobValue']
        elif 'isNull' in col and col['isNull']:
            return None
        else:
            return None

    def _validated_uuid(self, conversation_id: str) -> str:
        """Validate and normalize a UUID conversation id."""
        try:
            return str(uuid.UUID(conversation_id))
        except ValueError:
            raise ValueError("conversation_id must be a UUID")

    def _nullable_string_parameter(self, name: str, value: Optional[str]) -> Dict[str, Any]:
        if value is None or value == "":
            return {"name": name, "value": {"isNull": True}}
        return {"name": name, "value": {"stringValue": value}}

    def _nullable_long_parameter(self, name: str, value: Optional[int]) -> Dict[str, Any]:
        if value is None:
            return {"name": name, "value": {"isNull": True}}
        return {"name": name, "value": {"longValue": int(value)}}

    def _nullable_json_parameter(self, name: str, value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if value is None:
            return {"name": name, "value": {"isNull": True}}
        return {"name": name, "value": {"stringValue": json.dumps(value)}}

    def _conversation_from_record(self, record: List[Dict[str, Any]], *, created: bool = False, idempotent_replay: bool = False) -> ConversationRecord:
        metadata_raw = self._convert_data_api_value(record[7])
        metadata: Dict[str, Any] = {}
        if metadata_raw:
            try:
                metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        return ConversationRecord(
            conversation_id=self._convert_data_api_value(record[0]),
            user_id=self._convert_data_api_value(record[1]),
            title=self._convert_data_api_value(record[2]),
            status=self._convert_data_api_value(record[3]) or "active",
            message_count=self._convert_data_api_value(record[4]) or 0,
            summary=self._convert_data_api_value(record[5]),
            summary_message_count=self._convert_data_api_value(record[6]) or 0,
            metadata=metadata,
            created=created,
            idempotent_replay=idempotent_replay,
        )

    def _select_conversation(self, *, conversation_id: str, user_id: str) -> Optional[ConversationRecord]:
        sql = f"""
            SELECT conversation_id::text, user_id, title, status, message_count, summary, summary_message_count, metadata
            FROM {self._conversation_table_name}
            WHERE conversation_id = CAST(:conversation_id AS uuid)
              AND user_id = :user_id
              AND environment = :environment
            LIMIT 1
        """
        parameters = [
            {"name": "conversation_id", "value": {"stringValue": conversation_id}},
            {"name": "user_id", "value": {"stringValue": user_id}},
            {"name": "environment", "value": {"stringValue": self._environment}},
        ]
        response = self._execute_statement(sql, parameters)
        records = response.get("records", [])
        if not records:
            return None
        return self._conversation_from_record(records[0])

    def _select_conversation_by_request(self, *, user_id: str, request_id: str) -> Optional[ConversationRecord]:
        sql = f"""
            SELECT conversation_id::text, user_id, title, status, message_count, summary, summary_message_count, metadata
            FROM {self._conversation_table_name}
            WHERE user_id = :user_id
              AND environment = :environment
              AND request_id = :request_id
            LIMIT 1
        """
        parameters = [
            {"name": "user_id", "value": {"stringValue": user_id}},
            {"name": "environment", "value": {"stringValue": self._environment}},
            {"name": "request_id", "value": {"stringValue": request_id}},
        ]
        response = self._execute_statement(sql, parameters)
        records = response.get("records", [])
        if not records:
            return None
        return self._conversation_from_record(records[0], idempotent_replay=True)

    def _derive_title(self, title_seed: Optional[str]) -> str:
        cleaned = " ".join((title_seed or "").split())
        if not cleaned:
            return "New conversation"
        return cleaned[:80]

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
        if conversation_id:
            normalized_id = self._validated_uuid(conversation_id)
            record = self._select_conversation(conversation_id=normalized_id, user_id=user_id)
            if not record:
                raise ConversationAccessError("Conversation was not found for this user.")
            return record

        if request_id:
            existing = self._select_conversation_by_request(user_id=user_id, request_id=request_id)
            if existing:
                return existing

        new_conversation_id = str(uuid.uuid4())
        title = self._derive_title(title_seed)
        conversation_metadata = metadata or {}
        sql = f"""
            INSERT INTO {self._conversation_table_name}
                (conversation_id, user_id, environment, title, request_id, metadata)
            VALUES
                (CAST(:conversation_id AS uuid), :user_id, :environment, :title, :request_id, CAST(:metadata AS jsonb))
        """
        parameters = [
            {"name": "conversation_id", "value": {"stringValue": new_conversation_id}},
            {"name": "user_id", "value": {"stringValue": user_id}},
            {"name": "environment", "value": {"stringValue": self._environment}},
            {"name": "title", "value": {"stringValue": title}},
            self._nullable_string_parameter("request_id", request_id),
            {"name": "metadata", "value": {"stringValue": json.dumps(conversation_metadata)}},
        ]

        try:
            self._execute_statement(sql, parameters)
        except ClientError as e:
            if request_id and "duplicate" in str(e).lower():
                existing = self._select_conversation_by_request(user_id=user_id, request_id=request_id)
                if existing:
                    return existing
            raise

        return ConversationRecord(
            conversation_id=new_conversation_id,
            user_id=user_id,
            title=title,
            metadata=conversation_metadata,
            created=True,
        )

    def get_messages(self, conversation_id: str, limit: Optional[int] = None) -> List[BaseMessage]:
        """
        Retrieve visible chat messages for a conversation.
        
        Args:
            conversation_id: Backend-generated UUID conversation id
            limit: Optional maximum number of latest messages to return
        
        Returns:
            List of messages in the conversation
        """
        normalized_id = self._validated_uuid(conversation_id)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT :limit"

        sql = f"""
            SELECT message
            FROM (
                SELECT id, message
                FROM {self._table_name}
                WHERE conversation_id = CAST(:conversation_id AS uuid)
                ORDER BY id DESC
                {limit_clause}
            ) recent_messages
            ORDER BY id ASC
        """
        
        parameters = [
            {"name": "conversation_id", "value": {"stringValue": normalized_id}},
        ]
        if limit is not None:
            parameters.append({"name": "limit", "value": {"longValue": int(limit)}})

        try:
            response = self._execute_statement(sql, parameters)
            records = response.get('records', [])
            
            # Extract message JSONB values from records
            message_dicts = []
            for record in records:
                # Record contains one column: message (JSONB)
                message_value = self._convert_data_api_value(record[0])
                if message_value:
                    try:
                        # Parse JSONB string to dict
                        message_dict = json.loads(message_value) if isinstance(message_value, str) else message_value
                        message_dicts.append(message_dict)
                    except (json.JSONDecodeError, TypeError) as e:
                        log.warning(f"Failed to parse message JSON: {e}")
                        continue
            
            # Convert dicts to BaseMessage objects using langchain's utility
            messages = messages_from_dict(message_dicts)
            
            log.info(f"Retrieved {len(messages)} messages for conversation {normalized_id}")
            return messages
            
        except Exception as e:
            log.error(f"Failed to retrieve messages: {e}")
            raise

    def append_messages(self, conversation_id: str, messages: List[BaseMessage], metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Append messages to a conversation and optionally store metadata.
        
        Args:
            conversation_id: Backend-generated UUID conversation id
            messages: Messages to append
            metadata: Optional metadata to store (e.g., retrieval_filters)
        """
        if not messages:
            return

        normalized_id = self._validated_uuid(conversation_id)
        for message in messages:
            message_dict = message_to_dict(message)
            message_json = json.dumps(message_dict)
            
            sql = f"""
                INSERT INTO {self._table_name} (conversation_id, message)
                VALUES (CAST(:conversation_id AS uuid), CAST(:message AS jsonb))
            """
            
            parameters = [
                {"name": "conversation_id", "value": {"stringValue": normalized_id}},
                {"name": "message", "value": {"stringValue": message_json}},
            ]
            
            try:
                self._execute_statement(sql, parameters)
            except Exception as e:
                log.error(f"Failed to insert message: {e}")
                raise
        
        update_sql = f"""
            UPDATE {self._conversation_table_name}
            SET
                message_count = message_count + :message_count,
                last_message_at = NOW(),
                updated_at = NOW(),
                metadata = metadata || CAST(:metadata AS jsonb)
            WHERE conversation_id = CAST(:conversation_id AS uuid)
        """
        update_parameters = [
            {"name": "conversation_id", "value": {"stringValue": normalized_id}},
            {"name": "message_count", "value": {"longValue": len(messages)}},
            {"name": "metadata", "value": {"stringValue": json.dumps(metadata or {})}},
        ]
        try:
            self._execute_statement(update_sql, update_parameters)
        except Exception as e:
            log.warning("Failed to update conversation metadata after append: %s", e)

        log.info(f"Appended {len(messages)} messages to conversation {normalized_id}")

    def append_trace_events(self, conversation_id: str, trace_events: List[Any]) -> None:
        """Persist backend trace events for a conversation turn."""
        if not trace_events:
            return

        normalized_id = self._validated_uuid(conversation_id)
        for trace_event in trace_events:
            record = trace_event.to_record() if hasattr(trace_event, "to_record") else trace_event
            if not isinstance(record, dict):
                continue

            sql = f"""
                INSERT INTO {self._turn_events_table_name}
                    (
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
                VALUES
                    (
                        CAST(:event_id AS uuid),
                        :trace_id,
                        CAST(:conversation_id AS uuid),
                        :turn_index,
                        :step_index,
                        :step_name,
                        :status,
                        CAST(:started_at AS timestamptz),
                        CAST(:completed_at AS timestamptz),
                        :latency_ms,
                        :model_id,
                        CAST(:input_summary AS jsonb),
                        CAST(:output_summary AS jsonb),
                        CAST(:input_payload AS jsonb),
                        CAST(:output_payload AS jsonb),
                        CAST(:error AS jsonb),
                        CAST(:frontend_event AS jsonb),
                        CAST(:metadata AS jsonb)
                    )
            """
            parameters = [
                {"name": "event_id", "value": {"stringValue": record["event_id"]}},
                self._nullable_string_parameter("trace_id", record.get("trace_id")),
                {"name": "conversation_id", "value": {"stringValue": normalized_id}},
                self._nullable_long_parameter("turn_index", record.get("turn_index")),
                {"name": "step_index", "value": {"longValue": int(record["step_index"])}},
                {"name": "step_name", "value": {"stringValue": record["step_name"]}},
                {"name": "status", "value": {"stringValue": record["status"]}},
                self._nullable_string_parameter("started_at", record.get("started_at")),
                self._nullable_string_parameter("completed_at", record.get("completed_at")),
                self._nullable_long_parameter("latency_ms", record.get("latency_ms")),
                self._nullable_string_parameter("model_id", record.get("model_id")),
                self._nullable_json_parameter("input_summary", record.get("input_summary")),
                self._nullable_json_parameter("output_summary", record.get("output_summary")),
                self._nullable_json_parameter("input_payload", record.get("input_payload")),
                self._nullable_json_parameter("output_payload", record.get("output_payload")),
                self._nullable_json_parameter("error", record.get("error")),
                self._nullable_json_parameter("frontend_event", record.get("frontend_event")),
                {"name": "metadata", "value": {"stringValue": json.dumps(record.get("metadata") or {})}},
            ]

            self._execute_statement(sql, parameters)

        log.info("Appended %s trace events to conversation %s", len(trace_events), normalized_id)

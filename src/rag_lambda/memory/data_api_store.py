"""Aurora Data API implementation of chat history storage."""

import json
import time
import uuid
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict

from .base import ChatHistoryStore
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
        region: str = "us-east-1",
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
            region: AWS region
            max_retries: Maximum number of retry attempts
            initial_retry_delay: Initial delay in seconds before first retry
            max_retry_delay: Maximum delay in seconds between retries
        """
        self._db_cluster_arn = db_cluster_arn
        self._db_credentials_secret_arn = db_credentials_secret_arn
        self._database_name = database_name
        self._table_name = table_name
        self._region = region
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
        """Ensure chat history and metadata tables exist."""
        # Create chat history table (matching langchain-postgres schema exactly)
        # Schema matches PostgresChatMessageHistory from langchain_postgres
        create_history_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {self._table_name} (
                id SERIAL PRIMARY KEY,
                session_id UUID NOT NULL,
                message JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """
        
        # Create metadata table (separate from langchain schema, for our custom metadata)
        create_metadata_table_sql = f"""
            CREATE TABLE IF NOT EXISTS {self._table_name}_metadata (
                conversation_id VARCHAR(255) PRIMARY KEY,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        
        # Create index (matching langchain-postgres index name and structure)
        create_index_sql = f"CREATE INDEX IF NOT EXISTS idx_{self._table_name}_session_id ON {self._table_name} (session_id)"
        
        try:
            # Try to create chat history table (IF NOT EXISTS should handle existing tables)
            self._execute_statement(create_history_table_sql)
            log.info(f"Chat history table ensured: {self._table_name}")
        except ClientError as e:
            error_msg = str(e).lower()
            if 'already exists' in error_msg or 'duplicate' in error_msg or 'relation' in error_msg:
                log.info(f"Chat history table already exists: {self._table_name}")
            else:
                log.warning(f"Unexpected error creating chat history table: {e}")
                # Don't raise - table might already exist, continue
        
        try:
            # Create index (matching langchain-postgres)
            self._execute_statement(create_index_sql)
            log.info(f"Index ensured: idx_{self._table_name}_session_id")
        except ClientError as e:
            error_msg = str(e).lower()
            if 'already exists' in error_msg or 'duplicate' in error_msg or 'relation' in error_msg:
                log.debug(f"Index already exists: idx_{self._table_name}_session_id")
            else:
                log.warning(f"Failed to create index (non-critical): {e}")
                # Don't raise - index is optional for functionality
        
        try:
            # Create metadata table (separate from langchain schema)
            self._execute_statement(create_metadata_table_sql)
            log.info(f"Metadata table ensured: {self._table_name}_metadata")
        except ClientError as e:
            error_msg = str(e).lower()
            if 'already exists' in error_msg or 'duplicate' in error_msg or 'relation' in error_msg:
                log.info(f"Metadata table already exists: {self._table_name}_metadata")
            else:
                log.warning(f"Unexpected error creating metadata table: {e}")
                # Don't raise - table might already exist, continue

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

    def get_messages(self, conversation_id: str) -> List[BaseMessage]:
        """
        Retrieve messages for a conversation.
        
        Args:
            conversation_id: Unique identifier for the conversation (will be converted to UUID)
        
        Returns:
            List of messages in the conversation
        """
        # Convert conversation_id to UUID (langchain_postgres requires UUID)
        try:
            session_id_uuid = uuid.UUID(conversation_id)
        except ValueError:
            # If conversation_id is not a valid UUID, try to convert it
            # Generate a deterministic UUID from the conversation_id string
            session_id_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, conversation_id)
            log.warning(f"conversation_id '{conversation_id}' is not a valid UUID, using generated UUID: {session_id_uuid}")
        
        # Query matches langchain_postgres format: SELECT message FROM table WHERE session_id = ? ORDER BY id
        # Cast the parameter to UUID since the column is UUID type
        sql = f"""
            SELECT message
            FROM {self._table_name}
            WHERE session_id = :session_id::uuid
            ORDER BY id
        """
        
        parameters = [
            {'name': 'session_id', 'value': {'stringValue': str(session_id_uuid)}}
        ]
        
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
            
            log.info(f"Retrieved {len(messages)} messages for conversation {conversation_id}")
            return messages
            
        except Exception as e:
            log.error(f"Failed to retrieve messages: {e}")
            raise

    def append_messages(self, conversation_id: str, messages: List[BaseMessage], metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Append messages to a conversation and optionally store metadata.
        
        Args:
            conversation_id: Unique identifier for the conversation (will be converted to UUID)
            messages: Messages to append
            metadata: Optional metadata to store (e.g., retrieval_filters)
        """
        if not messages:
            return
        
        # Convert conversation_id to UUID (langchain_postgres requires UUID)
        try:
            session_id_uuid = uuid.UUID(conversation_id)
        except ValueError:
            # If conversation_id is not a valid UUID, generate a deterministic UUID
            session_id_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, conversation_id)
            log.warning(f"conversation_id '{conversation_id}' is not a valid UUID, using generated UUID: {session_id_uuid}")
        
        # Insert messages one by one (matching langchain_postgres format)
        # Format: INSERT INTO table (session_id, message) VALUES (?, ?)
        for message in messages:
            # Convert message to dict using langchain's utility (matches langchain_postgres)
            message_dict = message_to_dict(message)
            message_json = json.dumps(message_dict)
            
            sql = f"""
                INSERT INTO {self._table_name} (session_id, message)
                VALUES (:session_id::uuid, :message::jsonb)
            """
            
            parameters = [
                {'name': 'session_id', 'value': {'stringValue': str(session_id_uuid)}},
                {'name': 'message', 'value': {'stringValue': message_json}}
            ]
            
            try:
                self._execute_statement(sql, parameters)
            except Exception as e:
                log.error(f"Failed to insert message: {e}")
                raise
        
        # Store metadata if provided
        if metadata:
            sql = f"""
                INSERT INTO {self._table_name}_metadata (conversation_id, metadata, updated_at)
                VALUES (:conversation_id, :metadata::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (conversation_id) 
                DO UPDATE SET 
                    metadata = {self._table_name}_metadata.metadata || EXCLUDED.metadata::jsonb,
                    updated_at = CURRENT_TIMESTAMP
            """
            
            parameters = [
                {'name': 'conversation_id', 'value': {'stringValue': conversation_id}},
                {'name': 'metadata', 'value': {'stringValue': json.dumps(metadata)}}
            ]
            
            try:
                self._execute_statement(sql, parameters)
            except Exception as e:
                log.error(f"Failed to store metadata: {e}")
                # Don't raise - metadata is optional
                log.warning(f"Metadata storage failed but continuing: {e}")
        
        log.info(f"Appended {len(messages)} messages to conversation {conversation_id}")


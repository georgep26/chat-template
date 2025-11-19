"""
Conversation history integration using SQLChatMessageHistory.
Persists chat messages to Postgres for multi-turn context.
"""
import uuid
import psycopg2
from langchain_community.chat_message_histories.sql import SQLChatMessageHistory
from .prompt_config import PG_DSN


def conversation_id_exists(conversation_id: str) -> bool:
    """
    Check if a conversation ID exists in the database.
    
    Args:
        conversation_id: The conversation ID to check
    
    Returns:
        True if the conversation ID exists, False otherwise
    """
    if not PG_DSN:
        return False
    
    try:
        conn = psycopg2.connect(PG_DSN)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT conversation_id FROM messages WHERE conversation_id = %s",
            (conversation_id,)
        )
        exists = cursor.fetchone() is not None
        cursor.close()
        conn.close()
        return exists
    except Exception:
        # If there's an error (e.g., table doesn't exist), assume ID doesn't exist
        return False


def get_conversation_id() -> str:
    """
    Generate a new unique conversation ID that does not exist in the database.
    Uses UUID v4 and checks for conflicts, retrying if necessary.
    
    Returns:
        A unique conversation ID (UUID string)
    """
    max_retries = 10
    for _ in range(max_retries):
        new_id = str(uuid.uuid4())
        if not conversation_id_exists(new_id):
            return new_id
    
    # If we've exhausted retries (highly unlikely with UUID), raise an error
    raise RuntimeError("Failed to generate unique conversation ID after multiple attempts")


def get_history(conversation_id: str) -> SQLChatMessageHistory:
    """
    Creates/returns a chat history adapter backed by Postgres.
    LangChain will read+append messages for this session_id.
    
    Args:
        conversation_id: Unique identifier for the conversation session
    
    Returns:
        SQLChatMessageHistory instance configured for the conversation
    """
    return SQLChatMessageHistory(
        session_id=conversation_id,
        connection_string=PG_DSN,
        table_name="messages",               # LangChain's default table name
        session_id_field_name="conversation_id",
    )


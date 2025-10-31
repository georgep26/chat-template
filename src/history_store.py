"""
Conversation history integration using SQLChatMessageHistory.
Persists chat messages to Postgres for multi-turn context.
"""
from langchain_community.chat_message_histories.sql import SQLChatMessageHistory
from .prompt_config import PG_DSN


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


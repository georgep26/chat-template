"""Base interface for chat history storage."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage


class ChatHistoryStore(ABC):
    """Abstract base class for chat history storage backends."""

    @abstractmethod
    def get_messages(self, conversation_id: str) -> List[BaseMessage]:
        """
        Retrieve messages for a conversation.

        Args:
            conversation_id: Unique identifier for the conversation

        Returns:
            List of messages in the conversation
        """
        ...

    @abstractmethod
    def append_messages(self, conversation_id: str, messages: List[BaseMessage], metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Append messages to a conversation.

        Args:
            conversation_id: Unique identifier for the conversation
            messages: Messages to append
            metadata: Optional metadata to store with the conversation (e.g., retrieval_filters)
        """
        ...


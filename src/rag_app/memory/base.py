"""Base interface for chat history storage."""

from abc import ABC, abstractmethod
from typing import List

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
    def append_messages(self, conversation_id: str, messages: List[BaseMessage]) -> None:
        """
        Append messages to a conversation.

        Args:
            conversation_id: Unique identifier for the conversation
            messages: Messages to append
        """
        ...


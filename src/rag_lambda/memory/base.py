"""Base interface for chat history storage."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage


@dataclass(frozen=True)
class ConversationRecord:
    """Resolved conversation metadata returned by a history store."""

    conversation_id: str
    user_id: str
    title: Optional[str] = None
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)
    message_count: int = 0
    summary: Optional[str] = None
    summary_message_count: int = 0
    created: bool = False
    idempotent_replay: bool = False


class ConversationAccessError(Exception):
    """Raised when a conversation does not exist or is not owned by the user."""


class ChatHistoryStore(ABC):
    """Abstract base class for chat history storage backends."""

    def resolve_or_create_conversation(
        self,
        *,
        user_id: str,
        conversation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        title_seed: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConversationRecord:
        """
        Resolve an existing conversation or create a new backend-owned id.

        Concrete persistent stores should override this method to verify
        ownership. The default exists for simple in-memory fakes used by tests.
        """
        del request_id, title_seed, metadata
        resolved_id = conversation_id or str(uuid.uuid4())
        return ConversationRecord(
            conversation_id=resolved_id,
            user_id=user_id,
            created=conversation_id is None,
        )

    @abstractmethod
    def get_messages(self, conversation_id: str, limit: Optional[int] = None) -> List[BaseMessage]:
        """
        Retrieve messages for a conversation.

        Args:
            conversation_id: Unique identifier for the conversation
            limit: Optional maximum number of latest messages to return

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

    def append_trace_events(self, conversation_id: str, trace_events: List[Any]) -> None:
        """
        Append internal workflow trace events for a conversation turn.

        Stores that do not support trace persistence can leave this as a no-op.
        """
        del conversation_id, trace_events

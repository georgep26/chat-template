"""Response shapes for the history API Lambda."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ConversationSummary(BaseModel):
    """One conversation row in the list response."""

    conversation_id: str
    title: Optional[str] = None
    status: str = "active"
    message_count: int = 0
    created_at: Optional[str] = None
    last_message_at: Optional[str] = None


class ListConversationsResponse(BaseModel):
    conversations: List[ConversationSummary]
    next_offset: Optional[int] = None


class MessageRecord(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: Optional[str] = None
    sources: Optional[List[Dict[str, Any]]] = None


class GetMessagesResponse(BaseModel):
    conversation_id: str
    messages: List[MessageRecord]


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class RenameResponse(BaseModel):
    conversation: ConversationSummary


class DeleteResponse(BaseModel):
    conversation_id: str
    deleted: bool = True

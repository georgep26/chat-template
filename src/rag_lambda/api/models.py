"""Pydantic models for API requests and responses."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request model for chat API."""

    conversation_id: str
    user_id: str
    message: str
    metadata: Dict[str, Any] = {}
    retrieval_filters: Optional[Dict[str, List[str]]] = None


class Source(BaseModel):
    """Source document information."""

    document_id: str
    source_type: str
    score: float
    chunk: str


class ChatResponse(BaseModel):
    """Response model for chat API."""

    conversation_id: str
    answer: str
    sources: List[Source] = []
    trace_id: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


"""Pydantic models for API requests and responses."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request model for chat API."""

    conversation_id: Optional[str] = None
    request_id: Optional[str] = None
    # Deprecated for authenticated clients. The backend derives identity from JWT when auth is enabled.
    user_id: Optional[str] = None
    message: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    retrieval_filters: Optional[Dict[str, List[str]]] = None


class Source(BaseModel):
    """Source document information."""

    citation_number: int
    document_id: str
    source_type: str
    score: float
    chunk: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Response model for chat API."""

    conversation_id: str
    answer: str
    sources: List[Source] = Field(default_factory=list)
    trace_id: Optional[str] = None
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    config: Optional[Dict[str, Any]] = None

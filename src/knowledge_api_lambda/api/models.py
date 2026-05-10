"""Request/response shapes for the Knowledge API Lambda."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# Request shapes ---------------------------------------------------------


class SearchRequest(BaseModel):
    """Vector search request body."""

    query: str = Field(..., min_length=1, description="Natural language query.")
    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Optional Bedrock metadata filter. Accepts the operator form "
            "({'key': 'edition_year', 'greater_than_or_equals': 2021}) or the "
            "legacy dict form ({'document_type': ['pdf','codes']}). The server "
            "will andAll-merge tenant_id and access_tier constraints."
        ),
    )
    top_k: int = Field(default=10, ge=1, le=50)


# Response shapes --------------------------------------------------------


class ChunkSummary(BaseModel):
    """Chunk metadata without artifact content (used for listing)."""

    chunk_id: str
    document_id: str
    chunk_type: str
    chunk_index: Optional[int] = None
    document_title: Optional[str] = None
    title: Optional[str] = None
    chapter: Optional[str] = None
    section: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    token_count: Optional[int] = None
    char_count: Optional[int] = None
    content_type: Optional[str] = None
    access_tier: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ChunkDetail(ChunkSummary):
    """Chunk metadata + artifact content (used for direct fetch)."""

    content: str
    location: str
    source_location: str


class ListChunksResponse(BaseModel):
    chunks: List[ChunkSummary]
    next_offset: Optional[int] = None


class GetChunkResponse(BaseModel):
    chunk: ChunkDetail


class SearchSource(BaseModel):
    """One ranked retrieval result."""

    document_id: str
    chunk_id: Optional[str] = None
    score: float
    source_type: str
    chunk: str
    metadata: Dict[str, Any] = {}


class SearchResponse(BaseModel):
    sources: List[SearchSource]
    result_count: int

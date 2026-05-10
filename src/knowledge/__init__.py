"""Shared knowledge primitives — vector search, chunk reads, S3 artifact fetch.

This package is consumed both by the in-process RAG Lambda graph (via direct
import) and by the Knowledge API Lambda (which exposes these primitives over
HTTP for external agents). Keep this package free of Lambda-specific concerns.
"""

from .authz import build_bedrock_authz_filter, build_sql_authz_clause
from .chunk_read import ChunkReadStore, create_chunk_read_store
from .filters import FilterError, build_bedrock_filter, normalize_filter_input
from .vector_search import VectorSearchResult, search

__all__ = [
    "ChunkReadStore",
    "FilterError",
    "VectorSearchResult",
    "build_bedrock_authz_filter",
    "build_bedrock_filter",
    "build_sql_authz_clause",
    "create_chunk_read_store",
    "normalize_filter_input",
    "search",
]

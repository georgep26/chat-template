"""Pure-function vector search against a Bedrock Knowledge Base.

This module is the single source of truth for "given a query and filters,
return ranked source documents". Both the RAG Lambda's graph node and the
Knowledge API Lambda's ``/search`` route call this function, so any change
to retrieval semantics (filtering, post-processing, source shape) only
needs to happen here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from langchain_aws import AmazonKnowledgeBasesRetriever
from langchain_core.documents import Document

from .authz import DEFAULT_ACCESS_TIERS, merge_user_filter_with_authz


@dataclass
class VectorSearchResult:
    """One ranked retrieval result, with metadata flattened for callers.

    ``raw_metadata`` is the unmodified Bedrock metadata dict (in case a caller
    wants something not yet promoted to a typed field). ``metadata`` is the
    flattened, agent-friendly view: any ``source_metadata`` sidecar fields are
    lifted to the top level, and ``score`` / ``retrieval_location`` are added.
    """

    document: Document
    document_id: str
    chunk_id: Optional[str]
    score: float
    source_type: str
    chunk: str
    metadata: Dict[str, Any]
    raw_metadata: Dict[str, Any]


def search(
    *,
    query: str,
    knowledge_base_id: str,
    region: str,
    tenant_id: str,
    user_filter: Any = None,
    access_tiers: Sequence[str] = DEFAULT_ACCESS_TIERS,
    top_k: int = 10,
) -> List[VectorSearchResult]:
    """Run a Bedrock retrieve with server-side authorization always merged in.

    The user-supplied filter can narrow scope but never widen it: ``tenant_id``
    and ``access_tier`` constraints are appended via ``andAll``.
    """
    if not query or not query.strip():
        raise ValueError("query is required and cannot be empty.")
    if not knowledge_base_id:
        raise ValueError("knowledge_base_id is required.")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer.")

    bedrock_filter = merge_user_filter_with_authz(
        tenant_id=tenant_id,
        access_tiers=access_tiers,
        user_filter=user_filter,
    )

    vector_search_config: Dict[str, Any] = {"numberOfResults": top_k}
    if bedrock_filter:
        vector_search_config["filter"] = bedrock_filter

    retriever = AmazonKnowledgeBasesRetriever(
        knowledge_base_id=knowledge_base_id,
        region_name=region,
        retrieval_config={"vectorSearchConfiguration": vector_search_config},
    )

    docs = retriever.invoke(query)
    return [_to_result(doc) for doc in docs]


def _to_result(doc: Document) -> VectorSearchResult:
    raw_metadata: Dict[str, Any] = dict(doc.metadata or {})
    # Bedrock KB retrieval nests custom sidecar fields under "source_metadata".
    source_meta = raw_metadata.get("source_metadata") or {}
    if not isinstance(source_meta, dict):
        source_meta = {}

    flat: Dict[str, Any] = {**source_meta}
    flat["score"] = raw_metadata.get("score", 0.0)
    if "location" in raw_metadata:
        flat["retrieval_location"] = raw_metadata["location"]

    document_id = (
        source_meta.get("document_id")
        or raw_metadata.get("id")
        or raw_metadata.get("source")
        or "unknown"
    )
    chunk_id = source_meta.get("chunk_id") if isinstance(source_meta, dict) else None
    source_type = (
        source_meta.get("document_type")
        or raw_metadata.get("source_type", "document")
    )
    score = float(raw_metadata.get("score", 0.0) or 0.0)

    return VectorSearchResult(
        document=doc,
        document_id=str(document_id),
        chunk_id=str(chunk_id) if chunk_id else None,
        score=score,
        source_type=str(source_type),
        chunk=doc.page_content or "",
        metadata=flat,
        raw_metadata=raw_metadata,
    )

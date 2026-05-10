"""Retrieval node for RAG pipeline.

Thin wrapper around ``src.knowledge.vector_search`` so the RAG graph and the
Knowledge API Lambda share a single retrieval implementation. Filter operator
translation lives in ``src.knowledge.filters``; tenant + access-tier scoping
lives in ``src.knowledge.authz``.
"""

import json
import os
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from knowledge.vector_search import VectorSearchResult, search

from .state import MessagesState
from ..tracing import get_trace_collector


def _format_metadata_value(value: Any) -> str:
    """Format metadata values deterministically for prompt context."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def format_source_metadata(metadata: Dict[str, Any]) -> str:
    """Format all available source metadata for an answer prompt source block."""
    metadata_lines = []
    for key in sorted(metadata):
        value = metadata[key]
        if value is None:
            continue
        metadata_lines.append(f"{key}: {_format_metadata_value(value)}")
    return "\n".join(metadata_lines) if metadata_lines else "No metadata available."


def format_retrieved_context(docs: List[Document]) -> str:
    """Format retrieved docs as source blocks containing metadata and chunk text."""
    if not docs:
        return "Relevant context:\nNo retrieved source chunks."

    blocks = []
    for index, doc in enumerate(docs, start=1):
        metadata = doc.metadata or {}
        chunk = doc.page_content or ""
        blocks.append(
            "\n".join(
                [
                    f"<source number=\"{index}\">",
                    f"source_number: {index}",
                    "<metadata>",
                    format_source_metadata(metadata),
                    "</metadata>",
                    "<chunk>",
                    chunk,
                    "</chunk>",
                    "</source>",
                ]
            )
        )
    return "Relevant context:\n" + "\n\n".join(blocks)


def _resolve_tenant_id(state: MessagesState) -> str:
    tenant = state.get("tenant_id")
    if isinstance(tenant, str) and tenant:
        return tenant
    return os.getenv("DEFAULT_TENANT_ID") or os.getenv("ENVIRONMENT") or "default"


def retrieve_node(state: MessagesState, config: Optional[RunnableConfig] = None) -> MessagesState:
    """Retrieve relevant documents from knowledge base."""
    collector = get_trace_collector(config)
    trace_step = collector.start_step("retrieve") if collector else None

    retrieval_config = state.get("retrieval_config")
    if not retrieval_config:
        config = config or {}
        app_config = config.get("configurable", {})
        rag_chat_config = app_config.get("rag_chat", {})
        retrieval_config = rag_chat_config.get("retrieval", {})

    if not retrieval_config:
        raise ValueError("retrieval_config is required. Provide it in the state or config.")

    knowledge_base_id = retrieval_config.get("knowledge_base_id")
    if not knowledge_base_id:
        raise ValueError("knowledge_base_id is required. Provide it in the retrieval configuration.")

    region = retrieval_config.get("region", "us-east-1")
    top_k = int(retrieval_config.get("number_of_results", 10))
    user_filter = state.get("retrieval_filters")
    tenant_id = _resolve_tenant_id(state)

    last_user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]

    results: List[VectorSearchResult] = search(
        query=last_user.content,
        knowledge_base_id=knowledge_base_id,
        region=region,
        tenant_id=tenant_id,
        user_filter=user_filter,
        top_k=top_k,
    )
    docs = [r.document for r in results]

    state["messages"].append(
        SystemMessage(
            name="retriever_context",
            content=format_retrieved_context(docs),
        )
    )

    sources = [
        {
            "retrieval_source_number": index,
            "document_id": result.document_id,
            "source_type": result.source_type,
            "score": result.score,
            "chunk": result.chunk,
            "metadata": result.metadata,
        }
        for index, result in enumerate(results, start=1)
    ]
    state["sources"] = sources

    if collector and trace_step:
        collector.complete_step(
            trace_step,
            label="Retrieved sources",
            detail=f"Found {len(sources)} relevant source chunk{'s' if len(sources) != 1 else ''}.",
            input_summary={
                "knowledge_base_id": knowledge_base_id,
                "number_of_results": top_k,
                "filters": user_filter or None,
            },
            output_summary={
                "source_count": len(sources),
                "source_ids": [source["document_id"] for source in sources],
                "scores": [source["score"] for source in sources],
            },
            metadata={"kb_filter_applied": bool(user_filter)},
            frontend_metadata={"source_count": len(sources)},
        )
    return state

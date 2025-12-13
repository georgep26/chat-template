"""Retrieval node for RAG pipeline."""

import os
from typing import Any, Dict, List, Optional

import yaml
from langchain_aws import AmazonKnowledgeBasesRetriever
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from .state import MessagesState


def convert_filters_to_kb_format(retrieval_filters: Dict[str, List[str]]) -> Dict[str, Any]:
    """
    Convert retrieval filters from user format to AWS Knowledge Bases format.
    
    Args:
        retrieval_filters: Dictionary with metadata field names as keys and lists of values as values.
                          Example: {"document_type": ["codes", "town_documents"]}
    
    Returns:
        Dictionary in AWS Knowledge Bases filter format for use in retrieval_config.
        Example: {
            "andAll": [
                {
                    "orAll": [
                        {"equals": {"key": "document_type", "value": "codes"}},
                        {"equals": {"key": "document_type", "value": "town_documents"}}
                    ]
                }
            ]
        }
    """
    if not retrieval_filters:
        return {}
    
    # For each metadata field, create an orAll filter for all its values
    field_filters = []
    for field_name, values in retrieval_filters.items():
        if not values:
            continue
        
        # Create an orAll filter for this field's values
        equals_filters = [
            {"equals": {"key": field_name, "value": value}}
            for value in values
        ]
        
        if len(equals_filters) == 1:
            # Single value, no need for orAll
            field_filters.append(equals_filters[0])
        else:
            # Multiple values, wrap in orAll
            field_filters.append({"orAll": equals_filters})
    
    if not field_filters:
        return {}
    
    # If we have multiple fields, wrap in andAll; otherwise return the single filter
    if len(field_filters) == 1:
        return field_filters[0]
    else:
        return {"andAll": field_filters}


def retrieve_node(state: MessagesState, config: Optional[RunnableConfig] = None) -> MessagesState:
    """Retrieve relevant documents from knowledge base."""
    # Get retrieval config from state (retrieval config is still in state for backward compatibility)
    retrieval_config = state.get("retrieval_config")
    if not retrieval_config:
        # Fallback to config if not in state
        config = config or {}
        app_config = config.get("configurable", {})
        rag_chat_config = app_config.get("rag_chat", {})
        retrieval_config = rag_chat_config.get("retrieval", {})
    
    if not retrieval_config:
        raise ValueError("retrieval_config is required. Provide it in the state or config.")
    
    knowledge_base_id = retrieval_config.get("knowledge_base_id")
    if not knowledge_base_id:
        raise ValueError("knowledge_base_id is required. Provide it in the retrieval configuration.")
    
    # Build vector search configuration
    vector_search_config = {
        "numberOfResults": retrieval_config.get("number_of_results", 10)
    }
    
    # Add filters if provided
    retrieval_filters = state.get("retrieval_filters")
    if retrieval_filters:
        kb_filter = convert_filters_to_kb_format(retrieval_filters)
        if kb_filter:
            vector_search_config["filter"] = kb_filter
    
    # Create retriever with the knowledge_base_id from state
    kb_retriever = AmazonKnowledgeBasesRetriever(
        knowledge_base_id=knowledge_base_id,
        region_name=retrieval_config.get("region"),
        retrieval_config={
            "vectorSearchConfiguration": vector_search_config
        },
    )
    
    last_user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    docs = kb_retriever.invoke(last_user.content)
    
    # Attach retrieved docs as a synthetic system message
    context_text = "\n\n".join(d.page_content for d in docs)
    state["messages"].append(
        SystemMessage(
            name="retriever_context",
            content=f"Relevant context:\n{context_text}",
        )
    )
    
    # Capture document metadata for sources
    sources = []
    for doc in docs:
        source_info = {
            "document_id": doc.metadata.get("id", doc.metadata.get("source", "unknown")),
            "source_type": doc.metadata.get("source_type", "document"),
            "score": doc.metadata.get("score", 0.0),
            "chunk": doc.page_content or "",
        }
        sources.append(source_info)
    state["sources"] = sources
    return state

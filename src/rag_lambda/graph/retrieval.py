"""Retrieval node for RAG pipeline."""

import os
from typing import Dict

import yaml
from langchain_community.retrievers.bedrock import AmazonKnowledgeBasesRetriever
from langchain_core.messages import HumanMessage, SystemMessage

from .state import MessagesState


def retrieve_node(state: MessagesState) -> MessagesState:
    """Retrieve relevant documents from knowledge base."""
    # Get retrieval config from state
    retrieval_config = state.get("retrieval_config")
    if not retrieval_config:
        raise ValueError("retrieval_config is required. Provide it in the state.")
    
    knowledge_base_id = retrieval_config.get("knowledge_base_id")
    if not knowledge_base_id:
        raise ValueError("knowledge_base_id is required. Provide it in the retrieval configuration.")
    
    # Create retriever with the knowledge_base_id from state
    kb_retriever = AmazonKnowledgeBasesRetriever(
        knowledge_base_id=knowledge_base_id,
        region_name=retrieval_config.get("region"),
        retrieval_config={
            "vectorSearchConfiguration": {
                "numberOfResults": retrieval_config.get("number_of_results", 10)
            }
        },
    )
    
    last_user = [m for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    docs = kb_retriever.get_relevant_documents(last_user.content)
    
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
            "snippet": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
        }
        sources.append(source_info)
    state["sources"] = sources
    return state
